"""Research-first sports intelligence boundary."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import replace
from pathlib import Path
from typing import Any

from .moment_models import Moment, Participant
from .research_models import MatchIdentity, MatchResearch, MediaAlignment, ResearchEvent
from .runtime_service import (
    get_project,
    list_project_moments,
    list_project_artifacts,
    list_research_events,
    upsert_match_research,
    upsert_moment,
    upsert_research_event,
)


ANALYSIS_STRATEGIES = ("TRANSCRIPT_FIRST", "RESEARCH_FIRST", "HYBRID")
AVAILABILITY_STATUSES = ("AVAILABLE", "OUTSIDE_SOURCE", "UNKNOWN")

SEARCH_WINDOW_DEFAULTS = {
    "SCORE": (-120, 180),
    "CARD": (-60, 90),
    "ATTEMPT": (-45, 60),
    "SAVE": (-45, 60),
    "CELEBRATION": (-30, 120),
}


def _stable_id(prefix: str, *parts: object) -> str:
    raw = "|".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha1(raw.encode('utf-8')).hexdigest()[:16]}"


def identify_match(data: dict[str, Any]) -> MatchIdentity:
    return MatchIdentity(
        sport=str(data.get("sport") or "football"),
        competition=str(data.get("competition") or ""),
        date=str(data.get("match_date") or data.get("date") or ""),
        home_team=str(data.get("home_team") or ""),
        away_team=str(data.get("away_team") or ""),
        season=data.get("season"),
        leg=data.get("leg"),
        venue=data.get("venue"),
    )


def research_id_for(project_id: str, identity: MatchIdentity) -> str:
    return _stable_id(
        "research",
        project_id,
        identity.sport,
        identity.competition,
        identity.date,
        identity.home_team,
        identity.away_team,
    )


def research_event_id_for(research_id: str, event: dict[str, Any]) -> str:
    return _stable_id(
        "revt",
        research_id,
        event.get("match_minute"),
        event.get("match_second_optional"),
        event.get("universal_event_type"),
        event.get("team"),
        event.get("headline"),
    )


def load_research_fixture(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def persist_research_fixture(project_id: str, fixture: dict[str, Any], *, source_artifact_id: str | None = None,
                             db_path: str | Path | None = None) -> tuple[MatchResearch, list[ResearchEvent]]:
    identity = identify_match(fixture)
    research = MatchResearch(
        research_id=research_id_for(project_id, identity),
        project_id=project_id,
        source_artifact_id=source_artifact_id,
        sport=identity.sport,
        competition=identity.competition,
        season=identity.season,
        match_date=identity.date,
        home_team=identity.home_team,
        away_team=identity.away_team,
        home_score=fixture.get("home_score"),
        away_score=fixture.get("away_score"),
        venue=str(fixture.get("venue") or ""),
        stage=str(fixture.get("stage") or ""),
        importance=fixture.get("importance"),
        summary=str(fixture.get("summary") or ""),
        stakes=str(fixture.get("stakes") or ""),
        historical_context=str(fixture.get("historical_context") or ""),
        sources=list(fixture.get("sources") or []),
        metadata=dict(fixture.get("metadata") or {}),
    )
    saved_research = upsert_match_research(research, db_path=db_path)
    saved_events: list[ResearchEvent] = []
    for item in fixture.get("events") or []:
        event = ResearchEvent(
            event_id=research_event_id_for(saved_research.research_id, item),
            research_id=saved_research.research_id,
            project_id=project_id,
            match_minute=int(item["match_minute"]),
            match_second_optional=item.get("match_second_optional"),
            universal_event_type=str(item["universal_event_type"]),
            sport_event_type=str(item.get("sport_event_type") or item["universal_event_type"]),
            team=item.get("team"),
            participants=list(item.get("participants") or []),
            headline=str(item.get("headline") or ""),
            description=str(item.get("description") or ""),
            score_before=str(item.get("score_before") or ""),
            score_after=str(item.get("score_after") or ""),
            importance=item.get("importance"),
            confidence=item.get("confidence"),
            source_refs=list(item.get("source_refs") or []),
            metadata=dict(item.get("metadata") or {}),
        )
        saved_events.append(upsert_research_event(event, db_path=db_path))
    return saved_research, saved_events


def estimate_media_alignment(event: ResearchEvent, *, kickoff_media_offset_seconds: float | None = None,
                             halftime_duration_seconds: float | None = None,
                             source_duration_seconds: float | None = None) -> MediaAlignment:
    if kickoff_media_offset_seconds is None:
        return MediaAlignment(
            research_event_id=event.event_id,
            match_minute=event.match_minute,
            estimated_media_time=None,
            alignment_status="UNALIGNED",
            alignment_confidence=None,
            metadata={"availability_status": "UNKNOWN", "alignment_reason": "kickoff_offset_missing"},
        )
    match_seconds = event.match_minute * 60 + (event.match_second_optional or 0)
    media_time = float(kickoff_media_offset_seconds) + match_seconds
    if halftime_duration_seconds and event.match_minute >= 45:
        media_time += float(halftime_duration_seconds)
    before, after = event_search_window(event.universal_event_type)
    search_start = max(0.0, media_time + before)
    search_end = max(0.0, media_time + after)
    metadata: dict[str, Any] = {
        "method": "kickoff_offset",
        "availability_status": "AVAILABLE",
        "alignment_reason": "estimated_from_kickoff_offset",
        "kickoff_media_offset_seconds": float(kickoff_media_offset_seconds),
    }
    if source_duration_seconds is not None:
        source_duration = float(source_duration_seconds)
        available_match_duration = max(0.0, source_duration - float(kickoff_media_offset_seconds))
        metadata.update({
            "source_duration_seconds": source_duration,
            "estimated_match_coverage_start_minute": 0.0,
            "estimated_match_coverage_end_minute": available_match_duration / 60.0,
        })
        if media_time > source_duration:
            metadata["availability_status"] = "OUTSIDE_SOURCE"
            metadata["alignment_reason"] = "estimated_media_time_outside_source_duration"
    return MediaAlignment(
        research_event_id=event.event_id,
        match_minute=event.match_minute,
        estimated_media_time=media_time,
        alignment_status="ESTIMATED",
        alignment_confidence=0.5,
        search_window_start=search_start,
        search_window_end=search_end,
        metadata=metadata,
    )


def event_search_window(universal_event_type: str) -> tuple[int, int]:
    return SEARCH_WINDOW_DEFAULTS.get(universal_event_type, (-60, 90))


def _moment_id_for(project_id: str, event_id: str) -> str:
    return _stable_id("mom", project_id, event_id)


def seed_moments_from_research(research_id: str, *, kickoff_media_offset_seconds: float | None = None,
                                halftime_duration_seconds: float | None = None,
                                source_duration_seconds: float | None = None,
                                db_path: str | Path | None = None) -> list[Moment]:
    events = list_research_events(research_id, db_path=db_path)
    seeded: list[Moment] = []
    for event in events:
        alignment = estimate_media_alignment(
            event,
            kickoff_media_offset_seconds=kickoff_media_offset_seconds,
            halftime_duration_seconds=halftime_duration_seconds,
            source_duration_seconds=source_duration_seconds,
        )
        availability_status = alignment.metadata.get("availability_status", "UNKNOWN")
        event_metadata = dict(event.metadata)
        event_metadata["source_availability"] = {
            "availability_status": availability_status,
            "alignment_status": alignment.alignment_status,
            "alignment_reason": alignment.metadata.get("alignment_reason"),
            "estimated_media_time": alignment.estimated_media_time,
            "source_duration_seconds": alignment.metadata.get("source_duration_seconds"),
            "kickoff_media_offset_seconds": alignment.metadata.get("kickoff_media_offset_seconds"),
            "estimated_match_coverage_start_minute": alignment.metadata.get("estimated_match_coverage_start_minute"),
            "estimated_match_coverage_end_minute": alignment.metadata.get("estimated_match_coverage_end_minute"),
        }
        upsert_research_event(replace(event, metadata=event_metadata), db_path=db_path)
        if alignment.estimated_media_time is None:
            start = peak = end = 0.0
        else:
            start = float(alignment.search_window_start if alignment.search_window_start is not None else alignment.estimated_media_time)
            end = float(alignment.search_window_end if alignment.search_window_end is not None else alignment.estimated_media_time)
            peak = float(alignment.estimated_media_time)
        participants = [Participant(name=p.get("name"), role=p.get("role"), team=p.get("team"), participant_type=p.get("participant_type"))
                        for p in event.participants if isinstance(p, dict)]
        moment = Moment(
            moment_id=_moment_id_for(event.project_id, event.event_id),
            project_id=event.project_id,
            source_artifact_id=None,
            sport="football",
            universal_event_type=event.universal_event_type,
            sport_event_type=event.sport_event_type,
            start_seconds=start,
            peak_seconds=peak,
            end_seconds=end,
            participants=participants,
            team=event.team,
            importance=event.importance,
            confidence=event.confidence,
            review_state="UNREVIEWED",
            metadata={
                "origin": "research",
                "research_id": research_id,
                "research_event_id": event.event_id,
                "match_minute": event.match_minute,
                "match_second_optional": event.match_second_optional,
                "estimated_media_time": alignment.estimated_media_time,
                "alignment_status": alignment.alignment_status,
                "alignment_confidence": alignment.alignment_confidence,
                "alignment_reason": alignment.metadata.get("alignment_reason"),
                "availability_status": availability_status,
                "source_duration_seconds": alignment.metadata.get("source_duration_seconds"),
                "kickoff_media_offset_seconds": alignment.metadata.get("kickoff_media_offset_seconds"),
                "estimated_match_coverage_start_minute": alignment.metadata.get("estimated_match_coverage_start_minute"),
                "estimated_match_coverage_end_minute": alignment.metadata.get("estimated_match_coverage_end_minute"),
                "search_window": {
                    "start": alignment.search_window_start,
                    "end": alignment.search_window_end,
                },
                "out_of_source": availability_status == "OUTSIDE_SOURCE",
                "evidence": {"research": True, "transcript": False, "audio": False, "visual": False},
            },
        )
        seeded.append(upsert_moment(moment, db_path=db_path))
    return seeded


def list_available_research_moments(project_id: str, *, research_id: str | None = None,
                                    db_path: str | Path | None = None) -> list[Moment]:
    """Return research-origin moments available as footage for this source."""
    moments = [m for m in list_project_moments(project_id, db_path=db_path) if m.metadata.get("origin") == "research"]
    if research_id is not None:
        moments = [m for m in moments if m.metadata.get("research_id") == research_id]
    return [
        m for m in moments
        if m.metadata.get("availability_status", "AVAILABLE") == "AVAILABLE"
        and m.metadata.get("alignment_status") in {"ALIGNED", "VERIFIED"}
    ]


_TIMESTAMP_RE = re.compile(r"\[(\d+(?:\.\d+)?)s\s*-\s*(\d+(?:\.\d+)?)s\](.*)")


def get_transcript_window(project_id: str, start_time: float, end_time: float, *, db_path: str | Path | None = None) -> str:
    project = get_project(project_id, db_path=db_path)
    if project is None:
        return ""
    transcript_artifacts = list_project_artifacts(project_id, artifact_type="transcript", db_path=db_path)
    if not transcript_artifacts:
        return ""
    path = Path(transcript_artifacts[-1].path)
    if not path.exists():
        return ""
    selected: list[str] = []
    for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        match = _TIMESTAMP_RE.match(line.strip())
        if not match:
            continue
        line_start = float(match.group(1))
        line_end = float(match.group(2))
        if line_end >= start_time and line_start <= end_time:
            selected.append(line)
    return "\n".join(selected)
