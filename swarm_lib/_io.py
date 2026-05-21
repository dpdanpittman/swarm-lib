"""Internal I/O helpers shared across swarm_lib modules.

Not part of the public API. Keeps the atomic-write / read / timestamp logic
in one place so ``claims`` and ``status`` don't duplicate it.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


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
