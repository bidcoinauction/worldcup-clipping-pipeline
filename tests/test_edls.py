from __future__ import annotations

import json

import pytest

from pipeline import operator_console
from pipeline.edl_models import CanonicalEDL
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.runtime_service import (
    get_edit_brief,
    get_edl,
    get_project_runtime_summary,
    get_story_runtime_summary,
    index_existing_project,
    list_edit_brief_edls,
    list_project_edls,
    list_story_edls,
    register_artifact,
    update_edl_status,
    update_story_status,
    upsert_edit_brief,
    upsert_edl,
    upsert_moment,
    upsert_story,
)
from pipeline.story_adapter import canonical_story_id, adapt_story_suggestions
from tests.test_runtime_managed_analysis import _make_job


def _full_fixture(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moments = []
    for row in [
        {"clip_id": "001", "category": "GOAL", "start_time": 10, "end_time": 15},
        {"clip_id": "002", "category": "SAVE", "start_time": 40, "end_time": 44},
    ]:
        moment = adapt_detection_to_moments([row], project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
        moments.append(upsert_moment(moment))
    adapt_story_suggestions(
        [{"story_id": "story_edl", "title": "Story", "archetype": "COMEBACK", "moment_ids": ["001", "002"],
          "narrative_roles": {"HOOK": ["001"], "CLIMAX": ["002"]}, "recommended_formats": ["SHORT"], "estimated_duration": 45}],
        project_id=project.project_id,
        moments=moments,
    )
    story = upsert_story(project_id=project.project_id, story_id=canonical_story_id(project.project_id, "story_edl"),
                         title="Story", archetype="COMEBACK", metadata={"original_story_id": "story_edl"})
    return job, jobs_dir, project, story, moments


# ── Model ────────────────────────────────────────────────────────────────────


def test_edl_model_valid_and_invalid():
    edl = CanonicalEDL(
        edl_id="edl1", project_id="p1", story_id="s1", edit_brief_id="eb1", artifact_id="a1",
        format_treatment="SHORT", status="READY", target_duration=45, estimated_duration=44.2,
    )
    assert edl.status == "READY"
    assert edl.to_dict()["estimated_duration"] == 44.2
    with pytest.raises(ValueError, match="status"):
        CanonicalEDL("e2", "p1", "s1", "eb1", None, "SHORT", "NOPE")
    with pytest.raises(ValueError, match="format"):
        CanonicalEDL("e3", "p1", "s1", "eb1", None, "REELS", "DRAFT")


# ── Persistence ──────────────────────────────────────────────────────────────


def test_edl_persistence_and_listing(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    artifact = register_artifact(project_id=project.project_id, artifact_type="edl", path=tmp_path / "edl.json")

    edl = upsert_edl(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        format_treatment="SHORT",
        status="READY",
        artifact_id=artifact.artifact_id,
        target_duration=45,
        estimated_duration=44.0,
        metadata={"segment_count": 3},
    )
    medium_brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="MEDIUM", status="DRAFT")
    upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=medium_brief.edit_brief_id, format_treatment="MEDIUM", status="DRAFT")

    fetched = get_edl(edl.edl_id)
    by_story = list_story_edls(story.story_id)
    by_brief = list_edit_brief_edls(brief.edit_brief_id)
    by_project = list_project_edls(project.project_id)
    updated = update_edl_status(edl.edl_id, "ARCHIVED")

    assert fetched.artifact_id == artifact.artifact_id
    assert fetched.metadata == {"segment_count": 3}
    assert {e.format_treatment for e in by_story} == {"SHORT", "MEDIUM"}
    assert len(by_brief) == 1
    assert len(by_project) == 2
    assert updated.status == "ARCHIVED"


def test_edl_upsert_idempotent(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    assert len(list_story_edls(story.story_id)) == 1


# ── Relationship safety ──────────────────────────────────────────────────────


def test_edl_relationship_safety_and_format_consistency(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    from pipeline.runtime_service import upsert_project
    upsert_project(project_id="other_project", job_id="other_job", profile="football", sport="football", display_name="Other", status="READY")
    other_story = upsert_story(project_id="other_project", story_id="story_other", title="Other")

    with pytest.raises(ValueError, match="project"):
        upsert_edl(project_id=project.project_id, story_id=other_story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT")
    with pytest.raises(ValueError, match="format"):
        upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="LONG")


# ── Existing EDL integration ─────────────────────────────────────────────────


def _patch_edl(monkeypatch, tmp_path, *, ok=True):
    edl_path = tmp_path / "edl.json"

    def fake_build(*_args, **_kwargs):
        if not ok:
            return {"ok": False, "status": "FAILED", "error": "EDL build failed"}
        edl = {"segment_count": 2, "timeline_duration": 44.0}
        edl_path.write_text(json.dumps(edl), encoding="utf-8")
        return {"ok": True, "status": "COMPLETE", "edl": edl, "artifact_path": str(edl_path)}

    monkeypatch.setattr(operator_console, "_edl_build", fake_build)


def test_canonical_edl_generation_success(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "APPROVED")
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    _patch_edl(monkeypatch, tmp_path)

    result = operator_console.generate_canonical_edl(job["job_id"], "story_edl", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is True
    assert result.get("canonical") is True
    edls = list_story_edls(story.story_id)
    assert len(edls) == 1
    assert edls[0].status == "READY"
    assert edls[0].edit_brief_id == brief.edit_brief_id
    assert edls[0].estimated_duration == 44.0
    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)["edl_summary"]
    assert summary == {"edl_count": 1, "ready_edl_count": 1, "failed_edl_count": 0}


def test_canonical_edl_generation_failure_is_failed_not_ready(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "APPROVED")
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    _patch_edl(monkeypatch, tmp_path, ok=False)

    result = operator_console.generate_canonical_edl(job["job_id"], "story_edl", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is False
    assert list_story_edls(story.story_id)[0].status == "FAILED"


def test_canonical_edl_requires_read_brief(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "APPROVED")
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="FAILED")

    with pytest.raises(ValueError, match="READY"):
        operator_console.generate_canonical_edl(job["job_id"], "story_edl", "SHORT", jobs_dir=jobs_dir)


# ── Story summary / integration ──────────────────────────────────────────────


def test_story_runtime_summary_includes_edls(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")

    summary = get_story_runtime_summary(story.story_id)

    assert len(summary["edit_briefs"]) == 1
    assert len(summary["edls"]) == 1
    assert summary["edls"][0]["format_treatment"] == "SHORT"


def test_phase_3_end_to_end_chain(tmp_path, monkeypatch):
    job, jobs_dir, project, story, moments = _full_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "APPROVED")
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY",
                              editorial_intent="intent", target_duration=45)
    _patch_edl(monkeypatch, tmp_path)

    result = operator_console.generate_canonical_edl(job["job_id"], "story_edl", "SHORT", jobs_dir=jobs_dir)
    assert result["ok"] is True

    edl = list_story_edls(story.story_id)[0]
    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)

    assert edl.story_id == story.story_id
    assert edl.edit_brief_id == brief.edit_brief_id
    assert edl.project_id == project.project_id
    assert summary["edl_summary"]["ready_edl_count"] == 1
    assert len(moments) == 2


# ── Console ──────────────────────────────────────────────────────────────────


def test_console_story_detail_shows_edl_surface_and_generate(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _html_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")

    fake = _FakeHandler()
    ConsoleHandler._render_story_detail(fake, job["job_id"], story.story_id)
    html = _html_body(fake)

    assert "EDLs" in html
    assert "Generate EDL" in html


def test_console_edl_generation_action(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    update_story_status(story.story_id, "APPROVED")
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    _patch_edl(monkeypatch, tmp_path)

    body = json.dumps({"format": "SHORT"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/edl")
    ConsoleHandler.do_POST(fake)

    result = _json_body(fake)
    assert result["ok"] is True
    assert list_story_edls(story.story_id)[0].status == "READY"


def test_console_failed_edit_brief_cannot_generate_edl(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _moments = _full_fixture(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="FAILED")

    body = json.dumps({"format": "SHORT"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/edl")
    ConsoleHandler.do_POST(fake)

    assert fake.status == 400
    assert _json_body(fake)["ok"] is False


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_adds_edls(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE stories (story_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', archetype TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SUGGESTED', hook TEXT NOT NULL DEFAULT '', emotional_arc_json TEXT NOT NULL DEFAULT '[]', estimated_duration INTEGER, recommended_formats_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE story_moments (story_moment_id TEXT PRIMARY KEY, story_id TEXT NOT NULL, moment_id TEXT NOT NULL, narrative_role TEXT NOT NULL, sequence_order INTEGER NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE edit_briefs (edit_brief_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', editorial_intent TEXT NOT NULL DEFAULT '', target_duration INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO stories(story_id, project_id, title, created_at, updated_at) VALUES('s1', 'p1', 'Story', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO edit_briefs(edit_brief_id, project_id, story_id, format_treatment, status, created_at, updated_at) VALUES('eb1', 'p1', 's1', 'SHORT', 'READY', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        brief = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = 'eb1'").fetchone()
    assert "edls" in tables
    assert brief["status"] == "READY"