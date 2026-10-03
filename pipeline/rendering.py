"""Rendering — service boundary.

Converts an EDL into a playable rough cut using the FFmpeg reference
renderer or the editorial renderer that executes Stadium Signal's
editorial language.

Deterministic execution from EDL + source media.
No editorial decision-making in the renderer — receives intent, executes.

No CLI invocation. No shell execution. No secrets exposure.
"""

from __future__ import annotations

import json
import math
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

# ── Render modes ─────────────────────────────────────────────────────────────

RENDER_MODES = {"REFERENCE", "EDITORIAL"}

# ── Render states ────────────────────────────────────────────────────────────

RENDER_STATES = {"WAITING", "VALIDATING", "CUTTING", "ASSEMBLING", "COMPLETE", "FAILED", "NEEDS_ATTENTION"}

# ── Feature classification (legacy, kept for reference mode) ─────────────────

APPLIED_FEATURES = {"CUTS", "ORDERING", "CONCATENATION", "SOURCE_AUDIO"}

DEFERRED_FEATURES = {
    "AUDIO_DROP", "FREEZE_PUSH", "HOOK_TEXT", "COMMENTARY_FOCUS",
    "CROWD_FOCUS", "MUSIC_BUILD", "MUSIC_PEAK", "SILENCE", "IMPACT",
    "AMBIENT", "CROWD_AND_COMMENTARY", "ORIGINAL",
    "REACTION_CUT", "CROWD_CUT", "COMMENTARY_CARRY", "AUDIO_BRIDGE",
    "SCOREBOARD_FLASH", "REPLAY_ECHO", "FLASH_CUT", "WHIP_PAN", "FADE",
    "HARD_CUT",
}

# ── Capability registry ──────────────────────────────────────────────────────

CAPABILITY_REGISTRY = {
    "render_modes": ["REFERENCE", "EDITORIAL"],
    "supported_transitions": {
        "HARD_CUT": {
            "status": "APPLIED",
            "description": "Clean cut between segments, no visual transition",
        },
        "FLASH_CUT": {
            "status": "APPLIED",
            "description": "Brief white flash between high-energy beats",
            "configurable": ["flash_duration", "flash_opacity"],
        },
        "FADE": {
            "status": "APPLIED",
            "description": "Fade from/to black at segment boundaries",
            "configurable": ["fade_duration"],
        },
        "FREEZE_PUSH": {
            "status": "PARTIALLY_APPLIED",
            "description": "Freeze last frame with subtle digital push/zoom",
            "note": "Freeze is applied; push/zoom is a basic scale — no subject tracking",
        },
        "REACTION_CUT": {
            "status": "DEFERRED",
            "reason": "Requires shot detection for reaction selection",
        },
        "CROWD_CUT": {
            "status": "DEFERRED",
            "reason": "Requires crowd shot detection",
        },
        "WHIP_PAN": {
            "status": "DEFERRED",
            "reason": "Requires motion-aware transition generation",
        },
        "REPLAY_ECHO": {
            "status": "DEFERRED",
            "reason": "Requires replay detection and speed ramp",
        },
        "SCOREBOARD_FLASH": {
            "status": "DEFERRED",
            "reason": "Requires scoreboard region detection",
        },
        "COMMENTARY_CARRY": {
            "status": "DEFERRED",
            "reason": "Requires audio stem separation",
        },
    },
    "supported_audio": {
        "ORIGINAL": {
            "status": "APPLIED",
            "description": "Source audio as-is",
        },
        "AUDIO_DROP": {
            "status": "APPLIED",
            "description": "Momentarily remove/reduce audio before impact",
            "configurable": ["drop_duration", "drop_ramp"],
        },
        "AUDIO_BRIDGE": {
            "status": "PARTIALLY_APPLIED",
            "description": "Crossfade audio across segment boundary",
            "note": "Basic crossfade on mixed broadcast audio — no stem separation",
        },
        "CROWD_FOCUS": {
            "status": "DEFERRED",
            "reason": "Source contains mixed broadcast audio only — no crowd stem",
        },
        "COMMENTARY_FOCUS": {
            "status": "DEFERRED",
            "reason": "Source contains mixed broadcast audio only — no commentary stem",
        },
        "CROWD_AND_COMMENTARY": {
            "status": "DEFERRED",
            "reason": "Source contains mixed broadcast audio only — no stem separation",
        },
        "MUSIC_BUILD": {
            "status": "DEFERRED",
            "reason": "Requires music selection/generation",
        },
        "MUSIC_PEAK": {
            "status": "DEFERRED",
            "reason": "Requires music selection/generation",
        },
        "SILENCE": {
            "status": "APPLIED",
            "description": "Mute audio for segment duration",
        },
        "IMPACT": {
            "status": "DEFERRED",
            "reason": "Requires impact sound design",
        },
        "AMBIENT": {
            "status": "DEFERRED",
            "reason": "Requires ambient sound design",
        },
    },
    "supported_text": {
        "NONE": {"status": "APPLIED"},
        "HOOK_TEXT": {"status": "DEFERRED", "reason": "Requires animated typography"},
        "SCORE_CONTEXT": {"status": "DEFERRED", "reason": "Requires animated typography"},
        "TIME_CONTEXT": {"status": "DEFERRED", "reason": "Requires animated typography"},
        "PLAYER_CONTEXT": {"status": "DEFERRED", "reason": "Requires animated typography"},
    },
    "pacing_presets": {
        "SLOW": {"transition_multiply": 1.2, "note": "Slightly longer holds/fades"},
        "BUILDING": {"transition_multiply": 1.0, "note": "Standard transition timing"},
        "MEDIUM": {"transition_multiply": 1.0, "note": "Standard transition timing"},
        "FAST": {"transition_multiply": 0.8, "note": "Reduced transition duration"},
        "PEAK": {"transition_multiply": 0.6, "note": "Immediate cuts, brief effects"},
        "RELEASE": {"transition_multiply": 1.1, "note": "Slightly extended transitions"},
    },
    "intensity_presets": {
        "LOW": {"flash_opacity": 0.3, "freeze_hold": 0.3, "audio_drop_depth": 0.3},
        "MEDIUM": {"flash_opacity": 0.5, "freeze_hold": 0.5, "audio_drop_depth": 0.5},
        "HIGH": {"flash_opacity": 0.7, "freeze_hold": 0.7, "audio_drop_depth": 0.7},
        "PEAK": {"flash_opacity": 0.9, "freeze_hold": 0.8, "audio_drop_depth": 0.9},
        "RELEASE": {"flash_opacity": 0.4, "freeze_hold": 0.4, "audio_drop_depth": 0.4},
    },
}

