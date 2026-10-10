"""EditPlan construction service."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from .edit_plan_models import EditBeat, EditPlan, MotionGraphicTemplateRef, TimelineInstruction
from .runtime_service import (
    get_edit_brief,
    get_moment,
    list_story_moments,
    upsert_edit_beat,
    upsert_edit_plan,
    upsert_timeline_instruction,
)


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def edit_plan_id_for(story_id: str, edit_brief_id: str, target_platform: str, renderer: str) -> str:
    return _stable_id("eplan", story_id, edit_brief_id, target_platform, renderer)


def generate_edit_plan_from_story(
    *,
    project_id: str,
    story_id: str,
    edit_brief_id: str,
    title: str,
    target_platform: str = "TikTok",
    aspect_ratio: str = "9:16",
    target_duration: float | None = None,
    renderer: str = "FFMPEG",
    hook_text: str = "",
    story_archetype: str = "",
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> EditPlan:
    brief = get_edit_brief(edit_brief_id, db_path=db_path)
    if brief is None:
        raise ValueError(f"edit brief '{edit_brief_id}' not found")
    plan = EditPlan(
        edit_plan_id=edit_plan_id_for(story_id, edit_brief_id, target_platform, renderer),
        project_id=project_id,
        story_id=story_id,
        edit_brief_id=edit_brief_id,
        title=title,
        target_platform=target_platform,
        target_duration=target_duration or brief.target_duration,
        aspect_ratio=aspect_ratio,
        hook_text=hook_text,
        story_archetype=story_archetype,
        status="READY",
        renderer=renderer,
        metadata={
            "story_archetype": story_archetype,
            "hook_style": metadata.get("hook_style", "text_first") if metadata else "text_first",
            "hook_duration": metadata.get("hook_duration", 3) if metadata else 3,
            "climax_position": metadata.get("climax_position") if metadata else None,
            "freeze_frame_used": metadata.get("freeze_frame_used", False) if metadata else False,
            "crowd_reaction_used": metadata.get("crowd_reaction_used", False) if metadata else False,
            "subtitle_density": metadata.get("subtitle_density", "medium") if metadata else "medium",
            "runtime": target_duration or brief.target_duration,
            "music_style": metadata.get("music_style", "none") if metadata else "none",
            "motion_graphics_used": metadata.get("motion_graphics_used", False) if metadata else False,
            **(metadata or {}),
        },
    )
    saved = upsert_edit_plan(plan, db_path=db_path)
    relationships = list_story_moments(story_id, db_path=db_path)
    roles = ["HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"]
    for index, rel in enumerate(relationships, start=1):
        role = rel.narrative_role if rel.narrative_role in roles else roles[min(index - 1, len(roles) - 1)]
        beat = EditBeat(
            edit_beat_id=_stable_id("ebeat", saved.edit_plan_id, index, rel.moment_id),
            edit_plan_id=saved.edit_plan_id,
            sequence_order=index,
            narrative_role=role,
            source_moment_id=rel.moment_id,
            purpose=role.title(),
            description=f"Use moment {rel.moment_id} as {role.lower()}.",
            target_duration=None,
            metadata={"story_moment_id": rel.story_moment_id},
        )
        upsert_edit_beat(beat, db_path=db_path)
    return saved


def build_timeline_instructions(edit_plan: EditPlan, beats: list[EditBeat], *, source_artifact_id: str | None = None,
                                db_path: str | Path | None = None) -> list[TimelineInstruction]:
    timeline = 0.0
    result: list[TimelineInstruction] = []
    for beat in sorted(beats, key=lambda item: item.sequence_order):
        moment = get_moment(beat.source_moment_id, db_path=db_path) if beat.source_moment_id else None
        duration = beat.target_duration or 4.0
        resolved_source_artifact_id = source_artifact_id or (moment.source_artifact_id if moment else None)
        source_start = beat.source_start if beat.source_start is not None else (moment.start_seconds if moment else None)
        source_end = beat.source_end if beat.source_end is not None else (moment.end_seconds if moment else None)
        instruction = TimelineInstruction(
            instruction_id=_stable_id("inst", edit_plan.edit_plan_id, beat.edit_beat_id, "clip"),
            edit_plan_id=edit_plan.edit_plan_id,
            edit_beat_id=beat.edit_beat_id,
            instruction_type="CLIP",
            source_artifact_id=resolved_source_artifact_id,
            source_in=source_start,
            source_out=source_end,
            timeline_start=timeline,
            timeline_duration=duration,
            text=beat.text_overlay,
            transition=beat.transition_intent,
            metadata={"narrative_role": beat.narrative_role},
        )
        result.append(upsert_timeline_instruction(instruction, db_path=db_path))
        timeline += duration
    return result


def aura_edit_fixture_plan(*, project_id: str, story_id: str, edit_brief_id: str,
                           renderer: str = "CHATCUT", db_path: str | Path | None = None) -> EditPlan:
    template = MotionGraphicTemplateRef(
        template_id="AURA_TITLE",
        template_name="Aura Title",
        renderer="CHATCUT",
        parameters={"title": "THE GOAL THAT SILENCED TURIN"},
    )
    return generate_edit_plan_from_story(
        project_id=project_id,
        story_id=story_id,
        edit_brief_id=edit_brief_id,
        title="The Goal That Silenced Turin",
        target_platform="TikTok",
        aspect_ratio="9:16",
        target_duration=22,
        renderer=renderer,
        hook_text="THE GOAL THAT SILENCED TURIN",
        story_archetype="AURA / IMPOSSIBLE",
        metadata={
            "fixture": "football_aura_edit",
            "hook_style": "aura_title",
            "hook_duration": 3,
            "climax_position": 8,
            "freeze_frame_used": True,
            "crowd_reaction_used": True,
            "subtitle_density": "low",
            "music_style": "dramatic_rise",
            "motion_graphics_used": True,
            "motion_graphic_templates": [template.to_dict()],
        },
        db_path=db_path,
    )
