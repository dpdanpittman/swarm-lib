# Example: seven-step-chain

A reference consumer demonstrating swarm-lib's substrate against a
**Tribunal-shaped 7-step skill chain** (intent → plan → implement → review →
verify → classify → incentive).

This is what a real Tribunal port would look like at the substrate layer.
The handler here is a v0.1.0 **stub** — it writes a placeholder artifact
instead of invoking Claude. Swap in `claude -p --model <tier> --skill ...`
where the STUB block is marked in `handler.sh` to get live LLM-driven
behavior.

## What it demonstrates

- **The Yield Rule**: `driver.sh` enqueues the first task + writes
  `status.json`, then **exits immediately**. The driver's context is gone
  before any work runs.
- **Filesystem queue + worker_loop**: A `worker_loop.sh` polls the run
  directory and claims tasks one at a time.
- **Self-chaining handler**: Each handler invocation writes its artifact,
  then enqueues the **next** step with `depends_on=[current_task_id]`.
  No central orchestrator; the chain advances itself.
- **Per-task tier hints**: Each step carries a `tier_hint`
  (`haiku|sonnet|opus`) so a real LLM wiring can route to the right model.
- **`status.json` checkpoint**: After each step, `next_task_id` and
  `summary` update. Any fresh agent reading the file knows exactly
  where the chain is.

## Run it

From the swarm-lib repo root:

```bash
# 1. Install swarm-lib so 'swarm-cli' is on PATH
pip install -e .

# 2. Kick off a chain — captures the run directory
RUN_DIR=$(./examples/seven-step-chain/driver.sh "Audit the swarm-lib claim protocol for race conditions")
echo "Run at: $RUN_DIR"

# 3. Run a worker_loop against it (--max-iterations 7 = the whole chain)
swarm_lib/worker_loop.sh \
  --run-dir "$RUN_DIR" \
  --worker-id w.demo \
  --handler ./examples/seven-step-chain/handler.sh \
  --max-iterations 7 \
  --poll-interval 1

# 4. Inspect what landed
ls "$RUN_DIR/artifacts/"
swarm-cli status-show --run-dir "$RUN_DIR"
```

## Expected end state

After the worker loop completes 7 iterations:

```
$RUN_DIR/
├── BACKLOG.md
├── status.json                         # next_task_id is null; chain done
├── artifacts/
│   ├── t.<run_id>.intent.md
│   ├── t.<run_id>.plan.md
│   ├── t.<run_id>.implement.md
│   ├── t.<run_id>.review.md
│   ├── t.<run_id>.verify.md
│   ├── t.<run_id>.classify.md
│   └── t.<run_id>.incentive.md
├── done/                               # 7 task files, one per step
└── pending/ claimed/ failed/           # all empty
```

## Wiring it up to real Claude calls

Open `handler.sh` and find the `STUB` block. Replace it with something like:

```bash
SKILL_NAME="tribunal-$STEP"
SKILL_FILE="$HOME/src/tribunal/skills/$SKILL_NAME/SKILL.md"

case "$TIER" in
  haiku)  MODEL="claude-haiku-4-5-20251001" ;;
  sonnet) MODEL="claude-sonnet-4-6" ;;
  opus|*) MODEL="claude-opus-4-7" ;;
esac

PROMPT="$BACKLOG_CONTENT"
for prior in $(echo "$TASK_JSON" | jq -r '.payload.input_artifacts[]'); do
  PROMPT+=$'\n\n--- prior artifact: '"$prior"$' ---\n\n'
  PROMPT+=$(cat "$SWARM_RUN_DIR/$prior")
done

claude -p --model "$MODEL" \
  --system-prompt-file "$SKILL_FILE" \
  "$PROMPT" > "$SWARM_ARTIFACT_PATH"
```

The chain plumbing (enqueue-next, status update, done/failed routing) stays identical.

## Why this isn't yet the real Tribunal port

The actual `dpdanpittman/tribunal` repo is a substantial Go project with on-chain
settlement, ed25519-signed findings, clawpatch integration, and ledger
infrastructure. Porting that to use swarm-lib is its own design effort
(track in a separate ADR when ready). This example exists to:

1. Validate swarm-lib's substrate against a Tribunal-shaped workload
2. Give a starting point for the real port when that work begins
3. Serve as a copyable template for other multi-step LLM chains
