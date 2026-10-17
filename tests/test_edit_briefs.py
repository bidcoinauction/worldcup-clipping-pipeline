from __future__ import annotations

import json

import pytest

from pipeline import operator_console
from pipeline.edit_brief_models import CanonicalEditBrief
from pipeline.runtime_service import (
    get_edit_brief,
    get_project_runtime_summary,
    list_project_edit_briefs,
    list_story_edit_briefs,
    register_artifact,
    update_edit_brief_status,
    upsert_edit_brief,
    upsert_story,
)
from pipeline.story_adapter import adapt_story_suggestions
from tests.test_runtime_managed_analysis import _make_job
from tests.test_stories import _sample_suggestion
from pipeline.runtime_service import index_existing_project, register_artifact, upsert_moment
from pipeline.moment_adapter import adapt_detection_to_moments


def _approved_story(tmp_path, monkeypatch):
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
    adapt_story_suggestions([_sample_suggestion("story_brief", ["001", "002"])], project_id=project.project_id, moments=moments)
    stories = list(__import__("pipeline.runtime_service", fromlist=["list_project_stories"]).list_project_stories(project.project_id))
    story = next(s for s in stories if s.metadata.get("original_story_id") == "story_brief")
    return job, jobs_dir, project, story, moments


# ── Model ────────────────────────────────────────────────────────────────────


def test_edit_brief_model_valid_and_invalid():
    brief = CanonicalEditBrief(
        edit_brief_id="eb1",
        project_id="p1",
        story_id="s1",
        artifact_id="a1",
        format_treatment="SHORT",
        status="READY",
        editorial_intent="intent",
        target_duration=45,
    )
    assert brief.status == "READY"
    with pytest.raises(ValueError, match="status"):
        CanonicalEditBrief("eb2", "p1", "s1", None, "SHORT", "NOPE")
    with pytest.raises(ValueError, match="format"):
        CanonicalEditBrief("eb3", "p1", "s1", None, "REELS", "DRAFT")


# ── Persistence ─────────────────────────────────────────────────────────────


def test_edit_brief_persistence_and_listing(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _approved_story(tmp_path, monkeypatch)
    artifact = register_artifact(project_id=project.project_id, artifact_type="edit_brief", path=tmp_path / "brief_short.json")

    brief = upsert_edit_brief(
        project_id=project.project_id,
        story_id=story.story_id,
        format_treatment="SHORT",
        status="READY",
        artifact_id=artifact.artifact_id,
        editorial_intent="The night Turin applauded.",
        target_duration=45,
        metadata={"model": "gpt-4o"},
    )
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="MEDIUM", status="DRAFT")
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="LONG", status="DRAFT")

    fetched = get_edit_brief(brief.edit_brief_id)
    by_story = list_story_edit_briefs(story.story_id)
    by_project = list_project_edit_briefs(project.project_id)
    updated = update_edit_brief_status(brief.edit_brief_id, "ARCHIVED")

    assert fetched.artifact_id == artifact.artifact_id
    assert fetched.metadata == {"model": "gpt-4o"}
    assert {b.format_treatment for b in by_story} == {"SHORT", "MEDIUM", "LONG"}
    assert len(by_project) == 3
    assert updated.status == "ARCHIVED"


def test_edit_brief_upsert_idempotent_per_format(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _approved_story(tmp_path, monkeypatch)
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY")
    assert len(list_story_edit_briefs(story.story_id)) == 1


# ── Integration with existing generation ────────────────────────────────────


def _patch_generation(monkeypatch, tmp_path, *, ok=True):
    brief_path = tmp_path / "brief.json"

    def fake_generate(*_args, **_kwargs):
        if not ok:
            return {"ok": False, "status": "FAILED", "error": "Brief LLM unavailable"}
        brief = {
            "job_id": "job",
            "story_id": "story_brief",
            "format": "SHORT",
            "target_duration": 45,
            "editorial_intent": "Arena erupts.",
            "beats": [],
        }
        brief_path.write_text(json.dumps(brief), encoding="utf-8")
        return {"ok": True, "status": "COMPLETE", "brief": brief, "artifact_path": str(brief_path)}

    monkeypatch.setattr(operator_console, "_brief_generate", fake_generate)
    monkeypatch.setattr(operator_console, "require_edit_provider", lambda: None)


def test_canonical_edit_brief_generation_success(tmp_path, monkeypatch):
    job, jobs_dir, project, story, moments = _approved_story(tmp_path, monkeypatch)
    _patch_generation(monkeypatch, tmp_path)
    from pipeline.runtime_service import update_story_status
    update_story_status(story.story_id, "APPROVED")

    result = operator_console.generate_canonical_edit_brief(job["job_id"], "story_brief", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is True
    assert result.get("canonical") is True
    briefs = list_story_edit_briefs(story.story_id)
    assert len(briefs) == 1
    assert briefs[0].status == "READY"
    assert briefs[0].format_treatment == "SHORT"
    assert briefs[0].editorial_intent == "Arena erupts."
    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)["edit_brief_summary"]
    assert summary == {"edit_brief_count": 1, "ready_edit_brief_count": 1, "failed_edit_brief_count": 0}


def test_canonical_edit_brief_generation_failure_is_failed_not_ready(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _approved_story(tmp_path, monkeypatch)
    _patch_generation(monkeypatch, tmp_path, ok=False)
    from pipeline.runtime_service import update_story_status
    update_story_status(story.story_id, "APPROVED")

    result = operator_console.generate_canonical_edit_brief(job["job_id"], "story_brief", "SHORT", jobs_dir=jobs_dir)

    assert result["ok"] is False
    briefs = list_story_edit_briefs(story.story_id)
    assert briefs[0].status == "FAILED"


def test_rejected_story_cannot_generate_edit_brief(tmp_path, monkeypatch):
    job, jobs_dir, project, story, _moments = _approved_story(tmp_path, monkeypatch)
    from pipeline.runtime_service import update_story_status
    update_story_status(story.story_id, "REJECTED")

    with pytest.raises(ValueError, match="APPROVED"):
        operator_console.generate_canonical_edit_brief(job["job_id"], "story_brief", "SHORT", jobs_dir=jobs_dir)


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_adds_edit_briefs(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE stories (story_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, summary TEXT NOT NULL DEFAULT '', archetype TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'SUGGESTED', hook TEXT NOT NULL DEFAULT '', emotional_arc_json TEXT NOT NULL DEFAULT '[]', estimated_duration INTEGER, recommended_formats_json TEXT NOT NULL DEFAULT '[]', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE story_moments (story_moment_id TEXT PRIMARY KEY, story_id TEXT NOT NULL, moment_id TEXT NOT NULL, narrative_role TEXT NOT NULL, sequence_order INTEGER NOT NULL, created_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")
        conn.execute("INSERT INTO stories(story_id, project_id, title, created_at, updated_at) VALUES('s1', 'p1', 'Story', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        story = conn.execute("SELECT * FROM stories WHERE story_id = 's1'").fetchone()
    assert "edit_briefs" in tables
    assert story["title"] == "Story"