from __future__ import annotations

import json

from pipeline.operator_console import list_projects as console_list_projects
from pipeline.pilot import create_job
from pipeline.runtime_service import (
    append_pipeline_event,
    create_pipeline_run,
    get_artifact,
    get_latest_pipeline_run,
    get_project,
    get_project_by_job_id,
    index_existing_project,
    initialize_runtime_index,
    list_pipeline_events,
    list_project_artifacts,
    list_projects,
    register_artifact,
    update_pipeline_run,
    upsert_project,
)
from tests.test_pilot_intake import build_intake


def _db(tmp_path):
    return tmp_path / "runtime.sqlite3"


def _project(db_path):
    return upsert_project(
        project_id="project_alpha",
        job_id="job_alpha",
        profile="football",
        sport="football",
        display_name="Alpha Match",
        status="READY",
        db_path=db_path,
    )


def test_project_create_retrieve_upsert_list_and_job_lookup(tmp_path):
    db_path = _db(tmp_path)

    created = _project(db_path)
    assert created.project_id == "project_alpha"
    assert get_project("project_alpha", db_path=db_path) == created
    assert get_project_by_job_id("job_alpha", db_path=db_path) == created

    updated = upsert_project(
        project_id="project_alpha",
        job_id="job_alpha",
        profile="football",
        sport="football",
        display_name="Alpha Match Updated",
        status="RUNNING",
        db_path=db_path,
    )

    assert updated.display_name == "Alpha Match Updated"
    assert updated.status == "RUNNING"
    assert [project.project_id for project in list_projects(db_path=db_path)] == ["project_alpha"]


def test_register_artifact_reference_idempotent_and_parent_relationship(tmp_path):
    db_path = _db(tmp_path)
    project = _project(db_path)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    child = tmp_path / "analysis.json"
    child.write_text("{}", encoding="utf-8")

    first = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, db_path=db_path)
    second = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, db_path=db_path)
    analysis = register_artifact(
        project_id=project.project_id,
        artifact_type="analysis_state",
        path=child,
        parent_artifact_id=first.artifact_id,
        metadata={"stage": "analysis"},
        db_path=db_path,
    )

    assert first.artifact_id == second.artifact_id
    assert source.read_bytes() == b"media"
    assert analysis.parent_artifact_id == first.artifact_id
    assert get_artifact(analysis.artifact_id, db_path=db_path).metadata == {"stage": "analysis"}
    assert len(list_project_artifacts(project.project_id, artifact_type="source_media", db_path=db_path)) == 1


def test_pipeline_run_lifecycle_and_latest_lookup(tmp_path):
    db_path = _db(tmp_path)
    project = _project(db_path)

    run = create_pipeline_run(project_id=project.project_id, stage="analysis", progress_current=0, progress_total=10, db_path=db_path)
    assert run.status == "QUEUED"

    running = update_pipeline_run(run.run_id, status="RUNNING", progress_current=3, db_path=db_path)
    assert running.status == "RUNNING"
    assert running.started_at
    assert running.progress_current == 3

    succeeded = update_pipeline_run(run.run_id, status="SUCCEEDED", progress_current=10, db_path=db_path)
    assert succeeded.status == "SUCCEEDED"
    assert succeeded.finished_at
    assert get_latest_pipeline_run(project.project_id, stage="analysis", db_path=db_path).run_id == run.run_id


def test_failed_pipeline_run_stores_safe_error_information(tmp_path):
    db_path = _db(tmp_path)
    project = _project(db_path)
    run = create_pipeline_run(project_id=project.project_id, stage="transcription", db_path=db_path)

    failed = update_pipeline_run(
        run.run_id,
        status="FAILED",
        error_code="TRANSCRIPTION_MODEL_UNAVAILABLE",
        error_message="The transcription model is not available on this machine.",
        retry_count=1,
        db_path=db_path,
    )

    assert failed.status == "FAILED"
    assert failed.error_code == "TRANSCRIPTION_MODEL_UNAVAILABLE"
    assert "not available" in failed.error_message
    assert failed.retry_count == 1


def test_pipeline_events_append_in_order_with_progress_and_relationships(tmp_path):
    db_path = _db(tmp_path)
    project = _project(db_path)
    run = create_pipeline_run(project_id=project.project_id, stage="analysis", db_path=db_path)

    first = append_pipeline_event(
        project_id=project.project_id,
        run_id=run.run_id,
        event_type="RUN_QUEUED",
        stage="analysis",
        message="Queued",
        progress_current=0,
        progress_total=2,
        db_path=db_path,
    )
    second = append_pipeline_event(
        project_id=project.project_id,
        run_id=run.run_id,
        event_type="RUN_STARTED",
        stage="analysis",
        message="Started",
        progress_current=1,
        progress_total=2,
        metadata={"worker": "local"},
        db_path=db_path,
    )

    events = list_pipeline_events(run_id=run.run_id, db_path=db_path)
    assert [event.event_id for event in events] == [first.event_id, second.event_id]
    assert events[1].project_id == project.project_id
    assert events[1].progress_current == 1
    assert events[1].metadata == {"worker": "local"}


def test_index_existing_pilot_project_is_idempotent_and_preserves_files(tmp_path):
    db_path = _db(tmp_path)
    jobs_dir = tmp_path / "jobs"
    intake_path = tmp_path / "intakes" / "pilot_alpha_source_alpha.json"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"original media")
    intake = build_intake(str(source))
    intake_path.parent.mkdir()
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_dir)
    analysis_dir = jobs_dir / "ANALYSIS"
    analysis_dir.mkdir()
    moments_path = analysis_dir / f"{job['job_id']}_moments.json"
    moments_path.write_text("[]", encoding="utf-8")

    first = index_existing_project(job["job_id"], jobs_dir=jobs_dir, db_path=db_path)
    second = index_existing_project(job["job_id"], jobs_dir=jobs_dir, db_path=db_path)
    artifacts = list_project_artifacts(first.project_id, db_path=db_path)

    assert first.project_id == second.project_id == job["job_id"]
    assert source.read_bytes() == b"original media"
    assert len(list_projects(db_path=db_path)) == 1
    assert len(artifacts) == len({(artifact.artifact_type, artifact.path) for artifact in artifacts})
    assert {artifact.artifact_type for artifact in artifacts} >= {"intake_manifest", "source_media", "analysis_moments"}


def test_operator_console_project_list_includes_existing_projects_with_runtime_index(tmp_path, monkeypatch):
    db_path = _db(tmp_path)
    jobs_dir = tmp_path / "jobs"
    intake_path = tmp_path / "intakes" / "pilot_alpha_source_alpha.json"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    intake = build_intake(str(source))
    intake_path.parent.mkdir()
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_dir)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(db_path))

    rows = console_list_projects(jobs_dir=jobs_dir)

    assert any(row["job_id"] == job["job_id"] for row in rows)
    assert rows[0]["project_id"] == "football"
    assert initialize_runtime_index(db_path).exists()
