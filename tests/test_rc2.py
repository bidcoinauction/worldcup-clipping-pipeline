from __future__ import annotations

import json
import pathlib

import pytest

from pipeline import operator_console, runtime_db, smoke_service
from pipeline.performance import PerformanceReport, measure_stage
from pipeline.runtime_service import (
    index_existing_project,
    list_project_artifacts,
    list_project_moments,
    list_project_renders,
    list_project_stories,
    register_artifact,
    reconcile_interrupted_renders,
    update_moment_review_state,
    upsert_edit_brief,
    upsert_edl,
    upsert_moment,
    upsert_render,
    upsert_story,
)
from pipeline.moment_adapter import adapt_detection_to_moments
from tests.test_runtime_managed_analysis import _make_job


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))


# ── Performance instrumentation ──────────────────────────────────────────────


def test_performance_report_records_stages():
    report = PerformanceReport()
    with measure_stage(report, "transcription", reused=True):
        pass
    with measure_stage(report, "detection"):
        pass
    data = report.to_dict()
    assert [t["stage"] for t in data["stages"]] == ["transcription", "detection"]
    assert all(t["duration_seconds"] >= 0 for t in data["stages"])
    assert data["stages"][0]["reused"] is True
    assert data["stages"][1]["reused"] is False
    assert data["result"] == "PASS"
    assert "Transcription" in report.render()


def test_performance_report_marks_failure():
    report = PerformanceReport()
    with pytest.raises(RuntimeError):
        with measure_stage(report, "render"):
            raise RuntimeError("boom")
    data = report.to_dict()
    assert data["result"] == "FAIL"
    assert data["stages"][0]["status"] == "FAIL"


# ── Automated end-to-end smoke ───────────────────────────────────────────────


def _patch_expensive(monkeypatch, tmp_path):
    brief_path = tmp_path / "brief.json"
    edl_path = tmp_path / "edl.json"
    render_path = tmp_path / "rough.mp4"

    def fake_brief(*_a, **_k):
        brief_path.write_text(json.dumps({"format": "SHORT", "beats": [], "editorial_intent": "intent"}), encoding="utf-8")
        return {"ok": True, "status": "COMPLETE", "brief": {"format": "SHORT", "beats": [], "editorial_intent": "intent"},
                "artifact_path": str(brief_path)}

    def fake_edl(*_a, **_k):
        edl_path.write_text(json.dumps({"segment_count": 2, "timeline_duration": 44.0}), encoding="utf-8")
        return {"ok": True, "status": "COMPLETE", "edl": {"segment_count": 2, "timeline_duration": 44.0},
                "artifact_path": str(edl_path)}

    def fake_render(*_a, **_k):
        render_path.write_bytes(b"video")
        return {"ok": True, "status": "COMPLETE", "output": str(render_path), "duration": 44.0, "segment_count": 2, "mode": "REFERENCE"}

    monkeypatch.setattr(operator_console, "_brief_generate", fake_brief)
    monkeypatch.setattr(operator_console, "_edl_build", fake_edl)
    monkeypatch.setattr(operator_console, "_render_edl", fake_render)


