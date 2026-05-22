"""Internal I/O helpers shared across swarm_lib modules.

Not part of the public API. Keeps the atomic-write / read / timestamp logic
in one place so ``claims`` and ``status`` don't duplicate it.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator


def atomic_write_json(path: Path, data: Any) -> None:
    """Atomically write JSON to ``path`` via temp file + rename in same directory.

    Ensures readers never observe a partial write. Requires write access to
    ``path.parent``. The temp file lives in the same directory to guarantee
    the ``os.replace`` happens on the same filesystem (atomic rename across
    filesystems is not guaranteed).
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


def read_json(path: Path) -> Any:
    """Read and parse a JSON file."""
    with open(path, "r") as f:
        return json.load(f)


def now_iso() -> str:
    """Current UTC time as RFC3339-ish string with trailing 'Z'."""
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def status_lock(run_dir: Path) -> Iterator[None]:
    """Advisory exclusive lock for status.json read-modify-write sequences.

    Without this, two workers concurrently calling :func:`status.append_completed`
    race: both read the same ``completed_tasks`` list, both append their own
    task_id, and the second write clobbers the first — silent lost updates.

    Wrapping read-then-write inside ``with status_lock(run_dir): ...`` serializes
    concurrent callers using ``fcntl.flock`` on a sidecar ``.status.lock`` file.
    The lock is advisory (only honored by code that asks for it), but every
    status writer in the lib goes through this helper.

    POSIX-only by design — swarm-lib is already POSIX-bound (Maildir physics,
    os.replace atomicity).
    """
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / ".status.lock"
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)
