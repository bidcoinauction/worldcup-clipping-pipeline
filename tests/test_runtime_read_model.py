from __future__ import annotations

from pipeline.operator_console import analyze_project, get_analysis_status, get_project, get_project_status
from pipeline.runtime_service import (
    append_pipeline_event,
    create_pipeline_run,
    get_latest_pipeline_run,
    get_project_runtime_summary,
    index_existing_project,
    list_pipeline_events,
    register_artifact,
    update_pipeline_run,
)
from tests.test_runtime_managed_analysis import _make_job, _patch_success


def test_runtime_summary_returns_project_latest_run_events_and_artifacts(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = create_pipeline_run(project_id=project.project_id, stage="analysis", status="QUEUED")
    append_pipeline_event(project_id=project.project_id, run_id=run.run_id, event_type="RUN_QUEUED", stage="analysis", message="Queued")
    source_artifact = register_artifact(project_id=project.project_id, artifact_type="transcript", path=tmp_path / "transcript.json")

    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)

    assert summary["runtime_available"] is True
    assert summary["project"]["job_id"] == job["job_id"]
    assert summary["analysis"]["run_id"] == run.run_id
    assert summary["analysis"]["operator_status"] == "preparing"
    assert [event["event_type"] for event in summary["events"]] == ["RUN_QUEUED"]
    assert any(artifact["artifact_id"] == source_artifact.artifact_id for artifact in summary["artifacts"])


def test_console_detail_and_status_attach_runtime_summary(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")

    detail = get_project(job["job_id"], jobs_dir=jobs_dir)
    status = get_project_status(job["job_id"], jobs_dir=jobs_dir)

    assert detail["runtime"]["analysis"]["status"] == "FAILED"
    assert detail["runtime"]["analysis"]["error_code"] == "ANALYSIS_INTERRUPTED"
    assert status["runtime"]["analysis"]["status"] == "FAILED"


def test_partial_runtime_project_falls_back_to_legacy_analysis_state(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    index_existing_project(job["job_id"], jobs_dir=jobs_dir)

    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert "runtime_status" not in status
    assert status["analysis_status"] == ""


def test_legacy_unindexed_project_still_lists_opens_and_reports_status(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)

    detail = get_project(job["job_id"], jobs_dir=jobs_dir)
    status = get_project_status(job["job_id"], jobs_dir=jobs_dir)
    analysis = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert detail["job_id"] == job["job_id"]
    assert detail["runtime"]["runtime_available"] is True
    assert detail["runtime"]["analysis"] is None
    assert status["state"] == "READY"
    assert analysis["analysis_status"] == ""


def test_runtime_failure_summary_exposes_safe_error_only(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")
    update_pipeline_run(run.run_id, status="FAILED", error_code="TRANSCRIPTION_FAILED", error_message="The transcription model is unavailable.")

    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert status["runtime_status"] == "FAILED"
    assert status["runtime_error_code"] == "TRANSCRIPTION_FAILED"
    assert status["analysis_error"] == "The transcription model is unavailable."
    assert "Traceback" not in status["analysis_error"]


def test_runtime_summary_scopes_events_to_latest_retry_run(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    first = create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")
    append_pipeline_event(project_id=project.project_id, run_id=first.run_id, event_type="RUN_FAILED", stage="analysis", message="Failed")
    update_pipeline_run(first.run_id, status="FAILED", error_code="ANALYSIS_FAILED", error_message="Failed")
    second = create_pipeline_run(project_id=project.project_id, stage="analysis", status="QUEUED")
    append_pipeline_event(project_id=project.project_id, run_id=second.run_id, event_type="RUN_QUEUED", stage="analysis", message="Retry queued")

    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)
    first_events = list_pipeline_events(run_id=first.run_id)

    assert summary["latest_run_id"] == second.run_id
    assert summary["previous_run_status"] == "FAILED"
    assert summary["analysis_run_count"] == 2
    assert [event["event_type"] for event in summary["events"]] == ["RUN_QUEUED"]
    assert [event.event_type for event in first_events] == ["RUN_FAILED"]


def test_phase_1_success_lifecycle_console_reads_terminal_runtime_state(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    _patch_success(monkeypatch, tmp_path, transcript_reused=True)

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)
    detail = get_project(job["job_id"], jobs_dir=jobs_dir)

    assert result["ok"] is True
    assert status["runtime_status"] == "SUCCEEDED"
    assert status["analysis_status"] == "COMPLETE"
    assert detail["runtime"]["analysis"]["status"] == "SUCCEEDED"
    assert detail["runtime"]["moment_summary"]["moment_count"] > 0
    assert detail["runtime"]["moments"][0]["universal_event_type"] == "OTHER"
    assert detail["runtime"]["moments"][0]["review_state"] == "UNREVIEWED"
    artifact_types = {artifact["artifact_type"] for artifact in detail["runtime"]["artifacts"]}
    assert {"source_media", "intake_manifest", "transcript", "analysis_moments", "analysis_manifest"} <= artifact_types


def test_phase_1_failed_lifecycle_console_reads_safe_runtime_error(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setattr("pipeline.operator_console._analysis_preflight", lambda *_args: {"ok": False, "error": "Project is not execution-ready for analysis."})

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir)
    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert result["ok"] is False
    assert status["runtime_status"] == "FAILED"
    assert status["runtime_error_code"] == "ANALYSIS_PREFLIGHT_FAILED"
    assert status["analysis_error"] == "Project is not execution-ready for analysis."
