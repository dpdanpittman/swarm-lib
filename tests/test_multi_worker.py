"""Multi-worker correctness — concurrent claimants on one queue.

These tests prove that swarm-lib's POSIX atomic-rename claim protocol holds
under contention. M tasks, N workers (M > N), all racing — every task must
be claimed by exactly one worker, no double-claims, no orphans.

Two implementations exercise different concurrency models:

- ``test_concurrent_threaded_claimants_*`` — N threads in the same process
  call ``claims.try_claim`` in a tight loop. Exercises the in-process race
  on ``os.replace``.

- ``test_concurrent_subprocess_claimants_*`` — N subprocesses each running
  ``swarm-cli claim`` in a loop. Exercises the cross-process race (the more
  realistic deployment shape, since ``worker_loop.sh`` instances run as
  separate processes).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from collections import Counter
from pathlib import Path
from typing import Optional

import pytest

from swarm_lib import claims, orphan, status


# ---------------------------------------------------------------------------
# Threaded claimants
# ---------------------------------------------------------------------------

def _drain_via_threads(run_dir: Path, worker_ids: list[str]) -> list[tuple[str, str]]:
    """Run N concurrent threaded claimants until the queue is empty.

    Returns a list of (worker_id, task_id) tuples — one per successful claim.
    """
    results: list[tuple[str, str]] = []
    results_lock = threading.Lock()
    stop_signal = threading.Event()

    def worker(worker_id: str) -> None:
        # Each worker keeps trying until two consecutive Nones — by which
        # point the queue is observably drained from this worker's POV.
        consecutive_none = 0
        while not stop_signal.is_set():
            task = claims.try_claim(run_dir, worker_id=worker_id)
            if task is None:
                consecutive_none += 1
                if consecutive_none >= 2:
                    return
                continue
            consecutive_none = 0
            with results_lock:
                results.append((worker_id, task.task_id))
            claims.complete(task, success=True)

    threads = [threading.Thread(target=worker, args=(w,)) for w in worker_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker thread hung past timeout"
    return results


def test_threaded_claimants_no_double_claim(tmp_path: Path) -> None:
    run_dir = tmp_path / "mw_thread_basic"
    status.initialize(run_dir, run_id="mw1")

    task_count = 50
    for i in range(task_count):
        claims.enqueue(run_dir, task_id=f"t.{i:03d}", task_type="t", payload={})

    results = _drain_via_threads(run_dir, worker_ids=[f"w.{i}" for i in range(5)])

    # Each task claimed exactly once
    claimed_task_ids = [task_id for _, task_id in results]
    assert len(claimed_task_ids) == task_count
    counts = Counter(claimed_task_ids)
    assert all(c == 1 for c in counts.values()), f"double-claims: {counts.most_common(5)}"

    # All tasks ended up in done/
    done_files = list((run_dir / "done").glob("*.json"))
    assert len(done_files) == task_count

    # No orphans
    assert list((run_dir / "pending").glob("*.json")) == []
    for worker_dir in (run_dir / "claimed").iterdir():
        # Heartbeat files are allowed; .json claim files are not
        assert list(worker_dir.glob("*.json")) == [], \
            f"orphan claim in {worker_dir}"


def test_threaded_claimants_completed_tasks_matches_done_dir(tmp_path: Path) -> None:
    run_dir = tmp_path / "mw_thread_status"
    status.initialize(run_dir, run_id="mw2")

    task_count = 30
    for i in range(task_count):
        claims.enqueue(run_dir, task_id=f"t.{i:03d}", task_type="t", payload={})

    _drain_via_threads(run_dir, worker_ids=[f"w.{i}" for i in range(4)])

    s = status.read(run_dir)
    completed_set = set(s.checkpoint.completed_tasks)
    done_set = {p.stem for p in (run_dir / "done").glob("*.json")}
    assert completed_set == done_set, \
        f"status.completed_tasks ({completed_set}) drifted from done/ ({done_set})"


def test_threaded_workers_share_load(tmp_path: Path) -> None:
    """With enough tasks, multiple workers should each claim at least one.

    Not a strict balance test — the deterministic scan order in try_claim
    means worker 0 will tend to win early races. We just assert that the
    work didn't collapse onto a single worker (which would suggest the
    others got starved by something other than the race).
    """
    run_dir = tmp_path / "mw_load"
    status.initialize(run_dir, run_id="mw3")
    for i in range(60):
        claims.enqueue(run_dir, task_id=f"t.{i:03d}", task_type="t", payload={})

    results = _drain_via_threads(run_dir, worker_ids=[f"w.{i}" for i in range(6)])

    by_worker = Counter(w for w, _ in results)
    # With 6 workers vs 60 tasks, at least 2 distinct workers should claim
    # something even in the worst-case scheduling.
    assert len(by_worker) >= 2, f"all tasks went to one worker: {by_worker}"


# ---------------------------------------------------------------------------
# Subprocess claimants — the realistic deployment shape
# ---------------------------------------------------------------------------

def _swarm_cli_available() -> bool:
    try:
        subprocess.run(
            [sys.executable, "-m", "swarm_lib.cli", "--help"],
            capture_output=True,
            timeout=5,
            check=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _drain_via_subprocesses(
    run_dir: Path,
    worker_ids: list[str],
    max_seconds: int = 30,
) -> list[tuple[str, str]]:
    """Run N concurrent ``swarm-cli claim`` subprocesses in a Python wrapper.

    Each subprocess polls; on a successful claim, the parent thread issues
    ``swarm-cli complete --success`` and records the (worker, task) pair.
    """
    results: list[tuple[str, str]] = []
    results_lock = threading.Lock()

    def worker(worker_id: str) -> None:
        consecutive_none = 0
        while consecutive_none < 2:
            proc = subprocess.run(
                [sys.executable, "-m", "swarm_lib.cli", "claim",
                 "--run-dir", str(run_dir),
                 "--worker-id", worker_id],
                capture_output=True,
                text=True,
                timeout=10,
            )
            out = proc.stdout.strip()
            if not out:
                consecutive_none += 1
                continue
            consecutive_none = 0
            task_id = json.loads(out)["task_id"]
            subprocess.run(
                [sys.executable, "-m", "swarm_lib.cli", "complete",
                 "--run-dir", str(run_dir),
                 "--worker-id", worker_id,
                 "--task-id", task_id,
                 "--success"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            with results_lock:
                results.append((worker_id, task_id))

    threads = [threading.Thread(target=worker, args=(w,)) for w in worker_ids]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=max_seconds)
        assert not t.is_alive(), "subprocess worker hung"
    return results


@pytest.mark.skipif(not _swarm_cli_available(), reason="swarm-cli not invocable")
def test_subprocess_claimants_no_double_claim(tmp_path: Path) -> None:
    run_dir = tmp_path / "mw_sub_basic"
    status.initialize(run_dir, run_id="mw_sub_1")

    task_count = 20  # fewer than threaded test — subprocesses are slower
    for i in range(task_count):
        claims.enqueue(run_dir, task_id=f"t.{i:03d}", task_type="t", payload={})

    results = _drain_via_subprocesses(
        run_dir,
        worker_ids=[f"w.{i}" for i in range(4)],
    )

    claimed_task_ids = [task_id for _, task_id in results]
    assert len(claimed_task_ids) == task_count
    counts = Counter(claimed_task_ids)
    assert all(c == 1 for c in counts.values()), f"double-claims: {counts.most_common(5)}"

    assert list((run_dir / "pending").glob("*.json")) == []
    done_files = list((run_dir / "done").glob("*.json"))
    assert len(done_files) == task_count


# ---------------------------------------------------------------------------
# Reaper interaction
# ---------------------------------------------------------------------------

def test_reap_during_drain_does_not_corrupt(tmp_path: Path) -> None:
    """Spawn workers, mid-drain reap stale workers. Final state should be consistent.

    Models the realistic case where one worker dies (no heartbeat) while
    others continue draining; the reaper sweeps in and returns the dead
    worker's claim to pending/, where another worker picks it up.
    """
    run_dir = tmp_path / "mw_reap_drain"
    status.initialize(run_dir, run_id="mw_reap")

    for i in range(20):
        claims.enqueue(run_dir, task_id=f"t.{i:03d}", task_type="t", payload={})

    # One worker "dies" — claims one task and never completes
    dead = claims.try_claim(run_dir, worker_id="w.dead")
    assert dead is not None
    dead_task_id = dead.task_id  # noqa: F841 — for debugging if assertion below fires

    # No heartbeat written → stale_after=0 reaper will reclaim immediately
    reap_result = orphan.reap(run_dir, stale_after_seconds=0)
    assert reap_result.reaped_count == 1

    # Now drain with healthy workers
    results = _drain_via_threads(
        run_dir,
        worker_ids=[f"w.live.{i}" for i in range(3)],
    )

    # Every task (including the once-orphaned one) should be in done/ exactly once
    claimed_task_ids = [task_id for _, task_id in results]
    assert len(claimed_task_ids) == 20
    counts = Counter(claimed_task_ids)
    assert all(c == 1 for c in counts.values())
    done_files = {p.stem for p in (run_dir / "done").glob("*.json")}
    assert len(done_files) == 20
