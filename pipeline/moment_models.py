"""Canonical sport-agnostic Moment model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


UNIVERSAL_EVENT_TYPES = frozenset({
    "SCORE",
    "ATTEMPT",
    "DEFENSIVE_PLAY",
    "FOUL",
    "PENALTY",
    "CARD",
    "TURNOVER",
    "SAVE",
    "CELEBRATION",
    "CROWD_REACTION",
    "CONFRONTATION",
    "TACTICAL_SHIFT",
    "MOMENTUM_SHIFT",
    "OTHER",
})

REVIEW_STATES = frozenset({"UNREVIEWED", "KEEP", "REJECT", "STRONG", "MUST_USE"})

EMOTIONS = frozenset({
    "TENSION",
    "RELEASE",
    "CELEBRATION",
    "SHOCK",
    "ANGER",
    "CONFLICT",
    "RELIEF",
    "DESPAIR",
    "DOMINANCE",
    "CHAOS",
    "ANTICIPATION",
})

RELATION_TYPES = frozenset({
    "CAUSES",
    "ESCALATES",
    "CALLBACK_TO",
    "CONTRASTS",
    "RESPONDS_TO",
    "LEADS_TO",
    "SAME_SEQUENCE",
})


def _validate_weight(value: float | None) -> None:
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"invalid weight: {value}")


def _validate_confidence(value: float | None) -> None:
    if value is not None and not (0.0 <= value <= 1.0):
        raise ValueError(f"invalid confidence: {value}")


@dataclass(frozen=True)
class Participant:
    participant_type: str | None = None
    name: str | None = None
    role: str | None = None
    team: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "participant_type": self.participant_type,
            "name": self.name,
            "role": self.role,
            "team": self.team,
        }


@dataclass(frozen=True)
class Moment:
    moment_id: str
    project_id: str
    source_artifact_id: str | None
    sport: str
    universal_event_type: str
    sport_event_type: str
    start_seconds: float
    peak_seconds: float | None
    end_seconds: float
    participants: list[Participant] = field(default_factory=list)
    team: str | None = None
    signals: dict[str, float | None] = field(default_factory=dict)
    emotion: list[str] = field(default_factory=list)
    importance: float | None = None
    confidence: float | None = None
    review_state: str = "UNREVIEWED"
    reviewed_at: str | None = None
    reviewed_by: str | None = None
    origin_moment_id: str | None = None
    origin_project_id: str | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.universal_event_type not in UNIVERSAL_EVENT_TYPES:
            raise ValueError(f"invalid universal_event_type: {self.universal_event_type}")
        if self.review_state not in REVIEW_STATES:
            raise ValueError(f"invalid review_state: {self.review_state}")
        if self.start_seconds < 0 or self.end_seconds < 0:
            raise ValueError("moment timestamps must not be negative")
        if self.end_seconds < self.start_seconds:
            raise ValueError("moment end_seconds must be greater than or equal to start_seconds")
        if self.peak_seconds is not None and self.peak_seconds < 0:
            raise ValueError("moment peak_seconds must not be negative")
        invalid_emotions = [value for value in self.emotion if value not in EMOTIONS]
        if invalid_emotions:
            raise ValueError(f"invalid emotion value: {invalid_emotions[0]}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "moment_id": self.moment_id,
            "project_id": self.project_id,
            "source_artifact_id": self.source_artifact_id,
            "sport": self.sport,
            "universal_event_type": self.universal_event_type,
            "sport_event_type": self.sport_event_type,
            "start_seconds": self.start_seconds,
            "peak_seconds": self.peak_seconds,
            "end_seconds": self.end_seconds,
            "participants": [participant.to_dict() for participant in self.participants],
            "team": self.team,
            "signals": dict(self.signals),
            "emotion": list(self.emotion),
            "importance": self.importance,
            "confidence": self.confidence,
            "review_state": self.review_state,
            "reviewed_at": self.reviewed_at,
            "reviewed_by": self.reviewed_by,
            "origin_moment_id": self.origin_moment_id,
            "origin_project_id": self.origin_project_id,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class MomentRelation:
    relation_id: str
    project_id: str
    source_moment_id: str
    target_moment_id: str
    relation_type: str
    weight: float | None = None
    confidence: float | None = None
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.relation_type not in RELATION_TYPES:
            raise ValueError(f"invalid relation type: {self.relation_type}")
        if self.source_moment_id == self.target_moment_id:
            raise ValueError("self-relations are not allowed")
        _validate_weight(self.weight)
        _validate_confidence(self.confidence)

    def to_dict(self) -> dict[str, Any]:
        return {
            "relation_id": self.relation_id,
            "project_id": self.project_id,
            "source_moment_id": self.source_moment_id,
            "target_moment_id": self.target_moment_id,
            "relation_type": self.relation_type,
            "weight": self.weight,
            "confidence": self.confidence,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }
