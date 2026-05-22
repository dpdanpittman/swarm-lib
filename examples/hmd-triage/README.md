# Example: HMD triage — cheap classify, conditional escalate

**Hierarchical Model Dispatch (HMD)** lets a cheap model handle most tasks and
only escalates the genuinely hard ones to an expensive model. The pattern is
well-known in cost-sensitive LLM ops — this example shows the swarm-lib shape
for it.

## The flow

```
                  ┌─────────────────────────────────┐
  user enqueues   │     t.classify  (haiku)         │
  one task        │   reads input, decides routine  │
                  │   vs. complex, writes verdict   │
                  └────────────────┬────────────────┘
                                   │
                       verdict.routine?
                                   │
              ┌────────yes─────────┴────────no─────────┐
              │                                        │
              ▼                                        ▼
   classify writes the                  classify enqueues
   final answer itself                  t.escalate (opus)
   and completes.                       with depends_on=[t.classify]
                                        and exits.
                                                       │
                                                       ▼
                                          worker_loop claims
                                          t.escalate, runs opus,
                                          writes final answer.
```

A single user-visible task (`t.classify`) becomes a one-step chain on cheap
work or a two-step chain on hard work. The cost-routing decision is made by
the cheap model, not by the producer.

## What this example demonstrates

- **One task that decides its own continuation.** The classify handler decides
  whether the work is done or needs an escalate task — by reading the task's
  payload and writing either a final artifact OR a new pending task.
- **Tier hints as a contract, not a constraint.** Each task carries
  `tier_hint`; the handler reads it to decide which model to invoke. swarm-lib
  itself doesn't enforce model choice — that's the handler's job.
- **Cost shape**: 90%+ of tasks resolve in `t.classify` alone if the cheap
  classifier is well-calibrated. Only true escalations pay the opus tariff.

## Files

- `classify.sh` — stub handler. Reads task JSON, decides routine vs. complex
  via a deterministic heuristic (input length), writes the final artifact or
  enqueues an escalate follow-up.
- `escalate.sh` — stub handler for the opus tier. In the stub, just writes
  a placeholder; in real use, this is where `claude -p --model opus` lives.
- `driver.sh` — kicks off a run with one classify task.
- `dispatcher.sh` — single handler that routes by `task_type` (calls
  `classify.sh` or `escalate.sh` based on the incoming JSON). This is what
  `worker_loop.sh --handler` actually points at.

## Wiring real LLMs

Both handlers are stubs marked `# STUB:` where the real model call goes.
Replace with:

```bash
# In classify.sh
RESULT=$(claude -p --model haiku --output-file - <<EOF
You are a triage classifier. Decide if the following user request can be
handled by a fast/cheap model or needs an opus-tier model. Respond with
'routine' or 'complex' on the first line, then a one-line reason.

Request: $USER_REQUEST
EOF
)
```

```bash
# In escalate.sh
claude -p --model opus --output-file "$SWARM_ARTIFACT_PATH" <<EOF
$USER_REQUEST
(Pre-classified as complex by triage. Take your time and reason hard.)
EOF
```

You can swap `claude` for `ollama run gpt-oss:20b` if you're routing to a
local model — the contract is just "read stdin, write to artifact path."

## Run it

```bash
# 1. Install swarm-lib so swarm-cli is on PATH
pip install -e ../..

# 2. Kick off a run with one classify task
RUN_DIR=$(./driver.sh "Add two integers")
echo "Run at: $RUN_DIR"

# 3. Run a worker against it (in another shell, or use --max-iterations)
chmod +x dispatcher.sh classify.sh escalate.sh
../../swarm_lib/worker_loop.sh \
  --run-dir "$RUN_DIR" \
  --worker-id w.hmd \
  --handler ./dispatcher.sh \
  --max-iterations 3
```

Then check:

```bash
swarm-cli ls --run-dir "$RUN_DIR"
cat "$RUN_DIR/artifacts/"*.md
```

A short input → resolves in `t.classify` alone. A long input (e.g. paste
500+ chars) → triggers escalation, you'll see a second task land in `done/`.

## When NOT to use HMD

- When tasks are uniformly hard or uniformly easy — the classifier overhead
  doesn't pay for itself.
- When task volume is so low that the cheap-tier latency win is negligible.
- When the classifier itself is the bottleneck (escalation rate >50%).

HMD wins when you have a long-tail distribution of complexity AND volume is
high enough that the cheap-tier savings compound. Typical fits: code review,
support triage, content moderation, content generation pipelines.
