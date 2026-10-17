"""Canonical EDL runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .edit_brief import FORMAT_TREATMENTS

EDL_STATUSES = frozenset({"DRAFT", "GENERATING", "READY", "FAILED", "ARCHIVED"})


@dataclass(frozen=True)
class CanonicalEDL:
    edl_id: str
    project_id: str
    story_id: str
    edit_brief_id: str
    artifact_id: str | None
    format_treatment: str
    status: str
    target_duration: int | None = None
    estimated_duration: float | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in EDL_STATUSES:
            raise ValueError(f"invalid edl status: {self.status}")
        if self.format_treatment not in FORMAT_TREATMENTS:
            raise ValueError(f"invalid format treatment: {self.format_treatment}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "edl_id": self.edl_id,
            "project_id": self.project_id,
            "story_id": self.story_id,
            "edit_brief_id": self.edit_brief_id,
            "artifact_id": self.artifact_id,
            "format_treatment": self.format_treatment,
            "status": self.status,
            "target_duration": self.target_duration,
            "estimated_duration": self.estimated_duration,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }