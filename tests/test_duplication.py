from __future__ import annotations

import json

import pytest

from pipeline import operator_console
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.runtime_service import (
    duplicate_project,
    get_project,
    get_project_runtime_summary,
    index_existing_project,
    list_pipeline_runs,
    list_project_artifacts,
    list_project_edit_briefs,
    list_project_edls,
    list_project_exports,
    list_project_moments,
    list_project_renders,
    list_project_stories,
    register_artifact,
    upsert_moment,
    upsert_project,
    upsert_story,
)
from pipeline.story_adapter import canonical_story_id
from tests.test_runtime_managed_analysis import _make_job


@pytest.fixture(autouse=True)
def _isolated_runtime_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))


def _source_runtime(tmp_path, monkeypatch, *, sport="football"):
    source = upsert_project(
        project_id="project_a",
        job_id="project_a",
        profile="football",
        sport=sport,
        display_name="Germany vs Portugal",
        status="READY",
    )
    source_file = tmp_path / "source.mp4"
    source_file.write_bytes(b"media")
    artifact = register_artifact(project_id="project_a", artifact_type="source_media", path=source_file)
    analysis_artifact = register_artifact(project_id="project_a", artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moments = []
    for row in [
        {"clip_id": "001", "category": "GOAL", "start_time": 10, "end_time": 15},
        {"clip_id": "002", "category": "SAVE", "start_time": 40, "end_time": 44},
    ]:
        moment = adapt_detection_to_moments([row], project_id="project_a", source_artifact_id=analysis_artifact.artifact_id)[0]
        moments.append(upsert_moment(moment))
    return source, artifact, moments


# ── Lineage ──────────────────────────────────────────────────────────────────


def test_original_project_lineage(tmp_path, monkeypatch):
    source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)
    project = get_project("project_a")
    assert project.parent_project_id is None
    assert project.source_project_id == "project_a"


def test_nested_duplication_lineage(tmp_path, monkeypatch):
    _source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)

    result_b = duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")
    assert result_b["ok"] is True
    b = get_project("project_b")
    assert b.parent_project_id == "project_a"
    assert b.source_project_id == "project_a"

    result_c = duplicate_project(source_project_id="project_b", new_project_id="project_c", display_name="C", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")
    c = get_project("project_c")
    assert c.parent_project_id == "project_b"
    assert c.source_project_id == "project_a"


# ── Artifact reuse ───────────────────────────────────────────────────────────


def test_artifact_reuse_no_physical_copy(tmp_path, monkeypatch):
    source, artifact, _moments = _source_runtime(tmp_path, monkeypatch)

    duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")

    source_artifacts = list_project_artifacts("project_b")
    reused_source = next(a for a in source_artifacts if a.artifact_type == "source_media")
    assert reused_source.path == str(tmp_path / "source.mp4")
    assert reused_source.metadata.get("reused") is True
    assert reused_source.metadata.get("reused_from_project_id") == "project_a"
    assert reused_source.metadata.get("reused_from_artifact_id") == artifact.artifact_id
    assert (tmp_path / "source.mp4").read_bytes() == b"media"


# ── Moment clone / lineage ───────────────────────────────────────────────────


def test_moment_clone_lineage_and_review_reset(tmp_path, monkeypatch):
    _source, _artifact, moments = _source_runtime(tmp_path, monkeypatch)
    from pipeline.runtime_service import update_moment_review_state
    update_moment_review_state(moments[0].moment_id, "MUST_USE", reviewed_by="operator")

    duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")

    original = list_project_moments("project_a")
    cloned = list_project_moments("project_b")
    assert len(cloned) == 2
    for clone in cloned:
        assert clone.project_id == "project_b"
        assert clone.moment_id != original[0].moment_id
        assert clone.origin_moment_id is not None
        assert clone.origin_project_id == "project_a"
        assert clone.review_state == "UNREVIEWED"
        assert clone.reviewed_at is None
        assert clone.reviewed_by is None
        assert clone.importance == original[0].importance or clone.sport_event_type in {"GOAL", "SAVE"}
    assert original[0].review_state == "MUST_USE"
    assert original[0].reviewed_by == "operator"


# ── Downstream isolation ─────────────────────────────────────────────────────


def test_duplicate_starts_with_zero_downstream(tmp_path, monkeypatch):
    _source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)

    duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")

    assert list_project_stories("project_b") == []
    assert list_project_edit_briefs("project_b") == []
    assert list_project_edls("project_b") == []
    assert list_project_renders("project_b") == []
    assert list_project_exports("project_b") == []
    assert list_pipeline_runs("project_b") == []


