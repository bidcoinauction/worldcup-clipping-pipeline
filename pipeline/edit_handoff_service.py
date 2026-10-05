"""EditPlan execution validation and renderer handoff helpers."""

from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

from .edit_plan_models import EditPlan, TimelineInstruction
from .composition_service import composition_for_instruction, ffmpeg_video_filter_for_mode, handoff_composition_contract
from .runtime_service import (
    get_artifact,
    get_edit_brief,
    get_moment,
    list_edit_beats,
    list_timeline_instructions,
    register_artifact,
    upsert_edl,
    upsert_render,
)

HANDOFF_VERSION = "1.0"
SUPPORTED_RENDERERS = {"CHATCUT", "FFMPEG"}
VALID_ASPECT_RATIOS = {"9:16", "16:9", "1:1", "4:5"}


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def _sequence_map(edit_plan: EditPlan) -> dict[str, int]:
    return {beat.edit_beat_id: beat.sequence_order for beat in list_edit_beats(edit_plan.edit_plan_id)}


def _ordered_instructions(edit_plan: EditPlan) -> list[TimelineInstruction]:
    sequences = _sequence_map(edit_plan)
    return sorted(
        list_timeline_instructions(edit_plan.edit_plan_id),
        key=lambda item: (sequences.get(item.edit_beat_id or "", 999999), item.timeline_start or 0, item.instruction_id),
    )


def _artifact_duration(artifact_id: str | None) -> float | None:
    artifact = get_artifact(artifact_id) if artifact_id else None
    if artifact is None:
        return None
    value = artifact.metadata.get("duration_seconds") or artifact.metadata.get("duration")
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def validate_edit_handoff(edit_plan: EditPlan, *, source_duration_seconds: float | None = None) -> dict[str, Any]:
    """Validate that an EditPlan can be executed by renderer handoff adapters."""
    if source_duration_seconds is None:
        source_duration_seconds = (edit_plan.metadata.get("source_coverage") or {}).get("source_duration_seconds")
    errors: list[str] = []
    warnings: list[str] = []
    if edit_plan.renderer not in SUPPORTED_RENDERERS:
        errors.append(f"unsupported renderer: {edit_plan.renderer}")
    if edit_plan.aspect_ratio not in VALID_ASPECT_RATIOS:
        errors.append(f"invalid aspect ratio: {edit_plan.aspect_ratio}")

    beats = list_edit_beats(edit_plan.edit_plan_id)
    beat_by_id = {beat.edit_beat_id: beat for beat in beats}
    sequence_orders = [beat.sequence_order for beat in beats]
    if len(sequence_orders) != len(set(sequence_orders)):
        errors.append("sequence_order values must be unique")

    template_ids = [
        item.get("template_id")
        for item in edit_plan.metadata.get("motion_graphic_templates", [])
        if isinstance(item, dict)
    ]
    if any(not template_id for template_id in template_ids):
        errors.append("motion graphic templates require template_id")

    previous_timeline_start: float | None = None
    instructions = _ordered_instructions(edit_plan)
    for instruction in instructions:
        if instruction.edit_beat_id and instruction.edit_beat_id not in beat_by_id:
            errors.append(f"TimelineInstruction {instruction.instruction_id} references missing EditBeat {instruction.edit_beat_id}")
            continue
        beat = beat_by_id.get(instruction.edit_beat_id or "")
        if beat and beat.source_moment_id:
            moment = get_moment(beat.source_moment_id)
            if moment is None:
                errors.append(f"EditBeat {beat.edit_beat_id} references missing Moment {beat.source_moment_id}")
            elif moment.metadata.get("availability_status") == "OUTSIDE_SOURCE":
                errors.append(f"EditBeat {beat.edit_beat_id} references OUTSIDE_SOURCE Moment {beat.source_moment_id}")
        if instruction.source_artifact_id and get_artifact(instruction.source_artifact_id) is None:
            errors.append(f"TimelineInstruction {instruction.instruction_id} references missing source artifact {instruction.source_artifact_id}")
        duration = source_duration_seconds if source_duration_seconds is not None else _artifact_duration(instruction.source_artifact_id)
        if instruction.source_in is not None or instruction.source_out is not None:
            if instruction.source_in is None or instruction.source_out is None:
                errors.append(f"TimelineInstruction {instruction.instruction_id} has partial source range")
            elif instruction.source_out <= instruction.source_in:
                errors.append(f"TimelineInstruction {instruction.instruction_id} source_out must be greater than source_in")
            else:
                if instruction.source_in < 0:
                    errors.append(f"TimelineInstruction {instruction.instruction_id} source_in is negative")
                if duration is not None and instruction.source_out > duration:
                    errors.append(f"TimelineInstruction {instruction.instruction_id} source_out exceeds source duration")
        if instruction.timeline_duration is None or instruction.timeline_duration <= 0:
            errors.append(f"TimelineInstruction {instruction.instruction_id} timeline_duration must be positive")
        if instruction.timeline_start is None or instruction.timeline_start < 0:
            errors.append(f"TimelineInstruction {instruction.instruction_id} timeline_start must be non-negative")
        elif previous_timeline_start is not None and instruction.timeline_start < previous_timeline_start:
            errors.append("timeline_start values must be monotonic in sequence_order")
        if instruction.timeline_start is not None:
            previous_timeline_start = instruction.timeline_start
        if instruction.motion_graphic_template and not instruction.motion_graphic_template.get("template_id"):
            errors.append(f"TimelineInstruction {instruction.instruction_id} motion graphic template missing template_id")
    if not instructions:
        errors.append("EditPlan has no timeline instructions")
    return {"ok": not errors, "status": "VALID" if not errors else "INVALID", "errors": errors, "warnings": warnings}


