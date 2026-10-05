"""Adapters from existing detector output to canonical runtime Moments."""

from __future__ import annotations

import hashlib
from typing import Any

from .moment_models import EMOTIONS, Moment, Participant


FOOTBALL_EVENT_MAP = {
    "GOAL": "SCORE",
    "PENALTY_GOAL": "SCORE",
    "SHOT": "ATTEMPT",
    "SHOT_ON_TARGET": "ATTEMPT",
    "CHANCE": "ATTEMPT",
    "SAVE": "SAVE",
    "FOUL": "FOUL",
    "PENALTY": "PENALTY",
    "YELLOW_CARD": "CARD",
    "RED_CARD": "CARD",
    "CARD": "CARD",
    "CELEBRATION": "CELEBRATION",
    "CROWD_SPIKE": "CROWD_REACTION",
    "CROWD_REACTION": "CROWD_REACTION",
    "FIGHT": "CONFRONTATION",
    "ALTERCATION": "CONFRONTATION",
    "TACTICAL_SHIFT": "TACTICAL_SHIFT",
    "MOMENTUM_SHIFT": "MOMENTUM_SHIFT",
}

_SIGNAL_KEYS = ("audio", "commentary", "visual", "metadata")


def parse_timestamp_seconds(value: Any) -> float:
    if isinstance(value, bool) or value in (None, ""):
        raise ValueError("timestamp is required")
    if isinstance(value, (int, float)):
        seconds = float(value)
    elif isinstance(value, str):
        raw = value.strip()
        if not raw:
            raise ValueError("timestamp is required")
        try:
            seconds = float(raw)
        except ValueError:
            parts = raw.split(":")
            if not 2 <= len(parts) <= 3:
                raise ValueError(f"invalid timestamp: {value!r}") from None
            try:
                numbers = [float(part) for part in parts]
            except ValueError:
                raise ValueError(f"invalid timestamp: {value!r}") from None
            if len(numbers) == 2:
                minutes, secs = numbers
                seconds = minutes * 60 + secs
            else:
                hours, minutes, secs = numbers
                seconds = hours * 3600 + minutes * 60 + secs
    else:
        raise ValueError(f"invalid timestamp type: {type(value).__name__}")
    if seconds < 0:
        raise ValueError("timestamp must not be negative")
    return seconds


def _normalize_event_type(raw: Any) -> str:
    value = str(raw or "OTHER").strip().upper().replace(" ", "_").replace("-", "_")
    return value or "OTHER"


def football_universal_event_type(sport_event_type: str) -> str:
    return FOOTBALL_EVENT_MAP.get(_normalize_event_type(sport_event_type), "OTHER")


def _score(value: Any) -> float | None:
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if number < 0:
        return None
    if number > 1.0 and number <= 100.0:
        number = number / 100.0
    if number > 1.0:
        return None
    return number


def _participants(row: dict[str, Any]) -> list[Participant]:
    raw = row.get("participants")
    if isinstance(raw, list):
        participants = []
        for item in raw:
            if not isinstance(item, dict):
                continue
            participants.append(Participant(
                participant_type=item.get("participant_type") or item.get("type"),
                name=item.get("name"),
                role=item.get("role"),
                team=item.get("team"),
            ))
        return participants
    player = row.get("player") or row.get("participant")
    if isinstance(player, str) and player.strip():
        return [Participant(participant_type="PLAYER", name=player.strip(), role=row.get("role"), team=row.get("team"))]
    return []


def _signals(row: dict[str, Any]) -> dict[str, float | None]:
    raw = row.get("signals") if isinstance(row.get("signals"), dict) else {}
    return {key: _score(raw.get(key) if raw else row.get(f"{key}_signal")) for key in _SIGNAL_KEYS}


def _emotion(row: dict[str, Any]) -> list[str]:
    raw = row.get("emotion") or row.get("emotions") or row.get("emotional_angle")
    values = raw if isinstance(raw, list) else [raw]
    result = []
    for value in values:
        normalized = str(value or "").strip().upper().replace(" ", "_").replace("-", "_")
        if normalized in EMOTIONS and normalized not in result:
            result.append(normalized)
    return result


def _moment_id(project_id: str, source_artifact_id: str | None, row: dict[str, Any], sport_event_type: str, start: float) -> str:
    stable_id = row.get("event_id") or row.get("clip_id") or row.get("moment_id")
    raw = f"{project_id}|{source_artifact_id or ''}|{stable_id or ''}|{sport_event_type}|{start:.3f}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"mom_{digest}"


def adapt_detection_to_moments(
    rows: list[dict[str, Any]],
    *,
    project_id: str,
    source_artifact_id: str | None,
    sport: str = "football",
    created_at: str = "",
) -> list[Moment]:
    moments: list[Moment] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        start = parse_timestamp_seconds(row.get("start_time") or row.get("start") or row.get("start_seconds"))
        end = parse_timestamp_seconds(row.get("end_time") or row.get("end") or row.get("end_seconds"))
        if end < start:
            raise ValueError("moment end_seconds must be greater than or equal to start_seconds")
        peak_raw = row.get("peak_time") or row.get("peak") or row.get("peak_seconds")
        peak = parse_timestamp_seconds(peak_raw) if peak_raw not in (None, "") else None
        sport_event_type = _normalize_event_type(row.get("event_type") or row.get("category"))
        universal = football_universal_event_type(sport_event_type) if sport == "football" else "OTHER"
        confidence = _score(row.get("confidence"))
        importance = _score(row.get("importance") if row.get("importance") not in (None, "") else row.get("virality_score"))
        moments.append(Moment(
            moment_id=_moment_id(project_id, source_artifact_id, row, sport_event_type, start),
            project_id=project_id,
            source_artifact_id=source_artifact_id,
            sport=sport,
            universal_event_type=universal,
            sport_event_type=sport_event_type,
            start_seconds=start,
            peak_seconds=peak,
            end_seconds=end,
            participants=_participants(row),
            team=row.get("team") if isinstance(row.get("team"), str) else None,
            signals=_signals(row),
            emotion=_emotion(row),
            importance=importance,
            confidence=confidence,
            review_state="UNREVIEWED",
            created_at=created_at,
            metadata={
                "original_event_id": row.get("event_id") or row.get("clip_id") or row.get("moment_id"),
                "original_category": row.get("category"),
                "original_row": dict(row),
            },
        ))
    return moments
