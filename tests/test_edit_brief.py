"""Tests for the edit intelligence service boundary (Slice 8)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.edit_brief import (
    PACING_VALUES,
    INTENSITY_VALUES,
    TRANSITION_INTENT_VALUES,
    AUDIO_STRATEGY_VALUES,
    TEXT_INTENT_VALUES,
    FORMAT_TREATMENTS,
    BEAT_ROLES,
    FORMAT_GUIDANCE,
    generate_edit_brief,
    read_edit_brief,
    list_edit_briefs,
    write_edit_brief,
    read_brief_state,
    update_brief_state,
    validate_edit_brief,
    _build_edit_brief_prompt,
    _parse_brief_json,
    _safe_error_message,
)
from pipeline.story_engine import write_story_suggestions, ARCHETYPES, NARRATIVE_ROLES
from pipeline.detection import update_analysis_state, _write_moments
from tests.test_pilot_intake import build_intake


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"edit brief bytes" * 100)
    return path


SAMPLE_MOMENTS = [
    {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
     "virality_score": 9, "caption": "Gerrard header pulls one back",
     "hook_text": "The captain refuses to die", "retention_reason": "Comeback begins",
     "share_reason": "Leadership", "status": "needs_visual_scrub"},
    {"clip_id": "002", "category": "CHAOS", "start_time": "25", "end_time": "35",
     "virality_score": 8, "caption": "Smicer scores from distance",
     "hook_text": "From nowhere", "retention_reason": "Momentum shift",
     "share_reason": "Unexpected", "status": "needs_visual_scrub"},
    {"clip_id": "003", "category": "EMOTION", "start_time": "40", "end_time": "55",
     "virality_score": 10, "caption": "Alonso equalizer on the rebound",
     "hook_text": "Six minutes that changed everything",
     "retention_reason": "Peak emotion", "share_reason": "Impossible comeback",
     "status": "needs_visual_scrub"},
    {"clip_id": "004", "category": "AURA", "start_time": "60", "end_time": "70",
     "virality_score": 7, "caption": "Crowd disbelief after equalizer",
     "hook_text": "Anfield erupts", "retention_reason": "Raw emotion",
     "share_reason": "Atmosphere", "status": "needs_visual_scrub"},
    {"clip_id": "005", "category": "EMOTION", "start_time": "80", "end_time": "90",
     "virality_score": 8, "caption": "Dudek save from Shevchenko",
     "hook_text": "The save that won the cup", "retention_reason": "Conclusion",
     "share_reason": "Heroics", "status": "needs_visual_scrub"},
]

SAMPLE_STORIES = [
    {
        "story_id": "impossible_comeback",
        "title": "The Impossible Comeback",
        "archetype": "COMEBACK",
        "summary": "Liverpool trailed 3-0 at halftime.",
        "hook": "One of the greatest comebacks in football history",
        "moment_ids": ["001", "002", "003", "004", "005"],
        "estimated_duration": 58,
        "emotional_arc": ["DESPAIR", "BELIEF", "CHAOS", "SURVIVAL"],
        "narrative_roles": {
            "HOOK": ["001"], "SETUP": ["001"], "ESCALATION": ["002"],
            "CLIMAX": ["003"], "AFTERMATH": ["004", "005"],
        },
        "recommended_formats": ["SHORT", "MEDIUM"],
        "why_this_story": "Classic underdog narrative",
    },
]

SAMPLE_BRIEF = {
    "job_id": "test_job",
    "story_id": "impossible_comeback",
    "format": "SHORT",
    "target_duration": 58,
    "story_archetype": "COMEBACK",
    "editorial_intent": "Make the comeback feel impossible until belief suddenly overwhelms.",
    "emotional_arc": ["DESPAIR", "BELIEF", "CHAOS", "PEAK", "RELEASE"],
    "beats": [
        {
            "beat_id": "beat_001",
            "role": "HOOK",
            "moment_id": "004",
            "direction": "Start with the eruption before revealing how Liverpool got there.",
            "pacing": "PEAK",
            "intensity": "PEAK",
            "audio_strategy": "COMMENTARY_FOCUS",
            "transition_intent": "AUDIO_DROP",
            "text_intent": "HOOK_TEXT",
        },
        {
            "beat_id": "beat_002",
            "role": "SETUP",
            "moment_id": "001",
            "direction": "Return to Liverpool looking finished.",
            "pacing": "SLOW",
            "intensity": "LOW",
            "audio_strategy": "CROWD_FOCUS",
            "transition_intent": "",
            "text_intent": "",
        },
        {
            "beat_id": "beat_003",
            "role": "ESCALATION",
            "moment_id": "002",
            "direction": "Gerrard starts the comeback.",
            "pacing": "BUILDING",
            "intensity": "MEDIUM",
            "audio_strategy": "",
            "transition_intent": "REACTION_CUT",
            "text_intent": "",
        },
        {
            "beat_id": "beat_004",
            "role": "CLIMAX",
            "moment_id": "003",
            "direction": "The impossible becomes real.",
            "pacing": "PEAK",
            "intensity": "PEAK",
            "audio_strategy": "CROWD_AND_COMMENTARY",
            "transition_intent": "",
            "text_intent": "SCORE_CONTEXT",
        },
        {
            "beat_id": "beat_005",
            "role": "AFTERMATH",
            "moment_id": "005",
            "direction": "Let disbelief breathe.",
            "pacing": "RELEASE",
            "intensity": "RELEASE",
            "audio_strategy": "AMBIENT",
            "transition_intent": "FADE",
            "text_intent": "",
        },
    ],
}


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "brief_test", "project": sport},
        "media": {"source_id": "brief_source", "match_or_event_name": "Brief Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


def _setup_completed(media_file: Path, jobs_root: Path, tmp_path: Path) -> dict:
    """Create project with completed analysis, moments, and stories."""
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    _write_moments(job_id, jobs_root, SAMPLE_MOMENTS)
    update_analysis_state(job_id, status="COMPLETE", stage="PREPARING_RESULTS",
                          jobs_dir=jobs_root, analysis_manifest_count=len(SAMPLE_MOMENTS))
    write_story_suggestions(job_id, SAMPLE_STORIES, jobs_dir=jobs_root)

    return job


# ── Controlled vocabularies ─────────────────────────────────────────────────


def test_pacing_values():
    assert PACING_VALUES == {"SLOW", "BUILDING", "MEDIUM", "FAST", "PEAK", "RELEASE"}


def test_intensity_values():
    assert INTENSITY_VALUES == {"LOW", "MEDIUM", "HIGH", "PEAK", "RELEASE"}


def test_transition_intent_values():
    assert "HARD_CUT" in TRANSITION_INTENT_VALUES
    assert "FADE" in TRANSITION_INTENT_VALUES
    assert len(TRANSITION_INTENT_VALUES) == 12


def test_audio_strategy_values():
    assert "ORIGINAL" in AUDIO_STRATEGY_VALUES
    assert "CROWD_FOCUS" in AUDIO_STRATEGY_VALUES
    assert len(AUDIO_STRATEGY_VALUES) == 10


def test_text_intent_values():
    assert TEXT_INTENT_VALUES == {"NONE", "HOOK_TEXT", "SCORE_CONTEXT", "TIME_CONTEXT", "PLAYER_CONTEXT"}


def test_format_treatments():
    assert FORMAT_TREATMENTS == {"SHORT", "MEDIUM", "LONG"}


def test_beat_roles():
    assert BEAT_ROLES == {"HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"}


def test_format_guidance():
    assert "SHORT" in FORMAT_GUIDANCE
    assert "MEDIUM" in FORMAT_GUIDANCE
    assert "LONG" in FORMAT_GUIDANCE


# ── Brief state I/O ─────────────────────────────────────────────────────────


def test_update_and_read_brief_state(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_brief_state(job_id, story_id="s1", status="RUNNING", jobs_dir=jobs_root)
    state = read_brief_state(job_id, "s1", jobs_dir=jobs_root)
    assert state["brief_status"] == "RUNNING"
    assert state["brief_started_at"]


def test_brief_state_complete(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_brief_state(job_id, story_id="s1", status="RUNNING", jobs_dir=jobs_root)
    update_brief_state(job_id, story_id="s1", status="COMPLETE", jobs_dir=jobs_root)
    state = read_brief_state(job_id, "s1", jobs_dir=jobs_root)
    assert state["brief_status"] == "COMPLETE"
    assert state["brief_completed_at"]


def test_brief_state_error_cleared(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_brief_state(job_id, story_id="s1", status="FAILED", error="test", jobs_dir=jobs_root)
    update_brief_state(job_id, story_id="s1", status="COMPLETE", jobs_dir=jobs_root)
    state = read_brief_state(job_id, "s1", jobs_dir=jobs_root)
    assert state["brief_status"] == "COMPLETE"
    assert state["brief_error"] == ""


# ── Edit brief I/O ─────────────────────────────────────────────────────────


def test_write_and_read_edit_brief(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    brief = {**SAMPLE_BRIEF, "job_id": job_id}

    path = write_edit_brief(job_id, brief, jobs_dir=jobs_root)
    assert path.exists()
    assert "impossible_comeback" in str(path)
    assert "SHORT" in str(path)

    loaded = read_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert loaded is not None
    assert loaded["format"] == "SHORT"
    assert len(loaded["beats"]) == 5


def test_read_edit_brief_not_found(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = read_edit_brief(job["job_id"], "nonexistent", "SHORT", jobs_dir=jobs_root)
    assert result is None


def test_list_edit_briefs(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    brief_short = {**SAMPLE_BRIEF, "job_id": job_id, "format": "SHORT"}
    brief_medium = {**SAMPLE_BRIEF, "job_id": job_id, "format": "MEDIUM",
                    "story_id": "impossible_comeback"}

    write_edit_brief(job_id, brief_short, jobs_dir=jobs_root)
    write_edit_brief(job_id, brief_medium, jobs_dir=jobs_root)

    all_briefs = list_edit_briefs(job_id, jobs_dir=jobs_root)
    assert len(all_briefs) == 2

    filtered = list_edit_briefs(job_id, story_id="impossible_comeback", jobs_dir=jobs_root)
    assert len(filtered) == 2


# ── Validation ──────────────────────────────────────────────────────────────


def test_validate_edit_brief_valid():
    moment_ids = {"001", "002", "003", "004", "005"}
    errors = validate_edit_brief(SAMPLE_BRIEF, moment_ids)
    assert errors == []


def test_validate_edit_brief_missing_job_id():
    brief = {k: v for k, v in SAMPLE_BRIEF.items() if k != "job_id"}
    errors = validate_edit_brief(brief, {"001"})
    assert any("job_id" in e for e in errors)


def test_validate_edit_brief_missing_story_id():
    brief = {k: v for k, v in SAMPLE_BRIEF.items() if k != "story_id"}
    errors = validate_edit_brief(brief, {"001"})
    assert any("story_id" in e for e in errors)


def test_validate_edit_brief_unsupported_format():
    brief = {**SAMPLE_BRIEF, "format": "ULTRA"}
    errors = validate_edit_brief(brief, {"001"})
    assert any("format" in e for e in errors)


def test_validate_edit_brief_format_not_recommended():
    brief = {**SAMPLE_BRIEF, "format": "LONG"}
    errors = validate_edit_brief(brief, {"001"}, story_recommended_formats={"SHORT", "MEDIUM"})
    assert any("not recommended" in e for e in errors)


def test_validate_edit_brief_missing_editorial_intent():
    brief = {k: v for k, v in SAMPLE_BRIEF.items() if k != "editorial_intent"}
    errors = validate_edit_brief(brief, {"001"})
    assert any("editorial_intent" in e for e in errors)


def test_validate_edit_brief_no_beats():
    brief = {**SAMPLE_BRIEF, "beats": []}
    errors = validate_edit_brief(brief, {"001"})
    assert any("at least one beat" in e for e in errors)


def test_validate_edit_brief_beat_missing_beat_id():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"role": "HOOK", "moment_id": "001", "direction": "test", "pacing": "PEAK"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("beat_id" in e for e in errors)


def test_validate_edit_brief_beat_duplicate_beat_id():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test", "pacing": "PEAK"},
        {"beat_id": "b1", "role": "SETUP", "moment_id": "002", "direction": "test", "pacing": "SLOW"},
    ]}
    errors = validate_edit_brief(brief, {"001", "002"})
    assert any("duplicate beat_id" in e for e in errors)


def test_validate_edit_brief_unsupported_role():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "INVALID", "moment_id": "001", "direction": "test", "pacing": "PEAK"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("role" in e for e in errors)


def test_validate_edit_brief_invalid_moment():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "999", "direction": "test", "pacing": "PEAK"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("999" in e for e in errors)


def test_validate_edit_brief_unsupported_pacing():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test", "pacing": "WRONG"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("pacing" in e for e in errors)


def test_validate_edit_brief_unsupported_intensity():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test",
         "pacing": "PEAK", "intensity": "WRONG"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("intensity" in e for e in errors)


def test_validate_edit_brief_unsupported_transition():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test",
         "pacing": "PEAK", "transition_intent": "WRONG"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("transition_intent" in e for e in errors)


def test_validate_edit_brief_unsupported_audio():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test",
         "pacing": "PEAK", "audio_strategy": "WRONG"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("audio_strategy" in e for e in errors)


def test_validate_edit_brief_unsupported_text():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "001", "direction": "test",
         "pacing": "PEAK", "text_intent": "WRONG"}
    ]}
    errors = validate_edit_brief(brief, {"001"})
    assert any("text_intent" in e for e in errors)


def test_validate_edit_brief_empty_emotional_arc():
    brief = {**SAMPLE_BRIEF, "emotional_arc": []}
    errors = validate_edit_brief(brief, {"001"})
    assert any("emotional_arc" in e for e in errors)


# ── Nonchronological beat ordering ──────────────────────────────────────────


def test_validate_edit_brief_nonchronological_moments():
    """Beats need not follow match chronology."""
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "004", "direction": "Start with eruption",
         "pacing": "PEAK", "intensity": "PEAK"},
        {"beat_id": "b2", "role": "SETUP", "moment_id": "001", "direction": "Go back",
         "pacing": "SLOW", "intensity": "LOW"},
        {"beat_id": "b3", "role": "CLIMAX", "moment_id": "003", "direction": "Peak",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    errors = validate_edit_brief(brief, {"001", "003", "004"})
    assert errors == []


# ── Intentional moment reuse ────────────────────────────────────────────────


def test_validate_edit_brief_moment_reuse():
    """A moment may appear in multiple beats when editorially justified."""
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "004", "direction": "Hook with eruption",
         "pacing": "PEAK", "intensity": "PEAK"},
        {"beat_id": "b2", "role": "CLIMAX", "moment_id": "004", "direction": "Return to eruption",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    errors = validate_edit_brief(brief, {"004"})
    assert errors == []


# ── Prompt construction ─────────────────────────────────────────────────────


def test_build_edit_brief_prompt_contains_story():
    prompt = _build_edit_brief_prompt(SAMPLE_STORIES[0], SAMPLE_MOMENTS, "SHORT")
    assert "The Impossible Comeback" in prompt
    assert "COMEBACK" in prompt
    assert "SHORT" in prompt


def test_build_edit_brief_prompt_contains_moments():
    prompt = _build_edit_brief_prompt(SAMPLE_STORIES[0], SAMPLE_MOMENTS, "SHORT")
    assert "001" in prompt
    assert "005" in prompt
    assert "Gerrard header" in prompt


def test_build_edit_brief_prompt_contains_vocabularies():
    prompt = _build_edit_brief_prompt(SAMPLE_STORIES[0], SAMPLE_MOMENTS, "SHORT")
    assert "PEAK" in prompt
    assert "AUDIO_DROP" in prompt
    assert "COMMENTARY_FOCUS" in prompt


def test_build_edit_brief_prompt_medium():
    prompt = _build_edit_brief_prompt(SAMPLE_STORIES[0], SAMPLE_MOMENTS, "MEDIUM")
    assert "MEDIUM" in prompt
    assert FORMAT_GUIDANCE["MEDIUM"] in prompt


def test_build_edit_brief_prompt_long():
    prompt = _build_edit_brief_prompt(SAMPLE_STORIES[0], SAMPLE_MOMENTS, "LONG")
    assert "LONG" in prompt
    assert FORMAT_GUIDANCE["LONG"] in prompt


# ── JSON parsing ────────────────────────────────────────────────────────────


def test_parse_brief_json():
    raw = json.dumps({"editorial_intent": "test", "emotional_arc": ["A"], "beats": []})
    result = _parse_brief_json(raw)
    assert result["editorial_intent"] == "test"


def test_parse_brief_json_markdown_wrapped():
    inner = json.dumps({"editorial_intent": "test", "beats": []})
    raw = f"```json\n{inner}\n```"
    result = _parse_brief_json(raw)
    assert result["editorial_intent"] == "test"


def test_parse_brief_json_invalid():
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_brief_json("not json")


# ── generate_edit_brief ────────────────────────────────────────────────────


def test_generate_edit_brief_requires_story(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = generate_edit_brief(job["job_id"], "nonexistent", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_generate_edit_brief_unsupported_format(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = generate_edit_brief(job["job_id"], "impossible_comeback", "ULTRA", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "unsupported" in result["error"]


def test_generate_edit_brief_format_not_recommended(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = generate_edit_brief(job["job_id"], "impossible_comeback", "LONG", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "not recommended" in result["error"]


def test_generate_edit_brief_dry_run(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = generate_edit_brief(job["job_id"], "impossible_comeback", "SHORT",
                                 jobs_dir=jobs_root, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True


def test_generate_edit_brief_success(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    job_id = job["job_id"]

    raw_brief = {
        "editorial_intent": "Make the comeback feel impossible.",
        "emotional_arc": ["DESPAIR", "BELIEF", "CHAOS", "PEAK", "RELEASE"],
        "beats": [
            {"beat_id": "beat_001", "role": "HOOK", "moment_id": "004",
             "direction": "Start with eruption", "pacing": "PEAK", "intensity": "PEAK",
             "audio_strategy": "COMMENTARY_FOCUS", "transition_intent": "AUDIO_DROP",
             "text_intent": "HOOK_TEXT"},
            {"beat_id": "beat_002", "role": "SETUP", "moment_id": "001",
             "direction": "Return to despair", "pacing": "SLOW", "intensity": "LOW",
             "audio_strategy": "CROWD_FOCUS"},
            {"beat_id": "beat_003", "role": "ESCALATION", "moment_id": "002",
             "direction": "Gerrard scores", "pacing": "BUILDING", "intensity": "MEDIUM"},
            {"beat_id": "beat_004", "role": "CLIMAX", "moment_id": "003",
             "direction": "Equalizer", "pacing": "PEAK", "intensity": "PEAK",
             "audio_strategy": "CROWD_AND_COMMENTARY"},
            {"beat_id": "beat_005", "role": "AFTERMATH", "moment_id": "005",
             "direction": "Disbelief", "pacing": "RELEASE", "intensity": "RELEASE",
             "transition_intent": "FADE"},
        ],
    }

    with patch("pipeline.edit_brief._run_brief_llm", return_value=raw_brief):
        result = generate_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["brief"]["format"] == "SHORT"
    assert len(result["brief"]["beats"]) == 5

    # Verify artifact persisted
    loaded = read_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert loaded is not None
    assert loaded["editorial_intent"] == "Make the comeback feel impossible."


def test_generate_edit_brief_validates_output(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    bad_brief = {
        "editorial_intent": "test",
        "emotional_arc": [],
        "beats": [{"beat_id": "b1", "role": "HOOK", "moment_id": "999",
                    "direction": "test", "pacing": "PEAK"}],
    }

    with patch("pipeline.edit_brief._run_brief_llm", return_value=bad_brief):
        result = generate_edit_brief(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is False
    assert "Validation failed" in result["error"]


def test_generate_edit_brief_failure_preserves_story(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    with patch("pipeline.edit_brief._run_brief_llm", side_effect=RuntimeError("sk-abc1234567890123456789012345678")):
        result = generate_edit_brief(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is False
    assert "sk-abc1234567890123456789012345678" not in result["error"]

    # Story and moments untouched
    from pipeline.story_engine import read_story_suggestions
    stories = read_story_suggestions(job["job_id"], jobs_dir=jobs_root)
    assert len(stories) == 1
    assert stories[0]["story_id"] == "impossible_comeback"


def test_generate_edit_brief_regeneration(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    job_id = job["job_id"]

    brief_v1 = {
        "editorial_intent": "Version 1",
        "emotional_arc": ["A", "B"],
        "beats": [{"beat_id": "b1", "role": "HOOK", "moment_id": "001",
                    "direction": "v1", "pacing": "PEAK", "intensity": "PEAK"}],
    }
    brief_v2 = {
        "editorial_intent": "Version 2",
        "emotional_arc": ["X", "Y"],
        "beats": [{"beat_id": "b1", "role": "HOOK", "moment_id": "002",
                    "direction": "v2", "pacing": "SLOW", "intensity": "LOW"}],
    }

    with patch("pipeline.edit_brief._run_brief_llm", return_value=brief_v1):
        r1 = generate_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert r1["ok"] is True
    assert r1["brief"]["editorial_intent"] == "Version 1"

    with patch("pipeline.edit_brief._run_brief_llm", return_value=brief_v2):
        r2 = generate_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert r2["ok"] is True
    assert r2["brief"]["editorial_intent"] == "Version 2"


# ── Multiple formats ────────────────────────────────────────────────────────


def test_generate_edit_brief_short_and_medium(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    job_id = job["job_id"]

    raw_brief = {
        "editorial_intent": "Test",
        "emotional_arc": ["A"],
        "beats": [{"beat_id": "b1", "role": "HOOK", "moment_id": "001",
                    "direction": "test", "pacing": "PEAK", "intensity": "PEAK"}],
    }

    with patch("pipeline.edit_brief._run_brief_llm", return_value=raw_brief):
        r_short = generate_edit_brief(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert r_short["ok"] is True

    with patch("pipeline.edit_brief._run_brief_llm", return_value=raw_brief):
        r_medium = generate_edit_brief(job_id, "impossible_comeback", "MEDIUM", jobs_dir=jobs_root)
    assert r_medium["ok"] is True

    # Both briefs exist independently
    all_briefs = list_edit_briefs(job_id, jobs_dir=jobs_root)
    assert len(all_briefs) == 2


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_execution_in_edit_brief():
    import pipeline.edit_brief as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    assert "shell=True" not in source
    assert "subprocess" not in source


# ── Safe error message ──────────────────────────────────────────────────────


def test_safe_error_message_strips_api_keys():
    msg = _safe_error_message(Exception("Error with sk-abc1234567890123456789012345678 key"))
    assert "sk-abc123" not in msg


def test_safe_error_message_truncates_long():
    msg = _safe_error_message(Exception("x" * 1000))
    assert len(msg) <= 510


def test_safe_error_message_empty_fallback():
    msg = _safe_error_message(Exception(""))
    assert "failed" in msg.lower()
