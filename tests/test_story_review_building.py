from __future__ import annotations

import pytest

from pipeline.runtime_service import (
    add_story_moment,
    get_story_runtime_summary,
    list_story_moments,
    update_story_moment,
    update_story_status,
    upsert_story,
)
from pipeline.story_adapter import adapt_story_suggestions
from tests.test_stories import _project_and_moments, _sample_suggestion


def _approved_story(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moments = _project_and_moments(tmp_path, monkeypatch)
    suggestions = [_sample_suggestion("story_review", ["001", "002"])]
    adapt_story_suggestions(suggestions, project_id=project.project_id, moments=moments)
    stories = upsert_story(project_id=project.project_id, story_id="story_review", title="Review")
    story_id = stories.story_id
    return project, story_id, moments


# ── Story review ─────────────────────────────────────────────────────────────


def test_story_review_status_transitions(tmp_path, monkeypatch):
    project, story_id, _moments = _approved_story(tmp_path, monkeypatch)
    assert update_story_status(story_id, "APPROVED").status == "APPROVED"
    assert update_story_status(story_id, "REJECTED").status == "REJECTED"
    assert update_story_status(story_id, "SUGGESTED").status == "SUGGESTED"
    assert update_story_status(story_id, "APPROVED").status == "APPROVED"
    assert update_story_status(story_id, "ARCHIVED").status == "ARCHIVED"


def test_story_review_invalid_status_rejected(tmp_path, monkeypatch):
    _project, story_id, _moments = _approved_story(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="status"):
        update_story_status(story_id, "NOPE")


# ── Story building ───────────────────────────────────────────────────────────


def test_story_building_add_remove_and_reorder(tmp_path, monkeypatch):
    _project, story_id, moments = _approved_story(tmp_path, monkeypatch)
    add_story_moment(story_id, moments[0].moment_id, "HOOK", 0)
    add_story_moment(story_id, moments[1].moment_id, "SETUP", 1)
    add_story_moment(story_id, moments[2].moment_id, "CLIMAX", 2)

    rels = list_story_moments(story_id)
    assert [rel.sequence_order for rel in rels] == [0, 1, 2]

    # Change role
    update_story_moment(story_id, moments[1].moment_id, narrative_role="ESCALATION")
    assert [rel.narrative_role for rel in list_story_moments(story_id) if rel.moment_id == moments[1].moment_id] == ["ESCALATION"]

    # Change sequence then resequence deterministically
    updated = update_story_moment(story_id, moments[2].moment_id, sequence_order=0)
    assert [rel.sequence_order for rel in updated] == [1, 2, 3]


def test_story_building_deterministic_ordering_after_edit(tmp_path, monkeypatch):
    _project, story_id, moments = _approved_story(tmp_path, monkeypatch)
    add_story_moment(story_id, moments[0].moment_id, "HOOK", 5)
    add_story_moment(story_id, moments[1].moment_id, "CLIMAX", 8)

    updated = update_story_moment(story_id, moments[1].moment_id, sequence_order=0)

    assert [rel.sequence_order for rel in updated] == [1, 2]
    assert updated[0].moment_id == moments[1].moment_id


def test_story_building_invalid_role_rejected(tmp_path, monkeypatch):
    _project, story_id, moments = _approved_story(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="narrative role"):
        add_story_moment(story_id, moments[0].moment_id, "BAD_ROLE", 0)


def test_same_moment_reused_in_multiple_stories(tmp_path, monkeypatch):
    _project, _story_id, moments = _approved_story(tmp_path, monkeypatch)
    story_b = upsert_story(project_id=moments[0].project_id, story_id="story_b", title="B")
    add_story_moment(_story_id, moments[0].moment_id, "CLIMAX", 0)
    add_story_moment(story_b.story_id, moments[0].moment_id, "HOOK", 0)
    assert len(list_story_moments(_story_id)) == 1
    assert len(list_story_moments(story_b.story_id)) == 1


def test_reject_moment_remains_visible_and_usable(tmp_path, monkeypatch):
    _project, story_id, moments = _approved_story(tmp_path, monkeypatch)
    from pipeline.runtime_service import update_moment_review_state
    rejected = update_moment_review_state(moments[1].moment_id, "REJECT")
    add_story_moment(story_id, rejected.moment_id, "CLIMAX", 0)
    summary = get_story_runtime_summary(story_id)
    assert summary["ordered_moments"][0]["moment"]["review_state"] == "REJECT"


def test_story_detail_read_model_shows_ordered_moments(tmp_path, monkeypatch):
    _project, story_id, moments = _approved_story(tmp_path, monkeypatch)
    add_story_moment(story_id, moments[2].moment_id, "CLIMAX", 0)
    add_story_moment(story_id, moments[0].moment_id, "HOOK", 1)
    summary = get_story_runtime_summary(story_id)
    assert [entry["sequence_order"] for entry in summary["ordered_moments"]] == [0, 1]
    assert summary["ordered_moments"][0]["narrative_role"] == "CLIMAX"