"""Tests for swarm_lib.orphan — heartbeat write + reaper recovery."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from swarm_lib import claims, orphan, status


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "test_run"


# ---------------------------------------------------------------------------
# write_heartbeat
# ---------------------------------------------------------------------------

def test_write_heartbeat_creates_file(run_dir: Path) -> None:
    path = orphan.write_heartbeat(run_dir, worker_id="w.1")
    assert path.exists()
    assert path.name == orphan.HEARTBEAT_FILENAME
    assert path.parent.name == "w.1"


def test_write_heartbeat_with_note_records_task(run_dir: Path) -> None:
    orphan.write_heartbeat(run_dir, worker_id="w.1", note="t.current")
    path = run_dir / "claimed" / "w.1" / orphan.HEARTBEAT_FILENAME
    assert path.read_text() == "t.current"


def test_write_heartbeat_updates_mtime_on_second_call(run_dir: Path) -> None:
    orphan.write_heartbeat(run_dir, worker_id="w.1")
    path = run_dir / "claimed" / "w.1" / orphan.HEARTBEAT_FILENAME
    first_mtime = path.stat().st_mtime
    # Backdate so we observe the bump
    os.utime(path, (first_mtime - 10, first_mtime - 10))
    orphan.write_heartbeat(run_dir, worker_id="w.1")
    assert path.stat().st_mtime > first_mtime - 10


# ---------------------------------------------------------------------------
# reap
# ---------------------------------------------------------------------------

def _enqueue_and_claim(run_dir: Path, task_id: str, worker_id: str) -> claims.Task:
    """Helper: produce a task and claim it under worker_id."""
    claims.enqueue(run_dir, task_id=task_id, task_type="t", payload={})
    task = claims.try_claim(run_dir, worker_id=worker_id)
    assert task is not None
    return task


def test_reap_empty_queue_is_noop(run_dir: Path) -> None:
    # Ensure subdirs exist via an enqueue+claim, then complete, so claimed/ is empty
    task = _enqueue_and_claim(run_dir, "t.1", "w.1")
    claims.complete(task, success=True)

    result = orphan.reap(run_dir)
    assert result.reaped_count == 0
    assert result.skipped_live == []


def test_reap_returns_stale_claim_to_pending(run_dir: Path) -> None:
    _enqueue_and_claim(run_dir, "t.1", "w.dead")
    # No heartbeat written → reaper treats as stale on first sweep
    result = orphan.reap(run_dir, stale_after_seconds=1)
    assert result.reaped_count == 1
    assert result.reaped[0].worker_id == "w.dead"
    assert result.reaped[0].task_id == "t.1"
    assert (run_dir / "pending" / "t.1.json").exists()
    assert not (run_dir / "claimed" / "w.dead" / "t.1.json").exists()


def test_reap_skips_live_worker(run_dir: Path) -> None:
    _enqueue_and_claim(run_dir, "t.1", "w.alive")
    orphan.write_heartbeat(run_dir, worker_id="w.alive")
    # Heartbeat is fresh; stale_after 60s → worker is live
    result = orphan.reap(run_dir, stale_after_seconds=60)
    assert result.reaped_count == 0
    assert result.skipped_live == ["w.alive"]
    assert (run_dir / "claimed" / "w.alive" / "t.1.json").exists()


def test_reap_reaps_stale_heartbeat(run_dir: Path) -> None:
    _enqueue_and_claim(run_dir, "t.1", "w.stale")
    heartbeat = orphan.write_heartbeat(run_dir, worker_id="w.stale")
    # Backdate heartbeat well past stale window
    backdated = time.time() - 3600
    os.utime(heartbeat, (backdated, backdated))

    result = orphan.reap(run_dir, stale_after_seconds=60)
    assert result.reaped_count == 1
    assert result.reaped[0].worker_id == "w.stale"
    assert result.reaped[0].age_seconds >= 60


def test_reap_does_not_clobber_pending_collision(run_dir: Path) -> None:
    # Edge: a task with the same id is somehow both claimed (stale) AND in pending
    # (e.g. producer re-enqueued before reaper ran). Reap should not clobber.
    _enqueue_and_claim(run_dir, "t.1", "w.dead")
    # Hand-fabricate a pending file with the same id
    pending_collision = run_dir / "pending" / "t.1.json"
    pending_collision.write_text(json.dumps({"task_id": "t.1", "task_type": "t",
                                              "run_id": "x", "created_at": "z",
                                              "payload": {}, "depends_on": []}))

    result = orphan.reap(run_dir, stale_after_seconds=1)
    # Claim file should still be in claimed/ (not clobbered onto pending)
    assert (run_dir / "claimed" / "w.dead" / "t.1.json").exists()
    assert result.reaped_count == 0


def test_reap_handles_multiple_workers_mixed_state(run_dir: Path) -> None:
    _enqueue_and_claim(run_dir, "t.1", "w.live")
    _enqueue_and_claim(run_dir, "t.2", "w.stale")
    _enqueue_and_claim(run_dir, "t.3", "w.no-heartbeat")

    orphan.write_heartbeat(run_dir, worker_id="w.live")

    hb_stale = orphan.write_heartbeat(run_dir, worker_id="w.stale")
    backdated = time.time() - 3600
    os.utime(hb_stale, (backdated, backdated))

    # w.no-heartbeat: never wrote one

    result = orphan.reap(run_dir, stale_after_seconds=60)
    assert result.reaped_count == 2
    reaped_workers = {r.worker_id for r in result.reaped}
    assert reaped_workers == {"w.stale", "w.no-heartbeat"}
    assert result.skipped_live == ["w.live"]
    assert (run_dir / "pending" / "t.2.json").exists()
    assert (run_dir / "pending" / "t.3.json").exists()
    assert (run_dir / "claimed" / "w.live" / "t.1.json").exists()


def test_reaped_task_is_claimable_again(run_dir: Path) -> None:
    """After reap, the task is back in the queue and a new worker can claim it."""
    status.initialize(run_dir, run_id="r1")
    _enqueue_and_claim(run_dir, "t.1", "w.dead")
    orphan.reap(run_dir, stale_after_seconds=1)

    fresh = claims.try_claim(run_dir, worker_id="w.fresh")
    assert fresh is not None
    assert fresh.task_id == "t.1"
    assert "w.fresh" in str(fresh._path)
