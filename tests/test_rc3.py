from __future__ import annotations

import json
import pathlib

import pytest

from pipeline import operator_console, runtime_db
from pipeline.runtime_service import (
    register_artifact,
    update_moment_review_state,
    update_project_analysis_strategy,
    update_story_status,
    upsert_edit_brief,
    upsert_moment,
    upsert_story,
)
from pipeline.version import __version__, application_version


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))
    for key in ("OPENAI_API_KEY", "SLACK_WEBHOOK_URL", "AIRTABLE_API_TOKEN", "OORT_API_KEY"):
        monkeypatch.delenv(key, raising=False)


# ── Smoke classification ─────────────────────────────────────────────────────


def test_smoke_classification_fast_fixture(tmp_path, monkeypatch):
    from pipeline import smoke_service
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fixture media")
    result = smoke_service.run_smoke(source_file=source, jobs_dir=tmp_path / "jobs",
                                     db_path=tmp_path / "runtime.sqlite3", mode="fast",
                                     smoke_mode="FAST", source_type="FIXTURE", real_media=False)
    assert result["classification"] == {"smoke_mode": "FAST", "source_type": "FIXTURE", "real_media": False}


def test_release_check_scan_smoke_classification(tmp_path, monkeypatch):
    from scripts import release_check

    smoke_dir = tmp_path / "smoke"
    automated_path = smoke_dir / "auto" / "smoke_report.json"
    automated_path.parent.mkdir(parents=True)
    automated_path.write_text(json.dumps({
        "timings": {"result": "PASS"},
        "classification": {"smoke_mode": "FAST", "source_type": "FIXTURE", "real_media": False},
    }), encoding="utf-8")

    auto_pass, real_pass = release_check.scan_smoke_reports(smoke_dir)
    assert auto_pass is True
    assert real_pass is False

    real_path = smoke_dir / "real" / "smoke_report.json"
    real_path.parent.mkdir(parents=True)
    real_path.write_text(json.dumps({
        "timings": {"result": "PASS"},
        "classification": {"smoke_mode": "FULL", "source_type": "OPERATOR_MEDIA", "real_media": True},
    }), encoding="utf-8")

    auto_pass, real_pass = release_check.scan_smoke_reports(smoke_dir)
    assert auto_pass is True
    assert real_pass is True


# ── Version single source ────────────────────────────────────────────────────


def test_single_version_source():
    from pipeline.system_health import full_health_report
    assert application_version() == __version__
    assert full_health_report()["version"] == __version__


def test_pyproject_version_matches():
    root = pathlib.Path(__file__).resolve().parents[1]
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__.replace("-", "")}"' in pyproject


# ── Packaging entry points ───────────────────────────────────────────────────


def test_cli_entry_points_exist():
    from pipeline import cli
    for name in ("main_init", "main_doctor", "main_console", "main_release_check", "main_smoke"):
        assert callable(getattr(cli, name))


def test_pyproject_defines_scripts():
    root = pathlib.Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    for entry in ("clipper-init", "clipper-doctor", "clipper-console", "clipper-release-check", "clipper-smoke"):
        assert entry in text


# ── Workflow next-action read model ──────────────────────────────────────────


def _job_fixture(tmp_path, monkeypatch):
    from pipeline.pilot import create_job
    from pipeline.runtime_service import index_existing_project
    from pipeline.moment_adapter import adapt_detection_to_moments
    from tests.test_pilot_intake import build_intake

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    intake = build_intake(str(source))
    intake_path = tmp_path / "intake.json"
    intake_path.parent.mkdir(exist_ok=True)
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    job = create_job(intake, intake_path=intake_path, jobs_dir=tmp_path / "jobs")
    project = index_existing_project(job["job_id"], jobs_dir=tmp_path / "jobs")
    return job, project, tmp_path


def test_next_action_no_analysis(tmp_path, monkeypatch):
    job, _project, _tmp = _job_fixture(tmp_path, monkeypatch)
    status = operator_console.get_project_workflow_status(job["job_id"], jobs_dir=tmp_path / "jobs")
    assert status["primary_next_action"] == "Analyze Source"


def test_next_action_review_moments(tmp_path, monkeypatch):
    from pipeline.moment_adapter import adapt_detection_to_moments
    job, project, tmp = _job_fixture(tmp_path, monkeypatch)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp / "m.json")
    moment = adapt_detection_to_moments(
        [{"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5}],
        project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
    upsert_moment(moment)
    status = operator_console.get_project_workflow_status(job["job_id"], jobs_dir=tmp_path / "jobs")
    assert status["primary_next_action"] == "Review 1 Moments"


def test_next_action_approved_story_generate_brief(tmp_path, monkeypatch):
    from pipeline.moment_adapter import adapt_detection_to_moments
    from pipeline.story_adapter import canonical_story_id
    job, project, _tmp = _job_fixture(tmp_path, monkeypatch)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "m.json")
    moment = adapt_detection_to_moments(
        [{"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5}],
        project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
    upsert_moment(moment)
    update_moment_review_state(moment.moment_id, "KEEP")
    story_id = canonical_story_id(project.project_id, "s1")
    upsert_story(project_id=project.project_id, story_id=story_id, title="Story", metadata={"original_story_id": "s1"})
    update_story_status(story_id, "APPROVED")
    status = operator_console.get_project_workflow_status(job["job_id"], jobs_dir=tmp_path / "jobs")
    assert status["primary_next_action"] == "Generate Edit"


def test_next_action_ready_brief_generate_edl(tmp_path, monkeypatch):
    from pipeline.story_adapter import canonical_story_id
    job, project, _tmp = _job_fixture(tmp_path, monkeypatch)
    story_id = canonical_story_id(project.project_id, "s1")
    upsert_story(project_id=project.project_id, story_id=story_id, title="Story", metadata={"original_story_id": "s1"})
    update_story_status(story_id, "APPROVED")
    upsert_edit_brief(project_id=project.project_id, story_id=story_id, format_treatment="SHORT", status="READY")
    status = operator_console.get_project_workflow_status(job["job_id"], jobs_dir=tmp_path / "jobs")
    assert status["primary_next_action"] == "Generate Edit"


def test_research_first_workflow_strip_hides_transcription_detection(tmp_path, monkeypatch):
    job, project, _tmp = _job_fixture(tmp_path, monkeypatch)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    status = operator_console.get_project_workflow_status(job["job_id"], jobs_dir=tmp_path / "jobs")
    steps = [step["step"] for step in status["steps"]]
    assert steps == ["Source", "Research", "Moments", "Story", "Edit Plan", "Preview", "ChatCut", "Review", "Export"]
    assert "Transcription" not in steps
    assert "Detection" not in steps
    assert status["primary_next_action"] == "Seed Moments"


# ── Secret hygiene ───────────────────────────────────────────────────────────


def test_health_and_smoke_do_not_leak_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-secret-value-abcdef1234567890")
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/services/FAKE")
    from pipeline.system_health import full_health_report
    report = full_health_report()
    text = json.dumps(report)
    assert "sk-fake-secret-value-abcdef1234567890" not in text
    assert "FAKE" not in text

    from pipeline.safety import sanitize_text
    cleaned = sanitize_text("OPENAI_API_KEY=sk-fake-secret-value-abcdef1234567890")
    assert "sk-fake-secret-value-abcdef1234567890" not in cleaned
