# swarm-lib

> Filesystem-as-orchestrator for agentic workflows.

A small Python + Bash library that gives LLM-driven agentic workflows three primitives:

1. **Atomic-rename task queueing** — Maildir physics applied to agent work. Multiple workers race for tasks; exactly one wins. No broker, no external state.
2. **`status.json` checkpointing** — durable handoff. Any agent (Claude Code, ollama, n8n, shell) picks up where the last one stopped by reading a single file.
3. **Generic `worker_loop.sh`** — polls a queue, claims atomically, invokes a handler, moves the result. Workers are interchangeable.

**Status**: v0.2 — substrate hardened. Orphan recovery + status lock + cross-FS check landed; concurrent-worker correctness now under test. See [`DESIGN.md`](DESIGN.md) (or the [rendered HTML](DESIGN.html)) for the full spec.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

## What works today

- ✅ `swarm_lib.claims` — `enqueue` / `try_claim` / `complete` with POSIX atomic-rename + cross-filesystem startup check
- ✅ `swarm_lib.status` — `initialize` / `read` / `write` / `append_completed`, advisory-locked against concurrent writers
- ✅ `swarm_lib.orphan` — heartbeat + `reap()`; stale claims return to `pending/` automatically when the reaper runs
- ✅ `swarm_lib.cli` — `swarm-cli` exposing `enqueue` / `claim` / `complete` / `status-{init,show,write}` / `heartbeat` / `reap` / `ls`
- ✅ `swarm_lib/worker_loop.sh` — generic consumer loop, background heartbeat keeper, `SWARM_LOG_PATH` for incremental progress
- ✅ Multi-worker correctness under test (threaded + subprocess claimants, reaper-during-drain)
- ✅ Reference examples: `examples/seven-step-chain/` (Tribunal-shaped), `examples/hmd-triage/` (cheap classify → expensive escalate)

## What's next

- 🟡 Tribunal port — first real-world consumer; validates the substrate under load
- 🔲 v0.3: multi-host coordination + n8n federation + Kanban UI
- 🔲 PyPI release once Tribunal port stabilizes the API

## Quick install

```bash
git clone https://github.com/dpdanpittman/swarm-lib
cd swarm-lib
pip install -e .
# 'swarm-cli' is now on your PATH
```

## Quick example

```python
from swarm_lib import claims, status

run_dir = "~/swarm-runs/demo-1"

# Producer: write a task
status.initialize(run_dir, run_id="demo-1")
claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={"hello": "world"})

# Consumer: claim and finish it
task = claims.try_claim(run_dir, worker_id="w.demo")
if task:
    # ...do work, write artifacts...
    claims.complete(task, success=True)
```

Or run a generic worker loop against a bash handler:

```bash
cat > handler.sh <<'EOF'
#!/usr/bin/env bash
echo "handling task $SWARM_TASK_ID" > "$SWARM_ARTIFACT_PATH"
EOF
chmod +x handler.sh

swarm_lib/worker_loop.sh \
  --run-dir ~/swarm-runs/demo-1 \
  --worker-id w.demo \
  --handler ./handler.sh \
  --max-iterations 1
```

## Layout

```
swarm-lib/
├── DESIGN.md             # design spec (read this first; includes anti-fleet handler hygiene)
├── DESIGN.html           # rendered HTML companion
├── README.md             # this file
├── pyproject.toml        # package + swarm-cli entry point
├── LICENSE               # MIT
├── site/                 # marketing site (Astro 4 + Tailwind 3)
├── examples/
│   ├── seven-step-chain/ # Tribunal-shaped reference (intent → ... → incentive)
│   └── hmd-triage/       # cheap-classify + conditional-escalate pattern
├── tests/                # pytest suite (42 tests, multi-worker + orphan recovery covered)
│   ├── test_claims.py
│   ├── test_status.py
│   ├── test_orphan.py
│   └── test_multi_worker.py
└── swarm_lib/            # the Python package
    ├── _io.py            # internal: atomic_write_json, read_json, now_iso, status_lock
    ├── claims.py         # enqueue / try_claim / complete + Task + CrossFilesystemError
    ├── status.py         # status.json primitives + Status/Checkpoint
    ├── orphan.py         # write_heartbeat / reap — stuck-claim recovery
    ├── cli.py            # swarm-cli entry point (enqueue/claim/complete/status/heartbeat/reap/ls)
    └── worker_loop.sh    # generic consumer loop + background heartbeat keeper
```

## Handler hygiene

Handlers run with whatever privileges you give them. The Inkcloud
post-mortem includes a cautionary tale about a single agent given root and a
"relentlessly improve" instruction that turned into an internal DoS virus.
DESIGN.md's **Handler hygiene (anti-fleet)** section documents the required

- recommended discipline: confine writes to `$SWARM_RUN_DIR`, don't touch
  other workers' claims, treat payload as untrusted, sandbox where possible
  (workerd, unshare, container, bwrap).

## Why

| Problem                 | Manifestation                                                               | swarm-lib answer                         |
| ----------------------- | --------------------------------------------------------------------------- | ---------------------------------------- |
| Context starvation      | Long conversations accumulate context, hit compaction, mid-flight work dies | Tasks become claimable by fresh contexts |
| Sync tool-call blocking | Planner holds an expensive window open while subprocess churns              | Yield Rule: decompose → enqueue → exit   |
| Chat-history-as-state   | If the conversation dies, the work dies                                     | `status.json` is the durable contract    |

UNIX shops have done this for 30 years (Maildir, cron + lock files, `/var/spool/`). swarm-lib applies the discipline to LLM-driven agent work.

## Inspiration

- The Inkcloud Architecture Post-Mortem ("Unix Swarm Blueprint")
- Maildir (1995), cron + lock files, `/var/spool/`
- GitHub Actions reusable workflows + `workflow_call`

## License

MIT.
