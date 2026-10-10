"""High-level research-first orchestration around canonical runtime models."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .match_identification_service import MatchCandidate, MatchIdentificationService
from .research_provider import ResearchProvider, identity_cache_key
from .research_service import persist_research_fixture, seed_moments_from_research
from .runtime_service import get_artifact, list_project_research, update_project_analysis_strategy


@dataclass(frozen=True)
class SourceCoverage:
    source_duration_seconds: float | None = None
    kickoff_media_offset_seconds: float | None = None
    estimated_match_coverage_start_minute: float | None = None
    estimated_match_coverage_end_minute: float | None = None
    coverage_confidence: str = "LOW"
    partial_source: bool = False
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["evidence"] = list(self.evidence)
        return data


class ResearchFirstOrchestrator:
    def __init__(self, *, identifier: MatchIdentificationService | None = None, provider: ResearchProvider | None = None):
        self.identifier = identifier or MatchIdentificationService()
        self.provider = provider

    def inspect_source(self, source_artifact_id: str) -> SourceCoverage:
        artifact = get_artifact(source_artifact_id)
        if artifact is None:
            return SourceCoverage(evidence=[{"type": "missing_source_artifact"}])
        duration = artifact.metadata.get("duration_seconds") or artifact.metadata.get("duration")
        try:
            duration_f = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_f = None
        name = Path(artifact.path).name.lower()
        partial = "part" in name
        return SourceCoverage(source_duration_seconds=duration_f, partial_source=partial, coverage_confidence="MEDIUM" if duration_f else "LOW", evidence=[{"type": "filename", "value": Path(artifact.path).name}])

    def run(self, project_id: str, source_artifact_id: str, *, user_hint: str | None = None,
            confirmed_candidate: MatchCandidate | None = None,
            coverage: SourceCoverage | None = None) -> dict[str, Any]:
        update_project_analysis_strategy(project_id, "RESEARCH_FIRST")
        candidates = self.identifier.identify(project_id, source_artifact_id, user_hint=user_hint)
        candidate = confirmed_candidate or (candidates[0] if candidates else MatchCandidate())
        if candidate.confidence == "LOW" and confirmed_candidate is None:
            return {"ok": False, "status": "NEEDS_HINT", "candidates": [c.to_dict() for c in candidates], "full_match_transcription_used": False, "full_match_detection_used": False}
        if candidate.confidence == "MEDIUM" and confirmed_candidate is None:
            return {"ok": False, "status": "NEEDS_CONFIRMATION", "candidates": [c.to_dict() for c in candidates], "full_match_transcription_used": False, "full_match_detection_used": False}
        if self.provider is None:
            return {"ok": False, "status": "RESEARCH_PROVIDER_NOT_CONFIGURED", "candidate": candidate.to_dict(), "full_match_transcription_used": False, "full_match_detection_used": False}
        provider_result = self.provider.research(candidate)
        if not provider_result.ok or not provider_result.fixture:
            return {"ok": False, "status": provider_result.readiness, "candidate": candidate.to_dict(), "error": provider_result.error, "full_match_transcription_used": False, "full_match_detection_used": False}
        cache_key = provider_result.cache_key or identity_cache_key(candidate)
        existing = [research for research in list_project_research(project_id) if (research.metadata or {}).get("research_cache_key") == cache_key]
        if existing:
            research = existing[-1]
            events = []
        else:
            fixture = dict(provider_result.fixture)
            fixture.setdefault("metadata", {})
            fixture["metadata"] = {**fixture["metadata"], "research_cache_key": cache_key}
            research, events = persist_research_fixture(project_id, fixture, source_artifact_id=source_artifact_id)
        cov = coverage or self.inspect_source(source_artifact_id)
        moments = seed_moments_from_research(
            research.research_id,
            kickoff_media_offset_seconds=cov.kickoff_media_offset_seconds,
            source_duration_seconds=cov.source_duration_seconds,
        )
        return {
            "ok": True,
            "status": "MOMENTS_READY" if moments else "RESEARCH_READY",
            "candidate": candidate.to_dict(),
            "research_id": research.research_id,
            "event_count": len(events) if events else None,
            "moment_count": len(moments),
            "coverage": cov.to_dict(),
            "research_cache_key": cache_key,
            "full_match_transcription_used": False,
            "full_match_detection_used": False,
        }
