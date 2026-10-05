"""Canonical Render runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .channel_models import PLATFORMS
from .edit_brief import FORMAT_TREATMENTS
from .rendering import RENDER_MODES

RENDER_STATUSES = frozenset({"QUEUED", "RENDERING", "READY", "FAILED", "ARCHIVED"})
RENDER_REVIEW_STATES = frozenset({"UNREVIEWED", "APPROVED", "NEEDS_CHANGES", "REJECTED"})


@dataclass(frozen=True)
class CanonicalRender:
    render_id: str
    project_id: str
    story_id: str
    edit_brief_id: str
    edl_id: str
    artifact_id: str | None
    format_treatment: str
    render_profile: str
    status: str
    review_state: str
    channel_preset_id: str | None = None
    platform: str | None = None
    duration_seconds: float | None = None
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    review_note: str | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in RENDER_STATUSES:
            raise ValueError(f"invalid render status: {self.status}")
        if self.review_state not in RENDER_REVIEW_STATES:
            raise ValueError(f"invalid render review state: {self.review_state}")
        if self.format_treatment not in FORMAT_TREATMENTS:
            raise ValueError(f"invalid format treatment: {self.format_treatment}")
        if self.render_profile not in RENDER_MODES:
            raise ValueError(f"invalid render profile: {self.render_profile}")
        if self.platform is not None and self.platform not in PLATFORMS:
            raise ValueError(f"invalid platform: {self.platform}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "render_id": self.render_id,
            "project_id": self.project_id,
            "story_id": self.story_id,
            "edit_brief_id": self.edit_brief_id,
            "edl_id": self.edl_id,
            "artifact_id": self.artifact_id,
            "format_treatment": self.format_treatment,
            "render_profile": self.render_profile,
            "channel_preset_id": self.channel_preset_id,
            "platform": self.platform,
            "status": self.status,
            "review_state": self.review_state,
            "duration_seconds": self.duration_seconds,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "reviewed_at": self.reviewed_at,
            "reviewed_by": self.reviewed_by,
            "review_note": self.review_note,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }