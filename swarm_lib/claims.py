"""Atomic-rename task queue primitives.

Producers enqueue tasks via ``enqueue``. Consumers race for them via
``try_claim`` (POSIX ``os.replace`` is the atomic primitive — exactly one
worker wins per task). Completions move the task to ``done/`` or ``failed/``
via ``complete``.

The substrate is the filesystem. There is no broker, no daemon, no database.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class Task:
    task_id: str
    task_type: str
    run_id: str
    created_at: str
    payload: dict
    depends_on: list[str] = field(default_factory=list)
    tier_hint: Optional[str] = None
    created_by: Optional[str] = None
    deadline: Optional[str] = None
    # Internal: where the task file currently lives on disk.
    # Set by try_claim; used by complete to compute target directory.
    _path: Optional[Path] = None


# ---------------------------------------------------------------------------
# Internal I/O helpers
# ---------------------------------------------------------------------------

def _atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON to ``path`` via temp file + rename in same directory.

    Ensures readers never see a partial write. Requires write access to
    ``path.parent``.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        suffix=".tmp",
        prefix=f".{path.name}.",
        dir=path.parent,
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _read_json(path: Path) -> Any:
    with open(path, "r") as f:
        return json.load(f)


def _ensure_run_dir(run_dir: Path) -> Path:
    """Resolve run_dir and create the standard subdirectories."""
    run_dir = Path(run_dir).expanduser().resolve()
    for sub in ("pending", "claimed", "done", "failed", "artifacts"):
        (run_dir / sub).mkdir(parents=True, exist_ok=True)
    return run_dir


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _resolve_run_id(run_dir: Path) -> str:
    """Read run_id from status.json if it exists, otherwise infer from dir name."""
    status_path = run_dir / "status.json"
    if status_path.exists():
        try:
            return _read_json(status_path).get("run_id", run_dir.name)
        except (OSError, json.JSONDecodeError):
            pass
    return run_dir.name


def _completed_task_ids(run_dir: Path) -> set[str]:
    """Return the set of task_ids marked completed in status.json."""
    status_path = run_dir / "status.json"
    if not status_path.exists():
        return set()
    try:
        status = _read_json(status_path)
        return set(status.get("checkpoint", {}).get("completed_tasks", []))
    except (OSError, json.JSONDecodeError):
        return set()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def enqueue(
    run_dir: Path | str,
    task_id: str,
    task_type: str,
    payload: dict,
    depends_on: Optional[list[str]] = None,
    tier_hint: Optional[str] = None,
    created_by: Optional[str] = None,
    deadline: Optional[str] = None,
) -> None:
    """Atomically enqueue a task to ``<run_dir>/pending/<task_id>.json``.

    Idempotent in the sense that re-enqueueing with the same task_id overwrites
    the pending file. Caller is responsible for task_id uniqueness within a
    run.
    """
    rd = _ensure_run_dir(Path(run_dir))
    task_data = {
        "task_id": task_id,
        "task_type": task_type,
        "run_id": _resolve_run_id(rd),
        "created_at": _now_iso(),
        "created_by": created_by,
        "depends_on": depends_on or [],
        "payload": payload,
        "tier_hint": tier_hint,
        "deadline": deadline,
    }
    target = rd / "pending" / f"{task_id}.json"
    _atomic_write_json(target, task_data)


def try_claim(
    run_dir: Path | str,
    worker_id: str,
    task_type_filter: Optional[list[str]] = None,
) -> Optional[Task]:
    """Attempt to atomically claim a pending task.

    Returns a ``Task`` on success (file is now under
    ``claimed/<worker_id>/<task_id>.json``) or ``None`` if nothing claimable.

    Tasks are skipped when:
    - their ``task_type`` is not in ``task_type_filter`` (when provided)
    - any ``depends_on`` entry is not in ``status.json::completed_tasks``
    - another worker won the atomic-rename race
    """
    rd = _ensure_run_dir(Path(run_dir))
    completed = _completed_task_ids(rd)

    pending_dir = rd / "pending"
    worker_claim_dir = rd / "claimed" / worker_id
    worker_claim_dir.mkdir(parents=True, exist_ok=True)

    # Sorted scan for deterministic order across workers
    for task_file in sorted(pending_dir.glob("*.json")):
        try:
            task_data = _read_json(task_file)
        except (json.JSONDecodeError, OSError):
            # Malformed file or it disappeared mid-scan; skip
            continue

        if task_type_filter and task_data.get("task_type") not in task_type_filter:
            continue

        deps = set(task_data.get("depends_on", []))
        if not deps.issubset(completed):
            continue

        # Attempt atomic claim
        target = worker_claim_dir / task_file.name
        try:
            os.replace(task_file, target)
        except OSError:
            # Another worker won this race; try the next candidate
            continue

        return Task(
            task_id=task_data["task_id"],
            task_type=task_data["task_type"],
            run_id=task_data["run_id"],
            created_at=task_data["created_at"],
            payload=task_data["payload"],
            depends_on=task_data.get("depends_on", []),
            tier_hint=task_data.get("tier_hint"),
            created_by=task_data.get("created_by"),
            deadline=task_data.get("deadline"),
            _path=target,
        )

    return None


def complete(
    task: Task,
    success: bool,
    artifact_path: Optional[Path | str] = None,  # informational; caller writes artifacts
) -> None:
    """Move a claimed task to ``done/`` or ``failed/`` and update status.json.

    Caller is expected to have written any artifact files to
    ``<run_dir>/artifacts/`` BEFORE calling this, so done-state is consistent
    with artifact existence on disk.

    The ``artifact_path`` argument is informational only in v0.1 — the lib
    does not move or validate it.
    """
    if task._path is None:
        raise ValueError(
            "Task has no _path set; was it claimed via try_claim? "
            "Tasks constructed manually cannot be completed."
        )

    # claimed/<worker>/<task>.json -> ../../.. = run_dir
    run_dir = task._path.parent.parent.parent
    target_dir = run_dir / ("done" if success else "failed")
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / task._path.name

    os.replace(task._path, target)

    # Update status.json::completed_tasks on success.
    # (status.py will own this fully in Day-3 work; inline for v0.1 substrate.)
    if success:
        status_path = run_dir / "status.json"
        if status_path.exists():
            try:
                status_data = _read_json(status_path)
            except (OSError, json.JSONDecodeError):
                status_data = {"checkpoint": {}}
            ck = status_data.setdefault("checkpoint", {})
            completed = ck.setdefault("completed_tasks", [])
            if task.task_id not in completed:
                completed.append(task.task_id)
            ck["timestamp"] = _now_iso()
            _atomic_write_json(status_path, status_data)
