from __future__ import annotations

import json

import pytest

from pipeline import operator_console
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.render_models import CanonicalRender
from pipeline.runtime_service import (
    get_project_runtime_summary,
    get_render,
    get_story_runtime_summary,
    index_existing_project,
    list_edl_renders,
    list_project_renders,
    list_story_renders,
    register_artifact,
    update_render_review_state,
    update_render_status,
    update_story_status,
    upsert_edit_brief,
    upsert_edl,
    upsert_moment,
    upsert_render,
    upsert_story,
)
from pipeline.story_adapter import canonical_story_id, adapt_story_suggestions
from tests.test_runtime_managed_analysis import _make_job


def _chain(tmp_path, monkeypatch):
    """Moments -> Story(approved) -> READY EditBrief -> READY EDL."""
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
        [{"story_id": "story_render", "title": "Story", "archetype": "COMEBACK", "moment_ids": ["001", "002"],
          "narrative_roles": {"HOOK": ["001"], "CLIMAX": ["002"]}, "recommended_formats": ["SHORT"], "estimated_duration": 45}],
        project_id=project.project_id,
        moments=moments,
    )
    story = upsert_story(project_id=project.project_id, story_id=canonical_story_id(project.project_id, "story_render"),
                         title="Story", archetype="COMEBACK", metadata={"original_story_id": "story_render"})
    update_story_status(story.story_id, "APPROVED")
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=45)
    edl = upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
                     format_treatment="SHORT", status="READY", estimated_duration=44.0)
    return job, jobs_dir, project, story, brief, edl, moments


# ── Model ────────────────────────────────────────────────────────────────────


def test_render_model_valid_and_invalid():
    render = CanonicalRender(
        render_id="r1", project_id="p1", story_id="s1", edit_brief_id="eb1", edl_id="e1", artifact_id="a1",
        format_treatment="SHORT", render_profile="REFERENCE", status="READY", review_state="UNREVIEWED",
        duration_seconds=44.0, width=1080, height=1920, fps=30.0,
    )
    assert render.status == "READY"
    assert render.review_state == "UNREVIEWED"
    assert render.to_dict()["width"] == 1080
    with pytest.raises(ValueError, match="status"):
        CanonicalRender("r2", "p1", "s1", "eb1", "e1", None, "SHORT", "REFERENCE", "NOPE", "UNREVIEWED")
    with pytest.raises(ValueError, match="review"):
        CanonicalRender("r3", "p1", "s1", "eb1", "e1", None, "SHORT", "REFERENCE", "READY", "BAD")
    with pytest.raises(ValueError, match="profile"):
        CanonicalRender("r4", "p1", "s1", "eb1", "e1", None, "SHORT", "NOPE", "READY", "UNREVIEWED")


# ── Persistence ──────────────────────────────────────────────────────────────


def test_render_persistence_and_listing(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "rough.mp4")

    render = upsert_render(
        project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
        format_treatment="SHORT", render_profile="REFERENCE", status="READY", review_state="UNREVIEWED",
        artifact_id=artifact.artifact_id, duration_seconds=44.0, width=1080, height=1920, fps=30.0,
    )
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="EDITORIAL", status="QUEUED")

    fetched = get_render(render.render_id)
    by_story = list_story_renders(story.story_id)
    by_edl = list_edl_renders(edl.edl_id)
    by_project = list_project_renders(project.project_id)
    updated = update_render_status(render.render_id, "ARCHIVED")

    assert fetched.artifact_id == artifact.artifact_id
    assert {r.render_profile for r in by_story} == {"REFERENCE", "EDITORIAL"}
    assert len(by_edl) == 2
    assert len(by_project) == 2
    assert updated.status == "ARCHIVED"


def test_render_upsert_idempotent_per_profile(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="REFERENCE", status="READY")
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="REFERENCE", status="READY")
    assert len(list_story_renders(story.story_id)) == 1


# ── Relationship safety ──────────────────────────────────────────────────────


def test_render_relationship_safety(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    from pipeline.runtime_service import upsert_project
    upsert_project(project_id="other_project", job_id="other_job", profile="football", sport="football", display_name="Other", status="READY")
    other_story = upsert_story(project_id="other_project", story_id="story_other", title="Other")

    with pytest.raises(ValueError, match="project"):
        upsert_render(project_id=project.project_id, story_id=other_story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                      format_treatment="SHORT", render_profile="REFERENCE")
    with pytest.raises(ValueError, match="format"):
        upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                      format_treatment="LONG", render_profile="REFERENCE")


# ── Rendering integration ────────────────────────────────────────────────────


def _patch_render(monkeypatch, tmp_path, *, ok=True):
    out = tmp_path / "rough.mp4"

    def fake_render(*_args, **_kwargs):
        if not ok:
            return {"ok": False, "status": "FAILED", "error": "FFmpeg failed"}
        out.write_bytes(b"video")
        return {"ok": True, "status": "COMPLETE", "output": str(out), "duration": 44.0, "segment_count": 2, "mode": "REFERENCE"}

    monkeypatch.setattr(operator_console, "_render_edl", fake_render)