def test_end_to_end_smoke(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture media")
    jobs_dir = tmp_path / "jobs"
    _patch_expensive(monkeypatch, tmp_path)

    result = smoke_service.run_smoke(source_file=source, jobs_dir=jobs_dir,
                                     db_path=tmp_path / "runtime.sqlite3", mode="fast")

    assert result["timings"]["result"] == "PASS"
    assert result["timings"]["counts"]["Moments"] == 2
    assert result["timings"]["counts"]["Stories"] >= 1
    assert result["timings"]["counts"]["Renders"] == 1
    assert result["schema_version"] == runtime_db.SCHEMA_VERSION

    project_id = result["project_id"]
    assert len(list_project_moments(project_id)) == 2
    assert len(list_project_stories(project_id)) >= 1
    assert len(list_project_renders(project_id)) == 1


def test_smoke_reuse_no_duplicates(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture media")
    jobs_dir = tmp_path / "jobs"
    _patch_expensive(monkeypatch, tmp_path)

    first = smoke_service.run_smoke(source_file=source, jobs_dir=jobs_dir, db_path=tmp_path / "runtime.sqlite3", mode="fast")
    second = smoke_service.run_smoke(source_file=source, jobs_dir=jobs_dir, db_path=tmp_path / "runtime.sqlite3", mode="fast")

    assert first["project_id"] == second["project_id"]
    pid = first["project_id"]
    assert len(list_project_moments(pid)) == 2
    assert len(list_project_stories(pid)) == len(list_project_stories(pid))
    assert len(list_project_renders(pid)) == 1
    # transcript reused on second run
    assert second["timings"]["stages"][1]["reused"] is True


def test_smoke_fail_after_stage(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture media")
    jobs_dir = tmp_path / "jobs"
    _patch_expensive(monkeypatch, tmp_path)

    result = smoke_service.run_smoke(source_file=source, jobs_dir=jobs_dir, db_path=tmp_path / "runtime.sqlite3",
                                     mode="fast", fail_after="detection")
    stages = result["timings"]["stages"]
    assert stages[2]["stage"] == "detection"
    assert stages[2]["status"] == "FAIL"
    assert result["timings"]["result"] == "FAIL"


# ── Failure recovery: detection fails then retry reuses transcript ───────────


def test_detection_failure_retry_reuses_transcript(tmp_path, monkeypatch):
    from pipeline import operator_console as oc
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    transcript = tmp_path / "transcript.json"
    transcript.write_text('{"segments": []}', encoding="utf-8")

    def fake_preflight(_job, _dir):
        return {"ok": True, "source_file": str(tmp_path / "source.mp4"), "match_name": "Match",
                "existing_transcript": str(transcript)}

    def fake_ensure(_src, _match, _profile, _dir, _job_id):
        register_artifact(project_id=project.project_id, artifact_type="transcript", path=transcript)
        return transcript

    monkeypatch.setattr(oc, "_analysis_preflight", fake_preflight)
    monkeypatch.setattr(oc, "_ensure_transcription", fake_ensure)
    monkeypatch.setattr(oc, "build_prompt", lambda **_k: {"prompt": "p"})
    monkeypatch.setattr(oc, "require_detection_provider", lambda: None)
    monkeypatch.setattr(oc, "build_clip_manifest", lambda *_a, **_k: {
        "fieldnames": ["clip_id", "category", "start_time", "end_time"],
        "rows": [{"clip_id": "c1", "category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:03"}],
    })

    def fail_detect(*_a, **_k):
        raise RuntimeError("detection provider unavailable")

    monkeypatch.setattr(oc, "run_detection_call", fail_detect)
    first = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)
    assert first["ok"] is False

    monkeypatch.setattr(oc, "run_detection_call", lambda *_a, **_k: [{"category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:03"}])
    second = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)
    assert second["ok"] is True

    transcript_artifacts = [a for a in list_project_artifacts(project.project_id) if a.artifact_type == "transcript"]
    assert len(transcript_artifacts) == 1

    from pipeline.runtime_service import list_pipeline_runs
    runs = list_pipeline_runs(project.project_id, stage="analysis")
    assert len(runs) == 2
    assert {r.status for r in runs} == {"FAILED", "SUCCEEDED"}
    failed = next(r for r in runs if r.status == "FAILED")
    assert failed.error_code == "ANALYSIS_FAILED"


# ── Interrupted render reconciliation ────────────────────────────────────────


def test_interrupted_render_reconciliation(tmp_path, monkeypatch):
    project, brief, edl = _chain_render_fixture(tmp_path, monkeypatch)
    render = upsert_render(project_id=project.project_id, story_id="s1", edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", status="RENDERING")

    reconciled = reconcile_interrupted_renders(project.project_id, active_render_ids=set())
    assert len(reconciled) == 1
    assert reconciled[0].status == "FAILED"
    assert reconciled[0].metadata.get("error_code") == "RENDER_INTERRUPTED"

    live = upsert_render(project_id=project.project_id, story_id="s1", edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                         format_treatment="SHORT", render_profile="EDITORIAL", status="RENDERING")
    reconcile_interrupted_renders(project.project_id, active_render_ids={live.render_id})
    from pipeline.runtime_service import get_render
    assert get_render(live.render_id).status == "RENDERING"


def _chain_render_fixture(tmp_path, monkeypatch):
    from pipeline.runtime_service import upsert_project, upsert_story, upsert_edit_brief, upsert_edl
    project = upsert_project(project_id="p1", job_id="p1", profile="football", sport="football", display_name="A", status="READY")
    upsert_story(project_id="p1", story_id="s1", title="Story")
    brief = upsert_edit_brief(project_id="p1", story_id="s1", format_treatment="SHORT", status="READY")
    edl = upsert_edl(project_id="p1", story_id="s1", edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    return project, brief, edl


# ── Recovery via canonical boundaries (story survives detection failure) ──────


def test_story_survives_analysis_failure(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moment = adapt_detection_to_moments([{"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5}],
                                        project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
    upsert_moment(moment)
    upsert_story(project_id=project.project_id, story_id="s1", title="Story")

    # Simulate a failed analysis run without touching existing moments/story.
    from pipeline.runtime_service import create_pipeline_run, update_pipeline_run
    run = create_pipeline_run(project_id=project.project_id, stage="analysis", status="RUNNING")
    update_pipeline_run(run.run_id, status="FAILED", error_code="ANALYSIS_FAILED", error_message="provider down")

    assert len(list_project_moments(project.project_id)) == 1
    assert len(list_project_stories(project.project_id)) == 1
    assert list_project_moments(project.project_id)[0].review_state == "UNREVIEWED"