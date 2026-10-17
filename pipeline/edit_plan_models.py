"""Renderer-neutral edit plan models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .story_engine import NARRATIVE_ROLES


EDIT_PLAN_STATUSES = frozenset({"DRAFT", "READY", "RENDERING", "FAILED", "ARCHIVED"})
RENDERER_SELECTIONS = frozenset({"FFMPEG", "CHATCUT"})


@dataclass(frozen=True)
class MotionGraphicTemplateRef:
    template_id: str
    template_name: str
    renderer: str
    parameters: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "template_id": self.template_id,
            "template_name": self.template_name,
            "renderer": self.renderer,
            "parameters": dict(self.parameters),
        }


@dataclass(frozen=True)
class EditPlan:
    edit_plan_id: str
    project_id: str
    story_id: str
    edit_brief_id: str
    title: str
    target_platform: str
    target_duration: float | None
    aspect_ratio: str
    hook_text: str = ""
    story_archetype: str = ""
    status: str = "DRAFT"
    renderer: str = "FFMPEG"
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in EDIT_PLAN_STATUSES:
            raise ValueError(f"invalid edit plan status: {self.status}")
        if self.renderer not in RENDERER_SELECTIONS:
            raise ValueError(f"invalid renderer: {self.renderer}")

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class EditBeat:
    edit_beat_id: str
    edit_plan_id: str
    sequence_order: int
    narrative_role: str
    source_moment_id: str | None
    purpose: str = ""
    description: str = ""
    source_start: float | None = None
    source_end: float | None = None
    target_duration: float | None = None
    crop_intent: str = ""
    speed_intent: str = ""
    text_overlay: str = ""
    caption_intent: str = ""
    audio_intent: str = ""
    transition_intent: str = ""
    motion_graphic_intent: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.sequence_order < 1:
            raise ValueError("sequence_order must be positive")
        if self.narrative_role not in NARRATIVE_ROLES:
            raise ValueError(f"invalid narrative role: {self.narrative_role}")

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class TimelineInstruction:
    instruction_id: str
    edit_plan_id: str
    edit_beat_id: str | None
    instruction_type: str
    source_artifact_id: str | None = None
    source_in: float | None = None
    source_out: float | None = None
    timeline_start: float | None = None
    timeline_duration: float | None = None
    crop: dict[str, Any] = field(default_factory=dict)
    scale: dict[str, Any] = field(default_factory=dict)
    position: dict[str, Any] = field(default_factory=dict)
    speed: float | None = None
    freeze_frame: bool = False
    opacity: float | None = None
    text: str = ""
    caption_style: dict[str, Any] = field(default_factory=dict)
    audio_gain: float | None = None
    music_cue: str = ""
    sfx_cue: str = ""
    transition: str = ""
    motion_graphic_template: dict[str, Any] = field(default_factory=dict)
    motion_graphic_parameters: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        for key in ("crop", "scale", "position", "caption_style", "motion_graphic_template", "motion_graphic_parameters", "metadata"):
            data[key] = dict(getattr(self, key))
        return data
