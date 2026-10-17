from __future__ import annotations

import json

import pytest

from pipeline import operator_console
from pipeline.channel_models import ChannelPreset, get_channel_preset, list_channel_presets, resolve_channel_preset, validate_preset_compatibility
from pipeline.export_models import ExportPackage
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.runtime_service import (
    get_export_package,
    get_project_runtime_summary,
    get_render,
    get_story_runtime_summary,
    index_existing_project,
    list_project_exports,
    list_render_exports,
    list_story_exports,
    list_story_renders,
    register_artifact,
    update_render_review_state,
    update_story_status,
    upsert_edit_brief,
    upsert_edl,
    upsert_export_package,
    upsert_moment,
    upsert_render,
    upsert_story,
)
from pipeline.story_adapter import canonical_story_id, adapt_story_suggestions
from tests.test_runtime_managed_analysis import _make_job


def _chain(tmp_path, monkeypatch):
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
        [{"story_id": "story_export", "title": "Story", "archetype": "COMEBACK", "moment_ids": ["001", "002"],
          "narrative_roles": {"HOOK": ["001"], "CLIMAX": ["002"]}, "recommended_formats": ["SHORT"], "estimated_duration": 45}],
        project_id=project.project_id,
        moments=moments,
    )
    story = upsert_story(project_id=project.project_id, story_id=canonical_story_id(project.project_id, "story_export"),
                         title="Story", archetype="COMEBACK", metadata={"original_story_id": "story_export"})
    update_story_status(story.story_id, "APPROVED")
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=45)
    edl = upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
                     format_treatment="SHORT", status="READY", estimated_duration=44.0)
    return job, jobs_dir, project, story, brief, edl, moments


def _approved_render(project, story, brief, edl, *, preset_id="tiktok_vertical", tmp_path):
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / f"{preset_id}.mp4")
    render = upsert_render(
        project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
        format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id=preset_id, platform="TIKTOK",
        status="READY", artifact_id=artifact.artifact_id,
    )
    return update_render_review_state(render.render_id, "APPROVED", reviewed_by="operator")


# ── Channel presets ──────────────────────────────────────────────────────────


def test_channel_preset_model_valid_and_invalid():
    preset = ChannelPreset(preset_id="p1", name="TikTok", platform="TIKTOK", channel_name="A", aspect_ratio="9:16",
                           width=1080, height=1920, fps=30.0)
    assert preset.to_dict()["width"] == 1080
    with pytest.raises(ValueError, match="platform"):
        ChannelPreset("p2", "n", "TWITCH", "A", "9:16", 1080, 1920, 30.0)
    with pytest.raises(ValueError, match="dimensions"):
        ChannelPreset("p3", "n", "TIKTOK", "A", "9:16", 0, 1920, 30.0)
    with pytest.raises(ValueError, match="fps"):
        ChannelPreset("p4", "n", "TIKTOK", "A", "9:16", 1080, 1920, 0)


def test_channel_preset_lookup_and_compatibility():
    assert get_channel_preset("tiktok_vertical").platform == "TIKTOK"
    assert len(list_channel_presets()) >= 4
    with pytest.raises(ValueError, match="unknown"):
        resolve_channel_preset("nope")
    with pytest.raises(ValueError, match="format"):
        validate_preset_compatibility(resolve_channel_preset("tiktok_vertical"), format_treatment="LONG", render_profile="REFERENCE")
    with pytest.raises(ValueError, match="render profile"):
        validate_preset_compatibility(resolve_channel_preset("tiktok_vertical"), format_treatment="SHORT", render_profile="EDITORIAL")


# ── Render variants ──────────────────────────────────────────────────────────


def test_same_edl_multiple_platform_renders(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    tiktok = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="tiktok_vertical", platform="TIKTOK", status="READY")
    reels = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                          format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="instagram_reels", platform="INSTAGRAM_REELS", status="READY")

    assert tiktok.render_id != reels.render_id
    assert len(list_story_renders(story.story_id)) == 2
    assert get_render(tiktok.render_id).platform == "TIKTOK"


def test_render_review_state_preserved_across_variants(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    tiktok = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="tiktok_vertical", status="READY")
    update_render_review_state(tiktok.render_id, "APPROVED", reviewed_by="operator")
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                  format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="instagram_reels", status="READY")

    assert get_render(tiktok.render_id).review_state == "APPROVED"
    assert get_render(tiktok.render_id).reviewed_by == "operator"


def test_invalid_preset_edl_combo_rejected(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="format"):
        upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                      format_treatment="LONG", render_profile="REFERENCE", channel_preset_id="tiktok_vertical")
    with pytest.raises(ValueError, match="render profile"):
        upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                      format_treatment="SHORT", render_profile="EDITORIAL", channel_preset_id="tiktok_vertical")


# ── Export model ─────────────────────────────────────────────────────────────


