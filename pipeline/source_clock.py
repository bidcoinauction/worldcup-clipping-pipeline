from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


SOURCE_CLOCK_VERSION = "source_clock_v1"
FIRST_HALF = "FIRST_HALF"
SECOND_HALF = "SECOND_HALF"
EXTRA_TIME_FIRST = "EXTRA_TIME_FIRST"
EXTRA_TIME_SECOND = "EXTRA_TIME_SECOND"
LOW = "LOW"
MEDIUM = "MEDIUM"
HIGH = "HIGH"
VERIFIED = "VERIFIED"


@dataclass(frozen=True)
class SourceClockSegment:
    segment_type: str
    match_clock_start_seconds: float
    source_time_start: float
    source_time_end: float | None = None
    confidence: str = LOW
    method: str = "heuristic"
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confirmed_by_operator: bool = False
    updated_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def segment_from_dict(data: dict[str, Any]) -> SourceClockSegment:
    return SourceClockSegment(
        segment_type=str(data.get("segment_type") or data.get("anchor_type") or FIRST_HALF),
        match_clock_start_seconds=float(data.get("match_clock_start_seconds") or data.get("match_time_seconds") or 0.0),
        source_time_start=float(data.get("source_time_start") if data.get("source_time_start") is not None else data.get("media_time_seconds") or 0.0),
        source_time_end=float(data["source_time_end"]) if data.get("source_time_end") is not None else None,
        confidence=str(data.get("confidence") or LOW),
        method=str(data.get("method") or data.get("validation_method") or "heuristic"),
        evidence=list(data.get("evidence") or []),
        confirmed_by_operator=bool(data.get("confirmed_by_operator")),
        updated_at=str(data.get("updated_at") or ""),
    )


def source_clock_payload(segments: list[SourceClockSegment], *, candidates: list[dict[str, Any]] | None = None, status: str = "READY", review: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "version": SOURCE_CLOCK_VERSION,
        "status": status,
        "segments": [segment.to_dict() for segment in segments],
        "anchor_candidates": list(candidates or []),
        "review": dict(review or {}),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }


def segments_from_payload(payload: dict[str, Any] | None) -> list[SourceClockSegment]:
    if not payload:
        return []
    return [segment_from_dict(row) for row in payload.get("segments") or []]


def segment_for_event(match_seconds: float, segments: list[SourceClockSegment]) -> SourceClockSegment | None:
    ordered = sorted(segments, key=lambda row: row.match_clock_start_seconds)
    selected = None
    for segment in ordered:
        if match_seconds >= segment.match_clock_start_seconds:
            selected = segment
    return selected


def estimate_source_time(match_seconds: float, segments: list[SourceClockSegment]) -> tuple[float | None, SourceClockSegment | None]:
    segment = segment_for_event(match_seconds, segments)
    if segment is None:
        return None, None
    offset = match_seconds - segment.match_clock_start_seconds
    return segment.source_time_start + offset, segment


def event_match_seconds(event: Any) -> float:
    return float(event.match_minute * 60 + (event.match_second_optional or 0))


def event_position_provenance(event: Any) -> dict[str, Any]:
    metadata = getattr(event, "metadata", {}) or {}
    return {
        "display": metadata.get("event_position") or metadata.get("event_position_label") or f"{event.match_minute}'",
        "match_minute": event.match_minute,
        "match_second_optional": event.match_second_optional,
        "estimated_match_seconds": event_match_seconds(event),
    }


def improve_anchor_from_candidates(candidates: list[dict[str, Any]], *, tolerance_seconds: float = 8.0) -> SourceClockSegment | None:
    strong = [row for row in candidates if row.get("segment_type") == FIRST_HALF and row.get("confidence") in {MEDIUM, HIGH} and row.get("candidate_source_time_start") is not None]
    if len(strong) < 2:
        return None
    values = [float(row["candidate_source_time_start"]) for row in strong]
    if max(values) - min(values) > tolerance_seconds:
        return None
    averaged = sum(values) / len(values)
    return SourceClockSegment(
        segment_type=FIRST_HALF,
        match_clock_start_seconds=0.0,
        source_time_start=averaged,
        confidence=MEDIUM,
        method="multiple_aligned_event_consistency",
        evidence=[{"type": "aligned_event_anchor_candidates", "count": len(strong), "values": values}],
    )