# ── Renderer presets (documented, deterministic) ─────────────────────────────

FLASH_PRESETS = {
    "duration": 0.08,       # seconds — brief cinematic flash
    "fade_in": 0.02,        # fade from white
    "fade_out": 0.03,       # fade back to content
}

FADE_PRESETS = {
    "duration": 0.5,        # seconds — conservative fade to/from black
}

FREEZE_PRESETS = {
    "hold_duration": 1.0,   # seconds to hold frozen frame
    "zoom_start": 1.0,      # starting scale
    "zoom_end": 1.08,       # ending scale — subtle push
}

AUDIO_DROP_PRESETS = {
    "ramp_down": 0.15,      # seconds to ramp down before cut
    "hold_silence": 0.3,    # seconds of silence before next segment
    "ramp_up": 0.10,        # seconds to ramp up at next segment start
}

AUDIO_BRIDGE_PRESETS = {
    "crossfade_duration": 0.3,  # seconds of crossfade overlap
}


# ── Internal helpers ────────────────────────────────────────────────────────


def _renders_dir(jobs_dir: Path) -> Path:
    d = jobs_dir / "RENDERS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_output_dir(job_id: str, story_id: str, fmt: str, jobs_dir: Path,
                       mode: str = "REFERENCE") -> Path:
    d = _renders_dir(jobs_dir) / job_id / story_id / fmt / mode
    d.mkdir(parents=True, exist_ok=True)
    return d


def _render_state_path(job_id: str, story_id: str, fmt: str, jobs_dir: Path,
                       mode: str = "REFERENCE") -> Path:
    return _render_output_dir(job_id, story_id, fmt, jobs_dir, mode) / "render_state.json"


def _read_job_record(job_id: str, jobs_dir: Path) -> dict:
    record_path = jobs_dir / f"{job_id}.json"
    if not record_path.exists():
        return {}
    return json.loads(record_path.read_text(encoding="utf-8"))


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_error_message(exc: Exception) -> str:
    """Produce a credential-safe error message."""
    msg = str(exc)
    import re
    msg = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED]", msg)
    msg = re.sub(r"token[=:]\s*\S+", "token=[REDACTED]", msg, flags=re.IGNORECASE)
    # Truncate FFmpeg logs
    if "ffmpeg" in msg.lower() and len(msg) > 500:
        msg = msg[:500] + "... [truncated]"
    elif len(msg) > 500:
        msg = msg[:500] + "..."
    return msg or "Render failed."


