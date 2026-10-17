from __future__ import annotations

import json

import pytest

from pipeline import integration_service
from pipeline.integration_models import INTEGRATION_CAPABILITIES
from pipeline.integration_service import (
    get_integration_adapter,
    integration_health_report,
    registered_integration_adapters,
)


# ── Registry ─────────────────────────────────────────────────────────────────


def test_integration_registry_lookup_and_capabilities():
    adapters = registered_integration_adapters()
    ids = {adapter.adapter_id for adapter in adapters}
    assert {"oort", "airtable", "slack"} <= ids

    oort = get_integration_adapter("oort")
    assert oort.capabilities == ("ARTIFACT_STORAGE",)
    assert get_integration_adapter("airtable").capabilities == ("RECORD_SYNC",)
    assert set(get_integration_adapter("slack").capabilities) == {"NOTIFICATION", "REVIEW_CARD"}

    with pytest.raises(ValueError, match="unknown"):
        get_integration_adapter("nope")

    assert INTEGRATION_CAPABILITIES >= {"ARTIFACT_STORAGE", "RECORD_SYNC", "NOTIFICATION", "REVIEW_CARD", "DELIVERY"}


# ── Health ───────────────────────────────────────────────────────────────────


def test_adapter_health_not_configured(monkeypatch):
    for key in ("OORT_API_KEY", "OORT_BUCKET", "AIRTABLE_API_TOKEN", "AIRTABLE_BASE_ID", "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    health = get_integration_adapter("oort").health_check()
    assert health.status == "NOT_CONFIGURED"
    assert health.configured is False


def test_adapter_health_ready_when_configured(monkeypatch):
    monkeypatch.setenv("OORT_API_KEY", "k")
    monkeypatch.setenv("OORT_BUCKET", "b")
    adapter = get_integration_adapter("oort")
    monkeypatch.setattr(adapter, "_provider_available", lambda: True)
    health = adapter.health_check()
    assert health.status == "READY"
    assert health.configured is True
    assert health.available is True


def test_adapter_health_unavailable_when_provider_missing(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://hooks.slack.com/x")
    health = get_integration_adapter("slack").health_check()
    assert health.status == "UNAVAILABLE"
    assert health.configured is True
    assert health.available is False


def test_health_report_has_no_secrets(monkeypatch):
    monkeypatch.setenv("OORT_API_KEY", "secret-value")
    monkeypatch.setenv("OORT_BUCKET", "b")
    report = integration_health_report()
    for entry in report:
        text = json.dumps(entry)
        assert "secret-value" not in text


# ── Core independence ────────────────────────────────────────────────────────


def test_core_workflow_independent_of_integrations(tmp_path, monkeypatch):
    for key in ("OORT_API_KEY", "OORT_BUCKET", "AIRTABLE_API_TOKEN", "AIRTABLE_BASE_ID", "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))

    from pipeline.pilot import create_job
    from pipeline.runtime_service import index_existing_project, register_artifact, upsert_moment, upsert_story
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
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moment = adapt_detection_to_moments([{"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5}],
                                        project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
    upsert_moment(moment)
    upsert_story(project_id=project.project_id, story_id="s1", title="Story")

    assert project.project_id
    assert len(__import__("pipeline.runtime_service", fromlist=["list_project_moments"]).list_project_moments(project.project_id)) == 1
    assert len(__import__("pipeline.runtime_service", fromlist=["list_project_stories"]).list_project_stories(project.project_id)) == 1


# ── Failure isolation ────────────────────────────────────────────────────────


def test_integration_failure_does_not_touch_runtime_records(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    from pipeline.runtime_service import upsert_project, upsert_render, get_render, upsert_edit_brief, upsert_edl, register_artifact, upsert_story
    project = upsert_project(project_id="p1", job_id="p1", profile="football", sport="football", display_name="A", status="READY")
    upsert_story(project_id="p1", story_id="s1", title="Story")
    brief = upsert_edit_brief(project_id="p1", story_id="s1", format_treatment="SHORT", status="READY")
    edl = upsert_edl(project_id="p1", story_id="s1", edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    artifact = register_artifact(project_id="p1", artifact_type="render_video", path=str(tmp_path / "rough.mp4"))
    render = upsert_render(project_id="p1", story_id="s1", edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", status="READY", review_state="APPROVED", artifact_id=artifact.artifact_id)

    oort = get_integration_adapter("oort")
    result = oort.upload_artifact(str(tmp_path / "rough.mp4"))
    assert result.ok is False
    assert result.status == "NOT_AVAILABLE"

    persisted = get_render(render.render_id)
    assert persisted.status == "READY"
    assert persisted.review_state == "APPROVED"