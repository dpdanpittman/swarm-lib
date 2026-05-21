"""``status.json`` checkpoint primitives.

A ``status.json`` lives at the root of every swarm-lib run directory. It is
the durable contract for "where are we in this workflow." Any agent — fresh
Claude Code session, ollama-backed worker, plain shell script — can resume
from it without needing chat history.

Schema is documented in ``DESIGN.md``. Version is exposed as
:data:`SCHEMA_VERSION`. The lib refuses to read unknown major versions
(``0.x`` only in this release).
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from swarm_lib._io import atomic_write_json, now_iso, read_json


SCHEMA_VERSION = "0.1"


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class Checkpoint:
    summary: str
    next_step: str
    next_task_id: Optional[str]
    risk: str = ""
    completed_tasks: list[str] = field(default_factory=list)
    current_worker: Optional[str] = None
    timestamp: str = ""
    resume_command: Optional[str] = None  # See DESIGN.md — must be allowlisted before exec


@dataclass
class Status:
    schema_version: str
    run_id: str
    checkpoint: Checkpoint
    metadata: dict = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def initialize(
    run_dir: Path | str,
    run_id: str,
    summary: str = "Initialized",
    next_step: str = "",
    next_task_id: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Status:
    """Create a fresh ``status.json`` for a new run.

    Overwrites any existing file. Use :func:`write` to update without
    resetting ``completed_tasks``.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    status = Status(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        checkpoint=Checkpoint(
            summary=summary,
            next_step=next_step,
            next_task_id=next_task_id,
            timestamp=now_iso(),
        ),
        metadata=metadata or {},
    )
    _write(run_dir, status)
    return status


def read(run_dir: Path | str) -> Status:
    """Read and parse ``status.json``.

    Raises ``FileNotFoundError`` if the file doesn't exist, ``ValueError`` if
    the schema_version is unknown (major mismatch with this lib).
    """
    run_dir = Path(run_dir).expanduser().resolve()
    status_path = run_dir / "status.json"
    if not status_path.exists():
        raise FileNotFoundError(f"No status.json at {status_path}")

    raw = read_json(status_path)

    sv = str(raw.get("schema_version", "0.0"))
    if not sv.startswith("0."):
        raise ValueError(
            f"Unknown schema_version {sv!r} in {status_path}; "
            f"this swarm-lib supports 0.x."
        )

    ck_raw = raw.get("checkpoint", {})
    checkpoint = Checkpoint(
        summary=ck_raw.get("summary", ""),
        next_step=ck_raw.get("next_step", ""),
        next_task_id=ck_raw.get("next_task_id"),
        risk=ck_raw.get("risk", ""),
        completed_tasks=list(ck_raw.get("completed_tasks", [])),
        current_worker=ck_raw.get("current_worker"),
        timestamp=ck_raw.get("timestamp", ""),
        resume_command=ck_raw.get("resume_command"),
    )

    return Status(
        schema_version=sv,
        run_id=raw.get("run_id", run_dir.name),
        checkpoint=checkpoint,
        metadata=dict(raw.get("metadata", {})),
    )


def write(
    run_dir: Path | str,
    summary: str,
    next_step: str,
    next_task_id: Optional[str] = None,
    risk: str = "",
    current_worker: Optional[str] = None,
    resume_command: Optional[str] = None,
    metadata: Optional[dict] = None,
) -> Status:
    """Update ``status.json`` with a new checkpoint.

    Preserves the existing ``completed_tasks`` list (use
    :func:`append_completed` to add to it) and merges ``metadata`` additively.
    """
    run_dir = Path(run_dir).expanduser().resolve()

    try:
        existing = read(run_dir)
        completed = existing.checkpoint.completed_tasks
        run_id = existing.run_id
        existing_metadata = existing.metadata
    except FileNotFoundError:
        completed = []
        run_id = run_dir.name
        existing_metadata = {}

    merged_metadata = {**existing_metadata, **(metadata or {})}

    status = Status(
        schema_version=SCHEMA_VERSION,
        run_id=run_id,
        checkpoint=Checkpoint(
            summary=summary,
            next_step=next_step,
            next_task_id=next_task_id,
            risk=risk,
            completed_tasks=completed,
            current_worker=current_worker,
            timestamp=now_iso(),
            resume_command=resume_command,
        ),
        metadata=merged_metadata,
    )
    _write(run_dir, status)
    return status


def append_completed(run_dir: Path | str, task_id: str) -> Status:
    """Atomically add ``task_id`` to ``status.checkpoint.completed_tasks``.

    If no ``status.json`` exists yet, one is initialized with minimal fields.
    Idempotent — calling twice with the same ``task_id`` is a no-op.
    """
    run_dir = Path(run_dir).expanduser().resolve()

    try:
        status = read(run_dir)
    except FileNotFoundError:
        status = Status(
            schema_version=SCHEMA_VERSION,
            run_id=run_dir.name,
            checkpoint=Checkpoint(
                summary="",
                next_step="",
                next_task_id=None,
                timestamp=now_iso(),
            ),
        )

    if task_id not in status.checkpoint.completed_tasks:
        status.checkpoint.completed_tasks.append(task_id)
    status.checkpoint.timestamp = now_iso()

    _write(run_dir, status)
    return status


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _write(run_dir: Path, status: Status) -> None:
    """Serialize Status to status.json atomically."""
    data = {
        "schema_version": status.schema_version,
        "run_id": status.run_id,
        "checkpoint": asdict(status.checkpoint),
        "metadata": status.metadata,
    }
    atomic_write_json(run_dir / "status.json", data)
