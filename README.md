# swarm-lib

> Filesystem-as-orchestrator for agentic workflows.

A small Python + Bash library that gives LLM-driven agentic workflows three primitives:

1. **Atomic-rename task queueing** — Maildir physics applied to agent work. Multiple workers race for tasks; exactly one wins. No broker, no external state.
2. **`status.json` checkpointing** — durable handoff. Any agent (Claude Code, ollama, n8n, shell) picks up where the last one stopped by reading a single file.
3. **Generic `worker_loop.sh`** — polls a queue, claims, invokes a handler, moves the result. Workers are interchangeable.

**Status**: v0.1 in design. See `DESIGN.md` (or the [rendered HTML](DESIGN.html)) for the full spec.

## Layout

```
swarm-lib/
├── DESIGN.md             # v0.1 design spec
├── DESIGN.html           # HTML companion
├── README.md             # this file
├── site/                 # marketing / presentation site (Astro + Tailwind)
└── swarm_lib/            # the Python package (coming v0.1)
    ├── claims.py         # atomic enqueue / claim / complete primitives
    ├── status.py         # status.json read / write / validate
    └── worker_loop.sh    # generic consumer loop
```

## Why

Agentic workflows today have three structural problems:

| Problem | Manifestation | swarm-lib answer |
| ------- | ------------- | ---------------- |
| Context starvation | Long conversations accumulate context, hit compaction, mid-flight work dies | Tasks become claimable by fresh contexts |
| Sync tool-call blocking | Planner holds an expensive window open while subprocess churns | Yield Rule: decompose → enqueue → exit |
| Chat-history-as-state | If the conversation dies, the work dies | `status.json` is the durable contract |

UNIX shops have done this for 30 years (Maildir, cron + lock files, `/var/spool/`). swarm-lib applies the discipline to LLM-driven agent work.

## Inspiration

Direct inspiration: the Inkcloud Architecture Post-Mortem ("Unix Swarm Blueprint").

Prior art: Maildir (1995), cron + lock files, `/var/spool/`, systemd queue directories, GitHub Actions reusable workflows.

## License

MIT.