def source_media_declarations(edit_plan: EditPlan, *, source_duration_seconds: float | None = None) -> list[dict[str, Any]]:
    if source_duration_seconds is None:
        source_duration_seconds = (edit_plan.metadata.get("source_coverage") or {}).get("source_duration_seconds")
    declarations: list[dict[str, Any]] = []
    seen: set[str] = set()
    for instruction in _ordered_instructions(edit_plan):
        artifact_id = instruction.source_artifact_id
        if not artifact_id or artifact_id in seen:
            continue
        seen.add(artifact_id)
        artifact = get_artifact(artifact_id)
        if artifact is None:
            continue
        metadata = dict(artifact.metadata)
        declarations.append({
            "artifact_id": artifact.artifact_id,
            "source_path": artifact.path,
            "duration_seconds": source_duration_seconds if source_duration_seconds is not None else metadata.get("duration_seconds") or metadata.get("duration"),
            "width": metadata.get("width"),
            "height": metadata.get("height"),
            "fps": metadata.get("fps"),
        })
    return declarations


def build_text_overlays(edit_plan: EditPlan) -> list[dict[str, Any]]:
    overlays = []
    sequences = _sequence_map(edit_plan)
    for instruction in _ordered_instructions(edit_plan):
        if not instruction.text:
            continue
        overlays.append({
            "sequence_order": sequences.get(instruction.edit_beat_id or ""),
            "text": instruction.text,
            "timeline_start": instruction.timeline_start,
            "duration": instruction.timeline_duration,
            "position": instruction.position or {"intent": "safe_center"},
            "template_id": (instruction.motion_graphic_template or {}).get("template_id"),
            "style_intent": (instruction.caption_style or {}).get("intent") or "text_overlay",
        })
    return overlays


def write_caption_file(edit_plan: EditPlan, captions_dir: Path) -> Path | None:
    overlays = build_text_overlays(edit_plan)
    if not overlays:
        return None
    captions_dir.mkdir(parents=True, exist_ok=True)
    path = captions_dir / "captions.srt"
    blocks: list[str] = []
    for index, overlay in enumerate(overlays, start=1):
        start = float(overlay.get("timeline_start") or 0.0)
        end = start + float(overlay.get("duration") or 0.0)
        blocks.append(f"{index}\n{_srt_time(start)} --> {_srt_time(end)}\n{overlay['text']}\n")
    path.write_text("\n".join(blocks), encoding="utf-8")
    return path