def test_export_model_valid_and_invalid():
    export = ExportPackage(
        export_id="e1", project_id="p1", story_id="s1", render_id="r1", channel_preset_id="tiktok_vertical",
        platform="TIKTOK", status="READY", video_artifact_id="a1", caption="caption", hashtags=["#goal"],
    )
    assert export.to_dict()["hashtags"] == ["#goal"]
    with pytest.raises(ValueError, match="status"):
        ExportPackage("e2", "p1", "s1", "r1", "tiktok_vertical", "TIKTOK", "NOPE", "a1")
    with pytest.raises(ValueError, match="platform"):
        ExportPackage("e3", "p1", "s1", "r1", "tiktok_vertical", "TWITCH", "DRAFT", "a1")


# ── Export persistence ───────────────────────────────────────────────────────


def test_export_persistence_and_listing(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    render = _approved_render(project, story, brief, edl, preset_id="tiktok_vertical", tmp_path=tmp_path)

    export = upsert_export_package(
        project_id=project.project_id, story_id=story.story_id, render_id=render.render_id,
        channel_preset_id="tiktok_vertical", video_artifact_id=render.artifact_id, status="READY", caption="caption",
    )
    upsert_export_package(
        project_id=project.project_id, story_id=story.story_id, render_id=render.render_id,
        channel_preset_id="instagram_reels", video_artifact_id=render.artifact_id, status="DRAFT",
    )

    fetched = get_export_package(export.export_id)
    assert fetched.video_artifact_id == render.artifact_id
    assert len(list_story_exports(story.story_id)) == 2
    assert len(list_render_exports(render.render_id)) == 2
    assert len(list_project_exports(project.project_id)) == 2


def test_export_upsert_idempotent(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    render = _approved_render(project, story, brief, edl, preset_id="tiktok_vertical", tmp_path=tmp_path)
    upsert_export_package(project_id=project.project_id, story_id=story.story_id, render_id=render.render_id,
                          channel_preset_id="tiktok_vertical", video_artifact_id=render.artifact_id, status="READY")
    upsert_export_package(project_id=project.project_id, story_id=story.story_id, render_id=render.render_id,
                          channel_preset_id="tiktok_vertical", video_artifact_id=render.artifact_id, status="READY")
    assert len(list_story_exports(story.story_id)) == 1


# ── Approval requirement ─────────────────────────────────────────────────────


def test_export_requires_approved_read_render(tmp_path, monkeypatch):
    job, jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "r.mp4")
    render = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="tiktok_vertical", status="READY", artifact_id=artifact.artifact_id)

    with pytest.raises(ValueError, match="APPROVED"):
        operator_console.create_export_package(job["job_id"], render.render_id, "tiktok_vertical", jobs_dir=jobs_dir)
    update_render_review_state(render.render_id, "REJECTED")
    with pytest.raises(ValueError, match="APPROVED"):
        operator_console.create_export_package(job["job_id"], render.render_id, "tiktok_vertical", jobs_dir=jobs_dir)


# ── Relationship safety ──────────────────────────────────────────────────────


