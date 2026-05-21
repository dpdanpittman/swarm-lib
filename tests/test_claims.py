"""Tests for swarm_lib.claims — the atomic-rename queue primitives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_lib import claims


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    """Fresh run directory per test."""
    return tmp_path / "test_run"


# ---------------------------------------------------------------------------
# enqueue
# ---------------------------------------------------------------------------

def test_enqueue_creates_pending_file(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={"foo": "bar"})

    target = run_dir / "pending" / "t.1.json"
    assert target.exists()

    data = json.loads(target.read_text())
    assert data["task_id"] == "t.1"
    assert data["task_type"] == "plan"
    assert data["payload"] == {"foo": "bar"}
    assert data["depends_on"] == []
    assert data["tier_hint"] is None
    assert data["created_at"].endswith("Z")


def test_enqueue_with_depends_on_and_tier_hint(run_dir: Path) -> None:
    claims.enqueue(
        run_dir,
        task_id="t.1",
        task_type="implement",
        payload={},
        depends_on=["t.0"],
        tier_hint="sonnet",
        created_by="mabus",
    )
    data = json.loads((run_dir / "pending" / "t.1.json").read_text())
    assert data["depends_on"] == ["t.0"]
    assert data["tier_hint"] == "sonnet"
    assert data["created_by"] == "mabus"


def test_enqueue_creates_subdirs(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    for sub in ("pending", "claimed", "done", "failed", "artifacts"):
        assert (run_dir / sub).is_dir()


# ---------------------------------------------------------------------------
# try_claim
# ---------------------------------------------------------------------------

def test_try_claim_returns_none_when_empty(run_dir: Path) -> None:
    assert claims.try_claim(run_dir, worker_id="w.1") is None


def test_try_claim_atomically_moves_task(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})

    task = claims.try_claim(run_dir, worker_id="w.1")

    assert task is not None
    assert task.task_id == "t.1"
    assert task.task_type == "plan"
    assert not (run_dir / "pending" / "t.1.json").exists()
    assert (run_dir / "claimed" / "w.1" / "t.1.json").exists()


def test_try_claim_respects_task_type_filter(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    claims.enqueue(run_dir, task_id="t.2", task_type="implement", payload={})

    task = claims.try_claim(run_dir, worker_id="w.1", task_type_filter=["implement"])

    assert task is not None
    assert task.task_id == "t.2"
    # The plan task is still pending
    assert (run_dir / "pending" / "t.1.json").exists()


def test_try_claim_skips_unmet_dependencies(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={}, depends_on=["t.0"])

    # t.0 hasn't run, so t.1 should not be claimable
    task = claims.try_claim(run_dir, worker_id="w.1")
    assert task is None


def test_try_claim_honors_completed_dependencies(run_dir: Path, tmp_path: Path) -> None:
    # Pre-seed status.json marking t.0 as completed
    status = {
        "schema_version": "0.1",
        "run_id": "test_run",
        "checkpoint": {"completed_tasks": ["t.0"]},
    }
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps(status))

    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={}, depends_on=["t.0"])
    task = claims.try_claim(run_dir, worker_id="w.1")
    assert task is not None
    assert task.task_id == "t.1"


# ---------------------------------------------------------------------------
# Race semantics
# ---------------------------------------------------------------------------

def test_two_workers_race_exactly_one_wins(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})

    task_a = claims.try_claim(run_dir, worker_id="w.a")
    task_b = claims.try_claim(run_dir, worker_id="w.b")

    winners = [t for t in (task_a, task_b) if t is not None]
    assert len(winners) == 1, "exactly one worker should win the claim"


# ---------------------------------------------------------------------------
# complete
# ---------------------------------------------------------------------------

def test_complete_success_moves_to_done(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    task = claims.try_claim(run_dir, worker_id="w.1")

    claims.complete(task, success=True)

    assert not (run_dir / "claimed" / "w.1" / "t.1.json").exists()
    assert (run_dir / "done" / "t.1.json").exists()


def test_complete_failure_moves_to_failed(run_dir: Path) -> None:
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    task = claims.try_claim(run_dir, worker_id="w.1")

    claims.complete(task, success=False)

    assert (run_dir / "failed" / "t.1.json").exists()
    assert not (run_dir / "done" / "t.1.json").exists()


def test_complete_updates_status_completed_tasks(run_dir: Path) -> None:
    # Pre-seed status.json
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "status.json").write_text(json.dumps({
        "schema_version": "0.1",
        "run_id": "test_run",
        "checkpoint": {"completed_tasks": []},
    }))

    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    task = claims.try_claim(run_dir, worker_id="w.1")
    claims.complete(task, success=True)

    status = json.loads((run_dir / "status.json").read_text())
    assert "t.1" in status["checkpoint"]["completed_tasks"]


def test_complete_without_path_raises(run_dir: Path) -> None:
    """A Task manually constructed (not from try_claim) cannot be completed."""
    task = claims.Task(
        task_id="t.fake",
        task_type="plan",
        run_id="r",
        created_at="2026-01-01T00:00:00Z",
        payload={},
    )
    with pytest.raises(ValueError, match="_path"):
        claims.complete(task, success=True)


# ---------------------------------------------------------------------------
# End-to-end flow
# ---------------------------------------------------------------------------

def test_full_lifecycle_with_dependencies(run_dir: Path) -> None:
    """Enqueue a 2-step chain, claim+complete both, verify ordering via depends_on."""
    claims.enqueue(run_dir, task_id="t.intent", task_type="intent", payload={})
    claims.enqueue(
        run_dir, task_id="t.plan", task_type="plan", payload={}, depends_on=["t.intent"]
    )

    # First claim attempt: only t.intent should be available
    first = claims.try_claim(run_dir, worker_id="w.1")
    assert first is not None
    assert first.task_id == "t.intent"

    # t.plan depends on t.intent; not yet completable
    blocked = claims.try_claim(run_dir, worker_id="w.2")
    assert blocked is None

    # Complete t.intent (also seeds status.json::completed_tasks)
    # Need a status.json for the completed-task tracking to take effect on next claim
    (run_dir / "status.json").write_text(json.dumps({
        "schema_version": "0.1", "run_id": "test_run",
        "checkpoint": {"completed_tasks": []},
    }))
    claims.complete(first, success=True)

    # Now t.plan should be claimable
    second = claims.try_claim(run_dir, worker_id="w.2")
    assert second is not None
    assert second.task_id == "t.plan"
