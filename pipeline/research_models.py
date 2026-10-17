"""Research-first sports intelligence models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .moment_models import UNIVERSAL_EVENT_TYPES


ALIGNMENT_STATUSES = frozenset({"UNALIGNED", "ESTIMATED", "ALIGNED", "VERIFIED"})


@dataclass(frozen=True)
class MatchIdentity:
    sport: str
    competition: str
    date: str
    home_team: str
    away_team: str
    season: str | None = None
    leg: str | None = None
    venue: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class MatchResearch:
    research_id: str
    project_id: str
    source_artifact_id: str | None
    sport: str
    competition: str
    season: str | None
    match_date: str
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    venue: str = ""
    stage: str = ""
    importance: float | None = None
    summary: str = ""
    stakes: str = ""
    historical_context: str = ""
    sources: list[dict[str, Any]] = field(default_factory=list)
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["sources"] = list(self.sources)
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class ResearchEvent:
    event_id: str
    research_id: str
    project_id: str
    match_minute: int
    universal_event_type: str
    sport_event_type: str
    headline: str
    match_second_optional: int | None = None
    team: str | None = None
    participants: list[dict[str, Any]] = field(default_factory=list)
    description: str = ""
    score_before: str = ""
    score_after: str = ""
    importance: float | None = None
    confidence: float | None = None
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.universal_event_type not in UNIVERSAL_EVENT_TYPES:
            raise ValueError(f"invalid universal_event_type: {self.universal_event_type}")
        if self.match_minute < 0:
            raise ValueError("match_minute must not be negative")
        if self.match_second_optional is not None and not 0 <= self.match_second_optional <= 59:
            raise ValueError("match_second_optional must be in 0..59")
        for field_name in ("importance", "confidence"):
            value = getattr(self, field_name)
            if value is not None and not 0.0 <= value <= 1.0:
                raise ValueError(f"invalid {field_name}: {value}")

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["participants"] = list(self.participants)
        data["source_refs"] = list(self.source_refs)
        data["metadata"] = dict(self.metadata)
        return data


@dataclass(frozen=True)
class MediaAlignment:
    research_event_id: str
    match_minute: int
    estimated_media_time: float | None
    alignment_status: str = "UNALIGNED"
    alignment_confidence: float | None = None
    search_window_start: float | None = None
    search_window_end: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.alignment_status not in ALIGNMENT_STATUSES:
            raise ValueError(f"invalid alignment_status: {self.alignment_status}")
        if self.alignment_confidence is not None and not 0.0 <= self.alignment_confidence <= 1.0:
            raise ValueError("alignment_confidence must be in 0..1")

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = dict(self.metadata)
        return data
