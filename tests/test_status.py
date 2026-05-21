"""Tests for swarm_lib.status — status.json checkpoint primitives."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from swarm_lib import status
from swarm_lib.status import SCHEMA_VERSION


@pytest.fixture
def run_dir(tmp_path: Path) -> Path:
    return tmp_path / "test_run"


# ---------------------------------------------------------------------------
# initialize
# ---------------------------------------------------------------------------

def test_initialize_creates_status_file(run_dir: Path) -> None:
    s = status.initialize(run_dir, run_id="r1", summary="started", next_step="claim t.1")

    assert (run_dir / "status.json").exists()
    assert s.run_id == "r1"
    assert s.schema_version == SCHEMA_VERSION
    assert s.checkpoint.summary == "started"
    assert s.checkpoint.next_step == "claim t.1"
    assert s.checkpoint.completed_tasks == []
    assert s.checkpoint.timestamp.endswith("Z")


def test_initialize_overwrites_existing(run_dir: Path) -> None:
    status.initialize(run_dir, run_id="r1", summary="first")
    status.initialize(run_dir, run_id="r1", summary="second")

    s = status.read(run_dir)
    assert s.checkpoint.summary == "second"


# ---------------------------------------------------------------------------
# read
# ---------------------------------------------------------------------------

def test_read_raises_when_missing(run_dir: Path) -> None:
    with pytest.raises(FileNotFoundError):
        status.read(run_dir)


def test_read_parses_full_schema(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps({
        "schema_version": "0.1",
        "run_id": "r1",
        "checkpoint": {
            "summary": "after plan",
            "next_step": "invoke implement",
            "next_task_id": "t.implement",
            "risk": "depends on stytch routes",
            "completed_tasks": ["t.intent", "t.plan"],
            "current_worker": "mabus-1",
            "timestamp": "2026-05-20T20:00:00Z",
            "resume_command": None,
        },
        "metadata": {"consumer": "tribunal"},
    }))

    s = status.read(run_dir)
    assert s.run_id == "r1"
    assert s.checkpoint.next_task_id == "t.implement"
    assert s.checkpoint.completed_tasks == ["t.intent", "t.plan"]
    assert s.checkpoint.current_worker == "mabus-1"
    assert s.checkpoint.risk == "depends on stytch routes"
    assert s.metadata == {"consumer": "tribunal"}


def test_read_rejects_unknown_major_version(run_dir: Path) -> None:
    run_dir.mkdir(parents=True)
    (run_dir / "status.json").write_text(json.dumps({
        "schema_version": "2.0",
        "run_id": "r1",
        "checkpoint": {},
    }))

    with pytest.raises(ValueError, match="schema_version"):
        status.read(run_dir)


# ---------------------------------------------------------------------------
# write
# ---------------------------------------------------------------------------

def test_write_preserves_completed_tasks(run_dir: Path) -> None:
    status.initialize(run_dir, run_id="r1")
    status.append_completed(run_dir, "t.1")
    status.append_completed(run_dir, "t.2")

    status.write(run_dir, summary="updated", next_step="next thing", next_task_id="t.3")

    s = status.read(run_dir)
    assert s.checkpoint.summary == "updated"
    assert s.checkpoint.next_task_id == "t.3"
    # completed_tasks preserved
    assert s.checkpoint.completed_tasks == ["t.1", "t.2"]


def test_write_merges_metadata_additively(run_dir: Path) -> None:
    status.initialize(run_dir, run_id="r1", metadata={"consumer": "tribunal", "tier": "opus"})
    status.write(
        run_dir,
        summary="x",
        next_step="y",
        metadata={"tier": "sonnet", "extra": "field"},  # tier overridden, extra added
    )

    s = status.read(run_dir)
    assert s.metadata == {"consumer": "tribunal", "tier": "sonnet", "extra": "field"}


def test_write_creates_status_when_missing(run_dir: Path) -> None:
    # Don't initialize first
    status.write(run_dir, summary="fresh", next_step="next", next_task_id="t.1")

    s = status.read(run_dir)
    assert s.checkpoint.summary == "fresh"
    assert s.checkpoint.completed_tasks == []


# ---------------------------------------------------------------------------
# append_completed
# ---------------------------------------------------------------------------

def test_append_completed_appends(run_dir: Path) -> None:
    status.initialize(run_dir, run_id="r1")
    status.append_completed(run_dir, "t.1")
    status.append_completed(run_dir, "t.2")

    s = status.read(run_dir)
    assert s.checkpoint.completed_tasks == ["t.1", "t.2"]


def test_append_completed_is_idempotent(run_dir: Path) -> None:
    status.initialize(run_dir, run_id="r1")
    status.append_completed(run_dir, "t.1")
    status.append_completed(run_dir, "t.1")  # same task_id again

    s = status.read(run_dir)
    assert s.checkpoint.completed_tasks == ["t.1"]


def test_append_completed_creates_status_when_missing(run_dir: Path) -> None:
    # No status.json exists yet
    status.append_completed(run_dir, "t.1")

    s = status.read(run_dir)
    assert s.checkpoint.completed_tasks == ["t.1"]
    assert s.schema_version == SCHEMA_VERSION


# ---------------------------------------------------------------------------
# Integration with claims.complete
# ---------------------------------------------------------------------------

def test_claims_complete_updates_status_completed(run_dir: Path) -> None:
    from swarm_lib import claims

    status.initialize(run_dir, run_id="r1")
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    task = claims.try_claim(run_dir, worker_id="w.1")
    claims.complete(task, success=True)

    s = status.read(run_dir)
    assert "t.1" in s.checkpoint.completed_tasks


def test_claims_complete_failure_does_not_mark_completed(run_dir: Path) -> None:
    from swarm_lib import claims

    status.initialize(run_dir, run_id="r1")
    claims.enqueue(run_dir, task_id="t.1", task_type="plan", payload={})
    task = claims.try_claim(run_dir, worker_id="w.1")
    claims.complete(task, success=False)

    s = status.read(run_dir)
    assert "t.1" not in s.checkpoint.completed_tasks
