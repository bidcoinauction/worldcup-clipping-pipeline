"""Tests for the editorial renderer — Slice 11.

Tests cover:
- Reference renderer backward compatibility
- Editorial renderer mode
- HARD_CUT execution
- FLASH_CUT execution
- FADE execution
- FREEZE_PUSH execution
- AUDIO_DROP execution
- AUDIO_BRIDGE execution
- Unsupported intents remain deferred
- Pacing maps to documented renderer behavior
- Intensity maps to documented presets
- Feature execution reporting accuracy
- Reference and editorial outputs remain separate
- Failure preserves reference render
- Structured subprocess args remain safe
- shell=True is never used
- A/V mapping remains valid
- Console exposes reference/editorial distinction
"""

from __future__ import annotations

import ast
import json
import inspect
import shutil
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.rendering import (
    RENDER_MODES,
    CAPABILITY_REGISTRY,
    FLASH_PRESETS,
    FADE_PRESETS,
    FREEZE_PRESETS,
    AUDIO_DROP_PRESETS,
    AUDIO_BRIDGE_PRESETS,
    render_edl,
    read_render_state,
    update_render_state,
    validate_render_request,
    build_ffmpeg_args,
    build_ffmpeg_args_editorial,
    list_render_capabilities,
    get_transition_support,
    get_audio_support,
    _classify_features,
    _classify_features_v2,
    _pacing_multiply,
    _intensity_preset,
    _build_flash_filter,
    _build_fade_filter,
    _build_freeze_push_filter,
    _build_audio_drop_filter,
    _build_audio_bridge_filter,
    _build_silence_filter,
    _build_editorial_video_filters,
    _build_editorial_audio_filters,
    _build_editorial_concat_filters,
    _safe_error_message,
    _run_ffmpeg,
)
from pipeline.edl import write_edl
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
         "transition_in": "FLASH_CUT", "transition_out": "FREEZE_PUSH", "text_intent": ""},
        {"segment_id": "seg_004", "timeline_start": 35, "timeline_end": 41,
         "source_start": 40, "source_end": 46, "moment_id": "003",
         "story_role": "CLIMAX", "editorial_direction": "Equalizer",
         "pacing": "PEAK", "intensity": "PEAK", "audio_strategy": "CROWD_AND_COMMENTARY",
         "transition_in": "HARD_CUT", "transition_out": "FADE", "text_intent": "SCORE_CONTEXT"},
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


# ── Constants & Registry ─────────────────────────────────────────────────────


def test_render_modes():
    assert "REFERENCE" in RENDER_MODES
    assert "EDITORIAL" in RENDER_MODES


def test_capability_registry_has_transitions():
    reg = list_render_capabilities()
    assert "HARD_CUT" in reg["supported_transitions"]
    assert "FLASH_CUT" in reg["supported_transitions"]
    assert "FADE" in reg["supported_transitions"]
    assert "FREEZE_PUSH" in reg["supported_transitions"]


def test_capability_registry_has_audio():
    reg = list_render_capabilities()
    assert "ORIGINAL" in reg["supported_audio"]
    assert "AUDIO_DROP" in reg["supported_audio"]
    assert "AUDIO_BRIDGE" in reg["supported_audio"]
    assert "SILENCE" in reg["supported_audio"]


def test_capability_registry_has_pacing_presets():
    reg = list_render_capabilities()
    assert "SLOW" in reg["pacing_presets"]
    assert "PEAK" in reg["pacing_presets"]
    assert "RELEASE" in reg["pacing_presets"]


def test_capability_registry_has_intensity_presets():
    reg = list_render_capabilities()
    assert "LOW" in reg["intensity_presets"]
    assert "PEAK" in reg["intensity_presets"]


def test_get_transition_support():
    support = get_transition_support("HARD_CUT")
    assert support["status"] == "APPLIED"

    support = get_transition_support("REACTION_CUT")
    assert support["status"] == "DEFERRED"


def test_get_audio_support():
    support = get_audio_support("ORIGINAL")
    assert support["status"] == "APPLIED"

    support = get_audio_support("CROWD_FOCUS")
    assert support["status"] == "DEFERRED"


# ── Pacing & Intensity mappings ─────────────────────────────────────────────


