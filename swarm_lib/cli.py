"""Command-line interface for swarm-lib.

Exposes the lib's primitives over a subprocess boundary so non-Python
consumers (worker_loop.sh, n8n nodes, plain shell scripts) can participate
without importing the Python package.

Subcommands:

  swarm-cli enqueue       — add a task to a run's pending/ queue
  swarm-cli claim         — atomically claim the next pending task (prints JSON on stdout)
  swarm-cli complete      — mark a claimed task done or failed
  swarm-cli status-init   — create a fresh status.json for a new run
  swarm-cli status-show   — print the current status.json as JSON
  swarm-cli status-write  — update status.json (preserves completed_tasks)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from swarm_lib import claims, status


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

def cmd_enqueue(args: argparse.Namespace) -> int:
    payload = json.loads(args.payload) if args.payload else {}
    depends_on = args.depends_on.split(",") if args.depends_on else None
    claims.enqueue(
        run_dir=args.run_dir,
        task_id=args.task_id,
        task_type=args.task_type,
        payload=payload,
        depends_on=depends_on,
        tier_hint=args.tier_hint,
        created_by=args.created_by,
    )
    return 0


# ---------------------------------------------------------------------------
# claim
# ---------------------------------------------------------------------------

def cmd_claim(args: argparse.Namespace) -> int:
    task_type_filter = (
        args.task_type_filter.split(",") if args.task_type_filter else None
    )
    task = claims.try_claim(
        run_dir=args.run_dir,
        worker_id=args.worker_id,
        task_type_filter=task_type_filter,
    )
    if task is None:
        # Empty stdout; exit 0. Caller decides whether to sleep and retry.
        return 0
    # Emit single-line JSON on stdout for shell parsing
    print(json.dumps({
        "task_id": task.task_id,
        "task_type": task.task_type,
        "run_id": task.run_id,
        "created_at": task.created_at,
        "payload": task.payload,
        "depends_on": task.depends_on,
        "tier_hint": task.tier_hint,
        "created_by": task.created_by,
        "deadline": task.deadline,
    }))
    return 0


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

def cmd_complete(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    claimed_path = run_dir / "claimed" / args.worker_id / f"{args.task_id}.json"
    if not claimed_path.exists():
        print(
            f"error: no claimed task at {claimed_path}",
            file=sys.stderr,
        )
        return 1

    try:
        with open(claimed_path) as f:
            d = json.load(f)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: cannot read {claimed_path}: {exc}", file=sys.stderr)
        return 1

    task = claims.Task(
        task_id=d["task_id"],
        task_type=d["task_type"],
        run_id=d["run_id"],
        created_at=d["created_at"],
        payload=d["payload"],
        depends_on=d.get("depends_on", []),
        tier_hint=d.get("tier_hint"),
        created_by=d.get("created_by"),
        deadline=d.get("deadline"),
        _path=claimed_path,
    )

    claims.complete(task, success=args.success)
    return 0


# ---------------------------------------------------------------------------
# status-init
# ---------------------------------------------------------------------------

def cmd_status_init(args: argparse.Namespace) -> int:
    metadata = json.loads(args.metadata) if args.metadata else None
    status.initialize(
        run_dir=args.run_dir,
        run_id=args.run_id,
        summary=args.summary or "Initialized",
        next_step=args.next_step or "",
        next_task_id=args.next_task_id,
        metadata=metadata,
    )
    return 0


# ---------------------------------------------------------------------------
# status-show
# ---------------------------------------------------------------------------

def cmd_status_show(args: argparse.Namespace) -> int:
    try:
        s = status.read(args.run_dir)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps({
        "schema_version": s.schema_version,
        "run_id": s.run_id,
        "checkpoint": {
            "summary": s.checkpoint.summary,
            "next_step": s.checkpoint.next_step,
            "next_task_id": s.checkpoint.next_task_id,
            "risk": s.checkpoint.risk,
            "completed_tasks": s.checkpoint.completed_tasks,
            "current_worker": s.checkpoint.current_worker,
            "timestamp": s.checkpoint.timestamp,
            "resume_command": s.checkpoint.resume_command,
        },
        "metadata": s.metadata,
    }, indent=2))
    return 0


# ---------------------------------------------------------------------------
# status-write
# ---------------------------------------------------------------------------

def cmd_status_write(args: argparse.Namespace) -> int:
    metadata = json.loads(args.metadata) if args.metadata else None
    status.write(
        run_dir=args.run_dir,
        summary=args.summary,
        next_step=args.next_step,
        next_task_id=args.next_task_id,
        risk=args.risk or "",
        current_worker=args.current_worker,
        metadata=metadata,
    )
    return 0


# ---------------------------------------------------------------------------
# entry point
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="swarm-cli",
        description="Command-line interface for swarm-lib's queue and status primitives.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True, metavar="COMMAND")

    # enqueue
    p = sub.add_parser("enqueue", help="add a task to a run's pending/ queue")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--task-id", required=True)
    p.add_argument("--task-type", required=True)
    p.add_argument("--payload", help="JSON string; default '{}'")
    p.add_argument("--depends-on", help="comma-separated task_ids")
    p.add_argument("--tier-hint", help="cost-routing hint, e.g. haiku|sonnet|opus")
    p.add_argument("--created-by", help="identifier for the producer")
    p.set_defaults(func=cmd_enqueue)

    # claim
    p = sub.add_parser("claim", help="atomically claim the next pending task")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--worker-id", required=True)
    p.add_argument("--task-type-filter", help="comma-separated task_types")
    p.set_defaults(func=cmd_claim)

    # complete
    p = sub.add_parser("complete", help="mark a claimed task done or failed")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--worker-id", required=True)
    p.add_argument("--task-id", required=True)
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--success", action="store_true")
    g.add_argument("--failure", dest="success", action="store_false")
    p.set_defaults(func=cmd_complete)

    # status-init
    p = sub.add_parser("status-init", help="create a fresh status.json")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--run-id", required=True)
    p.add_argument("--summary")
    p.add_argument("--next-step")
    p.add_argument("--next-task-id")
    p.add_argument("--metadata", help="JSON string")
    p.set_defaults(func=cmd_status_init)

    # status-show
    p = sub.add_parser("status-show", help="print status.json as JSON")
    p.add_argument("--run-dir", required=True)
    p.set_defaults(func=cmd_status_show)

    # status-write
    p = sub.add_parser("status-write", help="update status.json (preserves completed_tasks)")
    p.add_argument("--run-dir", required=True)
    p.add_argument("--summary", required=True)
    p.add_argument("--next-step", required=True)
    p.add_argument("--next-task-id")
    p.add_argument("--risk")
    p.add_argument("--current-worker")
    p.add_argument("--metadata", help="JSON string")
    p.set_defaults(func=cmd_status_write)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
