"""Tests for the rendering service boundary (Slice 10)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.rendering import (
    RENDER_STATES,
    APPLIED_FEATURES,
    DEFERRED_FEATURES,
    render_edl,
    read_render_state,
    update_render_state,
    validate_render_request,
    build_ffmpeg_args,
    _classify_features,
    _safe_error_message,
    _run_ffmpeg,
)
from pipeline.edl import build_edl_from_brief, write_edl
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
    path.write_bytes(b"rendering test media" * 1000)
    return path


SAMPLE_MOMENTS = [
    {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
     "virality_score": 9, "caption": "Gerrard header", "status": "needs_visual_scrub"},
    {"clip_id": "002", "category": "CHAOS", "start_time": "25", "end_time": "35",
     "virality_score": 8, "caption": "Smicer scores", "status": "needs_visual_scrub"},
    {"clip_id": "003", "category": "EMOTION", "start_time": "40", "end_time": "55",
     "virality_score": 10, "caption": "Alonso equalizer", "status": "needs_visual_scrub"},
    {"clip_id": "004", "category": "AURA", "start_time": "60", "end_time": "70",
     "virality_score": 7, "caption": "Crowd disbelief", "status": "needs_visual_scrub"},
    {"clip_id": "005", "category": "EMOTION", "start_time": "80", "end_time": "90",
     "virality_score": 8, "caption": "Dudek save", "status": "needs_visual_scrub"},
]

SAMPLE_EDL = {
    "job_id": "test_job",
    "story_id": "impossible_comeback",
    "brief_id": "impossible_comeback_SHORT",
    "format": "SHORT",
    "timeline_duration": 43.0,
    "segment_count": 5,
    "segments": [
        {"segment_id": "seg_001", "timeline_start": 0, "timeline_end": 10,
         "source_start": 60, "source_end": 70, "moment_id": "004",
         "story_role": "HOOK", "editorial_direction": "Start with eruption",
         "pacing": "PEAK", "intensity": "PEAK", "audio_strategy": "COMMENTARY_FOCUS",
         "transition_in": "HARD_CUT", "transition_out": "AUDIO_DROP", "text_intent": "HOOK_TEXT"},
        {"segment_id": "seg_002", "timeline_start": 10, "timeline_end": 25,
         "source_start": 5, "source_end": 20, "moment_id": "001",
         "story_role": "SETUP", "editorial_direction": "Return to despair",
         "pacing": "SLOW", "intensity": "LOW", "audio_strategy": "CROWD_FOCUS",
         "transition_in": "HARD_CUT", "transition_out": "", "text_intent": ""},
        {"segment_id": "seg_003", "timeline_start": 25, "timeline_end": 35,
         "source_start": 25, "source_end": 35, "moment_id": "002",
         "story_role": "ESCALATION", "editorial_direction": "Gerrard scores",
         "pacing": "BUILDING", "intensity": "MEDIUM", "audio_strategy": "",
         "transition_in": "REACTION_CUT", "transition_out": "", "text_intent": ""},
        {"segment_id": "seg_004", "timeline_start": 35, "timeline_end": 41,
         "source_start": 40, "source_end": 46, "moment_id": "003",
         "story_role": "CLIMAX", "editorial_direction": "Equalizer",
         "pacing": "PEAK", "intensity": "PEAK", "audio_strategy": "CROWD_AND_COMMENTARY",
         "transition_in": "HARD_CUT", "transition_out": "", "text_intent": "SCORE_CONTEXT"},
        {"segment_id": "seg_005", "timeline_start": 41, "timeline_end": 43,
         "source_start": 80, "source_end": 82, "moment_id": "005",
         "story_role": "AFTERMATH", "editorial_direction": "Disbelief",
         "pacing": "RELEASE", "intensity": "RELEASE", "audio_strategy": "",
         "transition_in": "HARD_CUT", "transition_out": "FADE", "text_intent": ""},
    ],
}


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "render_test", "project": sport},
        "media": {"source_id": "render_source", "match_or_event_name": "Render Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


def _setup_completed(media_file: Path, jobs_root: Path, tmp_path: Path) -> dict:
    """Create project with full pipeline state."""
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    _write_moments(job_id, jobs_root, SAMPLE_MOMENTS)
    update_analysis_state(job_id, status="COMPLETE", stage="PREPARING_RESULTS",
                          jobs_dir=jobs_root, analysis_manifest_count=len(SAMPLE_MOMENTS))

    stories = [{"story_id": "impossible_comeback", "title": "The Impossible Comeback",
                "archetype": "COMEBACK", "summary": "Test", "hook": "Test",
                "moment_ids": ["001", "002", "003", "004", "005"],
                "estimated_duration": 58, "emotional_arc": ["A", "B"],
                "narrative_roles": {}, "recommended_formats": ["SHORT"],
                "why_this_story": "Test"}]
    write_story_suggestions(job_id, stories, jobs_dir=jobs_root)

    brief = {"job_id": job_id, "story_id": "impossible_comeback", "format": "SHORT",
             "target_duration": 58, "story_archetype": "COMEBACK",
             "editorial_intent": "Test", "emotional_arc": ["A"],
             "beats": [{"beat_id": "b1", "role": "HOOK", "moment_id": "004",
                         "direction": "test", "pacing": "PEAK", "intensity": "PEAK"}]}
    write_edit_brief(job_id, brief, jobs_dir=jobs_root)

    edl = {**SAMPLE_EDL, "job_id": job_id}
    write_edl(edl, jobs_dir=jobs_root)

    return job


# ── Constants ───────────────────────────────────────────────────────────────


def test_render_states():
    assert "WAITING" in RENDER_STATES
    assert "COMPLETE" in RENDER_STATES
    assert "FAILED" in RENDER_STATES


def test_applied_features():
    assert "CUTS" in APPLIED_FEATURES
    assert "SOURCE_AUDIO" in APPLIED_FEATURES


def test_deferred_features():
    assert "AUDIO_DROP" in DEFERRED_FEATURES
    assert "HOOK_TEXT" in DEFERRED_FEATURES


# ── Render state I/O ────────────────────────────────────────────────────────


def test_update_and_read_render_state(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_render_state(job_id, "s1", "SHORT", status="VALIDATING", jobs_dir=jobs_root)
    state = read_render_state(job_id, "s1", "SHORT", jobs_dir=jobs_root)
    assert state is not None
    assert state["render_status"] == "VALIDATING"
    assert state["render_started_at"]


def test_render_state_complete(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_render_state(job_id, "s1", "SHORT", status="VALIDATING", jobs_dir=jobs_root)
    update_render_state(job_id, "s1", "SHORT", status="COMPLETE",
                        output="/test.mp4", duration=52.4, jobs_dir=jobs_root)
    state = read_render_state(job_id, "s1", "SHORT", jobs_dir=jobs_root)
    assert state["render_status"] == "COMPLETE"
    assert state["render_completed_at"]
    assert state["output"] == "/test.mp4"
    assert state["duration"] == 52.4


def test_render_state_not_found(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    state = read_render_state(job["job_id"], "s1", "SHORT", jobs_dir=jobs_root)
    assert state is None


# ── Validation ──────────────────────────────────────────────────────────────


def test_validate_render_request_valid(media_file):
    errors = validate_render_request(SAMPLE_EDL, media_file)
    assert errors == []


def test_validate_render_request_no_segments():
    errors = validate_render_request({"segments": []}, "/fake/source.mp4")
    assert any("no segments" in e for e in errors)


def test_validate_render_request_missing_source():
    errors = validate_render_request(SAMPLE_EDL, "/nonexistent/source.mp4")
    assert any("not found" in e for e in errors)


def test_validate_render_request_invalid_source_range():
    edl = {"segments": [{"source_start": 10, "source_end": 5}]}
    errors = validate_render_request(edl, "/fake.mp4")
    assert any("invalid source range" in e for e in errors)


# ── FFmpeg args ─────────────────────────────────────────────────────────────


def test_build_ffmpeg_args_basic():
    args = build_ffmpeg_args(SAMPLE_EDL, "/source.mp4", "/output.mp4")
    assert args[0] == "ffmpeg"
    assert "-y" in args
    assert "-i" in args
    assert "/source.mp4" in args
    assert "/output.mp4" in args
    assert "-filter_complex" in args
    assert "-c:v" in args
    assert "libx264" in args
    assert "-c:a" in args
    assert "aac" in args
    assert "-pix_fmt" in args
    assert "yuv420p" in args
    assert "-movflags" in args
    assert "+faststart" in args


def test_build_ffmpeg_args_no_segments():
    with pytest.raises(ValueError, match="no segments"):
        build_ffmpeg_args({"segments": []}, "/source.mp4", "/output.mp4")


def test_build_ffmpeg_args_concat_count():
    args = build_ffmpeg_args(SAMPLE_EDL, "/source.mp4", "/output.mp4")
    # Should have 5 concat segments
    filter_idx = args.index("-filter_complex")
    filter_str = args[filter_idx + 1]
    assert "concat=n=5" in filter_str


def test_build_ffmpeg_args_source_ranges():
    args = build_ffmpeg_args(SAMPLE_EDL, "/source.mp4", "/output.mp4")
    filter_idx = args.index("-filter_complex")
    filter_str = args[filter_idx + 1]
    # First segment: source 60-70
    assert "trim=start=60:duration=10" in filter_str
    # Second segment: source 5-20
    assert "trim=start=5:duration=15" in filter_str


def test_build_ffmpeg_args_nonchronological():
    """EDL segments are in timeline order, not source order."""
    args = build_ffmpeg_args(SAMPLE_EDL, "/source.mp4", "/output.mp4")
    filter_idx = args.index("-filter_complex")
    filter_str = args[filter_idx + 1]
    # Segment 1 source starts at 60, segment 2 at 5 — nonchronological
    assert "trim=start=60" in filter_str
    assert "trim=start=5" in filter_str


# ── Feature classification ──────────────────────────────────────────────────


def test_classify_features_applied():
    applied, deferred = _classify_features(SAMPLE_EDL)
    assert "CUTS" in applied
    assert "ORDERING" in applied
    assert "CONCATENATION" in applied
    assert "SOURCE_AUDIO" in applied


def test_classify_features_deferred():
    applied, deferred = _classify_features(SAMPLE_EDL)
    assert "AUDIO_DROP" in deferred
    assert "COMMENTARY_FOCUS" in deferred
    assert "CROWD_FOCUS" in deferred
    assert "CROWD_AND_COMMENTARY" in deferred
    assert "HOOK_TEXT" in deferred
    assert "SCORE_CONTEXT" in deferred
    assert "REACTION_CUT" in deferred
    assert "FADE" in deferred


def test_classify_features_clean_edl():
    """EDL with no advanced intents has empty deferred."""
    edl = {"segments": [
        {"audio_strategy": "ORIGINAL", "transition_in": "HARD_CUT",
         "transition_out": "HARD_CUT", "text_intent": "NONE"},
    ]}
    applied, deferred = _classify_features(edl)
    assert "CUTS" in applied
    assert deferred == []


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_in_rendering_module():
    import pipeline.rendering as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    # shell=True should not appear in actual function calls (only in docstrings/comments explaining what we avoid)
    import ast
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    # shell= should only be False, never True
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        pytest.fail("shell=True found in function call")


def test_ffmpeg_args_use_structured_subprocess():
    """FFmpeg is called with shell=False and list args."""
    import inspect
    source = inspect.getsource(_run_ffmpeg)
    assert "shell=False" in source
    # Verify no shell=True in actual code (docstrings may mention it as what we avoid)
    import ast
    tree = ast.parse(inspect.getsource(_run_ffmpeg))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        pytest.fail("shell=True found in _run_ffmpeg")


# ── render_edl (service interface) ──────────────────────────────────────────


def test_render_edl_requires_edl(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = render_edl(job["job_id"], "nonexistent", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "not found" in result["error"]


def test_render_edl_dry_run(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                        jobs_dir=jobs_root, dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True


def test_render_edl_missing_source(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    # Create an EDL for a job that has no intake manifest
    job_id = job["job_id"]
    # Modify the job to have no intake
    job_record_path = jobs_root / f"{job_id}.json"
    job_data = json.loads(job_record_path.read_text(encoding="utf-8"))
    job_data["intake_manifest_path"] = ""
    job_record_path.write_text(json.dumps(job_data, indent=2), encoding="utf-8")

    result = render_edl(job_id, "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "source" in result["error"].lower() or "not found" in result["error"].lower()


def test_render_edl_ffmpeg_failure(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "FFmpeg error: invalid codec"

    with patch("pipeline.rendering._run_ffmpeg", return_value=mock_result):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "FAILED"

    state = read_render_state(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert state["render_status"] == "FAILED"


def test_render_edl_success(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    def create_output(*args, **kwargs):
        # Create the output file that FFmpeg would produce
        args_list = args[0] if args else kwargs.get("args", [])
        output_idx = args_list.index("/output.mp4") if "/output.mp4" in args_list else -1
        # Find the actual output path from the args
        for i, a in enumerate(args_list):
            if a == "-movflags":
                output_path = Path(args_list[i + 2])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fake mp4 content")
                break
        return mock_result

    with patch("pipeline.rendering._run_ffmpeg", side_effect=create_output):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert "CUTS" in result["applied_features"]
    assert "AUDIO_DROP" in result["deferred_features"]


def test_render_edl_failure_preserves_edl(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Error"

    with patch("pipeline.rendering._run_ffmpeg", return_value=mock_result):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)

    assert result["ok"] is False

    # EDL still exists
    from pipeline.edl import read_edl
    edl = read_edl(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert edl is not None


def test_render_edl_rerender_requires_explicit_action(media_file, jobs_root):
    """Re-rendering should overwrite, not auto-trigger."""
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    # First render
    update_render_state(job["job_id"], "impossible_comeback", "SHORT",
                        status="COMPLETE", output="/old.mp4", jobs_dir=jobs_root)

    # State persists until explicitly re-rendered
    state = read_render_state(job["job_id"], "impossible_comeback", "SHORT", jobs_dir=jobs_root)
    assert state["render_status"] == "COMPLETE"
    assert state["output"] == "/old.mp4"


# ── Safe error message ──────────────────────────────────────────────────────


def test_safe_error_message_strips_keys():
    msg = _safe_error_message(Exception("sk-abc1234567890123456789012345678"))
    assert "sk-abc123" not in msg


def test_safe_error_message_truncates_ffmpeg_log():
    long_log = "ffmpeg output: " + "x" * 1000
    msg = _safe_error_message(Exception(long_log))
    assert len(msg) <= 520  # includes truncation notice


def test_safe_error_message_empty_fallback():
    msg = _safe_error_message(Exception(""))
    assert "failed" in msg.lower()


# ── Renderer contract ───────────────────────────────────────────────────────


def test_render_state_contains_contract_fields():
    """Render state should contain all fields a consumer needs."""
    update_render_state("j", "s", "SHORT", status="COMPLETE",
                        output="/test.mp4", duration=52.4, segment_count=6,
                        renderer="ffmpeg", applied_features=["CUTS"],
                        deferred_features=["AUDIO_DROP"])
    state = read_render_state("j", "s", "SHORT")
    assert state["render_status"] == "COMPLETE"
    assert state["output"] == "/test.mp4"
    assert state["duration"] == 52.4
    assert state["segment_count"] == 6
    assert state["renderer"] == "ffmpeg"
    assert "CUTS" in state["applied_features"]
    assert "AUDIO_DROP" in state["deferred_features"]
