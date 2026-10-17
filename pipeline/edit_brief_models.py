"""Canonical Edit Brief runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .edit_brief import FORMAT_TREATMENTS

EDIT_BRIEF_STATUSES = frozenset({"DRAFT", "GENERATING", "READY", "FAILED", "ARCHIVED"})


@dataclass(frozen=True)
class CanonicalEditBrief:
    edit_brief_id: str
    project_id: str
    story_id: str
    artifact_id: str | None
    format_treatment: str
    status: str
    editorial_intent: str = ""
    target_duration: int | None = None
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in EDIT_BRIEF_STATUSES:
            raise ValueError(f"invalid edit brief status: {self.status}")
        if self.format_treatment not in FORMAT_TREATMENTS:
            raise ValueError(f"invalid format treatment: {self.format_treatment}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "edit_brief_id": self.edit_brief_id,
            "project_id": self.project_id,
            "story_id": self.story_id,
            "artifact_id": self.artifact_id,
            "format_treatment": self.format_treatment,
            "status": self.status,
            "editorial_intent": self.editorial_intent,
            "target_duration": self.target_duration,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }