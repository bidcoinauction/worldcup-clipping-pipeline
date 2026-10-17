"""Canonical Story runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .story_engine import ARCHETYPES, NARRATIVE_ROLES, RECOMMENDED_FORMATS

STORY_STATUSES = frozenset({"DRAFT", "SUGGESTED", "APPROVED", "REJECTED", "ARCHIVED"})


def _validate_archetype(archetype: str) -> None:
    if archetype not in ARCHETYPES:
        raise ValueError(f"invalid story archetype: {archetype}")


def _validate_status(status: str) -> None:
    if status not in STORY_STATUSES:
        raise ValueError(f"invalid story status: {status}")


def _validate_role(role: str) -> None:
    if role not in NARRATIVE_ROLES:
        raise ValueError(f"invalid narrative role: {role}")


@dataclass(frozen=True)
class Story:
    story_id: str
    project_id: str
    title: str
    summary: str = ""
    archetype: str = ""
    status: str = "SUGGESTED"
    hook: str = ""
    emotional_arc: list[str] = field(default_factory=list)
    estimated_duration: int | None = None
    recommended_formats: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_status(self.status)
        if self.archetype:
            _validate_archetype(self.archetype)

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_id": self.story_id,
            "project_id": self.project_id,
            "title": self.title,
            "summary": self.summary,
            "archetype": self.archetype,
            "status": self.status,
            "hook": self.hook,
            "emotional_arc": list(self.emotional_arc),
            "estimated_duration": self.estimated_duration,
            "recommended_formats": list(self.recommended_formats),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class StoryMoment:
    story_moment_id: str
    story_id: str
    moment_id: str
    narrative_role: str
    sequence_order: int
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        _validate_role(self.narrative_role)

    def to_dict(self) -> dict[str, Any]:
        return {
            "story_moment_id": self.story_moment_id,
            "story_id": self.story_id,
            "moment_id": self.moment_id,
            "narrative_role": self.narrative_role,
            "sequence_order": self.sequence_order,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }