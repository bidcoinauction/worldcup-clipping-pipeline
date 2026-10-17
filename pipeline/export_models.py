"""Canonical Export Package runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .channel_models import PLATFORMS

EXPORT_STATUSES = frozenset({"DRAFT", "READY", "DELIVERED", "FAILED", "ARCHIVED"})


@dataclass(frozen=True)
class ExportPackage:
    export_id: str
    project_id: str
    story_id: str
    render_id: str
    channel_preset_id: str
    platform: str
    status: str
    video_artifact_id: str
    thumbnail_artifact_id: str | None = None
    caption: str = ""
    title: str = ""
    description: str = ""
    hashtags: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in EXPORT_STATUSES:
            raise ValueError(f"invalid export status: {self.status}")
        if self.platform not in PLATFORMS:
            raise ValueError(f"invalid platform: {self.platform}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "export_id": self.export_id,
            "project_id": self.project_id,
            "story_id": self.story_id,
            "render_id": self.render_id,
            "channel_preset_id": self.channel_preset_id,
            "platform": self.platform,
            "status": self.status,
            "video_artifact_id": self.video_artifact_id,
            "thumbnail_artifact_id": self.thumbnail_artifact_id,
            "caption": self.caption,
            "title": self.title,
            "description": self.description,
            "hashtags": list(self.hashtags),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }