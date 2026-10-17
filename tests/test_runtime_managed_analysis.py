from __future__ import annotations

import json

from pipeline import operator_console
from pipeline.detection import _ACTIVE_ANALYSES, update_analysis_state
from pipeline.operator_console import analyze_project, get_analysis_status
from pipeline.pilot import create_job
from pipeline.runtime_service import (
    create_pipeline_run,
    get_latest_pipeline_run,
    index_existing_project,
    list_pipeline_events,
    list_pipeline_runs,
    list_project_moments,
    list_project_artifacts,
)
from tests.test_pilot_intake import build_intake


def _make_job(tmp_path, monkeypatch, *, project="football"):
    db_path = tmp_path / "runtime.sqlite3"
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(db_path))
    jobs_dir = tmp_path / "jobs"
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    intake = build_intake(str(source), overrides={"configuration": {"project": project}})
    intake_path = tmp_path / "intakes" / "pilot_alpha_source_alpha.json"
    intake_path.parent.mkdir()
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_dir)
    return job, jobs_dir, source, db_path


def _patch_success(monkeypatch, tmp_path, *, transcript_reused=False, transcript_name="transcript.json"):
    transcript = tmp_path / transcript_name
    transcript.write_text('{"segments": []}', encoding="utf-8")

    def fake_preflight(job, _jobs_dir):
        return {
            "ok": True,
            "source_file": str(tmp_path / "source.mp4"),
            "match_name": "Alpha Match",
            "existing_transcript": str(transcript) if transcript_reused else None,
        }

    def fake_ensure(_source_file, _match_name, _project_id, jobs_dir, job_id):
        update_analysis_state(
            job_id,
            status="RUNNING",
            stage=operator_console.STAGE_TRANSCRIBING,
            jobs_dir=jobs_dir,
            transcription_reused=transcript_reused,
            transcription_status="READY",
            transcription_reference=str(transcript),
        )
        return transcript

    monkeypatch.setattr(operator_console, "_analysis_preflight", fake_preflight)
    monkeypatch.setattr(operator_console, "_ensure_transcription", fake_ensure)
    monkeypatch.setattr(operator_console, "build_prompt", lambda **_kwargs: {"prompt": "prompt"})
    monkeypatch.setattr(operator_console, "require_detection_provider", lambda: None)
    monkeypatch.setattr(operator_console, "run_detection_call", lambda *_args, **_kwargs: [{"caption": "Moment"}])
    monkeypatch.setattr(
        operator_console,
        "build_clip_manifest",
        lambda *_args, **_kwargs: {
            "fieldnames": ["clip_id", "category", "start_time", "end_time"],
            "rows": [{"clip_id": "clip_001", "category": "EMOTION", "start_time": "00:00:01", "end_time": "00:00:05"}],
        },
    )
    return transcript


def _events(project_id, run_id):
    return [event.event_type for event in list_pipeline_events(project_id=project_id, run_id=run_id)]