def _srt_time(seconds: float) -> str:
    ms = int(round(seconds * 1000))
    hours, rem = divmod(ms, 3600000)
    minutes, rem = divmod(rem, 60000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def build_chatcut_handoff_manifest(edit_plan: EditPlan, *, source_duration_seconds: float | None = None,
                                   package_dir: Path | None = None) -> dict[str, Any]:
    sequences = _sequence_map(edit_plan)
    beats_by_id = {beat.edit_beat_id: beat for beat in list_edit_beats(edit_plan.edit_plan_id)}
    instructions = []
    for instruction in _ordered_instructions(edit_plan):
        beat = beats_by_id.get(instruction.edit_beat_id or "")
        moment = get_moment(beat.source_moment_id) if beat and beat.source_moment_id else None
        composition_mode = composition_for_instruction(instruction, beat=beat, moment=moment)
        item = instruction.to_dict()
        item["sequence_order"] = sequences.get(instruction.edit_beat_id or "")
        item["composition_mode"] = composition_mode
        item["composition"] = handoff_composition_contract(composition_mode)
        instructions.append(item)
    beats = [beat.to_dict() for beat in sorted(list_edit_beats(edit_plan.edit_plan_id), key=lambda beat: (beat.sequence_order, beat.edit_beat_id))]
    captions_path = str((package_dir / "captions" / "captions.srt")) if package_dir and (package_dir / "captions" / "captions.srt").exists() else None
    return {
        "schema_version": 1,
        "handoff_version": HANDOFF_VERSION,
        "chatcut_handoff_version": HANDOFF_VERSION,
        "package_type": "chatcut_handoff",
        "project_id": edit_plan.project_id,
        "story_id": edit_plan.story_id,
        "edit_plan_id": edit_plan.edit_plan_id,
        "renderer": "CHATCUT",
        "platform": edit_plan.target_platform,
        "platform_target": edit_plan.target_platform,
        "aspect_ratio": edit_plan.aspect_ratio,
        "target_duration": edit_plan.target_duration,
        "source_media": source_media_declarations(edit_plan, source_duration_seconds=source_duration_seconds),
        "timeline": instructions,
        "timeline_order": beats,
        "instructions": instructions,
        "source_references": sorted({i.get("source_artifact_id") for i in instructions if i.get("source_artifact_id")}),
        "motion_graphics": edit_plan.metadata.get("motion_graphic_templates", []),
        "branding": edit_plan.metadata.get("branding", {}),
        "captions": {"format": "srt", "path": captions_path, "intent": edit_plan.metadata.get("caption_intent", "")},
        "text_overlays": build_text_overlays(edit_plan),
        "audio": {"music_style": edit_plan.metadata.get("music_style", ""), "cues": [i.get("music_cue") for i in instructions if i.get("music_cue")]},
        "edit_plan": edit_plan.to_dict(),
        "metadata": {"deterministic": True, "notes": "Renderer handoff contract. Not a direct ChatCut API schema."},
        "notes": "Inspectable handoff package. Clipper remains source of truth for sports intelligence.",
    }


def prepare_chatcut_handoff_v1(edit_plan: EditPlan, *, output_dir: str | Path | None = None,
                               source_duration_seconds: float | None = None) -> dict[str, Any]:
    if source_duration_seconds is None:
        source_duration_seconds = (edit_plan.metadata.get("source_coverage") or {}).get("source_duration_seconds")
    validation = validate_edit_handoff(edit_plan, source_duration_seconds=source_duration_seconds)
    if not validation["ok"]:
        return {"ok": False, "status": "FAILED", "renderer": "CHATCUT", "validation": validation}
    root = Path(output_dir) if output_dir is not None else Path("data") / "pilot" / "chatcut_handoffs"
    package_dir = root / edit_plan.project_id / edit_plan.edit_plan_id
    for child in ("captions", "assets", "previews"):
        (package_dir / child).mkdir(parents=True, exist_ok=True)
    caption_path = write_caption_file(edit_plan, package_dir / "captions")
    manifest = build_chatcut_handoff_manifest(edit_plan, source_duration_seconds=source_duration_seconds, package_dir=package_dir)
    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    artifact = register_artifact(
        project_id=edit_plan.project_id,
        artifact_type="chatcut_handoff",
        path=manifest_path,
        metadata={
            "renderer": "chatcut",
            "handoff_version": HANDOFF_VERSION,
            "edit_plan_id": edit_plan.edit_plan_id,
            "manifest_path": str(manifest_path),
            "package_dir": str(package_dir),
        },
    )
    return {
        "ok": True,
        "status": "READY",
        "renderer": "CHATCUT",
        "handoff_version": HANDOFF_VERSION,
        "edit_plan_id": edit_plan.edit_plan_id,
        "handoff": True,
        "package_dir": str(package_dir),
        "manifest_path": str(manifest_path),
        "caption_path": str(caption_path) if caption_path else None,
        "artifact_id": artifact.artifact_id,
        "validation": validation,
    }


def _preview_ffmpeg_args(edit_plan: EditPlan, output_path: Path) -> list[str]:
    instructions = _ordered_instructions(edit_plan)
    beats = {beat.edit_beat_id: beat for beat in list_edit_beats(edit_plan.edit_plan_id)}
    source_ids = {instruction.source_artifact_id for instruction in instructions if instruction.source_artifact_id}
    if len(source_ids) != 1:
        raise ValueError("FFmpeg preview currently requires exactly one source artifact")
    artifact = get_artifact(next(iter(source_ids)))
    if artifact is None:
        raise ValueError("source artifact not found")
    filter_parts: list[str] = []
    concat_inputs: list[str] = []
    for index, instruction in enumerate(instructions):
        beat = beats.get(instruction.edit_beat_id or "")
        moment = get_moment(beat.source_moment_id) if beat and beat.source_moment_id else None
        composition_mode = composition_for_instruction(instruction, beat=beat, moment=moment)
        source_duration = float(instruction.source_out or 0) - float(instruction.source_in or 0)
        timeline_duration = float(instruction.timeline_duration or source_duration)
        pts_factor = timeline_duration / source_duration if source_duration else 1.0
        trimmed = f"trim{index}"
        label = f"v{index}"
        filter_parts.append(
            f"[0:v]trim=start={instruction.source_in}:duration={source_duration},setpts=PTS-STARTPTS,setpts={pts_factor:.8f}*PTS[{trimmed}];"
            f"{ffmpeg_video_filter_for_mode(composition_mode, input_label=trimmed, output_label=label)}"
        )
        concat_inputs.append(f"[{label}]")
    filter_parts.append(f"{''.join(concat_inputs)}concat=n={len(instructions)}:v=1:a=0[outv]")
    audio_parts: list[str] = []
    audio_inputs: list[str] = []
    for index, instruction in enumerate(instructions):
        source_duration = float(instruction.source_out or 0) - float(instruction.source_in or 0)
        timeline_duration = float(instruction.timeline_duration or source_duration)
        tempo = source_duration / timeline_duration if timeline_duration else 1.0
        label = f"a{index}"
        audio_parts.append(f"[0:a]atrim=start={instruction.source_in}:duration={source_duration},asetpts=PTS-STARTPTS,{_atempo_filter(tempo)}[{label}]")
        audio_inputs.append(f"[{label}]")
    audio_parts.append(f"{''.join(audio_inputs)}concat=n={len(instructions)}:v=0:a=1[outa]")
    return [
        "ffmpeg", "-y", "-i", artifact.path,
        "-filter_complex", ";".join(filter_parts + audio_parts),
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "24",
        "-c:a", "aac", "-b:a", "128k", "-pix_fmt", "yuv420p", "-movflags", "+faststart",
        str(output_path),
    ]


def _atempo_filter(tempo: float) -> str:
    parts: list[float] = []
    current = float(tempo)
    while current > 2.0:
        parts.append(2.0)
        current /= 2.0
    while current < 0.5:
        parts.append(0.5)
        current /= 0.5
    parts.append(current)
    return ",".join(f"atempo={part:.8f}" for part in parts)


def _ffprobe_video(path: Path) -> dict[str, Any]:
    if not shutil.which("ffprobe"):
        return {}
    result = subprocess.run([
        "ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
        "stream=width,height,r_frame_rate:format=duration", "-of", "json", str(path),
    ], capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        return {}
    data = json.loads(result.stdout or "{}")
    stream = (data.get("streams") or [{}])[0]
    duration = float((data.get("format") or {}).get("duration") or 0)
    fps_value = stream.get("r_frame_rate") or "0/1"
    num, den = (fps_value.split("/") + ["1"])[:2]
    fps = float(num) / float(den or 1)
    return {"duration_seconds": duration, "width": stream.get("width"), "height": stream.get("height"), "fps": fps}


def render_ffmpeg_preview_from_edit_plan(edit_plan: EditPlan, *, output_dir: str | Path | None = None,
                                         source_duration_seconds: float | None = None,
                                         timeout: int = 300,
                                         dry_run: bool = False) -> dict[str, Any]:
    if source_duration_seconds is None:
        source_duration_seconds = (edit_plan.metadata.get("source_coverage") or {}).get("source_duration_seconds")
    validation = validate_edit_handoff(edit_plan, source_duration_seconds=source_duration_seconds)
    if not validation["ok"]:
        return {"ok": False, "status": "FAILED", "renderer": "FFMPEG", "validation": validation}
    if not shutil.which("ffmpeg") and not dry_run:
        return {"ok": False, "status": "FAILED", "renderer": "FFMPEG", "error": "ffmpeg not found"}
    root = Path(output_dir) if output_dir is not None else Path("data") / "pilot" / "ffmpeg_previews"
    package_dir = root / edit_plan.project_id / edit_plan.edit_plan_id
    package_dir.mkdir(parents=True, exist_ok=True)
    output_path = package_dir / "preview.mp4"
    args = _preview_ffmpeg_args(edit_plan, output_path)
    if dry_run:
        return {"ok": True, "status": "DRY_RUN", "renderer": "FFMPEG", "command": args, "output": str(output_path), "deferred_features": _preview_deferred_features(edit_plan), "validation": validation}
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if result.returncode != 0:
        return {"ok": False, "status": "FAILED", "renderer": "FFMPEG", "error": result.stderr[-500:], "validation": validation}
    artifact = register_artifact(
        project_id=edit_plan.project_id,
        artifact_type="render_video",
        path=output_path,
        metadata={"renderer": "ffmpeg", "edit_plan_id": edit_plan.edit_plan_id, "preview": True},
    )
    brief = get_edit_brief(edit_plan.edit_brief_id)
    edl = upsert_edl(
        project_id=edit_plan.project_id,
        story_id=edit_plan.story_id,
        edit_brief_id=edit_plan.edit_brief_id,
        format_treatment=brief.format_treatment if brief else "SHORT",
        status="READY",
        target_duration=int(edit_plan.target_duration or 0) or None,
        estimated_duration=sum(float(i.timeline_duration or 0) for i in _ordered_instructions(edit_plan)),
        metadata={"edit_plan_id": edit_plan.edit_plan_id, "preview": True, "renderer": "ffmpeg"},
    )
    probe = _ffprobe_video(output_path)
    deferred_features = _preview_deferred_features(edit_plan)
    render = upsert_render(
        project_id=edit_plan.project_id,
        story_id=edit_plan.story_id,
        edit_brief_id=edit_plan.edit_brief_id,
        edl_id=edl.edl_id,
        format_treatment=brief.format_treatment if brief else "SHORT",
        render_profile="REFERENCE",
        status="READY",
        review_state="UNREVIEWED",
        artifact_id=artifact.artifact_id,
        duration_seconds=probe.get("duration_seconds"),
        width=probe.get("width"),
        height=probe.get("height"),
        fps=probe.get("fps"),
        metadata={"renderer": "ffmpeg", "edit_plan_id": edit_plan.edit_plan_id, "preview": True, "deferred_features": deferred_features},
    )
    return {
        "ok": True,
        "status": "READY",
        "renderer": "FFMPEG",
        "preview": True,
        "output": str(output_path),
        "artifact_id": artifact.artifact_id,
        "render_id": render.render_id,
        "duration_seconds": probe.get("duration_seconds"),
        "width": probe.get("width"),
        "height": probe.get("height"),
        "fps": probe.get("fps"),
        "deferred_features": deferred_features,
        "validation": validation,
    }


def _preview_deferred_features(edit_plan: EditPlan) -> list[str]:
    deferred: set[str] = set()
    if build_text_overlays(edit_plan):
        deferred.add("text_overlays_not_burned_in")
    if edit_plan.metadata.get("motion_graphic_templates"):
        deferred.add("motion_graphics_not_rendered")
    if any(instruction.freeze_frame for instruction in _ordered_instructions(edit_plan)):
        deferred.add("freeze_frame_approximated_by_timing")
    return sorted(deferred)


def editplan_quality_report(edit_plan: EditPlan) -> list[dict[str, Any]]:
    beats = {beat.edit_beat_id: beat for beat in list_edit_beats(edit_plan.edit_plan_id)}
    rows: list[dict[str, Any]] = []
    for instruction in _ordered_instructions(edit_plan):
        beat = beats.get(instruction.edit_beat_id or "")
        moment = get_moment(beat.source_moment_id) if beat and beat.source_moment_id else None
        composition_mode = composition_for_instruction(instruction, beat=beat, moment=moment)
        rows.append({
            "sequence_order": beat.sequence_order if beat else None,
            "narrative_role": beat.narrative_role if beat else instruction.metadata.get("narrative_role"),
            "moment_id": beat.source_moment_id if beat else None,
            "source_window": [instruction.source_in, instruction.source_out],
            "duration": instruction.timeline_duration,
            "crop_intent": (instruction.crop or {}).get("intent") or (beat.crop_intent if beat else ""),
            "composition_mode": composition_mode,
            "speed": instruction.speed,
            "freeze": instruction.freeze_frame,
            "text": instruction.text,
            "audio_cue": instruction.music_cue or instruction.sfx_cue,
            "motion_graphic": (instruction.motion_graphic_template or {}).get("template_id") or (beat.motion_graphic_intent if beat else ""),
        })
    return rows


def execution_parity_report(edit_plan: EditPlan, manifest: dict[str, Any], preview_result: dict[str, Any]) -> dict[str, Any]:
    plan_windows = [(i.source_in, i.source_out) for i in _ordered_instructions(edit_plan)]
    manifest_windows = [(i.get("source_in"), i.get("source_out")) for i in manifest.get("timeline", [])]
    return {
        "same_edit_plan_id": manifest.get("edit_plan_id") == edit_plan.edit_plan_id,
        "same_story_id": manifest.get("story_id") == edit_plan.story_id,
        "same_source_windows": plan_windows == manifest_windows,
        "same_sequence_order": [i.get("sequence_order") for i in manifest.get("timeline", [])] == sorted(_sequence_map(edit_plan).values()),
        "same_aspect_ratio": manifest.get("aspect_ratio") == edit_plan.aspect_ratio,
        "preview_ready": bool(preview_result.get("ok")),
    }
