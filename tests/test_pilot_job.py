"""Job-record lifecycle: creation, states, atomicity, event log, safety."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from pipeline import pilot
from pipeline.pilot import (
    JobExistsError,
    JobNotFoundError,
    JobPathError,
    append_event,
    create_job,
    list_jobs,
    read_job,
    show_job,
)
from tests.test_pilot_intake import build_intake

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"media bytes" * 200)
    return path


# ── Creation and states ──────────────────────────────────────────────────────


def test_ready_intake_creates_ready_job(media_file: Path, jobs_root: Path):
    job = create_job(build_intake(str(media_file)), intake_path="intake.json", source="test")
    assert job["current_state"] == "READY"
    assert job["job_id"] == "pilot_alpha_source_alpha"
    assert job["pilot_id"] == "pilot_alpha"
    assert job["source_id"] == "source_alpha"
    assert job["project_id"] == "football"
    assert job["created_at"] and job["updated_at"]
    assert job["readiness_summary"]["execution_ready"] is True


def test_unconfirmed_rights_create_awaiting_rights(media_file: Path, jobs_root: Path):
    job = create_job(
        build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}}),
        source="test",
    )
    assert job["current_state"] == "AWAITING_RIGHTS"


def test_invalid_config_reference_creates_validation_failed(media_file: Path, jobs_root: Path):
    job = create_job(
        build_intake(str(media_file), overrides={"configuration": {"brand": "does_not_exist"}}),
        source="test",
    )
    assert job["current_state"] == "VALIDATION_FAILED"


def test_missing_source_creates_validation_failed(tmp_path: Path, jobs_root: Path):
    job = create_job(build_intake(str(tmp_path / "missing.mp4")), source="test")
    assert job["current_state"] == "VALIDATION_FAILED"
    assert job["readiness_summary"]["source_ready"] is False


def test_initial_event_recorded(media_file: Path, jobs_root: Path):
    job = create_job(build_intake(str(media_file)), source="test")
    events_path = jobs_root / f"{job['job_id']}.events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    assert len(events) == 1
    first = events[0]
    assert first["event_type"] == "CREATED"
    assert first["new_state"] == job["current_state"]
    assert first["previous_state"] is None
    assert first["source"] == "test"


# ── Stable identity, duplicate handling, atomicity ───────────────────────────


def test_duplicate_handling_is_deterministic(media_file: Path, jobs_root):
    create_job(build_intake(str(media_file)), source="test")
    with pytest.raises(JobExistsError):
        create_job(build_intake(str(media_file)), source="test")
    rows = list_jobs(jobs_dir=jobs_root)
    assert len(rows) == 1


def test_atomic_writes_preserve_event_history(media_file: Path, jobs_root):
    job = create_job(build_intake(str(media_file)), source="test")
    append_event(job["job_id"], "REVIEW", new_state="REVIEW_REQUIRED",
                 previous_state="READY", source="operator")
    append_event(job["job_id"], "APPROVE", new_state="APPROVED",
                 previous_state="REVIEW_REQUIRED", source="operator")
    events_path = jobs_root / f"{job['job_id']}.events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    assert [e["event_type"] for e in events] == ["CREATED", "REVIEW", "APPROVE"]


def test_job_root_cannot_be_escaped(jobs_root):
    with pytest.raises(JobPathError):
        pilot._job_files("..", jobs_root)
    with pytest.raises(JobPathError):
        pilot._job_files("pilot/../escape", jobs_root)


def test_job_record_contains_no_secrets_or_pii(media_file: Path, jobs_root: Path):
    intake = build_intake(str(media_file))
    intake["rights"]["confirmed_by"] = "Sensitive Client Person"
    intake["rights"]["notes"] = "some personal detail"
    job = create_job(intake, source="test")
    record = read_job(job["job_id"])
    blob = json.dumps(record)
    assert "Sensitive Client" not in blob
    assert "personal detail" not in blob
    assert "confirmation" not in blob


def test_show_is_read_only(media_file: Path, jobs_root):
    job = create_job(build_intake(str(media_file)), source="test")
    before = sorted(p.name for p in jobs_root.iterdir())
    summary = show_job(job["job_id"])
    after = sorted(p.name for p in jobs_root.iterdir())
    assert before == after
    assert summary["event_count"] == 1
    assert summary["current_state"] == "READY"


def test_read_missing_job_raises(jobs_root):
    with pytest.raises(JobNotFoundError):
        read_job("nope")


def test_list_jobs_empty_when_no_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(tmp_path / "empty"))
    assert list_jobs() == []


def test_job_id_ignored_by_git():
    gitignore = (_repo_root() / ".gitignore").read_text(encoding="utf-8")
    assert "data/pilot/" in gitignore


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]