def test_export_relationship_safety(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    render = _approved_render(project, story, brief, edl, preset_id="tiktok_vertical", tmp_path=tmp_path)
    from pipeline.runtime_service import upsert_project
    upsert_project(project_id="other_project", job_id="other_job", profile="football", sport="football", display_name="Other", status="READY")

    with pytest.raises(ValueError, match="does not belong"):
        upsert_export_package(project_id="other_project", story_id=story.story_id, render_id=render.render_id,
                              channel_preset_id="tiktok_vertical", video_artifact_id=render.artifact_id)


# ── Console ──────────────────────────────────────────────────────────────────


def _patch_render(monkeypatch, tmp_path, *, ok=True):
    out = tmp_path / "variant.mp4"

    def fake_render(*_args, **_kwargs):
        if not ok:
            return {"ok": False, "status": "FAILED", "error": "FFmpeg failed"}
        out.write_bytes(b"video")
        return {"ok": True, "status": "COMPLETE", "output": str(out), "duration": 44.0, "segment_count": 2, "mode": "REFERENCE"}

    monkeypatch.setattr(operator_console, "_render_edl", fake_render)


def test_console_variant_generation_and_export(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, _brief, _edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    _patch_render(monkeypatch, tmp_path)

    body = json.dumps({"format": "SHORT", "mode": "REFERENCE", "preset_id": "tiktok_vertical"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/render")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True

    render = list_story_renders(story.story_id)[0]
    assert render.platform == "TIKTOK"
    body = json.dumps({"review_state": "APPROVED"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/renders/{render.render_id}/review")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True

    body = json.dumps({"preset_id": "tiktok_vertical", "caption": "Great goal"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/renders/{render.render_id}/export")
    ConsoleHandler.do_POST(fake)
    result = _json_body(fake)
    assert result["ok"] is True
    assert result["export"]["platform"] == "TIKTOK"
    assert len(list_story_exports(story.story_id)) == 1


def test_console_unapproved_render_cannot_export(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "r.mp4")
    render = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                           format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="tiktok_vertical", status="READY", artifact_id=artifact.artifact_id)

    body = json.dumps({"preset_id": "tiktok_vertical"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/renders/{render.render_id}/export")
    ConsoleHandler.do_POST(fake)

    assert fake.status == 400
    assert _json_body(fake)["ok"] is False


def test_console_story_detail_shows_presets_and_exports(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _html_body
    from pipeline.console_server import ConsoleHandler
    job, jobs_dir, project, story, brief, edl, _moments = _chain(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    render = _approved_render(project, story, brief, edl, preset_id="tiktok_vertical", tmp_path=tmp_path)
    upsert_export_package(project_id=project.project_id, story_id=story.story_id, render_id=render.render_id,
                          channel_preset_id="tiktok_vertical", video_artifact_id=render.artifact_id, status="READY", caption="caption")

    fake = _FakeHandler()
    ConsoleHandler._render_story_detail(fake, job["job_id"], story.story_id)
    html = _html_body(fake)

    assert "TikTok Vertical" in html
    assert "Generate Variant" in html
    assert "Export Packages" in html
    assert "Create Export Package" in html


# ── Integration ──────────────────────────────────────────────────────────────


def test_same_story_multiple_platform_exports(tmp_path, monkeypatch):
    _job, _jobs_dir, project, story, brief, edl, moments = _chain(tmp_path, monkeypatch)
    tiktok = _approved_render(project, story, brief, edl, preset_id="tiktok_vertical", tmp_path=tmp_path)
    reels_artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "reels.mp4")
    reels = upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                          format_treatment="SHORT", render_profile="REFERENCE", channel_preset_id="instagram_reels", platform="INSTAGRAM_REELS",
                          status="READY", artifact_id=reels_artifact.artifact_id)
    reels = update_render_review_state(reels.render_id, "APPROVED")

    upsert_export_package(project_id=project.project_id, story_id=story.story_id, render_id=tiktok.render_id,
                          channel_preset_id="tiktok_vertical", video_artifact_id=tiktok.artifact_id, status="READY", caption="TikTok")
    upsert_export_package(project_id=project.project_id, story_id=story.story_id, render_id=reels.render_id,
                          channel_preset_id="instagram_reels", video_artifact_id=reels.artifact_id, status="READY", caption="Reels")

    exports = list_story_exports(story.story_id)
    assert len(exports) == 2
    assert {export.platform for export in exports} == {"TIKTOK", "INSTAGRAM_REELS"}
    assert len(moments) == 2
    summary = get_project_runtime_summary(_job["job_id"], jobs_dir=_jobs_dir)["export_summary"]
    assert summary["export_count"] == 2
    assert summary["ready_export_count"] == 2


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_migrates_renders_and_adds_exports(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE stories (story_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', archetype TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SUGGESTED', hook TEXT NOT NULL DEFAULT '', emotional_arc_json TEXT NOT NULL DEFAULT '[]', estimated_duration INTEGER, recommended_formats_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE edit_briefs (edit_brief_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', editorial_intent TEXT NOT NULL DEFAULT '', target_duration INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE edls (edl_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, edit_brief_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', target_duration INTEGER, estimated_duration REAL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE renders (render_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, story_id TEXT NOT NULL, edit_brief_id TEXT NOT NULL, edl_id TEXT NOT NULL, artifact_id TEXT, format_treatment TEXT NOT NULL, render_profile TEXT NOT NULL, status TEXT NOT NULL, review_state TEXT NOT NULL, duration_seconds REAL, width INTEGER, height INTEGER, fps REAL, reviewed_at TEXT, reviewed_by TEXT, review_note TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}', UNIQUE(edl_id, render_profile));
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO stories(story_id, project_id, title, created_at, updated_at) VALUES('s1', 'p1', 'Story', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO edit_briefs(edit_brief_id, project_id, story_id, format_treatment, status, created_at, updated_at) VALUES('eb1', 'p1', 's1', 'SHORT', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO edls(edl_id, project_id, story_id, edit_brief_id, format_treatment, status, created_at, updated_at) VALUES('edl1', 'p1', 's1', 'eb1', 'SHORT', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO renders(render_id, project_id, story_id, edit_brief_id, edl_id, format_treatment, render_profile, status, review_state, created_at, updated_at) VALUES('r1', 'p1', 's1', 'eb1', 'edl1', 'SHORT', 'REFERENCE', 'READY', 'APPROVED', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        render_cols = {row[1] for row in conn.execute("PRAGMA table_info(renders)")}
        render = conn.execute("SELECT * FROM renders WHERE render_id = 'r1'").fetchone()
    assert "export_packages" in tables
    assert "channel_preset_id" in render_cols
    assert render["review_state"] == "APPROVED"