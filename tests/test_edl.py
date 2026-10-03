"""Tests for the EDL service boundary (Slice 9)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.edl import (
    build_edl,
    build_edl_from_brief,
    read_edl,
    write_edl,
    list_edls,
    read_edl_state,
    update_edl_state,
    validate_edl,
    _fmt_timestamp,
    _parse_time,
    _safe_error_message,
)
from pipeline.edit_brief import write_edit_brief
from pipeline.story_engine import write_story_suggestions
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
    path.write_bytes(b"edl bytes" * 100)
    return path


SAMPLE_MOMENTS = [
    {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
     "virality_score": 9, "caption": "Gerrard header pulls one back",
     "hook_text": "The captain refuses to die", "status": "needs_visual_scrub"},
    {"clip_id": "002", "category": "CHAOS", "start_time": "25", "end_time": "35",
     "virality_score": 8, "caption": "Smicer scores from distance",
     "hook_text": "From nowhere", "status": "needs_visual_scrub"},
    {"clip_id": "003", "category": "EMOTION", "start_time": "40", "end_time": "55",
     "virality_score": 10, "caption": "Alonso equalizer",
     "hook_text": "Six minutes that changed everything", "status": "needs_visual_scrub"},
    {"clip_id": "004", "category": "AURA", "start_time": "60", "end_time": "70",
     "virality_score": 7, "caption": "Crowd disbelief",
     "hook_text": "Anfield erupts", "status": "needs_visual_scrub"},
    {"clip_id": "005", "category": "EMOTION", "start_time": "80", "end_time": "90",
     "virality_score": 8, "caption": "Dudek save",
     "hook_text": "The save that won the cup", "status": "needs_visual_scrub"},
]

SAMPLE_BRIEF = {
    "job_id": "test_job",
    "story_id": "impossible_comeback",
    "format": "SHORT",
    "target_duration": 58,
    "story_archetype": "COMEBACK",
    "editorial_intent": "Make the comeback feel impossible.",
    "emotional_arc": ["DESPAIR", "BELIEF", "CHAOS", "PEAK", "RELEASE"],
    "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "004",
         "direction": "Start with eruption", "pacing": "PEAK", "intensity": "PEAK",
         "audio_strategy": "COMMENTARY_FOCUS", "transition_intent": "AUDIO_DROP",
         "text_intent": "HOOK_TEXT"},
        {"beat_id": "b2", "role": "SETUP", "moment_id": "001",
         "direction": "Return to despair", "pacing": "SLOW", "intensity": "LOW",
         "audio_strategy": "CROWD_FOCUS"},
        {"beat_id": "b3", "role": "ESCALATION", "moment_id": "002",
         "direction": "Gerrard scores", "pacing": "BUILDING", "intensity": "MEDIUM"},
        {"beat_id": "b4", "role": "CLIMAX", "moment_id": "003",
         "direction": "Equalizer", "pacing": "PEAK", "intensity": "PEAK",
         "audio_strategy": "CROWD_AND_COMMENTARY"},
        {"beat_id": "b5", "role": "AFTERMATH", "moment_id": "005",
         "direction": "Disbelief", "pacing": "RELEASE", "intensity": "RELEASE",
         "transition_intent": "FADE"},
    ],
}


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "edl_test", "project": sport},
        "media": {"source_id": "edl_source", "match_or_event_name": "EDL Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


def _setup_completed(media_file: Path, jobs_root: Path, tmp_path: Path) -> dict:
    """Create project with completed analysis, moments, stories, and brief."""
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    _write_moments(job_id, jobs_root, SAMPLE_MOMENTS)
    update_analysis_state(job_id, status="COMPLETE", stage="PREPARING_RESULTS",
                          jobs_dir=jobs_root, analysis_manifest_count=len(SAMPLE_MOMENTS))

    stories = [{
        "story_id": "impossible_comeback",
        "title": "The Impossible Comeback",
        "archetype": "COMEBACK",
        "summary": "Liverpool trailed 3-0.",
        "hook": "Greatest comeback",
        "moment_ids": ["001", "002", "003", "004", "005"],
        "estimated_duration": 58,
        "emotional_arc": ["DESPAIR", "BELIEF"],
        "narrative_roles": {"HOOK": ["004"], "SETUP": ["001"]},
        "recommended_formats": ["SHORT", "MEDIUM"],
        "why_this_story": "Classic",
    }]
    write_story_suggestions(job_id, stories, jobs_dir=jobs_root)

    brief = {**SAMPLE_BRIEF, "job_id": job_id}
    write_edit_brief(job_id, brief, jobs_dir=jobs_root)

    return job


# ── Helpers ─────────────────────────────────────────────────────────────────


def test_fmt_timestamp():
    assert _fmt_timestamp(0.0) == "00:00.0"
    assert _fmt_timestamp(65.5) == "01:05.5"
    assert _fmt_timestamp(3600.0) == "60:00.0"


def test_parse_time_float():
    assert _parse_time(5.0) == 5.0


def test_parse_time_int():
    assert _parse_time(5) == 5.0


def test_parse_time_string():
    assert _parse_time("5.5") == 5.5


def test_parse_time_empty():
    assert _parse_time("") == 0.0


def test_safe_error_message_strips_keys():
    msg = _safe_error_message(Exception("sk-abc1234567890123456789012345678"))
    assert "sk-abc123" not in msg


def test_safe_error_message_truncates():
    msg = _safe_error_message(Exception("x" * 1000))
    assert len(msg) <= 510


# ── EDL state I/O ──────────────────────────────────────────────────────────


def test_update_and_read_edl_state(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_edl_state(job_id, story_id="s1", status="RUNNING", jobs_dir=jobs_root)
    state = read_edl_state(job_id, "s1", jobs_dir=jobs_root)
    assert state["edl_status"] == "RUNNING"
    assert state["edl_started_at"]


def test_edl_state_complete(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_edl_state(job_id, story_id="s1", status="RUNNING", jobs_dir=jobs_root)
    update_edl_state(job_id, story_id="s1", status="COMPLETE", jobs_dir=jobs_root)
    state = read_edl_state(job_id, "s1", jobs_dir=jobs_root)
    assert state["edl_status"] == "COMPLETE"


# ── EDL artifact I/O ───────────────────────────────────────────────────────


def test_write_and_read_edl(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    edl = {
        "job_id": job_id, "story_id": "s1", "brief_id": "s1_SHORT",
        "format": "SHORT", "timeline_duration": 10.0, "segment_count": 1,
        "segments": [{"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 10,
                       "source_start": 5, "source_end": 15, "moment_id": "001",
                       "story_role": "HOOK", "editorial_direction": "test",
                       "pacing": "PEAK", "intensity": "PEAK"}],
    }
    path = write_edl(edl, jobs_dir=jobs_root)
    assert path.exists()
    assert "_edl.json" in str(path)

    loaded = read_edl(job_id, "s1", "SHORT", jobs_dir=jobs_root)
    assert loaded is not None
    assert loaded["segment_count"] == 1


def test_read_edl_not_found(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = read_edl(job["job_id"], "nonexistent", "SHORT", jobs_dir=jobs_root)
    assert result is None


def test_list_edls(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    edl1 = {"job_id": job_id, "story_id": "s1", "format": "SHORT",
            "timeline_duration": 10, "segment_count": 0, "segments": []}
    edl2 = {"job_id": job_id, "story_id": "s1", "format": "MEDIUM",
            "timeline_duration": 20, "segment_count": 0, "segments": []}

    write_edl(edl1, jobs_dir=jobs_root)
    write_edl(edl2, jobs_dir=jobs_root)

    all_edls = list_edls(job_id, jobs_dir=jobs_root)
    assert len(all_edls) == 2


# ── build_edl_from_brief (deterministic) ───────────────────────────────────


def test_build_edl_from_brief_basic():
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)

    assert edl["job_id"] == "job1"
    assert edl["story_id"] == "impossible_comeback"
    assert edl["format"] == "SHORT"
    assert edl["segment_count"] == 5
    assert edl["timeline_duration"] > 0
    assert len(edl["segments"]) == 5


def test_build_edl_from_brief_contiguous_timeline():
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)
    segments = edl["segments"]

    for i in range(1, len(segments)):
        prev_end = segments[i - 1]["timeline_end"]
        curr_start = segments[i]["timeline_start"]
        assert abs(curr_start - prev_end) < 0.001, \
            f"Timeline gap/overlap between segment {i-1} and {i}"


def test_build_edl_from_brief_deterministic():
    edl1 = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)
    edl2 = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)

    assert edl1["timeline_duration"] == edl2["timeline_duration"]
    assert edl1["segment_count"] == edl2["segment_count"]
    for s1, s2 in zip(edl1["segments"], edl2["segments"]):
        assert s1["source_start"] == s2["source_start"]
        assert s1["source_end"] == s2["source_end"]
        assert s1["timeline_start"] == s2["timeline_start"]


def test_build_edl_from_brief_preserves_editorial():
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)
    segs = edl["segments"]

    assert segs[0]["story_role"] == "HOOK"
    assert segs[0]["moment_id"] == "004"
    assert segs[0]["editorial_direction"] == "Start with eruption"
    assert segs[0]["pacing"] == "PEAK"
    assert segs[0]["intensity"] == "PEAK"
    assert segs[0]["audio_strategy"] == "COMMENTARY_FOCUS"
    assert segs[0]["text_intent"] == "HOOK_TEXT"


def test_build_edl_from_brief_no_beats():
    brief = {**SAMPLE_BRIEF, "beats": []}
    with pytest.raises(ValueError, match="no beats"):
        build_edl_from_brief("job1", brief, SAMPLE_MOMENTS)


def test_build_edl_from_brief_invalid_moment():
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "999",
         "direction": "test", "pacing": "PEAK", "intensity": "PEAK"}
    ]}
    with pytest.raises(ValueError, match="999"):
        build_edl_from_brief("job1", brief, SAMPLE_MOMENTS)


# ── Moment reuse ────────────────────────────────────────────────────────────


def test_build_edl_moment_reuse():
    """Same moment can appear in multiple segments."""
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "004",
         "direction": "Hook with eruption", "pacing": "PEAK", "intensity": "PEAK"},
        {"beat_id": "b2", "role": "CLIMAX", "moment_id": "004",
         "direction": "Return to eruption", "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    edl = build_edl_from_brief("job1", brief, SAMPLE_MOMENTS)
    assert edl["segment_count"] == 2
    assert edl["segments"][0]["moment_id"] == "004"
    assert edl["segments"][1]["moment_id"] == "004"
    # Segments are separate with their own timeline ranges
    assert edl["segments"][0]["timeline_end"] == edl["segments"][1]["timeline_start"]


# ── Nonchronological source ────────────────────────────────────────────────


def test_build_edl_nonchronological_source():
    """Source ordering may differ from timeline ordering."""
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "004",
         "direction": "Hook", "pacing": "PEAK", "intensity": "PEAK"},
        {"beat_id": "b2", "role": "SETUP", "moment_id": "001",
         "direction": "Setup", "pacing": "SLOW", "intensity": "LOW"},
    ]}
    edl = build_edl_from_brief("job1", brief, SAMPLE_MOMENTS)
    # moment 004 starts at 60s, moment 001 starts at 5s — nonchronological source
    assert edl["segments"][0]["source_start"] > edl["segments"][1]["source_start"]
    # But timeline is chronological
    assert edl["segments"][0]["timeline_start"] < edl["segments"][1]["timeline_start"]


# ── Source bounds ───────────────────────────────────────────────────────────


def test_build_edl_respects_source_bounds():
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)
    for seg in edl["segments"]:
        assert seg["source_start"] >= 0
        assert seg["source_end"] > seg["source_start"]


def test_build_edl_short_trim():
    """SHORT treatment trims long moments."""
    # moment 003 is 40-55 (15s), SHORT should trim it
    brief = {**SAMPLE_BRIEF, "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "003",
         "direction": "test", "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    edl = build_edl_from_brief("job1", brief, SAMPLE_MOMENTS, source_duration=100)
    seg = edl["segments"][0]
    # Original moment is 15s, SHORT trims 20% from end
    assert seg["source_end"] - seg["source_start"] < 15.0


# ── Validation ──────────────────────────────────────────────────────────────


def test_validate_edl_valid():
    moment_ids = {"001", "002", "003", "004", "005"}
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)
    errors = validate_edl(edl, moment_ids)
    assert errors == []


def test_validate_edl_no_segments():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": []}
    errors = validate_edl(edl, {"001"})
    assert any("at least one segment" in e for e in errors)


def test_validate_edl_duplicate_segment_id():
    moment_ids = {"001"}
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
        {"segment_id": "seg_001", "timeline_start": 5, "timeline_end": 10,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "SETUP",
         "pacing": "SLOW", "intensity": "LOW"},
    ]}
    errors = validate_edl(edl, moment_ids)
    assert any("duplicate segment_id" in e for e in errors)


def test_validate_edl_invalid_moment():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "999", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    errors = validate_edl(edl, {"001"})
    assert any("999" in e for e in errors)


def test_validate_edl_source_end_before_start():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 10, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    errors = validate_edl(edl, {"001"})
    assert any("source_end" in e and "source_start" in e for e in errors)


def test_validate_edl_timeline_gap():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
        {"segment_id": "seg_002", "timeline_start": 10, "timeline_end": 15,
         "source_start": 0, "source_end": 5, "moment_id": "002", "story_role": "SETUP",
         "pacing": "SLOW", "intensity": "LOW"},
    ]}
    errors = validate_edl(edl, {"001", "002"})
    assert any("gap" in e for e in errors)


def test_validate_edl_timeline_overlap():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 10,
         "source_start": 0, "source_end": 10, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
        {"segment_id": "seg_002", "timeline_start": 5, "timeline_end": 15,
         "source_start": 0, "source_end": 10, "moment_id": "002", "story_role": "SETUP",
         "pacing": "SLOW", "intensity": "LOW"},
    ]}
    errors = validate_edl(edl, {"001", "002"})
    assert any("overlap" in e or "gap" in e for e in errors)


def test_validate_edl_source_exceeds_duration():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    errors = validate_edl(edl, {"001"}, source_duration=3.0)
    assert any("exceeds source duration" in e for e in errors)


def test_validate_edl_unsupported_pacing():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "WRONG", "intensity": "PEAK"},
    ]}
    errors = validate_edl(edl, {"001"})
    assert any("pacing" in e for e in errors)


def test_validate_edl_unsupported_transition():
    edl = {"job_id": "j", "story_id": "s", "brief_id": "b", "format": "SHORT", "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 5,
         "source_start": 0, "source_end": 5, "moment_id": "001", "story_role": "HOOK",
         "pacing": "PEAK", "intensity": "PEAK", "transition_in": "WRONG"},
    ]}
    errors = validate_edl(edl, {"001"})
    assert any("transition_in" in e for e in errors)


# ── build_edl (service interface) ──────────────────────────────────────────


def test_build_edl_requires_brief(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = build_edl(job["job_id"], "nonexistent", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_build_edl_dry_run(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = build_edl(job["job_id"], "impossible_comeback", "SHORT",
                       jobs_dir=jobs_root, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True


def test_build_edl_success(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = build_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["edl"]["segment_count"] == 5

    # Verify persisted
    loaded = read_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert loaded is not None
    assert loaded["segment_count"] == 5


def test_build_edl_failure_preserves_brief(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    # Use a brief with invalid moment to trigger failure
    from pipeline.edit_brief import write_edit_brief as _write_brief
    bad_brief = {**SAMPLE_BRIEF, "job_id": job["job_id"], "beats": [
        {"beat_id": "b1", "role": "HOOK", "moment_id": "999",
         "direction": "test", "pacing": "PEAK", "intensity": "PEAK"}
    ]}
    _write_brief(job["job_id"], bad_brief, jobs_dir=jobs_root)

    result = build_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False

    # Brief still exists
    from pipeline.edit_brief import read_edit_brief
    brief = read_edit_brief(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert brief is not None


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_execution_in_edl():
    import pipeline.edl as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    assert "shell=True" not in source
    assert "subprocess" not in source


# ── Renderer contract ───────────────────────────────────────────────────────


def test_edl_contains_renderer_contract_fields():
    """EDL should contain all fields a future renderer needs."""
    edl = build_edl_from_brief("job1", SAMPLE_BRIEF, SAMPLE_MOMENTS)

    assert "source" in edl
    assert "timeline_duration" in edl
    assert "segments" in edl

    for seg in edl["segments"]:
        assert "segment_id" in seg
        assert "timeline_start" in seg
        assert "timeline_end" in seg
        assert "source_start" in seg
        assert "source_end" in seg
        assert "moment_id" in seg
        assert "story_role" in seg
        assert "editorial_direction" in seg
        assert "pacing" in seg
        assert "intensity" in seg
        assert "audio_strategy" in seg
        assert "transition_in" in seg
        assert "transition_out" in seg
        assert "text_intent" in seg
