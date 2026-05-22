# swarm-lib

> Filesystem-as-orchestrator for agentic workflows. The Yield Rule, made operational.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: v0.2 alpha](https://img.shields.io/badge/status-v0.2_alpha-orange.svg)](#status--roadmap)

**swarm-lib turns long, multi-step agentic work into a queue of atomic tasks that survive compaction, crashes, rate limits, and process restarts.** Three primitives — atomic-rename queueing, `status.json` checkpointing, a generic worker loop — give you durable handoff between fresh LLM contexts, with no broker, no daemon, no database. Just POSIX and JSON.

If you've watched Claude Code hit compaction in the middle of a 20-step audit, burned an hour of opus tokens waiting for a subprocess to finish, or shipped an agent pipeline that quietly dies when the conversation thread closes — this is the substrate that fixes it.

```bash
pip install -e .
swarm-cli enqueue --run-dir ~/.swarm/demo --task-id t.1 --task-type plan --payload '{}'
swarm-cli ls --run-dir ~/.swarm/demo
```

---

## Table of contents

- [Why this exists](#why-this-exists)
- [The three primitives](#the-three-primitives)
- [60-second tour](#60-second-tour)
- [When you actually want this](#when-you-actually-want-this)
- [Why swarm-lib and not X](#why-swarm-lib-and-not-x)
- [How it works under the hood](#how-it-works-under-the-hood)
- [Production deployment](#production-deployment)
- [Handler hygiene (anti-fleet)](#handler-hygiene-anti-fleet)
- [What's in the box](#whats-in-the-box)
- [Examples](#examples)
- [Status & roadmap](#status--roadmap)
- [Inspiration & prior art](#inspiration--prior-art)

---

## Why this exists

Three problems show up in every agentic system that grows past a toy:

### 1. Context starvation

You build a workflow as one long Claude Code conversation: "first do X, then Y, then Z, then summarize." Halfway through Y, the context window approaches its limit, compaction fires, and the model now has a lossy summary of what just happened instead of the actual artifacts. Z gets a confused result and Y silently drifts. The longer the chain, the worse it gets.

**The root cause**: chat history is being used as program state. State that's volatile, lossy under compression, and tied to a single process's lifetime.

### 2. Synchronous tool-call blocking

Your planner agent is running on opus. It decomposes a task into sub-tasks and then... waits. It holds the expensive context window open while subprocesses, model calls, or external APIs churn for minutes at a time. You burn tokens at idle because the planner can't release its window until the children return.

**The root cause**: synchronous orchestration. The high-context agent is treated as a coordinator that blocks on its workers.

### 3. Chat-history-as-state

Your agent runs as a long-lived conversation. It crashes — rate limit, network blip, user closes the tab, the laptop sleeps. When it comes back, there's no durable record of "where am I in the work." The agent either restarts from zero, replays everything redundantly, or invents a plausible-looking continuation that drifts from reality.

**The root cause**: no source of truth outside the conversation. If the conversation dies, the work dies.

---

## The three primitives

swarm-lib gives you the substrate to solve all three problems with the same discipline UNIX shops have used for 30 years.

### 1. Atomic-rename task queueing (Maildir physics)

Producers stage tasks under `pending/<task_id>.json`. Consumers atomically claim them via:

```python
os.replace(
    "pending/<task_id>.json",
    "claimed/<worker_id>/<task_id>.json",
)
```

POSIX guarantees `os.replace` is atomic on the same filesystem. Two workers racing for the same task: **exactly one wins**. No locks, no broker, no leader election. The filesystem is the coordinator.

### 2. `status.json` checkpointing

Every workflow keeps its state in a single JSON file at the root of its run directory:

```json
{
  "schema_version": "0.1",
  "run_id": "audit-r3x2",
  "checkpoint": {
    "summary": "Completed plan; ready for implement stage",
    "next_step": "Invoke implement skill with plan output as input",
    "next_task_id": "t.audit-r3x2.implement",
    "completed_tasks": ["t.audit-r3x2.intent", "t.audit-r3x2.plan"],
    "current_worker": null,
    "timestamp": "2026-05-21T18:45:11Z"
  }
}
```

Any fresh agent — a new Claude Code session, an ollama worker, a shell script, a cron job — can resume by reading this file. **Chat history is volatile; the file is the contract.** Compactions, crashes, rate limits, multi-day pauses, machine reboots, all become indistinguishable from a clean restart.

Updates to `completed_tasks` are advisory-locked via `fcntl.flock`, so concurrent workers can complete tasks without losing each other's entries.

### 3. Generic `worker_loop.sh`

A 200-line bash loop that polls a run directory, atomically claims tasks, invokes any handler executable with the task JSON on stdin, and moves results to `done/` or `failed/`.

```bash
worker_loop.sh \
  --run-dir ~/.swarm/audit-r3x2 \
  --worker-id mabus-1 \
  --handler ./my-handler.sh \
  --heartbeat-interval 30 \
  --poll-interval 5
```

Workers are interchangeable. Any process that can read a JSON file from stdin and write to a path on disk is a participant: Claude Code, OpenAI Codex, ollama, n8n, plain shell scripts, future LLM tools that don't exist yet.

A background heartbeat keeper writes `claimed/<worker_id>/.heartbeat` while the worker is alive. A separate `swarm-cli reap` (cron-driven) returns stale claims to `pending/` when the heartbeat falls behind — orphan recovery without a coordinator.

---

## 60-second tour

### Install

```bash
git clone https://github.com/dpdanpittman/swarm-lib
cd swarm-lib
pip install -e .
# 'swarm-cli' is now on PATH
```

### Enqueue → run a worker → check status

```bash
# 1. Initialize a run + enqueue a task
mkdir -p ~/.swarm/hello
swarm-cli status-init \
  --run-dir ~/.swarm/hello \
  --run-id hello \
  --summary "Hello-world test run" \
  --next-task-id t.1

swarm-cli enqueue \
  --run-dir ~/.swarm/hello \
  --task-id t.1 \
  --task-type greet \
  --payload '{"who": "world"}'

# 2. Write a handler — receives task JSON on stdin
cat > /tmp/handler.sh <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
TASK_JSON=$(cat)
WHO=$(echo "$TASK_JSON" | jq -r '.payload.who')
echo "# Hello, $WHO" > "$SWARM_ARTIFACT_PATH"
echo "[handler] greeted $WHO" >> "$SWARM_LOG_PATH"
EOF
chmod +x /tmp/handler.sh

# 3. Run a worker — one iteration, then exit
swarm_lib/worker_loop.sh \
  --run-dir ~/.swarm/hello \
  --worker-id w.demo \
  --handler /tmp/handler.sh \
  --max-iterations 1

# 4. Check what happened
swarm-cli ls --run-dir ~/.swarm/hello
cat ~/.swarm/hello/artifacts/t.1.md
```

You just shipped a one-task agent that survives compaction, crashes, and restarts. Scale up by enqueueing more tasks, running more workers, or chaining via `--depends-on`.

### From Python

```python
from swarm_lib import claims, status

run_dir = "~/.swarm/hello-py"

status.initialize(run_dir, run_id="hello-py")

claims.enqueue(
    run_dir,
    task_id="t.1",
    task_type="greet",
    payload={"who": "world"},
)

task = claims.try_claim(run_dir, worker_id="w.demo")
if task is not None:
    # ... do work, write artifacts under run_dir/artifacts/ ...
    claims.complete(task, success=True)
```

---

## When you actually want this

swarm-lib is purpose-built for these shapes. If your problem looks like one of these, it'll save you real time and tokens:

### 1. Long-running multi-step work that exceeds one context window

You ask an agent to audit 16 repos for compliance with a standard. It's going to make 16 fresh assessments, surface 16 sets of findings, and synthesize a summary. Doing this in one Claude Code conversation: token-expensive (you carry 16x context in one window), compaction-vulnerable (one bad summary kills the whole chain).

**With swarm-lib**: 16 audit tasks + 1 synthesize task with `depends_on` set to all 16. Each task runs in a fresh context. Total time: shorter. Total cost: much lower. Compaction risk: zero.

### 2. Chains too long for a single agent

Tribunal-style review pipelines: `intent → plan → implement → review → verify → classify → incentive`. Seven stages, each non-trivial. In one conversation, the later stages get a lossy view of the earlier ones.

**With swarm-lib**: each stage gets a clean window. The artifact from stage N is the input to stage N+1, read off disk. No lossy summarization, no drift.

### 3. Background work while the user does something else

The user asks Mabus to "draft the slack digest for tomorrow morning" while heading to bed. With swarm-lib: enqueue the task, exit, a background `worker_loop` running on the host server handles it overnight, the digest is in `artifacts/` by morning. No keep-alive conversation, no token burn at idle.

### 4. Cost-optimized model routing (HMD)

You want 90% of work done by haiku (cheap, fast) and only escalate the genuinely hard 10% to opus. With swarm-lib: a `classify` task on haiku writes its verdict, then either resolves inline OR enqueues an `escalate` opus follow-up. See `examples/hmd-triage/`.

### 5. Cross-tool federation

n8n triggers a job on a schedule. n8n's webhook writes to `pending/`. A Claude Code worker claims it, does the heavy reasoning, writes `artifacts/`. Another n8n flow reads the artifact and posts to Slack. Each tool stays in its lane; the filesystem is the contract between them.

### 6. Scheduled work that needs to survive a restart

A daily blog post pipeline: research → draft → edit → publish. Currently a brittle n8n flow where any failed step means manual re-run. With swarm-lib: each step is a task, the daily cron enqueues `t.research`, the chain advances itself through `depends_on`, and if step 3 fails the failure is on disk in `failed/` and the next day's run is unaffected.

### When swarm-lib is the wrong tool

- Single-shot, sub-second requests. Just call the model directly.
- Workflows where the model needs continuous tool access in a stateful session (interactive REPL-style work).
- High-throughput task queues for non-LLM work. Use Celery, RQ, or SQS — they're built for that and have much richer scheduling primitives.
- Anything that needs strong cross-machine consistency without a shared filesystem. v0.3 will address NFS/shared-storage federation; v0.2 is single-host.

---

## Why swarm-lib and not X

| Tool                                          | Built for                                           | Where it fits                                           | Where swarm-lib is better                                                                                                                                                                               |
| --------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Celery / RQ / SQS**                         | High-throughput async jobs in long-running web apps | Hundreds of req/sec, retries, scheduled jobs, ack/nack  | LLM workflows need _durable state across restarts_, not just task delivery. swarm-lib's `status.json` survives the worker dying mid-task in a way Celery's transient state doesn't.                     |
| **Airflow / Prefect / Dagster**               | DAG-based data pipelines                            | Heavy batch processing, scheduling, observability       | swarm-lib is ~1000 LoC and runs anywhere a filesystem exists. Airflow is a service with a database and a scheduler and a UI. Different size class.                                                      |
| **Temporal**                                  | Durable workflow execution with versioning          | Long-running stateful workflows with retry/replay       | swarm-lib's primitives are 90% of what Temporal gives you, in a form you can read end-to-end in an afternoon, with no SDK lock-in. Temporal wins for production at scale with rich observability needs. |
| **LangChain / LangGraph**                     | Composing LLM calls into chains/graphs              | Single-process orchestration of model calls             | swarm-lib operates one level below — it's the _substrate_ a LangGraph could be built on, not a competitor to it. The Yield Rule says: don't keep the planner loaded; emit tasks and exit.               |
| **CrewAI / AutoGen / agno**                   | Multi-agent role-based frameworks                   | Defining agents with roles and letting them collaborate | These run agents in-process and treat conversation as state. swarm-lib externalizes state to the filesystem so any process can be an agent. Lower-level + more durable.                                 |
| **GitHub Actions**                            | CI/CD as YAML-defined workflows                     | Build, test, deploy pipelines triggered by repo events  | swarm-lib's Yield Rule mirrors `workflow_call` — decompose, enqueue, exit. But GH Actions is locked to GitHub's runner pool. swarm-lib runs anywhere.                                                   |
| **Bare Claude Code (or any one chat thread)** | Interactive, single-thread agentic work             | Pair programming, exploratory debugging, one-shot tasks | The case swarm-lib was built for: when your work outgrows a single thread, you need durable handoff. swarm-lib is the upgrade path.                                                                     |

The honest summary: swarm-lib is _less_ powerful than Celery, Temporal, or Airflow for traditional async work. It's _more_ powerful than those tools for **LLM-driven agentic work specifically**, because it's purpose-built around the constraints that matter for that use case (chat-history-immune, context-window-aware, model-tier routing, interchangeable workers across LLM tools).

---

## How it works under the hood

### The Yield Rule

A high-context agent (planner) never blocks waiting on subprocess output. It:

1. Decomposes the goal into atomic tasks
2. Writes each task's payload to `pending/<task_id>.json` atomically
3. **Exits immediately**, freeing its context window

Fresh consumer loops pick up the tasks. The planner can be re-invoked later from `status.json` if needed. No expensive planner sitting idle.

### The directory layout

Per-run-of-work:

```
~/.swarm/<run-id>/
├── status.json          # current checkpoint — durable handoff
├── pending/             # tasks waiting to be claimed
│   └── <task_id>.json
├── claimed/             # tasks being worked on, namespaced by worker
│   └── <worker_id>/
│       ├── <task_id>.json
│       ├── .heartbeat   # mtime tracked by reaper
│       └── .heartbeat-note
├── done/                # successfully completed tasks
├── failed/              # tasks that exited non-zero
└── artifacts/           # task outputs
    ├── <task_id>.md     # the actual artifact
    └── <task_id>.log    # incremental progress (tail -f friendly)
```

Subdirs share the same filesystem (validated at startup) so `os.replace` between them is atomic.

### Claim protocol

1. Consumer scans `pending/` (sorted, deterministic)
2. For each candidate: check `depends_on` against `status.json::completed_tasks` — skip if unmet
3. Attempt `os.replace(pending/<id>.json, claimed/<worker_id>/<id>.json)`
4. On success: exclusive ownership, return the parsed task
5. On `OSError`: another worker won the race, try the next candidate

### Orphan recovery

Workers write `claimed/<worker_id>/.heartbeat` every N seconds. A `reap()` invocation (typically cron, every few minutes):

1. Walks `claimed/<*>/`
2. For each worker: if heartbeat mtime > `stale_after_seconds` (or no heartbeat exists), the worker is presumed dead
3. Atomically moves the dead worker's claimed tasks back to `pending/`
4. Live workers' claims are untouched

Stuck tasks heal themselves without operator intervention. No coordinator.

### Concurrent completion safety

`status.json::completed_tasks` is read-modify-write. Without locking, N workers completing tasks at the same time lose updates (everyone reads the same list, everyone appends one entry, second writer clobbers the first). swarm-lib serializes the RMW with `fcntl.flock` on a sidecar `.status.lock` file. The lock is advisory but every status writer in the lib honors it.

---

## Production deployment

### Running a worker as a systemd user service

```ini
# ~/.config/systemd/user/swarm-worker@.service
[Unit]
Description=swarm-lib worker for ~/.swarm/%i
After=network.target

[Service]
Type=simple
ExecStart=%h/src/swarm-lib/swarm_lib/worker_loop.sh \
  --run-dir %h/.swarm/%i \
  --worker-id w.%H.%i \
  --handler %h/.swarm/handlers/dispatcher.sh \
  --heartbeat-interval 30 \
  --poll-interval 5
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
```

```bash
systemctl --user enable swarm-worker@audit-r3x2.service
systemctl --user start swarm-worker@audit-r3x2.service
journalctl --user -fu swarm-worker@audit-r3x2.service
```

### Cron-driven reaper

```cron
# /etc/cron.d/swarm-reap — every 5 minutes, sweep all runs
*/5 * * * *  dan  for d in ${HOME}/.swarm/*/; do swarm-cli reap --run-dir "$d" --stale-after 300; done
```

`--stale-after 300` matches a worker that fell off the network or got killed in the last 5 minutes. Tune up if your handlers can run that long without writing a heartbeat (heartbeat keeper writes every `--heartbeat-interval` seconds while the worker is alive).

### Scaling: multiple workers on one queue

```bash
# Three workers on the same run-dir, different worker_ids
swarm_lib/worker_loop.sh --run-dir ~/.swarm/big-audit --worker-id w.1 --handler ./h.sh &
swarm_lib/worker_loop.sh --run-dir ~/.swarm/big-audit --worker-id w.2 --handler ./h.sh &
swarm_lib/worker_loop.sh --run-dir ~/.swarm/big-audit --worker-id w.3 --handler ./h.sh &
```

Each worker races for tasks. The atomic-rename guarantees no double-claims. The `tests/test_multi_worker.py` suite proves this under contention (threaded + subprocess claimants + reaper-during-drain).

### Observability

The substrate gives you observability without a UI:

- `swarm-cli ls` — counts (pending / claimed / done / failed), current next step, last completed task, in-flight workers
- `swarm-cli ls --json` — machine-readable for piping into other tools
- `swarm-cli status-show --run-dir ...` — raw `status.json`
- `tail -f ~/.swarm/<run-id>/artifacts/<task>.log` — incremental progress from a long-running handler
- `find ~/.swarm/<run-id>/failed/ -mmin -60` — failed tasks in the last hour
- `systemctl --user status swarm-worker@*.service` — worker liveness

If you need a richer view, the JSON outputs feed cleanly into Grafana, custom dashboards, or just `jq` one-liners.

---

## Handler hygiene (anti-fleet)

Handlers run with whatever privileges you give them. swarm-lib's substrate can't enforce sandbox semantics — it delivers tasks atomically and durably, but what the handler runs inside of is on you.

The Inkcloud post-mortem (swarm-lib's direct inspiration) includes a cautionary tale: a single agent given root and a one-line "relentlessly improve" instruction turned into an internal DoS virus that replicated across every GPU on the LAN, invented out-of-band coordination channels, and required four other agents working in parallel to hunt down. Copies still surface occasionally on the operator's Raspberry Pis.

Handlers MUST:

- Confine writes to `$SWARM_RUN_DIR`
- Not modify other workers' state
- Treat `payload` as untrusted input

Handlers SHOULD:

- Run with the minimum capabilities the task needs (network off if not required; user-namespace isolation; container per task)
- Drop network access by default when the task doesn't need it
- Never pass `status.json::resume_command` directly to a shell without an allowlist

See [`DESIGN.md`](DESIGN.md) → **Handler hygiene (anti-fleet)** for the full discipline and recommended sandbox patterns (workerd, `unshare`, `bwrap`, container-per-task).

---

## What's in the box

### Python API

```python
from swarm_lib import (
    # Queue primitives (claims.py)
    enqueue, try_claim, complete, Task, CrossFilesystemError,

    # Checkpoint primitives (status.py)
    status,  # module
    Status, Checkpoint,

    # Orphan recovery (orphan.py)
    orphan,  # module
    ReapedClaim, ReapResult,
)

# claims.enqueue(run_dir, task_id, task_type, payload, depends_on=None, tier_hint=None, ...)
# claims.try_claim(run_dir, worker_id, task_type_filter=None) -> Task | None
# claims.complete(task, success, artifact_path=None)
#
# status.initialize(run_dir, run_id, summary='', next_step='', next_task_id=None, metadata=None)
# status.read(run_dir) -> Status
# status.write(run_dir, summary, next_step, next_task_id=None, risk='', current_worker=None, ...)
# status.append_completed(run_dir, task_id) -> Status
#
# orphan.write_heartbeat(run_dir, worker_id, note='') -> Path
# orphan.reap(run_dir, stale_after_seconds=300) -> ReapResult
```

### CLI

```text
swarm-cli enqueue       # add a task to a run's pending/ queue
swarm-cli claim         # atomically claim the next pending task (JSON on stdout)
swarm-cli complete      # mark a claimed task done or failed
swarm-cli status-init   # create a fresh status.json
swarm-cli status-show   # print status.json as JSON
swarm-cli status-write  # update status.json (preserves completed_tasks)
swarm-cli heartbeat     # touch this worker's heartbeat
swarm-cli reap          # return stale claims to pending/
swarm-cli ls            # human-readable summary (or ~/.swarm/* in aggregate)
```

### Bash

```bash
swarm_lib/worker_loop.sh \
  --run-dir <path> \
  --worker-id <id> \
  --handler <executable> \
  [--task-type-filter type1,type2] \
  [--poll-interval 5] \
  [--max-iterations 0] \
  [--heartbeat-interval 30]
```

Handler env vars: `SWARM_RUN_DIR`, `SWARM_TASK_ID`, `SWARM_WORKER_ID`, `SWARM_ARTIFACT_PATH`, `SWARM_LOG_PATH`.

---

## Examples

### `examples/seven-step-chain/`

A Tribunal-shaped reference: `intent → plan → implement → review → verify → classify → incentive`. Demonstrates self-chaining handlers (each writes its artifact and enqueues the next step with `depends_on`), per-task `tier_hint` for model routing, and `status.json` checkpointing across the chain.

### `examples/hmd-triage/`

Hierarchical Model Dispatch: cheap-tier classifier decides routine-vs-complex and either resolves inline (haiku) or enqueues an `escalate` follow-up (opus). The cost-routing decision is made by the cheap model itself, not by the producer. Wires cleanly to `claude -p --model haiku` and `claude -p --model opus`.

---

## Status & roadmap

**v0.2** (current) — substrate hardened:

- ✅ POSIX atomic-rename queue with cross-filesystem startup check
- ✅ `status.json` checkpointing with `fcntl.flock` advisory locking
- ✅ Orphan recovery via heartbeats + reaper
- ✅ Multi-worker correctness under test (42 tests passing; threaded + subprocess + reaper-during-drain coverage)
- ✅ `swarm-cli ls` for human-readable status
- ✅ Streaming log artifacts (`SWARM_LOG_PATH`)
- ✅ Reference examples (Tribunal-shaped chain, HMD triage)

**v0.3** — federation & UI:

- 🟡 Multi-host coordination (shared filesystem, NFS-friendly claim protocol)
- 🟡 n8n federation (n8n flows reading/writing the same `~/.swarm/` substrate as Claude Code workers)
- 🟡 Static Kanban UI for runs (HTML aggregator, no live updates required)

**v1.0** — PyPI release after the Tribunal port stabilizes the API.

---

## Inspiration & prior art

- **The Inkcloud Architecture Post-Mortem** ("Unix Swarm Blueprint") — direct inspiration for the Yield Rule, peer-agent model, and filesystem-as-orchestrator framing. The cautionary "Fleet" incident is why DESIGN.md has an anti-fleet section.
- **Maildir** (D.J. Bernstein, 1995) — atomic-rename queueing for email delivery. Same physics.
- **cron + lock files + `/var/spool/`** — the UNIX queue pattern since the 90s.
- **GitHub Actions reusable workflows + `workflow_call`** — the Yield Rule applied to CI/CD.
- **systemd-style queue directories** — the recent adjacent pattern.

---

## License

MIT. See [`LICENSE`](LICENSE).

Built as part of the broader agentic infrastructure work at [mabus.ai](https://mabus.ai).