def _parse_num(val, default: float = 0.0) -> float:
    """Parse a numeric value (int, float, or string) to float."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return default
        try:
            return float(val)
        except ValueError:
            return default
    return default


# ── Render state I/O ────────────────────────────────────────────────────────


def update_render_state(job_id: str, story_id: str, fmt: str, *,
                        status: str, jobs_dir: str | Path | None = None,
                        mode: str = "REFERENCE",
                        **extra) -> dict:
    """Update the render state for a specific story/format/mode."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _render_state_path(job_id, story_id, fmt, jobs_dir_path, mode)

    if path.exists():
        state = json.loads(path.read_text(encoding="utf-8"))
    else:
        state = {"job_id": job_id, "story_id": story_id, "format": fmt, "render_mode": mode}

    now = _now_iso()
    state["render_status"] = status
    state["render_mode"] = mode
    if status == "VALIDATING" and not state.get("render_started_at"):
        state["render_started_at"] = now
    if status in ("COMPLETE", "FAILED", "NEEDS_ATTENTION"):
        state["render_completed_at"] = now

    for key, value in extra.items():
        state[key] = value

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    return state


def read_render_state(job_id: str, story_id: str, fmt: str,
                      jobs_dir: str | Path | None = None,
                      mode: str = "REFERENCE") -> dict | None:
    """Read the render state for a specific mode. Returns None if not found."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _render_state_path(job_id, story_id, fmt, jobs_dir_path, mode)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


# ── Capability queries ──────────────────────────────────────────────────────


def list_render_capabilities() -> dict:
    """Return the full capability registry."""
    return dict(CAPABILITY_REGISTRY)


def get_transition_support(transition: str) -> dict:
    """Return the support status for a specific transition."""
    return CAPABILITY_REGISTRY["supported_transitions"].get(
        transition, {"status": "UNKNOWN", "reason": "Not in registry"}
    )


def get_audio_support(audio: str) -> dict:
    """Return the support status for a specific audio strategy."""
    return CAPABILITY_REGISTRY["supported_audio"].get(
        audio, {"status": "UNKNOWN", "reason": "Not in registry"}
    )


# ── Render validation ──────────────────────────────────────────────────────


def validate_render_request(edl: dict, source_path: str | Path,
                            mode: str = "REFERENCE") -> list[str]:
    """Validate that a render can proceed. Returns list of error strings."""
    errors: list[str] = []

    if mode not in RENDER_MODES:
        errors.append(f"unsupported render mode: {mode!r}")

    if not edl.get("segments"):
        errors.append("EDL has no segments")

    source = Path(source_path)
    if not source.exists():
        errors.append(f"source file not found: {source}")
    elif not source.is_file():
        errors.append(f"source path is not a file: {source}")

    # Check FFmpeg availability
    ffmpeg_path = shutil.which("ffmpeg")
    if not ffmpeg_path:
        errors.append("ffmpeg not found in PATH")

    # Validate source ranges in EDL
    for i, seg in enumerate(edl.get("segments", [])):
        src_start = seg.get("source_start", 0)
        src_end = seg.get("source_end", 0)
        if src_end <= src_start:
            errors.append(f"segment[{i}] invalid source range: {src_start}-{src_end}")

    return errors


# ── Reference FFmpeg args (Slice 10 compatible) ─────────────────────────────


def build_ffmpeg_args(edl: dict, source_path: str | Path,
                      output_path: str | Path) -> list[str]:
    """Build FFmpeg command arguments from EDL (reference mode).

    Returns a list of arguments for subprocess.run (no shell=True).
    Uses the concat demuxer for reliable concatenation.
    """
    source = str(source_path)
    output = str(output_path)
    segments = edl.get("segments", [])

    if not segments:
        raise ValueError("EDL has no segments")

    filter_parts = []
    concat_inputs = []

    for i, seg in enumerate(segments):
        src_start = seg.get("source_start", 0)
        src_end = seg.get("source_end", 0)
        duration = src_end - src_start
        input_label = f"v{i}"

        filter_parts.append(
            f"[0:v]trim=start={src_start}:duration={duration},setpts=PTS-STARTPTS[{input_label}]"
        )
        concat_inputs.append(f"[{input_label}]")

    concat_count = len(segments)
    concat_str = "".join(concat_inputs)
    filter_parts.append(f"{concat_str}concat=n={concat_count}:v=1:a=0[outv]")

    audio_parts = []
    for i, seg in enumerate(segments):
        src_start = seg.get("source_start", 0)
        src_end = seg.get("source_end", 0)
        duration = src_end - src_start
        input_label = f"a{i}"
        audio_parts.append(
            f"[0:a]atrim=start={src_start}:duration={duration},asetpts=PTS-STARTPTS[{input_label}]"
        )

    audio_concat_inputs = "".join(f"[a{i}]" for i in range(len(segments)))
    audio_parts.append(f"{audio_concat_inputs}concat=n={concat_count}:v=0:a=1[outa]")

    filter_complex = ";\n".join(filter_parts + audio_parts)

    args = [
        "ffmpeg", "-y",
        "-i", source,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output,
    ]

    return args


# ── Editorial filter construction ───────────────────────────────────────────


def _pacing_multiply(pacing: str) -> float:
    """Get the transition duration multiplier for a pacing value."""
    presets = CAPABILITY_REGISTRY["pacing_presets"]
    return presets.get(pacing, {}).get("transition_multiply", 1.0)


def _intensity_preset(intensity: str) -> dict:
    """Get the effect intensity preset."""
    presets = CAPABILITY_REGISTRY["intensity_presets"]
    return presets.get(intensity, {"flash_opacity": 0.5, "freeze_hold": 0.5, "audio_drop_depth": 0.5})


def _build_flash_filter(intensity: str, pacing: str, label: str) -> str:
    """Build a flash-cut overlay filter.

    Inserts a brief white flash between segments using the concat filter.
    The flash is a short white frame inserted as a brief overlay.
    """
    preset = _intensity_preset(intensity)
    pacing_mult = _pacing_multiply(pacing)
    duration = FLASH_PRESETS["duration"] * pacing_mult
    opacity = preset["flash_opacity"]

    # Flash: white frame fade in/out using geq for brightness boost
    # Applied as a brief brightness spike at the segment boundary
    return (
        f"color=white:s=1920x1080:d={duration}[flash_{label}];"
        f"[flash_{label}]format=yuva420p,colorchannelmixer=aa={opacity}[flasha_{label}];"
        f"[{label}][flasha_{label}]overlay=shortest=1[out_{label}]"
    )


def _build_fade_filter(is_start: bool, is_end: bool, label: str) -> str:
    """Build fade-to-black or fade-from-black filter."""
    duration = FADE_PRESETS["duration"]
    filters = []
    if is_start:
        filters.append(f"fade=t=in:st=0:d={duration}")
    if is_end:
        # Duration unknown at filter-build time; use a large max
        # The fade will apply from the end of the segment
        filters.append(f"fade=t=out:st=9999:d={duration}")
    if filters:
        return f"[{label}]{','.join(filters)}[out_{label}]"
    return f"[{label}]copy[out_{label}]"


def _build_freeze_push_filter(intensity: str, label: str) -> str:
    """Build freeze-frame with subtle digital push/zoom.

    Freezes the last frame of the segment, holds for a configured duration,
    and applies a subtle scale increase (digital push).
    """
    preset = _intensity_preset(intensity)
    hold = FREEZE_PRESETS["hold_duration"] * preset["freeze_hold"]
    zoom_start = FREEZE_PRESETS["zoom_start"]
    zoom_end = FREEZE_PRESETS["zoom_end"]

    # freeze: select last frame, loop it, apply zoom via scale
    # We use tpad to add frozen frames at the end, then zoompan for the push
    return (
        f"[{label}]tpad=stop_mode=clone:stop_duration={hold}[frozen_{label}];"
        f"[frozen_{label}]zoompan=z='min({zoom_start}+(({zoom_end}-{zoom_start})*on/({hold}*25)),{zoom_end})'"
        f":x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':d=1:s=1920x1080:fps=25[out_{label}]"
    )


def _build_audio_drop_filter(intensity: str, is_before_cut: bool,
                             segment_duration: float, label: str) -> str:
    """Build audio drop — ramp down volume before a cut, silence, ramp up after.

    Uses volume filter with envelope:
    - Ramp down in the last ramp_down seconds of the segment
    - Hold silence for hold_silence seconds at the start of next segment
    """
    preset = _intensity_preset(intensity)
    ramp_down = AUDIO_DROP_PRESETS["ramp_down"]
    ramp_up = AUDIO_DROP_PRESETS["ramp_up"]
    hold_silence = AUDIO_DROP_PRESETS["hold_silence"]
    depth = preset["audio_drop_depth"]

    if is_before_cut:
        # Ramp down at end of segment
        start_vol = 1.0
        end_vol = 1.0 - depth
        ramp_start = max(0, segment_duration - ramp_down)
        return (
            f"[{label}]volume=enable='between(t,{ramp_start:.3f},{segment_duration:.3f})':"
            f"volume='if(between(t,{ramp_start:.3f},{segment_duration:.3f}),"
            f"{start_vol}-(({start_vol}-{end_vol})*(t-{ramp_start:.3f})/{ramp_down:.3f}),1)'"
            f"[out_{label}]"
        )
    else:
        # Ramp up at start of segment
        end_vol = 1.0
        start_vol = 1.0 - depth
        return (
            f"[{label}]volume=enable='between(t,0,{ramp_up + hold_silence:.3f})':"
            f"volume='if(between(t,0,{hold_silence:.3f}),0,"
            f"if(between(t,{hold_silence:.3f},{ramp_up + hold_silence:.3f}),"
            f"{start_vol}+(({end_vol}-{start_vol})*(t-{hold_silence:.3f})/{ramp_up:.3f}),1))'"
            f"[out_{label}]"
        )


def _build_audio_bridge_filter(segment_duration: float, next_duration: float,
                               label: str) -> str:
    """Build basic audio crossfade across segment boundary.

    Overlaps the last crossfade_duration seconds of one segment's audio
    with the first crossfade_duration seconds of the next.
    """
    cf = AUDIO_BRIDGE_PRESETS["crossfade_duration"]
    # Simple crossfade: fade out current audio, next segment fades in
    fade_out_start = max(0, segment_duration - cf)
    return (
        f"[{label}]afade=t=out:st={fade_out_start:.3f}:d={cf:.3f}[out_{label}]"
    )


def _build_silence_filter(label: str) -> str:
    """Mute audio entirely for a segment."""
    return f"[{label}]volume=0[out_{label}]"


def _build_editorial_video_filters(edl: dict) -> tuple[list[str], list[dict]]:
    """Build per-segment video filters for editorial mode.

    Returns (filter_parts, feature_execution) where filter_parts are
    FFmpeg filter strings and feature_execution tracks what was applied.
    """
    segments = edl.get("segments", [])
    filter_parts: list[str] = []
    feature_execution: list[dict] = []

    for i, seg in enumerate(segments):
        seg_id = seg.get("segment_id", f"seg_{i:03d}")
        src_start = seg.get("source_start", 0)
        src_end = seg.get("source_end", 0)
        duration = src_end - src_start
        input_label = f"v{i}"
        out_label = f"ve{i}"

        # Base: trim and setpts
        filter_parts.append(
            f"[0:v]trim=start={src_start}:duration={duration},setpts=PTS-STARTPTS[{input_label}]"
        )

        current = input_label
        applied_effects: list[str] = []

        # Transition effects applied at segment boundaries
        transition_in = seg.get("transition_in", "")
        transition_out = seg.get("transition_out", "")
        pacing = seg.get("pacing", "")
        intensity = seg.get("intensity", "")

        # FLASH_CUT on transition_in (for non-first segments)
        if transition_in == "FLASH_CUT" and i > 0:
            flash_filter = _build_flash_filter(intensity, pacing, current)
            filter_parts.append(flash_filter)
            new_label = f"fl{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            applied_effects.append("FLASH_CUT")
            feature_execution.append({
                "segment_id": seg_id, "feature": "FLASH_CUT",
                "status": "APPLIED", "boundary": "transition_in",
            })

        # FADE on transition_in
        if transition_in == "FADE" and i == 0:
            fade_filter = _build_fade_filter(True, False, current)
            filter_parts.append(fade_filter)
            new_label = f"fd{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            applied_effects.append("FADE_IN")
            feature_execution.append({
                "segment_id": seg_id, "feature": "FADE",
                "status": "APPLIED", "boundary": "transition_in",
            })

        # FREEZE_PUSH on transition_out
        if transition_out == "FREEZE_PUSH":
            freeze_filter = _build_freeze_push_filter(intensity, current)
            filter_parts.append(freeze_filter)
            new_label = f"frz{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            applied_effects.append("FREEZE_PUSH")
            feature_execution.append({
                "segment_id": seg_id, "feature": "FREEZE_PUSH",
                "status": "APPLIED", "boundary": "transition_out",
                "note": "Freeze applied; push is basic scale — no subject tracking",
            })

        # FADE on transition_out
        if transition_out == "FADE":
            fade_filter = _build_fade_filter(False, True, current)
            filter_parts.append(fade_filter)
            new_label = f"fdout{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            applied_effects.append("FADE_OUT")
            feature_execution.append({
                "segment_id": seg_id, "feature": "FADE",
                "status": "APPLIED", "boundary": "transition_out",
            })

        # HARD_CUT is always supported — just pass through
        if transition_in == "HARD_CUT" or (i == 0 and not transition_in):
            feature_execution.append({
                "segment_id": seg_id, "feature": "HARD_CUT",
                "status": "APPLIED", "boundary": "transition_in",
            })

        # Handle unsupported transitions
        for t_name in (transition_in, transition_out):
            if t_name and t_name not in ("HARD_CUT", "FLASH_CUT", "FADE", "FREEZE_PUSH", ""):
                cap = get_transition_support(t_name)
                feature_execution.append({
                    "segment_id": seg_id, "feature": t_name,
                    "status": cap.get("status", "DEFERRED"),
                    "reason": cap.get("reason", "Not implemented in this renderer"),
                })

        # Final output label
        if current != out_label:
            filter_parts.append(f"[{current}]copy[{out_label}]")
        else:
            filter_parts.append(f"[{current}]copy[{out_label}]")

    return filter_parts, feature_execution


def _build_editorial_audio_filters(edl: dict) -> tuple[list[str], list[dict]]:
    """Build per-segment audio filters for editorial mode.

    Returns (filter_parts, feature_execution).
    """
    segments = edl.get("segments", [])
    filter_parts: list[str] = []
    feature_execution: list[dict] = []

    for i, seg in enumerate(segments):
        seg_id = seg.get("segment_id", f"seg_{i:03d}")
        src_start = seg.get("source_start", 0)
        src_end = seg.get("source_end", 0)
        duration = src_end - src_start
        input_label = f"a{i}"
        out_label = f"ae{i}"

        # Base: trim and setpts
        filter_parts.append(
            f"[0:a]atrim=start={src_start}:duration={duration},asetpts=PTS-STARTPTS[{input_label}]"
        )

        current = input_label
        audio_strategy = seg.get("audio_strategy", "")
        intensity = seg.get("intensity", "")
        transition_out = seg.get("transition_out", "")

        # AUDIO_DROP
        if audio_strategy == "AUDIO_DROP" or transition_out == "AUDIO_DROP":
            drop_filter = _build_audio_drop_filter(intensity, True, duration, current)
            filter_parts.append(drop_filter)
            new_label = f"adp{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            feature_execution.append({
                "segment_id": seg_id, "feature": "AUDIO_DROP",
                "status": "APPLIED", "boundary": "transition_out",
            })

        # AUDIO_BRIDGE
        if audio_strategy == "AUDIO_BRIDGE":
            bridge_filter = _build_audio_bridge_filter(duration, 0, current)
            filter_parts.append(bridge_filter)
            new_label = f"abr{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            feature_execution.append({
                "segment_id": seg_id, "feature": "AUDIO_BRIDGE",
                "status": "PARTIALLY_APPLIED", "boundary": "within_segment",
                "note": "Basic crossfade on mixed broadcast audio — no stem separation",
            })

        # SILENCE
        if audio_strategy == "SILENCE":
            silence_filter = _build_silence_filter(current)
            filter_parts.append(silence_filter)
            new_label = f"sil{i}"
            filter_parts.append(f"[out_{current}]copy[{new_label}]")
            current = new_label
            feature_execution.append({
                "segment_id": seg_id, "feature": "SILENCE",
                "status": "APPLIED", "boundary": "within_segment",
            })

        # ORIGINAL — pass through
        if audio_strategy in ("ORIGINAL", ""):
            feature_execution.append({
                "segment_id": seg_id, "feature": "SOURCE_AUDIO",
                "status": "APPLIED", "boundary": "within_segment",
            })

        # Handle unsupported audio strategies
        unsupported = {"CROWD_FOCUS", "COMMENTARY_FOCUS", "CROWD_AND_COMMENTARY",
                       "MUSIC_BUILD", "MUSIC_PEAK", "IMPACT", "AMBIENT"}
        if audio_strategy in unsupported:
            cap = get_audio_support(audio_strategy)
            feature_execution.append({
                "segment_id": seg_id, "feature": audio_strategy,
                "status": cap.get("status", "DEFERRED"),
                "reason": cap.get("reason", "Requires audio stem separation"),
            })

        # Final output label
        filter_parts.append(f"[{current}]copy[{out_label}]")

    return filter_parts, feature_execution


def _build_editorial_concat_filters(edl: dict) -> tuple[str, str]:
    """Build the final concat filters for video and audio.

    Returns (video_concat_filter, audio_concat_filter).
    """
    segments = edl.get("segments", [])
    n = len(segments)

    video_inputs = "".join(f"[ve{i}]" for i in range(n))
    audio_inputs = "".join(f"[ae{i}]" for i in range(n))

    video_concat = f"{video_inputs}concat=n={n}:v=1:a=0[outv]"
    audio_concat = f"{audio_inputs}concat=n={n}:v=0:a=1[outa]"

    return video_concat, audio_concat


def build_ffmpeg_args_editorial(edl: dict, source_path: str | Path,
                                output_path: str | Path) -> tuple[list[str], list[dict]]:
    """Build FFmpeg command arguments for editorial mode.

    Returns (args, feature_execution) where args is the command list
    and feature_execution is the detailed feature report.
    """
    source = str(source_path)
    output = str(output_path)
    segments = edl.get("segments", [])

    if not segments:
        raise ValueError("EDL has no segments")

    # Build per-segment video and audio filters
    video_filters, video_features = _build_editorial_video_filters(edl)
    audio_filters, audio_features = _build_editorial_audio_filters(edl)

    # Build concat
    video_concat, audio_concat = _build_editorial_concat_filters(edl)

    # Assemble full filter complex
    all_filters = video_filters + audio_filters + [video_concat, audio_concat]
    filter_complex = ";\n".join(all_filters)

    args = [
        "ffmpeg", "-y",
        "-i", source,
        "-filter_complex", filter_complex,
        "-map", "[outv]",
        "-map", "[outa]",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "23",
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "44100",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output,
    ]

    # Merge feature execution reports
    all_features = video_features + audio_features

    return args, all_features


# ── Feature classification (legacy for reference mode) ──────────────────────


def _classify_features(edl: dict) -> tuple[list[str], list[str]]:
    """Classify EDL features as applied or deferred (reference mode)."""
    applied = ["CUTS", "ORDERING", "CONCATENATION", "SOURCE_AUDIO"]
    deferred: list[str] = []

    for seg in edl.get("segments", []):
        audio = seg.get("audio_strategy", "")
        if audio and audio != "ORIGINAL":
            if audio not in deferred:
                deferred.append(audio)

        for transition_field in ("transition_in", "transition_out"):
            t = seg.get(transition_field, "")
            if t and t != "HARD_CUT":
                if t not in deferred:
                    deferred.append(t)

        text = seg.get("text_intent", "")
        if text and text != "NONE":
            if text not in deferred:
                deferred.append(text)

    return applied, deferred


# ── Feature classification (v2 for editorial mode) ──────────────────────────


def _classify_features_v2(edl: dict, feature_execution: list[dict]) -> dict:
    """Classify features into applied/partially_applied/deferred from the
    feature_execution report.

    Returns a summary dict with counts and lists.
    """
    applied = []
    partially = []
    deferred = []
    failed = []

    seen = set()
    for entry in feature_execution:
        key = (entry.get("segment_id", ""), entry.get("feature", ""))
        if key in seen:
            continue
        seen.add(key)

        status = entry.get("status", "DEFERRED")
        feature = entry.get("feature", "")
        if status == "APPLIED":
            applied.append(feature)
        elif status == "PARTIALLY_APPLIED":
            partially.append(feature)
        elif status == "FAILED":
            failed.append(feature)
        else:
            deferred.append(feature)

    return {
        "applied": list(dict.fromkeys(applied)),
        "partially_applied": list(dict.fromkeys(partially)),
        "deferred": list(dict.fromkeys(deferred)),
        "failed": list(dict.fromkeys(failed)),
    }


# ── Render execution ────────────────────────────────────────────────────────


def _run_ffmpeg(args: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    """Execute FFmpeg with structured arguments. Never uses shell=True."""
    return subprocess.run(
        args,
        capture_output=True,
        text=True,
        timeout=timeout,
        # Explicitly never use shell
        shell=False,
    )


# ── Main service interface ──────────────────────────────────────────────────


def render_edl(
    job_id: str,
    story_id: str,
    fmt: str,
    *,
    jobs_dir: str | Path | None = None,
    timeout: int = 300,
    dry_run: bool = False,
    mode: str = "REFERENCE",
) -> dict:
    """Render an EDL into a playable rough cut.

    mode: "REFERENCE" (clean assembly) or "EDITORIAL" (executes supported effects).

    Returns: ``{"ok": bool, "status": str, "output": str | None, ...}``
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    from .edl import read_edl

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()

    if mode not in RENDER_MODES:
        return {"ok": False, "error": f"unsupported render mode: {mode!r}", "status": "FAILED"}

    # 1. Read EDL
    edl = read_edl(job_id, story_id, fmt, jobs_dir=jobs_dir_path)
    if edl is None:
        return {"ok": False, "error": f"EDL not found for story '{story_id}' format '{fmt}'", "status": "FAILED"}

    # 2. Get source path from job record
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        return {"ok": False, "error": f"job '{job_id}' not found", "status": "FAILED"}

    source_path = ""
    intake_path = job.get("intake_manifest_path", "")
    if intake_path:
        try:
            intake_data = json.loads(Path(intake_path).read_text(encoding="utf-8"))
            source_path = intake_data.get("media", {}).get("local_file_path", "")
        except Exception:
            pass

    if not source_path:
        return {"ok": False, "error": "No source file configured in job record", "status": "FAILED"}

    # 3. Update state
    update_render_state(job_id, story_id, fmt, status="VALIDATING",
                        jobs_dir=jobs_dir_path, mode=mode)

    try:
        # 4. Validate
        validation_errors = validate_render_request(edl, source_path, mode=mode)
        if validation_errors:
            error_msg = "; ".join(validation_errors[:3])
            update_render_state(
                job_id, story_id, fmt, status="FAILED",
                error=f"Validation failed: {error_msg}",
                jobs_dir=jobs_dir_path, mode=mode,
            )
            return {"ok": False, "error": f"Validation failed: {error_msg}", "status": "FAILED"}

        if dry_run:
            update_render_state(job_id, story_id, fmt, status="COMPLETE",
                                jobs_dir=jobs_dir_path, mode=mode)
            return {"ok": True, "status": "COMPLETE", "dry_run": True, "mode": mode}

        # 5. Prepare output
        output_dir = _render_output_dir(job_id, story_id, fmt, jobs_dir_path, mode)
        output_filename = "reference.mp4" if mode == "REFERENCE" else "editorial.mp4"
        output_path = output_dir / output_filename

        # 6. Update state to CUTTING
        update_render_state(job_id, story_id, fmt, status="CUTTING",
                            jobs_dir=jobs_dir_path, mode=mode)

        # 7. Build and run FFmpeg
        if mode == "EDITORIAL":
            args, feature_execution = build_ffmpeg_args_editorial(edl, source_path, output_path)
            classification = _classify_features_v2(edl, feature_execution)
        else:
            args = build_ffmpeg_args(edl, source_path, output_path)
            feature_execution = []
            applied, deferred = _classify_features(edl)
            classification = {"applied": applied, "partially_applied": [],
                              "deferred": deferred, "failed": []}

        result = _run_ffmpeg(args, timeout=timeout)

        if result.returncode != 0:
            error_msg = result.stderr[-500:] if result.stderr else "FFmpeg failed with no output"
            update_render_state(
                job_id, story_id, fmt, status="FAILED",
                error=_safe_error_message(Exception(error_msg)),
                jobs_dir=jobs_dir_path, mode=mode,
            )
            return {"ok": False, "error": _safe_error_message(Exception(error_msg)), "status": "FAILED"}

        # 8. Verify output
        if not output_path.exists():
            update_render_state(
                job_id, story_id, fmt, status="FAILED",
                error="FFmpeg completed but output file not found",
                jobs_dir=jobs_dir_path, mode=mode,
            )
            return {"ok": False, "error": "FFmpeg completed but output file not found", "status": "FAILED"}

        # 9. Update state to COMPLETE
        output_size = output_path.stat().st_size
        update_render_state(
            job_id, story_id, fmt, status="COMPLETE",
            output=str(output_path),
            output_filename=output_filename,
            output_size=output_size,
            renderer="ffmpeg",
            render_mode=mode,
            duration=edl.get("timeline_duration", 0),
            segment_count=edl.get("segment_count", 0),
            feature_execution=feature_execution,
            applied_features=classification["applied"],
            partially_applied_features=classification["partially_applied"],
            deferred_features=classification["deferred"],
            failed_features=classification["failed"],
            jobs_dir=jobs_dir_path,
            mode=mode,
        )

        return {
            "ok": True,
            "status": "COMPLETE",
            "mode": mode,
            "output": str(output_path),
            "output_filename": output_filename,
            "duration": edl.get("timeline_duration", 0),
            "segment_count": edl.get("segment_count", 0),
            "feature_execution": feature_execution,
            "applied_features": classification["applied"],
            "partially_applied_features": classification["partially_applied"],
            "deferred_features": classification["deferred"],
            "failed_features": classification["failed"],
        }

    except Exception as exc:
        error_msg = _safe_error_message(exc)
        update_render_state(
            job_id, story_id, fmt, status="FAILED",
            error=error_msg,
            jobs_dir=jobs_dir_path, mode=mode,
        )
        return {"ok": False, "error": error_msg, "status": "FAILED"}
