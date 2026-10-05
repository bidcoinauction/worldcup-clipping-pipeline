from __future__ import annotations

import json

import pytest

from pipeline import runtime_db
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.runtime_service import (
    add_story_moment,
    get_moment,
    get_project_runtime_summary,
    get_story,
    index_existing_project,
    list_project_moments,
    list_project_stories,
    list_story_moments,
    register_artifact,
    remove_story_moment,
    replace_story_moments,
    update_story_status,
    upsert_moment,
    upsert_story,
)
from pipeline.story_adapter import adapt_story_suggestions, canonical_story_id
from pipeline.story_models import Story, StoryMoment
from tests.test_runtime_managed_analysis import _make_job


def _project_and_moments(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moments = []
    for row in [
        {"clip_id": "001", "category": "GOAL", "start_time": 10, "end_time": 15, "caption": "Goal"},
        {"clip_id": "002", "category": "SAVE", "start_time": 40, "end_time": 44, "caption": "Save"},
        {"clip_id": "003", "category": "RED_CARD", "start_time": 70, "end_time": 75, "caption": "Red"},
    ]:
        moment = adapt_detection_to_moments(
            [row],
            project_id=project.project_id,
            source_artifact_id=artifact.artifact_id,
        )[0]
        moments.append(upsert_moment(moment))
    return job, jobs_dir, project, moments


def _sample_suggestion(sid: str, moment_ids, roles=None) -> dict:
    return {
        "story_id": sid,
        "title": "The Comeback",
        "summary": "A story.",
        "archetype": "COMEBACK",
        "hook": "Why it matters",
        "moment_ids": moment_ids,
        "narrative_roles": roles or {"HOOK": [moment_ids[0]], "CLIMAX": [moment_ids[-1]]},
        "estimated_duration": 45,
        "emotional_arc": ["DESPAIR", "BELIEF"],
        "recommended_formats": ["SHORT", "MEDIUM"],
    }


# ── Models ───────────────────────────────────────────────────────────────────


def test_story_model_valid():
    story = Story(story_id="s1", project_id="p1", title="Story", archetype="COMEBACK", status="SUGGESTED")
    assert story.status == "SUGGESTED"
    assert story.to_dict()["archetype"] == "COMEBACK"


def test_story_model_invalid_status_and_archetype():
    with pytest.raises(ValueError, match="status"):
        Story(story_id="s1", project_id="p1", title="Story", status="NOPE")
    with pytest.raises(ValueError, match="archetype"):
        Story(story_id="s1", project_id="p1", title="Story", archetype="NOPE")


def test_story_moment_model_valid_and_invalid_role():
    rel = StoryMoment(story_moment_id="sm1", story_id="s1", moment_id="m1", narrative_role="CLIMAX", sequence_order=1)
    assert rel.sequence_order == 1
    with pytest.raises(ValueError, match="narrative role"):
        StoryMoment(story_moment_id="sm1", story_id="s1", moment_id="m1", narrative_role="BAD", sequence_order=1)


# ── Persistence ──────────────────────────────────────────────────────────────


def test_story_persistence_list_and_status_update(tmp_path, monkeypatch):
    _job, _jobs_dir, project, _moments = _project_and_moments(tmp_path, monkeypatch)
    upsert_story(project_id=project.project_id, story_id="story_alpha", title="Alpha", archetype="COMEBACK", metadata={"x": 1})
    upsert_story(project_id=project.project_id, story_id="story_beta", title="Beta", archetype="CHAOS")

    fetched = get_story("story_alpha")
    stories = list_project_stories(project.project_id)
    updated = update_story_status("story_alpha", "APPROVED")

    assert fetched.title == "Alpha"
    assert fetched.metadata == {"x": 1}
    assert {story.story_id for story in stories} == {"story_alpha", "story_beta"}
    assert updated.status == "APPROVED"
    with pytest.raises(ValueError, match="status"):
        update_story_status("story_alpha", "BAD")


# ── Relationships ────────────────────────────────────────────────────────────


def test_story_moment_relationships_and_cross_project_rejection(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    story = upsert_story(project_id=project.project_id, story_id="story_rel", title="Rel")

    add_story_moment(story.story_id, moments[0].moment_id, "HOOK", 0)
    add_story_moment(story.story_id, moments[1].moment_id, "CLIMAX", 1)
    rels = list_story_moments(story.story_id)

    assert [rel.sequence_order for rel in rels] == [0, 1]
    assert {rel.narrative_role for rel in rels} == {"HOOK", "CLIMAX"}
    assert remove_story_moment(story.story_id, moments[0].moment_id, sequence_order=0) is True
    assert len(list_story_moments(story.story_id)) == 1

    from pipeline.runtime_service import upsert_project
    other_project = upsert_project(
        project_id="other_project",
        job_id="other_job",
        profile="football",
        sport="football",
        display_name="Other",
        status="READY",
    )
    other_artifact = register_artifact(project_id=other_project.project_id, artifact_type="analysis_moments", path=tmp_path / "other_moments.json")
    other_moment = upsert_moment(adapt_detection_to_moments(
        [{"clip_id": "other_001", "category": "GOAL", "start_time": 1, "end_time": 3}],
        project_id=other_project.project_id,
        source_artifact_id=other_artifact.artifact_id,
    )[0])

    with pytest.raises(ValueError, match="different project"):
        add_story_moment(story.story_id, other_moment.moment_id, "HOOK", 0)


def test_one_moment_multiple_stories_and_many_moments_per_story(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    story_a = upsert_story(project_id=project.project_id, story_id="story_a", title="A")
    story_b = upsert_story(project_id=project.project_id, story_id="story_b", title="B")
    story_c = upsert_story(project_id=project.project_id, story_id="story_c", title="C")

    add_story_moment(story_a.story_id, moments[0].moment_id, "CLIMAX", 0)
    add_story_moment(story_b.story_id, moments[0].moment_id, "HOOK", 0)
    add_story_moment(story_c.story_id, moments[0].moment_id, "AFTERMATH", 0)
    add_story_moment(story_c.story_id, moments[1].moment_id, "SETUP", 1)

    assert len(list_story_moments(story_a.story_id)) == 1
    assert len(list_story_moments(story_b.story_id)) == 1
    assert {rel.moment_id for rel in list_story_moments(story_c.story_id)} == {moments[0].moment_id, moments[1].moment_id}


def test_story_changes_do_not_mutate_moment(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    before = get_moment(moments[0].moment_id)
    story = upsert_story(project_id=project.project_id, story_id="story_mut", title="Mut")
    add_story_moment(story.story_id, moments[0].moment_id, "CLIMAX", 0)
    after = get_moment(moments[0].moment_id)

    assert before.to_dict() == after.to_dict()


def test_replace_story_moments(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    story = upsert_story(project_id=project.project_id, story_id="story_replace", title="Replace")
    add_story_moment(story.story_id, moments[0].moment_id, "HOOK", 0)

    replace_story_moments(story.story_id, [
        {"moment_id": moments[1].moment_id, "narrative_role": "HOOK", "sequence_order": 0},
        {"moment_id": moments[2].moment_id, "narrative_role": "CLIMAX", "sequence_order": 1},
    ])

    rels = list_story_moments(story.story_id)
    assert len(rels) == 2
    assert {rel.moment_id for rel in rels} == {moments[1].moment_id, moments[2].moment_id}


# ── Adapter ──────────────────────────────────────────────────────────────────


def test_adapter_normalizes_suggestions_and_is_idempotent(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    suggestions = [_sample_suggestion("comeback", ["001", "003"])]

    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)
    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)

    stories = list_project_stories(project.project_id)
    assert len(stories) == 1
    assert stories[0].story_id == canonical_story_id(project.project_id, "comeback")
    rels = list_story_moments(stories[0].story_id)
    assert len(rels) == 2
    assert {rel.narrative_role for rel in rels} == {"HOOK", "CLIMAX"}


def test_adapter_preserves_operator_status_and_provenance(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    suggestions = [_sample_suggestion("approved_story", ["001", "002"])]
    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)
    story_id = canonical_story_id(project.project_id, "approved_story")
    update_story_status(story_id, "APPROVED")

    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)

    story = get_story(story_id)
    assert story.status == "APPROVED"
    assert story.metadata["original_story_id"] == "approved_story"
    assert story.metadata["original_archetype"] == "COMEBACK"


def test_adapter_unresolved_moment_fails_safely(tmp_path, monkeypatch):
    _job, _jobs_dir, project, _moments = _project_and_moments(tmp_path, monkeypatch)
    suggestions = [_sample_suggestion("broken", ["999"])]

    with pytest.raises(ValueError, match="cannot be resolved"):
        adapt_story_suggestions(suggestions, project_id=project.project_id, moments=[])


# ── Runtime summary ──────────────────────────────────────────────────────────


def test_runtime_summary_story_counts(tmp_path, monkeypatch):
    job, jobs_dir, project, _moments = _project_and_moments(tmp_path, monkeypatch)
    upsert_story(project_id=project.project_id, story_id="s1", title="S1", status="SUGGESTED")
    upsert_story(project_id=project.project_id, story_id="s2", title="S2", status="SUGGESTED")
    upsert_story(project_id=project.project_id, story_id="s3", title="S3", status="APPROVED")
    upsert_story(project_id=project.project_id, story_id="s4", title="S4", status="REJECTED")

    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)

    assert summary["story_summary"] == {"story_count": 4, "suggested_count": 2, "approved_count": 1, "rejected_count": 1}
    assert len(summary["stories"]) == 4


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_preserves_existing_phase2_data(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_type TEXT NOT NULL, path TEXT NOT NULL, mime_type TEXT NOT NULL, status TEXT NOT NULL, parent_artifact_id TEXT, created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE pipeline_runs (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT, finished_at TEXT, progress_current INTEGER, progress_total INTEGER, error_code TEXT, error_message TEXT, retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            CREATE TABLE pipeline_events (event_id TEXT PRIMARY KEY, run_id TEXT, project_id TEXT NOT NULL, event_type TEXT NOT NULL, stage TEXT NOT NULL, message TEXT NOT NULL, progress_current INTEGER, progress_total INTEGER, created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}');
            CREATE TABLE moments (moment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_artifact_id TEXT, sport TEXT NOT NULL, universal_event_type TEXT NOT NULL, sport_event_type TEXT NOT NULL, start_seconds REAL NOT NULL, peak_seconds REAL, end_seconds REAL NOT NULL, importance REAL, confidence REAL, team TEXT, review_state TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT, participants_json TEXT NOT NULL DEFAULT '[]', signals_json TEXT NOT NULL DEFAULT '{}', emotion_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        project = conn.execute("SELECT * FROM projects WHERE project_id = 'p1'").fetchone()
    assert {"stories", "story_moments"} <= tables
    assert project["display_name"] == "Match"


# ── Integration ──────────────────────────────────────────────────────────────


def test_phase_3_integration_flow(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    suggestions = [
        _sample_suggestion("story_alpha", ["001", "003"]),
        _sample_suggestion("story_beta", ["001"]),
    ]

    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)

    stories = list_project_stories(project.project_id)
    alpha = next(s for s in stories if s.story_id == canonical_story_id(project.project_id, "story_alpha"))
    beta = next(s for s in stories if s.story_id == canonical_story_id(project.project_id, "story_beta"))
    alpha_rels = list_story_moments(alpha.story_id)
    beta_rels = list_story_moments(beta.story_id)

    shared = next(moment for moment in moments if moment.moment_id in {rel.moment_id for rel in alpha_rels})
    assert shared.moment_id in {rel.moment_id for rel in beta_rels}
    assert [rel.sequence_order for rel in alpha_rels] == [0, 1]
    assert shared.start_seconds != alpha_rels[0].sequence_order
    assert sorted(alpha_rels, key=lambda rel: rel.sequence_order)[0].narrative_role == "HOOK"
    assert list_project_moments(project.project_id) == moments