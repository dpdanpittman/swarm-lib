"""Orphan recovery — return stuck claims to the pending queue.

A worker that dies mid-task (compaction kill, OOM, rate-limit abort, network
partition, ``kill -9``) leaves its claimed task file in
``claimed/<worker_id>/<task_id>.json`` with no live process to finish it.
Without recovery, that task is silently stuck forever.

The contract:

- ``worker_loop.sh`` writes ``claimed/<worker_id>/.heartbeat`` every N seconds
  while it holds an active claim. The file's mtime advances; its contents are
  free-form (current task_id + ISO timestamp by convention).
- :func:`reap` walks ``claimed/``, finds workers whose heartbeat is older than
  ``stale_after`` (or absent entirely), and atomically moves their open
  claims back to ``pending/``.

Run :func:`reap` from cron, a systemd timer, or worker startup. Idempotent —
calling it on a healthy queue is a no-op.
"""

from __future__ import annotations

import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


HEARTBEAT_FILENAME = ".heartbeat"
DEFAULT_STALE_AFTER_SECONDS = 300  # 5 minutes; conservative for LLM-driven handlers


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass
class ReapedClaim:
    worker_id: str
    task_id: str
    age_seconds: float  # heartbeat age at reap time; -1.0 if no heartbeat existed


@dataclass
class ReapResult:
    """Summary of one :func:`reap` invocation."""
    run_dir: Path
    stale_after_seconds: int
    reaped: list[ReapedClaim]
    skipped_live: list[str]  # worker_ids whose heartbeat was fresh enough

    @property
    def reaped_count(self) -> int:
        return len(self.reaped)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def reap(
    run_dir: Path | str,
    stale_after_seconds: int = DEFAULT_STALE_AFTER_SECONDS,
    now: Optional[float] = None,
) -> ReapResult:
    """Move stale claims back to ``pending/``.

    A claim is stale when the worker's ``.heartbeat`` file mtime is older
    than ``stale_after_seconds``, or no heartbeat file exists at all (a
    worker that never wrote one and is now gone).

    Returns a :class:`ReapResult` describing what was moved. Caller is
    responsible for any logging.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    claimed_dir = run_dir / "claimed"
    pending_dir = run_dir / "pending"
    pending_dir.mkdir(parents=True, exist_ok=True)

    now_ts = now if now is not None else time.time()
    reaped: list[ReapedClaim] = []
    skipped_live: list[str] = []

    if not claimed_dir.exists():
        return ReapResult(
            run_dir=run_dir,
            stale_after_seconds=stale_after_seconds,
            reaped=reaped,
            skipped_live=skipped_live,
        )

    for worker_dir in sorted(claimed_dir.iterdir()):
        if not worker_dir.is_dir():
            continue
        worker_id = worker_dir.name

        # Find any actual claim files (skip the heartbeat sentinel itself)
        claim_files = [p for p in worker_dir.glob("*.json")]
        if not claim_files:
            # Empty worker dir — leave it alone, harmless
            continue

        heartbeat = worker_dir / HEARTBEAT_FILENAME
        age = _heartbeat_age(heartbeat, now_ts)

        if age >= 0 and age < stale_after_seconds:
            skipped_live.append(worker_id)
            continue

        # Stale or missing: move every claim file back to pending/
        for claim_path in claim_files:
            try:
                target = pending_dir / claim_path.name
                # If a same-named task already exists in pending/ (rare but
                # possible if a producer re-enqueued), skip rather than
                # clobber. Caller can inspect and decide.
                if target.exists():
                    continue
                os.replace(claim_path, target)
                reaped.append(ReapedClaim(
                    worker_id=worker_id,
                    task_id=claim_path.stem,
                    age_seconds=age,
                ))
            except OSError:
                # Race with the worker itself coming back to life and
                # completing the task; ignore.
                continue

    return ReapResult(
        run_dir=run_dir,
        stale_after_seconds=stale_after_seconds,
        reaped=reaped,
        skipped_live=skipped_live,
    )


def write_heartbeat(
    run_dir: Path | str,
    worker_id: str,
    note: str = "",
) -> Path:
    """Touch the worker's heartbeat file to ``now``.

    Called from worker_loop.sh between iterations and during long handler
    execution. ``note`` is free-form (typically the current task_id) and
    written into the file for debugging.
    """
    run_dir = Path(run_dir).expanduser().resolve()
    worker_dir = run_dir / "claimed" / worker_id
    worker_dir.mkdir(parents=True, exist_ok=True)
    heartbeat = worker_dir / HEARTBEAT_FILENAME
    heartbeat.write_text(note or "")
    # write_text already updates mtime; explicit utime not needed
    return heartbeat


# ---------------------------------------------------------------------------
# Internal
# ---------------------------------------------------------------------------

def _heartbeat_age(heartbeat: Path, now_ts: float) -> float:
    """Return seconds since heartbeat mtime, or -1.0 if the file is absent."""
    try:
        mtime = heartbeat.stat().st_mtime
    except FileNotFoundError:
        return -1.0
    return max(0.0, now_ts - mtime)