def test_pacing_multiply_peak():
    """PEAK pacing should use shorter transitions."""
    mult = _pacing_multiply("PEAK")
    assert mult < 1.0


def test_pacing_multiply_slow():
    """SLOW pacing should use longer transitions."""
    mult = _pacing_multiply("SLOW")
    assert mult > 1.0


def test_pacing_multiply_medium():
    """MEDIUM pacing should be neutral."""
    mult = _pacing_multiply("MEDIUM")
    assert mult == 1.0


def test_intensity_preset_peak():
    """PEAK intensity should have strong effects."""
    preset = _intensity_preset("PEAK")
    assert preset["flash_opacity"] > 0.7
    assert preset["audio_drop_depth"] > 0.7


def test_intensity_preset_low():
    """LOW intensity should have subtle effects."""
    preset = _intensity_preset("LOW")
    assert preset["flash_opacity"] < 0.5


# ── Flash cut filter ────────────────────────────────────────────────────────


def test_build_flash_filter():
    f = _build_flash_filter("PEAK", "PEAK", "in")
    assert "color=white" in f
    assert "flash_in" in f
    assert "overlay" in f


def test_build_flash_filter_deterministic():
    f1 = _build_flash_filter("MEDIUM", "BUILDING", "x")
    f2 = _build_flash_filter("MEDIUM", "BUILDING", "x")
    assert f1 == f2


# ── Fade filter ─────────────────────────────────────────────────────────────


def test_build_fade_filter_start():
    f = _build_fade_filter(True, False, "in")
    assert "fade=t=in" in f
    assert "fade=t=out" not in f


def test_build_fade_filter_end():
    f = _build_fade_filter(False, True, "in")
    assert "fade=t=out" in f
    assert "fade=t=in" not in f


def test_build_fade_filter_both():
    f = _build_fade_filter(True, True, "in")
    assert "fade=t=in" in f
    assert "fade=t=out" in f


def test_build_fade_filter_none():
    f = _build_fade_filter(False, False, "in")
    assert "copy" in f


# ── Freeze push filter ──────────────────────────────────────────────────────


def test_build_freeze_push_filter():
    f = _build_freeze_push_filter("PEAK", "in")
    assert "tpad" in f
    assert "zoompan" in f


def test_build_freeze_push_filter_deterministic():
    f1 = _build_freeze_push_filter("HIGH", "x")
    f2 = _build_freeze_push_filter("HIGH", "x")
    assert f1 == f2


# ── Audio drop filter ───────────────────────────────────────────────────────


def test_build_audio_drop_before_cut():
    f = _build_audio_drop_filter("PEAK", True, 10.0, "in")
    assert "volume" in f
    assert "enable" in f


def test_build_audio_drop_after_cut():
    f = _build_audio_drop_filter("PEAK", False, 10.0, "in")
    assert "volume" in f


def test_build_audio_drop_deterministic():
    f1 = _build_audio_drop_filter("MEDIUM", True, 5.0, "x")
    f2 = _build_audio_drop_filter("MEDIUM", True, 5.0, "x")
    assert f1 == f2


# ── Audio bridge filter ─────────────────────────────────────────────────────


def test_build_audio_bridge_filter():
    f = _build_audio_bridge_filter(10.0, 10.0, "in")
    assert "afade" in f
    assert "out" in f


# ── Silence filter ──────────────────────────────────────────────────────────


def test_build_silence_filter():
    f = _build_silence_filter("in")
    assert "volume=0" in f


# ── Editorial video filters ─────────────────────────────────────────────────


def test_editorial_video_filters_hard_cut():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "transition_in": "HARD_CUT", "transition_out": "",
         "pacing": "MEDIUM", "intensity": "MEDIUM"},
    ]}
    filters, features = _build_editorial_video_filters(edl)
    assert any("HARD_CUT" in f.get("feature", "") for f in features)
    assert any(f.get("status") == "APPLIED" for f in features)


def test_editorial_video_filters_flash_cut():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "transition_in": "HARD_CUT", "transition_out": "",
         "pacing": "MEDIUM", "intensity": "MEDIUM"},
        {"segment_id": "s2", "source_start": 5, "source_end": 10,
         "transition_in": "FLASH_CUT", "transition_out": "",
         "pacing": "PEAK", "intensity": "PEAK"},
    ]}
    filters, features = _build_editorial_video_filters(edl)
    flash_features = [f for f in features if f.get("feature") == "FLASH_CUT"]
    assert len(flash_features) >= 1
    assert flash_features[0]["status"] == "APPLIED"


