"""Manual pilot job transitions, event integrity, revision, and safety."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.pilot import (
    JobRevisionError,
    JobTransitionError,
    confirm_delivery,
    create_job,
    generate_delivery_package,
    read_history,
    read_job,
    register_output_manifest,
    review_output,
    show_job,
    transition_job,
)
from tests.test_pilot_outputs import _fixture_files, _output_manifest
from tests.test_pilot_intake import build_intake


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"transition media" * 200)
    return path


def _write_intake(tmp_path: Path, data: dict, name: str = "intake.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _create_ready_job(tmp_path: Path, media_file: Path, jobs_root: Path) -> tuple[dict, Path]:
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    job = create_job(json.loads(intake_path.read_text(encoding="utf-8")), intake_path=intake_path, jobs_dir=jobs_root)
    return job, intake_path


def _events_path(jobs_root: Path, job_id: str) -> Path:
    return jobs_root / f"{job_id}.events.json"


def _event_count(jobs_root: Path, job_id: str) -> int:
    return len(json.loads(_events_path(jobs_root, job_id).read_text(encoding="utf-8")))


def test_complete_manual_transition_path(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    job_id = job["job_id"]

    job = transition_job(job_id, "RUNNING", metadata={"operator": "op", "reason": "Started manual run"},
                         expected_revision=0, jobs_dir=jobs_root)
    assert job["current_state"] == "RUNNING"
    assert job["revision"] == 1

    job = transition_job(job_id, "REVIEW_REQUIRED",
                         metadata={"operator": "op", "reason": "Exports ready for review"},
                         artifact_references=["outputs/review/job-alpha"], expected_revision=1, jobs_dir=jobs_root)
    assert job["current_state"] == "REVIEW_REQUIRED"

    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    register_output_manifest(job_id, manifest, jobs_dir=jobs_root, expected_revision=2)
    review_output(job_id, "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="reviewer",
                  reason="Approved clip one", include_in_delivery=True, jobs_dir=jobs_root)
    review_output(job_id, "outputs_alpha", "clip_002_shorts", status="APPROVED", operator="reviewer",
                  reason="Approved clip two", include_in_delivery=True, jobs_dir=jobs_root)

    job = transition_job(job_id, "APPROVED",
                         metadata={"operator": "reviewer", "approval_statement": "Approved for delivery", "deliverable_count": 2},
                         expected_revision=5, jobs_dir=jobs_root)
    assert job["current_state"] == "APPROVED"

    result = generate_delivery_package(job_id, package_id="pkg_alpha", operator="op", delivery_method="shared_folder",
                                       delivery_destination="deliveries/job-alpha", expected_revision=6,
                                       jobs_dir=jobs_root)
    assert result["job"]["revision"] == 7

    job = transition_job(job_id, "DELIVERY_READY",
                         metadata={"delivery_package_id": "pkg_alpha", "deliverable_count": 2},
                         artifact_references=["docs/pilot/DELIVERY_CHECKLIST.md"], expected_revision=7, jobs_dir=jobs_root)
    assert job["current_state"] == "DELIVERY_READY"

    confirmed = confirm_delivery(job_id, "pkg_alpha", operator="op", confirmation="Delivery recorded",
                                 delivered_count=2, expected_revision=8, jobs_dir=jobs_root)
    assert confirmed["job"]["current_state"] == "DELIVERY_READY"

    job = transition_job(job_id, "DELIVERED",
                         metadata={"operator": "op", "confirmation": "Client received package", "delivery_package_id": "pkg_alpha", "delivered_item_count": 2},
                         expected_revision=9, jobs_dir=jobs_root)
    assert job["current_state"] == "DELIVERED"
    assert job["revision"] == 10

    history = read_history(job_id, jobs_dir=jobs_root)
    transitions = [event["new_state"] for event in history if event["event_type"] in {"CREATED", "TRANSITION"}]
    assert transitions == [
        "READY", "RUNNING", "REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY", "DELIVERED"
    ]
    event_ids = [event["event_id"] for event in history if event["event_id"]]
    assert len(event_ids) == len(set(event_ids))
    assert [event["sequence"] for event in history] == sorted(event["sequence"] for event in history)
    assert read_job(job_id, jobs_dir=jobs_root)["current_state"] == history[-1]["new_state"]


def test_invalid_transition_rejected_without_event(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    before = _events_path(jobs_root, job["job_id"]).read_text(encoding="utf-8")
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "DELIVERED", metadata={"operator": "op"}, jobs_dir=jobs_root)
    assert "READY" in str(exc.value)
    assert "DELIVERED" in str(exc.value)
    assert "allowed next states" in str(exc.value)
    assert _events_path(jobs_root, job["job_id"]).read_text(encoding="utf-8") == before
    assert read_job(job["job_id"], jobs_dir=jobs_root)["current_state"] == "READY"


def test_duplicate_transition_command_rejected_without_duplicate_success(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    before = _event_count(jobs_root, job["job_id"])
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    assert _event_count(jobs_root, job["job_id"]) == before


def test_missing_metadata_rejected_with_field_path(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "RUNNING", metadata={}, jobs_dir=jobs_root)
    assert "operator" in str(exc.value)
    assert "READY" in str(exc.value)
    assert "RUNNING" in str(exc.value)


def test_stale_revision_rejected_without_event(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    transitioned = transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, expected_revision=0, jobs_dir=jobs_root)
    assert transitioned["revision"] == 1
    before = _event_count(jobs_root, job["job_id"])
    with pytest.raises(JobRevisionError) as exc:
        transition_job(job["job_id"], "REVIEW_REQUIRED",
                       metadata={"operator": "op", "reason": "ready"}, artifact_references=["outputs/review"],
                       expected_revision=0, jobs_dir=jobs_root)
    assert "current revision 1" in str(exc.value)
    assert _event_count(jobs_root, job["job_id"]) == before


def test_expired_rights_block_running_without_modifying_intake(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake = build_intake(str(media_file))
    intake_path = _write_intake(tmp_path, intake)
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)
    saved = json.loads(intake_path.read_text(encoding="utf-8"))
    saved["rights"]["expiration_date"] = "2000-01-01"
    intake_path.write_text(json.dumps(saved, indent=2) + "\n", encoding="utf-8")
    before = intake_path.read_text(encoding="utf-8")
    before_events = _event_count(jobs_root, job["job_id"])
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    assert "RIGHTS_EXPIRED" in str(exc.value)
    assert intake_path.read_text(encoding="utf-8") == before
    assert _event_count(jobs_root, job["job_id"]) == before_events


def test_unconfirmed_rights_block_ready_transition(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}})
    intake_path = _write_intake(tmp_path, intake)
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)
    assert job["current_state"] == "AWAITING_RIGHTS"
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "READY", metadata={"reason": "rights resolved"}, jobs_dir=jobs_root)
    assert "RIGHTS_NOT_CONFIRMED" in str(exc.value)


def test_failure_transition_and_explicit_recovery(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    failed = transition_job(job["job_id"], "FAILED",
                            metadata={"operator": "op", "reason": "Processing command failed", "failure_category": "PROCESSING", "retry_allowed": True},
                            jobs_dir=jobs_root)
    assert failed["current_state"] == "FAILED"
    recovered = transition_job(job["job_id"], "READY",
                               metadata={"operator": "op", "recovery_reason": "Fixed configuration", "recovery_confirmed": True},
                               jobs_dir=jobs_root)
    assert recovered["current_state"] == "READY"
    assert [event["new_state"] for event in read_history(job["job_id"], jobs_dir=jobs_root)][-2:] == ["FAILED", "READY"]


def test_failed_recovery_requires_confirmation(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    transition_job(job["job_id"], "FAILED",
                   metadata={"operator": "op", "reason": "review issue", "failure_category": "REVIEW", "retry_allowed": True},
                   jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "READY", metadata={"operator": "op", "recovery_reason": "Fixed"}, jobs_dir=jobs_root)
    assert "recovery_confirmed" in str(exc.value)


def test_terminal_states_remain_terminal(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    transition_job(job["job_id"], "CANCELLED",
                   metadata={"operator": "op", "reason": "Client paused", "client_requested": True}, jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "READY", metadata={"reason": "restart"}, jobs_dir=jobs_root)
    assert show_job(job["job_id"], jobs_dir=jobs_root)["allowed_next_states"] == []


def test_cancellation_does_not_delete_source_or_outputs(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    output = tmp_path / "outputs" / "clip.mp4"
    output.parent.mkdir()
    output.write_bytes(b"clip")
    source_before = media_file.read_bytes()
    output_before = output.read_bytes()
    transition_job(job["job_id"], "CANCELLED",
                   metadata={"operator": "op", "reason": "Client requested stop", "client_requested": True,
                             "disposition": "leave source and outputs in place"},
                   artifact_references=[str(output)], jobs_dir=jobs_root)
    assert media_file.read_bytes() == source_before
    assert output.read_bytes() == output_before


def test_unknown_state_and_invalid_failure_category_rejected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "BOGUS", jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError) as exc:
        transition_job(job["job_id"], "FAILED",
                       metadata={"operator": "op", "reason": "bad", "failure_category": "NOPE", "retry_allowed": False},
                       jobs_dir=jobs_root)
    assert "failure_category" in str(exc.value)


def test_artifact_traversal_and_secret_metadata_rejected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "RUNNING", metadata={"operator": "op", "reason": "sk-secret"}, jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, artifact_references=["../escape"], jobs_dir=jobs_root)
    assert _event_count(jobs_root, job["job_id"]) == 1


def test_job_event_inconsistency_is_detected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _ = _create_ready_job(tmp_path, media_file, jobs_root)
    record_path = jobs_root / f"{job['job_id']}.json"
    data = json.loads(record_path.read_text(encoding="utf-8"))
    data["current_state"] = "RUNNING"
    record_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(Exception) as exc:
        read_history(job["job_id"], jobs_dir=jobs_root)
    assert "state mismatch" in str(exc.value)


def test_legacy_job_without_revision_remains_readable_and_gains_revision(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    job_id = "legacy_job_source"
    jobs_root.mkdir(parents=True)
    (jobs_root / f"{job_id}.json").write_text(json.dumps({
        "schema_version": 1,
        "job_id": job_id,
        "pilot_id": "legacy_job",
        "source_id": "source",
        "project_id": "football",
        "created_at": "2026-01-01T00:00:00+00:00",
        "updated_at": "2026-01-01T00:00:00+00:00",
        "current_state": "READY",
        "intake_manifest_path": str(intake_path),
        "expected_output_root": "FootballArchive",
        "readiness_summary": {"structurally_valid": True, "source_ready": True, "rights_cleared": True, "execution_ready": True},
        "event_count": 1,
    }, indent=2) + "\n", encoding="utf-8")
    (jobs_root / f"{job_id}.events.json").write_text(json.dumps([{
        "timestamp": "2026-01-01T00:00:00+00:00",
        "event_type": "CREATED",
        "previous_state": None,
        "new_state": "READY",
        "message": "legacy",
        "related_codes": [],
        "operator": None,
        "source": "legacy",
    }], indent=2) + "\n", encoding="utf-8")

    assert show_job(job_id, jobs_dir=jobs_root)["revision"] == 0
    updated = transition_job(job_id, "RUNNING", metadata={"operator": "op"}, expected_revision=0, jobs_dir=jobs_root)
    assert updated["revision"] == 1
    assert read_job(job_id, jobs_dir=jobs_root)["revision"] == 1