def test_canonical_render_generation_success(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _brief, edl, _moments = _chain(tmp_path, monkeypatch)
    _patch_render(monkeypatch, tmp_path)

    result = operator_console.generate_canonical_render(job["job_id"], "story_render", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is True
    assert result.get("canonical") is True
    render = list_story_renders(story.story_id)[0]
    assert render.status == "READY"
    assert render.review_state == "UNREVIEWED"
    assert render.edl_id == edl.edl_id
    assert render.duration_seconds == 44.0
    assert render.artifact_id
    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)["render_summary"]
    assert summary["render_count"] == 1
    assert summary["ready_render_count"] == 1


def test_canonical_render_failure_is_failed_without_fake_artifact(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _brief, edl, _moments = _chain(tmp_path, monkeypatch)
    _patch_render(monkeypatch, tmp_path, ok=False)

    result = operator_console.generate_canonical_render(job["job_id"], "story_render", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is False
    render = list_story_renders(story.story_id)[0]
    assert render.status == "FAILED"
    assert render.artifact_id is None


def test_canonical_render_requires_read_edl(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _brief, edl, _moments = _chain(tmp_path, monkeypatch)
    from pipeline.runtime_service import update_edl_status
    update_edl_status(edl.edl_id, "FAILED")

    with pytest.raises(ValueError, match="READY"):
        operator_console.generate_canonical_render(job["job_id"], "story_render", "SHORT", jobs_dir=jobs_dir)


# ── Review ───────────────────────────────────────────────────────────────────


def test_render_review_lifecycle(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    render = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", status="READY")

    approved = update_render_review_state(render.render_id, "APPROVED", reviewed_by="operator")
    needs = update_render_review_state(render.render_id, "NEEDS_CHANGES", reviewed_by="operator", review_note="trim intro")
    rejected = update_render_review_state(render.render_id, "REJECTED", reviewed_by="operator")
    unreviewed = update_render_review_state(render.render_id, "UNREVIEWED")

    assert approved.reviewed_at
    assert approved.reviewed_by == "operator"
    assert needs.review_note == "trim intro"
    assert rejected.review_state == "REJECTED"
    assert unreviewed.review_state == "UNREVIEWED"
    assert unreviewed.reviewed_at is None
    assert unreviewed.review_note is None


def test_render_review_invalid_state_rejected(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    render = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", status="READY")
    with pytest.raises(ValueError, match="review"):
        update_render_review_state(render.render_id, "BAD")


# ── Story summary ────────────────────────────────────────────────────────────


def test_story_runtime_summary_includes_renders(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="REFERENCE", status="READY")

    summary = get_story_runtime_summary(story.story_id)

    assert len(summary["renders"]) == 1
    assert summary["renders"][0]["render_profile"] == "REFERENCE"


# ── Console ──────────────────────────────────────────────────────────────────


def test_console_render_generation_and_review(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _brief, _edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    _patch_render(monkeypatch, tmp_path)

    body = json.dumps({"format": "SHORT", "mode": "REFERENCE"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/render")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True

    render = list_story_renders(story.story_id)[0]
    body = json.dumps({"review_state": "APPROVED"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/renders/{render.render_id}/review")
    ConsoleHandler.do_POST(fake)
    result = _json_body(fake)
    assert result["ok"] is True
    assert result["render"]["review_state"] == "APPROVED"


def test_console_failed_edl_cannot_render(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _brief, edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    from pipeline.runtime_service import update_edl_status
    update_edl_status(edl.edl_id, "FAILED")

    body = json.dumps({"format": "SHORT"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/render")
    ConsoleHandler.do_POST(fake)

    assert fake.status == 400
    assert _json_body(fake)["ok"] is False


def test_console_story_detail_shows_render_surface(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _html_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "rough.mp4")
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="REFERENCE", status="READY", artifact_id=artifact.artifact_id)

    fake = _FakeHandler()
    ConsoleHandler._render_story_detail(fake, job["job_id"], story.story_id)
    html = _html_body(fake)

    assert "Rough Cuts" in html
    assert "Generate Variant" in html
    assert f"/render_video/{job['job_id']}/" in html


def test_render_video_preview_safety(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))

    # Unknown render id -> 404, no arbitrary file access
    fake = _FakeHandler()
    ConsoleHandler._serve_render_video(fake, job["job_id"], "does_not_exist")
    assert fake.status == 404
    assert _json_body(fake)["ok"] is False

    # Cross-project render rejected
    render = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", status="READY")
    fake = _FakeHandler()
    ConsoleHandler._serve_render_video(fake, "other_job", render.render_id)
    assert fake.status == 404


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_adds_renders(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE stories (story_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', archetype TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SUGGESTED', hook TEXT NOT NULL DEFAULT '', emotional_arc_json TEXT NOT NULL DEFAULT '[]', estimated_duration INTEGER, recommended_formats_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE edit_briefs (edit_brief_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', editorial_intent TEXT NOT NULL DEFAULT '', target_duration INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE edls (edl_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, edit_brief_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', target_duration INTEGER, estimated_duration REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO stories(story_id, project_id, title, created_at, updated_at) VALUES('s1', 'p1', 'Story', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO edit_briefs(edit_brief_id, project_id, story_id, format_treatment, status, created_at, updated_at) VALUES('eb1', 'p1', 's1', 'SHORT', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO edls(edl_id, project_id, story_id, edit_brief_id, format_treatment, status, created_at, updated_at) VALUES('edl1', 'p1', 's1', 'eb1', 'SHORT', 'READY', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        edl = conn.execute("SELECT * FROM edls WHERE edl_id = 'edl1'").fetchone()
    assert "renders" in tables
    assert edl["status"] == "READY"