def test_editorial_video_filters_fade():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "transition_in": "FADE", "transition_out": "FADE",
         "pacing": "MEDIUM", "intensity": "MEDIUM"},
    ]}
    filters, features = _build_editorial_video_filters(edl)
    fade_features = [f for f in features if f.get("feature") == "FADE"]
    assert len(fade_features) >= 1


def test_editorial_video_filters_freeze_push():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "transition_in": "HARD_CUT", "transition_out": "FREEZE_PUSH",
         "pacing": "MEDIUM", "intensity": "HIGH"},
    ]}
    filters, features = _build_editorial_video_filters(edl)
    freeze_features = [f for f in features if f.get("feature") == "FREEZE_PUSH"]
    assert len(freeze_features) >= 1
    assert freeze_features[0]["status"] == "APPLIED"
    assert "note" in freeze_features[0]


def test_editorial_video_filters_unsupported_deferred():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "transition_in": "REACTION_CUT", "transition_out": "",
         "pacing": "MEDIUM", "intensity": "MEDIUM"},
    ]}
    filters, features = _build_editorial_video_filters(edl)
    reaction = [f for f in features if f.get("feature") == "REACTION_CUT"]
    assert len(reaction) >= 1
    assert reaction[0]["status"] == "DEFERRED"


# ── Editorial audio filters ─────────────────────────────────────────────────


def test_editorial_audio_filters_original():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "audio_strategy": "ORIGINAL"},
    ]}
    filters, features = _build_editorial_audio_filters(edl)
    source = [f for f in features if f.get("feature") == "SOURCE_AUDIO"]
    assert len(source) >= 1
    assert source[0]["status"] == "APPLIED"


def test_editorial_audio_filters_silence():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "audio_strategy": "SILENCE"},
    ]}
    filters, features = _build_editorial_audio_filters(edl)
    silence = [f for f in features if f.get("feature") == "SILENCE"]
    assert len(silence) >= 1
    assert silence[0]["status"] == "APPLIED"


def test_editorial_audio_filters_audio_drop():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "audio_strategy": "AUDIO_DROP", "intensity": "PEAK"},
    ]}
    filters, features = _build_editorial_audio_filters(edl)
    drop = [f for f in features if f.get("feature") == "AUDIO_DROP"]
    assert len(drop) >= 1
    assert drop[0]["status"] == "APPLIED"


def test_editorial_audio_filters_unsupported_deferred():
    edl = {"segments": [
        {"segment_id": "s1", "source_start": 0, "source_end": 5,
         "audio_strategy": "CROWD_FOCUS"},
    ]}
    filters, features = _build_editorial_audio_filters(edl)
    crowd = [f for f in features if f.get("feature") == "CROWD_FOCUS"]
    assert len(crowd) >= 1
    assert crowd[0]["status"] == "DEFERRED"


# ── Concat filters ──────────────────────────────────────────────────────────


def test_editorial_concat_filters():
    edl = {"segments": [
        {"segment_id": "s1"}, {"segment_id": "s2"}, {"segment_id": "s3"},
    ]}
    v_concat, a_concat = _build_editorial_concat_filters(edl)
    assert "concat=n=3:v=1:a=0" in v_concat
    assert "concat=n=3:v=0:a=1" in a_concat


# ── Feature classification v2 ───────────────────────────────────────────────


def test_classify_features_v2():
    execution = [
        {"segment_id": "s1", "feature": "HARD_CUT", "status": "APPLIED"},
        {"segment_id": "s1", "feature": "AUDIO_DROP", "status": "APPLIED"},
        {"segment_id": "s2", "feature": "FLASH_CUT", "status": "APPLIED"},
        {"segment_id": "s2", "feature": "CROWD_FOCUS", "status": "DEFERRED",
         "reason": "No stem"},
        {"segment_id": "s3", "feature": "FREEZE_PUSH", "status": "PARTIALLY_APPLIED",
         "note": "Freeze only"},
    ]
    result = _classify_features_v2({}, execution)
    assert "HARD_CUT" in result["applied"]
    assert "AUDIO_DROP" in result["applied"]
    assert "FLASH_CUT" in result["applied"]
    assert "CROWD_FOCUS" in result["deferred"]
    assert "FREEZE_PUSH" in result["partially_applied"]