def test_managed_analysis_success_creates_run_events_and_artifacts(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    _patch_success(monkeypatch, tmp_path)

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")

    assert result["ok"] is True
    assert run.status == "SUCCEEDED"
    assert run.started_at
    assert run.finished_at
    event_types = _events(project.project_id, run.run_id)
    assert event_types[:5] == ["RUN_QUEUED", "RUN_STARTED", "SOURCE_VALIDATED", "TRANSCRIPTION_STARTED", "TRANSCRIPTION_COMPLETED"]
    assert "DETECTION_STARTED" in event_types
    assert "DETECTION_COMPLETED" in event_types
    assert event_types[-1] == "RUN_SUCCEEDED"
    artifact_types = {artifact.artifact_type for artifact in list_project_artifacts(project.project_id)}
    assert {"transcript", "analysis_moments", "analysis_manifest"} <= artifact_types
    canonical_moments = list_project_moments(project.project_id)
    assert len(canonical_moments) == 1
    assert canonical_moments[0].review_state == "UNREVIEWED"


def test_managed_analysis_failed_preflight_records_safe_failure(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setattr(operator_console, "_analysis_preflight", lambda *_args: {"ok": False, "error": "Project is not execution-ready for analysis."})

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")

    assert result["ok"] is False
    assert run.status == "FAILED"
    assert run.error_code == "ANALYSIS_PREFLIGHT_FAILED"
    assert "execution-ready" in run.error_message
    event_types = _events(project.project_id, run.run_id)
    assert "RUN_FAILED" in event_types
    assert "RUN_SUCCEEDED" not in event_types


def test_managed_analysis_failed_transcription_records_safe_failure(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setattr(operator_console, "_analysis_preflight", lambda *_args: {
        "ok": True,
        "source_file": str(tmp_path / "source.mp4"),
        "match_name": "Alpha Match",
        "existing_transcript": None,
    })

    def fail_transcription(*_args):
        raise RuntimeError("Transcription failed because model is unavailable")

    monkeypatch.setattr(operator_console, "_ensure_transcription", fail_transcription)

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")

    assert result["ok"] is False
    assert run.status == "FAILED"
    assert run.error_code == "TRANSCRIPTION_FAILED"
    assert "Transcription failed" in run.error_message
    event_types = _events(project.project_id, run.run_id)
    assert "TRANSCRIPTION_STARTED" in event_types
    assert "RUN_FAILED" in event_types
    assert "RUN_SUCCEEDED" not in event_types


def test_managed_analysis_transcript_reuse_does_not_emit_transcription_events(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    transcript = _patch_success(monkeypatch, tmp_path, transcript_reused=True)

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")
    event_types = _events(project.project_id, run.run_id)

    assert result["ok"] is True
    assert "TRANSCRIPT_REUSED" in event_types
    assert "TRANSCRIPTION_STARTED" not in event_types
    assert "TRANSCRIPTION_COMPLETED" not in event_types
    artifacts = list_project_artifacts(project.project_id, artifact_type="transcript")
    assert artifacts[0].path == str(transcript)


def _patch_analysis_after_transcription(monkeypatch, tmp_path):
    monkeypatch.setattr(operator_console, "build_prompt", lambda **_kwargs: {"prompt": "prompt"})
    monkeypatch.setattr(operator_console, "require_detection_provider", lambda: None)
    monkeypatch.setattr(operator_console, "run_detection_call", lambda *_args, **_kwargs: [{"caption": "Moment"}])
    monkeypatch.setattr(
        operator_console,
        "build_clip_manifest",
        lambda *_args, **_kwargs: {
            "fieldnames": ["clip_id", "category", "start_time", "end_time"],
            "rows": [{"clip_id": "clip_001", "category": "EMOTION", "start_time": "00:00:01", "end_time": "00:00:05"}],
        },
    )


def test_analyze_reuses_existing_transcript_via_production_preflight(tmp_path, monkeypatch):
    """The real analysis preflight finds an existing transcript via the production
    lookup; transcription must be REUSED (no transcribe_source call, no second
    transcript artifact, no nested smoke identity involved)."""
    import pipeline.transcription as transcription_mod

    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    existing = tmp_path / "existing_transcript.json"
    existing.write_text('{"segments": []}', encoding="utf-8")

    monkeypatch.setattr(transcription_mod, "find_existing_transcript", lambda *_a, **_k: existing)
    _patch_analysis_after_transcription(monkeypatch, tmp_path)

    def must_not_transcribe(*_args, **_kwargs):
        raise AssertionError("transcribe_source must not be called when a transcript is reused")

    monkeypatch.setattr(transcription_mod, "transcribe_source", must_not_transcribe)

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")
    event_types = _events(project.project_id, run.run_id)

    assert result["ok"] is True
    assert "TRANSCRIPT_REUSED" in event_types
    assert "TRANSCRIPTION_STARTED" not in event_types
    assert "TRANSCRIPTION_COMPLETED" not in event_types
    artifacts = list_project_artifacts(project.project_id, artifact_type="transcript")
    assert len(artifacts) == 1
    assert artifacts[0].path == str(existing)


def test_analyze_repeated_runs_keep_single_transcript_artifact(tmp_path, monkeypatch):
    """Repeated full analysis on the same source keeps exactly one transcript
    artifact (same reusable transcript each run)."""
    import pipeline.transcription as transcription_mod

    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    existing = tmp_path / "existing_transcript.json"
    existing.write_text('{"segments": []}', encoding="utf-8")

    monkeypatch.setattr(transcription_mod, "find_existing_transcript", lambda *_a, **_k: existing)
    _patch_analysis_after_transcription(monkeypatch, tmp_path)

    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifacts = list_project_artifacts(project.project_id, artifact_type="transcript")
    assert len(artifacts) == 1
    assert artifacts[0].path == str(existing)


def test_managed_analysis_retry_creates_new_run(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setattr(operator_console, "_analysis_preflight", lambda *_args: {"ok": False, "error": "Project is not execution-ready for analysis."})
    analyze_project(job["job_id"], jobs_dir=jobs_dir)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    failed = get_latest_pipeline_run(project.project_id, stage="analysis")

    _patch_success(monkeypatch, tmp_path)
    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    runs = list_pipeline_runs(project.project_id, stage="analysis")

    assert len(runs) == 2
    assert failed.run_id != runs[0].run_id
    assert {run.status for run in runs} == {"FAILED", "SUCCEEDED"}


def test_managed_analysis_artifact_registration_is_idempotent_for_same_paths(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    _patch_success(monkeypatch, tmp_path)

    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifacts = list_project_artifacts(project.project_id)

    keys = [(artifact.artifact_type, artifact.path) for artifact in artifacts]
    assert len(keys) == len(set(keys))


def test_managed_analysis_fails_if_moment_normalization_fails_without_touching_raw_artifacts(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    _patch_success(monkeypatch, tmp_path)
    monkeypatch.setattr(operator_console, "adapt_detection_to_moments", lambda *_args, **_kwargs: (_ for _ in ()).throw(ValueError("bad moment")))

    result = analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = get_latest_pipeline_run(project.project_id, stage="analysis")

    assert result["ok"] is False
    assert run.status == "FAILED"
    assert run.error_code == "MOMENT_NORMALIZATION_FAILED"
    artifact_types = {artifact.artifact_type for artifact in list_project_artifacts(project.project_id)}
    assert {"analysis_moments", "analysis_manifest"} <= artifact_types
    assert list_project_moments(project.project_id) == []


def test_console_analysis_status_prefers_runtime_run(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    create_pipeline_run(project_id=project.project_id, stage="analysis", status="QUEUED")

    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert status["runtime_status"] == "QUEUED"
    assert status["analysis_status"] == "RUNNING"


def test_console_analysis_status_falls_back_without_runtime_run(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)

    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)

    assert "runtime_status" not in status
    assert status["analysis_status"] == ""


def test_interrupted_runtime_run_is_marked_failed_on_read_path(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    run = create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")

    status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)
    interrupted = get_latest_pipeline_run(project.project_id, stage="analysis")
    event_types = _events(project.project_id, run.run_id)

    assert status["runtime_status"] == "FAILED"
    assert interrupted.error_code == "ANALYSIS_INTERRUPTED"
    assert "RUN_INTERRUPTED" in event_types


def test_active_runtime_run_is_not_marked_interrupted(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")
    _ACTIVE_ANALYSES.add(job["job_id"])
    try:
        status = get_analysis_status(job["job_id"], jobs_dir=jobs_dir)
    finally:
        _ACTIVE_ANALYSES.discard(job["job_id"])

    assert status["runtime_status"] == "RUNNING"