def test_downstream_isolation_after_duplication(tmp_path, monkeypatch):
    _source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)
    duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")

    upsert_story(project_id="project_b", story_id="story_b", title="B Story")
    cloned = list_project_moments("project_b")
    update_review = __import__("pipeline.runtime_service", fromlist=["update_moment_review_state"]).update_moment_review_state
    update_review(cloned[0].moment_id, "KEEP")

    assert len(list_project_stories("project_a")) == 0
    original = list_project_moments("project_a")
    assert all(moment.review_state == "UNREVIEWED" for moment in original)


# ── Analysis reuse ───────────────────────────────────────────────────────────


def test_analysis_reuse_no_fake_pipeline_run(tmp_path, monkeypatch):
    _source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)

    duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")

    assert list_pipeline_runs("project_b") == []
    summary = get_project_runtime_summary("project_b")
    assert summary["lineage"]["analysis_reused"] is True
    assert summary["lineage"]["is_duplicate"] is True


# ── Compatibility failures ───────────────────────────────────────────────────


def test_duplication_compatibility_failures(tmp_path, monkeypatch):
    _source, _artifact, _moments = _source_runtime(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="not found"):
        duplicate_project(source_project_id="missing", new_project_id="project_x", display_name="X")
    with pytest.raises(ValueError, match="already exists"):
        duplicate_project(source_project_id="project_a", new_project_id="project_a", display_name="X")
    with pytest.raises(ValueError, match="reuse 'football' intelligence"):
        duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", sport="basketball")
    with pytest.raises(ValueError, match="reuse mode"):
        duplicate_project(source_project_id="project_a", new_project_id="project_b", display_name="B", reuse_mode="NOPE")


def test_duplication_moments_mode_requires_moments(tmp_path, monkeypatch):
    upsert_project(project_id="empty_a", job_id="empty_a", profile="football", sport="football", display_name="Empty", status="READY")

    with pytest.raises(ValueError, match="moments"):
        duplicate_project(source_project_id="empty_a", new_project_id="empty_b", display_name="B", reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")


# ── Operator Console integration ─────────────────────────────────────────────


def test_operator_console_duplicate_integration(tmp_path, monkeypatch):
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
    upsert_story(project_id=project.project_id, story_id=canonical_story_id(project.project_id, "story_a"), title="A Story")

    result = operator_console.duplicate_project(job["job_id"], display_name="Derived B", jobs_dir=jobs_dir)

    assert result["ok"] is True
    dup_job_id = result["job_id"]
    dup_project = get_project(dup_job_id)
    assert dup_project.parent_project_id == project.project_id
    assert dup_project.source_project_id == project.project_id
    assert len(list_project_moments(dup_project.project_id)) == 2
    assert list_project_stories(dup_project.project_id) == []
    assert len(list_project_stories(project.project_id)) == 1
    assert len(list_project_moments(project.project_id)) == 2


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_lineage_columns(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE moments (moment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, source_artifact_id TEXT, sport TEXT NOT NULL, universal_event_type TEXT NOT NULL, sport_event_type TEXT NOT NULL, start_seconds REAL NOT NULL, peak_seconds REAL, end_seconds REAL NOT NULL, importance REAL, confidence REAL, team TEXT, review_state TEXT NOT NULL, reviewed_at TEXT, reviewed_by TEXT, participants_json TEXT NOT NULL DEFAULT '[]', signals_json TEXT NOT NULL DEFAULT '{}', emotion_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        project_cols = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
        moment_cols = {row[1] for row in conn.execute("PRAGMA table_info(moments)")}
        project = conn.execute("SELECT * FROM projects WHERE project_id = 'p1'").fetchone()
    assert {"parent_project_id", "source_project_id", "reuse_mode"} <= project_cols
    assert {"origin_moment_id", "origin_project_id"} <= moment_cols
    assert project["display_name"] == "Match"