def test_classify_features_v2_deduplicates():
    execution = [
        {"segment_id": "s1", "feature": "HARD_CUT", "status": "APPLIED"},
        {"segment_id": "s2", "feature": "HARD_CUT", "status": "APPLIED"},
    ]
    result = _classify_features_v2({}, execution)
    assert result["applied"].count("HARD_CUT") == 1


# ── Editorial FFmpeg args ───────────────────────────────────────────────────


def test_build_ffmpeg_args_editorial_basic():
    args, features = build_ffmpeg_args_editorial(SAMPLE_EDL, "/src.mp4", "/out.mp4")
    assert args[0] == "ffmpeg"
    assert "-filter_complex" in args
    assert "-c:v" in args
    assert "libx264" in args
    assert isinstance(features, list)


def test_build_ffmpeg_args_editorial_no_segments():
    with pytest.raises(ValueError, match="no segments"):
        build_ffmpeg_args_editorial({"segments": []}, "/src.mp4", "/out.mp4")


def test_build_ffmpeg_args_editorial_features_report():
    args, features = build_ffmpeg_args_editorial(SAMPLE_EDL, "/src.mp4", "/out.mp4")
    # Should have HARD_CUT features
    hard_cuts = [f for f in features if f["feature"] == "HARD_CUT"]
    assert len(hard_cuts) >= 1
    # Should have FLASH_CUT for seg_003
    flash = [f for f in features if f["feature"] == "FLASH_CUT"]
    assert len(flash) >= 1
    # Should have FADE for seg_004 and seg_005
    fade = [f for f in features if f["feature"] == "FADE"]
    assert len(fade) >= 2
    # Should have FREEZE_PUSH for seg_003
    freeze = [f for f in features if f["feature"] == "FREEZE_PUSH"]
    assert len(freeze) >= 1
    # Should have AUDIO_DROP for seg_001
    drop = [f for f in features if f["feature"] == "AUDIO_DROP"]
    assert len(drop) >= 1


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_in_rendering_module():
    import pipeline.rendering as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        pytest.fail("shell=True found in function call")


def test_ffmpeg_args_use_structured_subprocess():
    source = inspect.getsource(_run_ffmpeg)
    assert "shell=False" in source
    tree = ast.parse(inspect.getsource(_run_ffmpeg))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg == "shell":
                    if isinstance(kw.value, ast.Constant) and kw.value.value is True:
                        pytest.fail("shell=True found in _run_ffmpeg")


# ── Validation ──────────────────────────────────────────────────────────────


def test_validate_render_request_valid():
    errors = validate_render_request(SAMPLE_EDL, "/fake/source.mp4", mode="REFERENCE")
    # Will have "not found" for source, but mode is valid
    assert not any("mode" in e for e in errors)


def test_validate_render_request_invalid_mode():
    errors = validate_render_request(SAMPLE_EDL, "/fake/source.mp4", mode="BROKEN")
    assert any("mode" in e for e in errors)


# ── Render state with mode ──────────────────────────────────────────────────


