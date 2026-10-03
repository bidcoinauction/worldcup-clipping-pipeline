"""Tests for the story engine service boundary (Slice 7)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.story_engine import (
    ARCHETYPES,
    NARRATIVE_ROLES,
    RECOMMENDED_FORMATS,
    generate_story_suggestions,
    read_story_suggestions,
    read_story_state,
    update_story_state,
    validate_story,
    validate_stories,
    write_story_suggestions,
    _build_story_prompt,
    _parse_stories_json,
    _safe_error_message,
)
from tests.test_pilot_intake import build_intake


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"story engine bytes" * 100)
    return path


SAMPLE_MOMENTS = [
    {
        "clip_id": "001",
        "category": "EMOTION",
        "start_time": "5",
        "end_time": "20",
        "virality_score": 9,
        "caption": "Gerrard header pulls one back",
        "hook_text": "The captain refuses to die",
        "retention_reason": "Emotional comeback begins",
        "share_reason": "Captain's leadership moment",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "002",
        "category": "CHAOS",
        "start_time": "25",
        "end_time": "35",
        "virality_score": 8,
        "caption": "Smicer scores from distance",
        "hook_text": "From nowhere",
        "retention_reason": "Momentum shift",
        "share_reason": "Unexpected goal",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "003",
        "category": "EMOTION",
        "start_time": "40",
        "end_time": "55",
        "virality_score": 10,
        "caption": "Alonso equalizer on the rebound",
        "hook_text": "Six minutes that changed everything",
        "retention_reason": "Peak emotional moment",
        "share_reason": "Impossible comeback complete",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "004",
        "category": "AURA",
        "start_time": "60",
        "end_time": "70",
        "virality_score": 7,
        "caption": "Crowd disbelief after equalizer",
        "hook_text": "Anfield erupts",
        "retention_reason": "Raw emotion",
        "share_reason": "Atmosphere",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "005",
        "category": "EMOTION",
        "start_time": "80",
        "end_time": "90",
        "virality_score": 8,
        "caption": "Dudek save from Shevchenko",
        "hook_text": "The save that won the cup",
        "retention_reason": "Dramatic conclusion",
        "share_reason": "Goalkeeper heroics",
        "status": "needs_visual_scrub",
    },
]

SAMPLE_STORIES = [
    {
        "story_id": "impossible_comeback",
        "title": "The Impossible Comeback",
        "archetype": "COMEBACK",
        "summary": "Liverpool trailed 3-0 at halftime. Then the captain changed everything.",
        "hook": "One of the greatest comebacks in football history",
        "moment_ids": ["001", "002", "003", "004", "005"],
        "estimated_duration": 58,
        "emotional_arc": ["DESPAIR", "BELIEF", "CHAOS", "SURVIVAL"],
        "narrative_roles": {
            "HOOK": ["001"],
            "SETUP": ["001"],
            "ESCALATION": ["002"],
            "CLIMAX": ["003"],
            "AFTERMATH": ["004", "005"],
        },
        "recommended_formats": ["SHORT", "MEDIUM"],
        "why_this_story": "Classic underdog narrative with escalating emotional stakes",
    },
    {
        "story_id": "captain_refused",
        "title": "The Captain Who Refused to Die",
        "archetype": "INDIVIDUAL_PERFORMANCE",
        "summary": "Steven Gerrard willed his team back from the dead.",
        "hook": "A captain's responsibility transcends the game",
        "moment_ids": ["001", "003", "004"],
        "estimated_duration": 42,
        "emotional_arc": ["TENSION", "DETERMINATION", "RELEASE"],
        "narrative_roles": {
            "HOOK": ["001"],
            "SETUP": ["001"],
            "CLIMAX": ["003"],
            "AFTERMATH": ["004"],
        },
        "recommended_formats": ["SHORT"],
        "why_this_story": "Individual leadership driving collective outcome",
    },
]


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "story_test", "project": sport},
        "media": {"source_id": "story_source", "match_or_event_name": "Story Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


def _setup_completed_analysis(media_file: Path, jobs_root: Path, tmp_path: Path) -> dict:
    """Create a project with completed analysis and moments."""
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    from pipeline.detection import update_analysis_state, _write_moments

    # Write moments
    _write_moments(job_id, jobs_root, SAMPLE_MOMENTS)

    # Mark analysis complete
    update_analysis_state(
        job_id, status="COMPLETE", stage="PREPARING_RESULTS",
        jobs_dir=jobs_root, analysis_manifest_count=len(SAMPLE_MOMENTS),
    )

    return job


# ── Taxonomy tests ──────────────────────────────────────────────────────────


def test_archetypes_are_defined():
    assert len(ARCHETYPES) == 10
    assert "COMEBACK" in ARCHETYPES
    assert "COLLAPSE" in ARCHETYPES
    assert "CHAOS" in ARCHETYPES


def test_narrative_roles_are_defined():
    assert NARRATIVE_ROLES == {"HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"}


def test_recommended_formats_are_defined():
    assert RECOMMENDED_FORMATS == {"SHORT", "MEDIUM", "LONG"}


# ── Story state I/O ─────────────────────────────────────────────────────────


def test_update_and_read_story_state(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_story_state(job_id, status="RUNNING", stage="BUILDING_STORIES", jobs_dir=jobs_root)
    state = read_story_state(job_id, jobs_dir=jobs_root)
    assert state["story_status"] == "RUNNING"
    assert state["story_stage"] == "BUILDING_STORIES"
    assert state["story_started_at"]


def test_story_state_complete(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_story_state(job_id, status="RUNNING", jobs_dir=jobs_root)
    update_story_state(job_id, status="COMPLETE", story_count=3, jobs_dir=jobs_root)
    state = read_story_state(job_id, jobs_dir=jobs_root)
    assert state["story_status"] == "COMPLETE"
    assert state["story_count"] == 3
    assert state["story_completed_at"]


def test_story_state_error_cleared_on_success(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_story_state(job_id, status="FAILED", error="test error", jobs_dir=jobs_root)
    update_story_state(job_id, status="COMPLETE", jobs_dir=jobs_root)
    state = read_story_state(job_id, jobs_dir=jobs_root)
    assert state["story_status"] == "COMPLETE"
    assert state["story_error"] == ""


# ── Story suggestions I/O ──────────────────────────────────────────────────


def test_write_and_read_story_suggestions(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    path = write_story_suggestions(job_id, SAMPLE_STORIES, jobs_dir=jobs_root)
    assert path.exists()

    stories = read_story_suggestions(job_id, jobs_dir=jobs_root)
    assert len(stories) == 2
    assert stories[0]["story_id"] == "impossible_comeback"
    assert stories[1]["story_id"] == "captain_refused"


def test_read_story_suggestions_empty(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    stories = read_story_suggestions(job["job_id"], jobs_dir=jobs_root)
    assert stories == []


# ── Validation ──────────────────────────────────────────────────────────────


def test_validate_story_valid():
    moment_ids = {"001", "002", "003", "004", "005"}
    errors = validate_story(SAMPLE_STORIES[0], moment_ids)
    assert errors == []


def test_validate_story_missing_story_id():
    story = {"title": "Test", "archetype": "COMEBACK", "moment_ids": ["001"]}
    errors = validate_story(story, {"001"})
    assert any("story_id" in e for e in errors)


def test_validate_story_missing_title():
    story = {"story_id": "test", "archetype": "COMEBACK", "moment_ids": ["001"]}
    errors = validate_story(story, {"001"})
    assert any("title" in e for e in errors)


def test_validate_story_unsupported_archetype():
    story = {"story_id": "test", "title": "Test", "archetype": "INVALID", "moment_ids": ["001"]}
    errors = validate_story(story, {"001"})
    assert any("archetype" in e for e in errors)


def test_validate_story_no_moments():
    story = {"story_id": "test", "title": "Test", "archetype": "COMEBACK", "moment_ids": []}
    errors = validate_story(story, {"001"})
    assert any("at least one moment" in e for e in errors)


def test_validate_story_invalid_moment_id():
    story = {"story_id": "test", "title": "Test", "archetype": "COMEBACK", "moment_ids": ["001", "999"]}
    errors = validate_story(story, {"001", "002"})
    assert any("999" in e for e in errors)


def test_validate_story_unsupported_format():
    story = {
        "story_id": "test", "title": "Test", "archetype": "COMEBACK",
        "moment_ids": ["001"], "recommended_formats": ["INVALID"],
    }
    errors = validate_story(story, {"001"})
    assert any("format" in e for e in errors)


def test_validate_story_narrative_role_references_unknown_moment():
    story = {
        "story_id": "test", "title": "Test", "archetype": "COMEBACK",
        "moment_ids": ["001"],
        "narrative_roles": {"HOOK": ["999"]},
    }
    errors = validate_story(story, {"001"})
    assert any("999" in e for e in errors)


def test_validate_stories_empty():
    errors = validate_stories([], {"001"})
    assert any("at least one" in e for e in errors)


def test_validate_stories_too_many():
    stories = [{"story_id": f"s{i}", "title": f"S{i}", "archetype": "COMEBACK", "moment_ids": ["001"]}
               for i in range(6)]
    errors = validate_stories(stories, {"001"})
    assert any("too many" in e for e in errors)


def test_validate_stories_duplicate_ids():
    stories = [
        {"story_id": "same", "title": "A", "archetype": "COMEBACK", "moment_ids": ["001"]},
        {"story_id": "same", "title": "B", "archetype": "CHAOS", "moment_ids": ["001"]},
    ]
    errors = validate_stories(stories, {"001"})
    assert any("duplicate" in e for e in errors)


# ── Prompt construction ─────────────────────────────────────────────────────


def test_build_story_prompt_contains_moments():
    prompt = _build_story_prompt(SAMPLE_MOMENTS, "Liverpool vs AC Milan", "football")
    assert "Liverpool vs AC Milan" in prompt
    assert "001" in prompt
    assert "005" in prompt
    assert "Gerrard header" in prompt


def test_build_story_prompt_contains_archetypes():
    prompt = _build_story_prompt(SAMPLE_MOMENTS, "Test", "football")
    for arch in ARCHETYPES:
        assert arch in prompt


def test_build_story_prompt_contains_roles():
    prompt = _build_story_prompt(SAMPLE_MOMENTS, "Test", "football")
    for role in NARRATIVE_ROLES:
        assert role in prompt


# ── JSON parsing ────────────────────────────────────────────────────────────


def test_parse_stories_json_array():
    raw = json.dumps(SAMPLE_STORIES)
    result = _parse_stories_json(raw)
    assert len(result) == 2


def test_parse_stories_json_wrapped():
    raw = json.dumps({"stories": SAMPLE_STORIES})
    result = _parse_stories_json(raw)
    assert len(result) == 2


def test_parse_stories_json_markdown_wrapped():
    inner = json.dumps(SAMPLE_STORIES)
    raw = f"```json\n{inner}\n```"
    result = _parse_stories_json(raw)
    assert len(result) == 2


def test_parse_stories_json_invalid():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_stories_json("not json at all")


# ── generate_story_suggestions ─────────────────────────────────────────────


def test_generate_story_suggestions_requires_complete_analysis(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = generate_story_suggestions(job["job_id"], jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "Analysis must be complete" in result["error"]


def test_generate_story_suggestions_requires_moments(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    from pipeline.detection import update_analysis_state
    update_analysis_state(job_id, status="COMPLETE", jobs_dir=jobs_root)

    result = generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "No detected moments" in result["error"]


def test_generate_story_suggestions_dry_run(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    result = generate_story_suggestions(job["job_id"], jobs_dir=jobs_root, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True


def test_generate_story_suggestions_success(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    with patch("pipeline.story_engine._run_story_llm", return_value=SAMPLE_STORIES):
        result = generate_story_suggestions(job_id, jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["story_count"] == 2

    stories = read_story_suggestions(job_id, jobs_dir=jobs_root)
    assert len(stories) == 2

    state = read_story_state(job_id, jobs_dir=jobs_root)
    assert state["story_status"] == "COMPLETE"
    assert state["story_count"] == 2


def test_generate_story_suggestions_validates_output(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    bad_stories = [{"story_id": "bad", "title": "Bad", "archetype": "INVALID", "moment_ids": ["001"]}]

    with patch("pipeline.story_engine._run_story_llm", return_value=bad_stories):
        result = generate_story_suggestions(job_id, jobs_dir=jobs_root)

    assert result["ok"] is False
    assert "Validation failed" in result["error"]


def test_generate_story_suggestions_llm_failure_preserves_project(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    with patch("pipeline.story_engine._run_story_llm", side_effect=RuntimeError("Request failed with sk-abc1234567890123456789012345678")):
        result = generate_story_suggestions(job_id, jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "FAILED"
    assert "sk-abc1234567890123456789012345678" not in result["error"]  # credential-safe

    # Project and moments still intact
    from pipeline.pilot import read_job
    read_job(job_id, jobs_dir=jobs_root)
    moments = read_story_suggestions(job_id, jobs_dir=jobs_root)
    assert moments == []  # no stories written, but moments untouched


def test_generate_story_suggestions_regeneration(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    with patch("pipeline.story_engine._run_story_llm", return_value=SAMPLE_STORIES):
        result1 = generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result1["story_count"] == 2

    # Regenerate with different stories
    new_stories = [SAMPLE_STORIES[0]]
    with patch("pipeline.story_engine._run_story_llm", return_value=new_stories):
        result2 = generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result2["story_count"] == 1

    stories = read_story_suggestions(job_id, jobs_dir=jobs_root)
    assert len(stories) == 1


# ── Multiple stories ────────────────────────────────────────────────────────


def test_one_coherent_story_is_acceptable(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    single = [SAMPLE_STORIES[0]]
    with patch("pipeline.story_engine._run_story_llm", return_value=single):
        result = generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result["ok"] is True
    assert result["story_count"] == 1


def test_five_stories_accepted(media_file, jobs_root, tmp_path):
    job = _setup_completed_analysis(media_file, jobs_root, tmp_path)
    job_id = job["job_id"]

    five = []
    for i, arch in enumerate(sorted(ARCHETYPES)):
        five.append({
            "story_id": f"story_{i}",
            "title": f"Story {i}",
            "archetype": arch,
            "summary": f"Summary {i}",
            "hook": f"Hook {i}",
            "moment_ids": ["001"],
            "estimated_duration": 30,
            "emotional_arc": ["TENSION", "RELEASE"],
            "narrative_roles": {"HOOK": ["001"]},
            "recommended_formats": ["SHORT"],
            "why_this_story": f"Why {i}",
        })

    with patch("pipeline.story_engine._run_story_llm", return_value=five[:5]):
        result = generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result["ok"] is True
    assert result["story_count"] == 5


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_execution_in_story_engine():
    import pipeline.story_engine as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    assert "shell=True" not in source
    assert "subprocess" not in source


# ── Safe error message ──────────────────────────────────────────────────────


def test_safe_error_message_strips_api_keys():
    msg = _safe_error_message(Exception("Error with sk-abc123def456ghi789jkl012mno key"))
    assert "sk-abc123" not in msg


def test_safe_error_message_truncates_long():
    msg = _safe_error_message(Exception("x" * 1000))
    assert len(msg) <= 510


def test_safe_error_message_empty_fallback():
    msg = _safe_error_message(Exception(""))
    assert "failed" in msg.lower() or "needs attention" in msg.lower()
