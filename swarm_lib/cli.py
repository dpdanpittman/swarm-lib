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
  swarm-cli heartbeat     — touch this worker's heartbeat (orphan-recovery contract)
  swarm-cli reap          — return stale claims to pending/
  swarm-cli ls            — human-readable summary of a run (or ~/.swarm/* in aggregate)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

from swarm_lib import claims, orphan, status


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
# heartbeat
# ---------------------------------------------------------------------------

def cmd_heartbeat(args: argparse.Namespace) -> int:
    orphan.write_heartbeat(
        run_dir=args.run_dir,
        worker_id=args.worker_id,
        note=args.note or "",
    )
    return 0


# ---------------------------------------------------------------------------
# reap
# ---------------------------------------------------------------------------

def cmd_reap(args: argparse.Namespace) -> int:
    result = orphan.reap(
        run_dir=args.run_dir,
        stale_after_seconds=args.stale_after,
    )
    output = {
        "run_dir": str(result.run_dir),
        "stale_after_seconds": result.stale_after_seconds,
        "reaped_count": result.reaped_count,
        "reaped": [
            {
                "worker_id": r.worker_id,
                "task_id": r.task_id,
                "age_seconds": r.age_seconds,
            }
            for r in result.reaped
        ],
        "skipped_live": result.skipped_live,
    }
    print(json.dumps(output, indent=2))
    return 0


# ---------------------------------------------------------------------------
# ls — human-readable summary
# ---------------------------------------------------------------------------

# ANSI escape helpers. No-op when stdout isn't a tty (piped, redirected, CI).
def _color(enabled: bool, code: str, text: str) -> str:
    if not enabled:
        return text
    return f"\033[{code}m{text}\033[0m"


def _summarize_run(run_dir: Path, color: bool) -> dict:
    """Return per-run summary dict for ls output."""
    summary: dict = {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "exists": run_dir.exists(),
        "pending": 0,
        "claimed_by_worker": {},
        "done": 0,
        "failed": 0,
        "next_step": None,
        "next_task_id": None,
        "last_completed": None,
        "current_worker": None,
        "completed_count": 0,
    }

    if not run_dir.exists():
        return summary

    pending_dir = run_dir / "pending"
    done_dir = run_dir / "done"
    failed_dir = run_dir / "failed"
    claimed_dir = run_dir / "claimed"

    if pending_dir.exists():
        summary["pending"] = sum(1 for _ in pending_dir.glob("*.json"))
    if done_dir.exists():
        summary["done"] = sum(1 for _ in done_dir.glob("*.json"))
    if failed_dir.exists():
        summary["failed"] = sum(1 for _ in failed_dir.glob("*.json"))
    if claimed_dir.exists():
        for worker_dir in sorted(claimed_dir.iterdir()):
            if not worker_dir.is_dir():
                continue
            n = sum(1 for _ in worker_dir.glob("*.json"))
            if n > 0:
                summary["claimed_by_worker"][worker_dir.name] = n

    try:
        s = status.read(run_dir)
        summary["next_step"] = s.checkpoint.next_step or None
        summary["next_task_id"] = s.checkpoint.next_task_id
        summary["current_worker"] = s.checkpoint.current_worker
        summary["completed_count"] = len(s.checkpoint.completed_tasks)
        if s.checkpoint.completed_tasks:
            summary["last_completed"] = s.checkpoint.completed_tasks[-1]
    except (FileNotFoundError, ValueError):
        # No status.json yet (queue-only run) — fine
        pass

    return summary


def _render_run_text(s: dict, color: bool) -> str:
    """Multi-line plaintext rendering of one run summary."""
    lines = []
    header = _color(color, "1;36", s["run_id"])
    lines.append(f"{header}  {s['run_dir']}")

    pending = s["pending"]
    done = s["done"]
    failed = s["failed"]
    claimed_total = sum(s["claimed_by_worker"].values())

    pending_str = _color(color, "33" if pending else "2", f"pending={pending}")
    claimed_str = _color(color, "34" if claimed_total else "2", f"claimed={claimed_total}")
    done_str = _color(color, "32" if done else "2", f"done={done}")
    failed_str = _color(color, "31" if failed else "2", f"failed={failed}")
    lines.append(f"  {pending_str}  {claimed_str}  {done_str}  {failed_str}")

    if s["claimed_by_worker"]:
        for worker, n in s["claimed_by_worker"].items():
            lines.append(f"    in flight: {worker} ({n})")

    if s["next_task_id"]:
        lines.append(f"  next: {_color(color, '36', s['next_task_id'])}  {s['next_step'] or ''}")
    elif s["next_step"]:
        lines.append(f"  next: {s['next_step']}")

    if s["last_completed"]:
        lines.append(f"  last completed: {s['last_completed']}  ({s['completed_count']} total)")

    return "\n".join(lines)


def cmd_ls(args: argparse.Namespace) -> int:
    color = sys.stdout.isatty() and not args.no_color and not args.json

    # Decide which run-dirs to summarize.
    if args.run_dir:
        run_dirs = [Path(args.run_dir).expanduser().resolve()]
    else:
        root = Path(args.root).expanduser().resolve() if args.root else Path.home() / ".swarm"
        if not root.exists():
            msg = {"error": f"no swarm root at {root}", "hint": "pass --run-dir to summarize a specific run"}
            if args.json:
                print(json.dumps(msg, indent=2))
            else:
                print(f"no swarm root at {root}; pass --run-dir to summarize a specific run", file=sys.stderr)
            return 1
        run_dirs = sorted(p for p in root.iterdir() if p.is_dir())
        if not run_dirs:
            if args.json:
                print(json.dumps([], indent=2))
            else:
                print(f"no runs under {root}", file=sys.stderr)
            return 0

    summaries = [_summarize_run(rd, color) for rd in run_dirs]

    if args.json:
        print(json.dumps(summaries, indent=2))
        return 0

    rendered = [_render_run_text(s, color) for s in summaries]
    print("\n\n".join(rendered))
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

    # heartbeat
    p = sub.add_parser(
        "heartbeat",
        help="touch claimed/<worker>/.heartbeat so the reaper doesn't see this worker as dead",
    )
    p.add_argument("--run-dir", required=True)
    p.add_argument("--worker-id", required=True)
    p.add_argument("--note", help="free-form note (typically current task_id)")
    p.set_defaults(func=cmd_heartbeat)

    # reap
    p = sub.add_parser(
        "reap",
        help="move stale claims back to pending/ (returns JSON summary)",
    )
    p.add_argument("--run-dir", required=True)
    p.add_argument(
        "--stale-after",
        type=int,
        default=orphan.DEFAULT_STALE_AFTER_SECONDS,
        help=f"seconds since last heartbeat to consider stale (default: {orphan.DEFAULT_STALE_AFTER_SECONDS})",
    )
    p.set_defaults(func=cmd_reap)

    # ls
    p = sub.add_parser(
        "ls",
        help="human-readable summary of a run (or ~/.swarm/* if --run-dir omitted)",
    )
    p.add_argument(
        "--run-dir",
        help="single run directory; if omitted, scans --root (default ~/.swarm)",
    )
    p.add_argument("--root", help="parent dir to scan (default: ~/.swarm)")
    p.add_argument("--json", action="store_true", help="emit JSON instead of text")
    p.add_argument("--no-color", action="store_true", help="disable ANSI color")
    p.set_defaults(func=cmd_ls)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