def test_render_state_reference_mode(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    update_render_state(job["job_id"], "s1", "SHORT", status="COMPLETE",
                        mode="REFERENCE", jobs_dir=jobs_root)
    state = read_render_state(job["job_id"], "s1", "SHORT",
                              jobs_dir=jobs_root, mode="REFERENCE")
    assert state is not None
    assert state["render_status"] == "COMPLETE"
    assert state["render_mode"] == "REFERENCE"


def test_render_state_editorial_mode(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    update_render_state(job["job_id"], "s1", "SHORT", status="COMPLETE",
                        mode="EDITORIAL", jobs_dir=jobs_root)
    state = read_render_state(job["job_id"], "s1", "SHORT",
                              jobs_dir=jobs_root, mode="EDITORIAL")
    assert state is not None
    assert state["render_mode"] == "EDITORIAL"


def test_render_states_independent(media_file, jobs_root):
    """Reference and editorial states are independent."""
    job = _create_project(media_file, jobs_root)
    update_render_state(job["job_id"], "s1", "SHORT", status="COMPLETE",
                        mode="REFERENCE", output="/ref.mp4", jobs_dir=jobs_root)
    update_render_state(job["job_id"], "s1", "SHORT", status="COMPLETE",
                        mode="EDITORIAL", output="/edit.mp4", jobs_dir=jobs_root)
    ref = read_render_state(job["job_id"], "s1", "SHORT",
                            jobs_dir=jobs_root, mode="REFERENCE")
    edit = read_render_state(job["job_id"], "s1", "SHORT",
                             jobs_dir=jobs_root, mode="EDITORIAL")
    assert ref["output"] == "/ref.mp4"
    assert edit["output"] == "/edit.mp4"


# ── render_edl with mode ────────────────────────────────────────────────────


def test_render_edl_invalid_mode(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    result = render_edl(job["job_id"], "s1", "SHORT",
                        jobs_dir=jobs_root, mode="BROKEN")
    assert result["ok"] is False
    assert "mode" in result["error"].lower()


def test_render_edl_reference_dry_run(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                        jobs_dir=jobs_root, dry_run=True, mode="REFERENCE")
    assert result["ok"] is True
    assert result["mode"] == "REFERENCE"


def test_render_edl_editorial_dry_run(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)
    result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                        jobs_dir=jobs_root, dry_run=True, mode="EDITORIAL")
    assert result["ok"] is True
    assert result["mode"] == "EDITORIAL"


def test_render_edl_editorial_success(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    def create_output(*args, **kwargs):
        args_list = args[0] if args else kwargs.get("args", [])
        for i, a in enumerate(args_list):
            if a == "-movflags":
                output_path = Path(args_list[i + 2])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fake mp4 content")
                break
        return mock_result

    with patch("pipeline.rendering._run_ffmpeg", side_effect=create_output):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                            jobs_dir=jobs_root, mode="EDITORIAL")

    assert result["ok"] is True
    assert result["mode"] == "EDITORIAL"
    assert "feature_execution" in result
    assert "applied_features" in result
    assert "partially_applied_features" in result
    assert "deferred_features" in result


def test_render_edl_reference_success(media_file, jobs_root):
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    mock_result = MagicMock()
    mock_result.returncode = 0
    mock_result.stderr = ""

    def create_output(*args, **kwargs):
        args_list = args[0] if args else kwargs.get("args", [])
        for i, a in enumerate(args_list):
            if a == "-movflags":
                output_path = Path(args_list[i + 2])
                output_path.parent.mkdir(parents=True, exist_ok=True)
                output_path.write_bytes(b"fake mp4 content")
                break
        return mock_result

    with patch("pipeline.rendering._run_ffmpeg", side_effect=create_output):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                            jobs_dir=jobs_root, mode="REFERENCE")

    assert result["ok"] is True
    assert result["mode"] == "REFERENCE"
    assert result["output_filename"] == "reference.mp4"


def test_render_edl_editorial_failure_preserves_reference(media_file, jobs_root):
    """Editorial failure should not affect reference render state."""
    job = _setup_completed(media_file, jobs_root, tmp_path=jobs_root.parent)

    # Create a reference render first
    update_render_state(job["job_id"], "impossible_comeback", "SHORT",
                        status="COMPLETE", output="/ref.mp4",
                        mode="REFERENCE", jobs_dir=jobs_root)

    # Editorial fails
    mock_result = MagicMock()
    mock_result.returncode = 1
    mock_result.stderr = "Error"

    with patch("pipeline.rendering._run_ffmpeg", return_value=mock_result):
        result = render_edl(job["job_id"], "impossible_comeback", "SHORT",
                            jobs_dir=jobs_root, mode="EDITORIAL")

    assert result["ok"] is False

    # Reference state is preserved
    ref = read_render_state(job["job_id"], "impossible_comeback", "SHORT",
                            jobs_dir=jobs_root, mode="REFERENCE")
    assert ref["render_status"] == "COMPLETE"
    assert ref["output"] == "/ref.mp4"


# ── Safe error message ──────────────────────────────────────────────────────


def test_safe_error_message_strips_keys():
    msg = _safe_error_message(Exception("sk-abc1234567890123456789012345678"))
    assert "sk-abc123" not in msg


def test_safe_error_message_truncates():
    long_log = "x" * 1000
    msg = _safe_error_message(Exception(long_log))
    assert len(msg) <= 520
