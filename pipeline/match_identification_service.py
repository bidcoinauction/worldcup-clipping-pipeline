"""Cheap-first match identification for research-first workflows."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .runtime_service import get_artifact


_TEAM_ALIASES = {
    "germany": "Germany",
    "italy": "Italy",
    "portugal": "Portugal",
    "netherlands": "Netherlands",
    "holland": "Netherlands",
    "mexico": "Mexico",
    "south africa": "South Africa",
}


@dataclass(frozen=True)
class MatchCandidate:
    sport: str = "football"
    team_a: str = ""
    team_b: str = ""
    competition: str = ""
    stage: str = ""
    date: str = ""
    season: str = ""
    venue: str = ""
    score: str = ""
    confidence: str = "LOW"
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["evidence"] = list(self.evidence)
        return data


def _tokens(text: str) -> list[str]:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", " ", text.lower())
    return [part for part in cleaned.split() if part]


def _teams_from_text(text: str) -> list[str]:
    normalized = " ".join(_tokens(text))
    found: list[tuple[int, str]] = []
    for raw, canonical in _TEAM_ALIASES.items():
        match = re.search(rf"\b{re.escape(raw)}\b", normalized)
        if match and canonical not in [item[1] for item in found]:
            found.append((match.start(), canonical))
    return [team for _pos, team in sorted(found, key=lambda item: item[0])]


def _year_from_text(text: str) -> str:
    for token in _tokens(text):
        if re.fullmatch(r"19\d{2}|20\d{2}", token):
            return token
    return ""


def identify_match_from_text(text: str, *, user_hint: str | None = None) -> MatchCandidate:
    evidence: list[dict[str, Any]] = []
    combined = f"{text} {user_hint or ''}".strip()
    teams = _teams_from_text(combined)
    year = _year_from_text(combined)
    if text:
        evidence.append({"type": "filename_or_metadata", "value": text})
    if user_hint:
        evidence.append({"type": "user_hint", "value": user_hint})
    lower = combined.lower()
    competition = "2006 FIFA World Cup" if year == "2006" and ("world" in lower or len(teams) >= 2) else ""
    confidence = "LOW"
    if len(teams) >= 2 and year:
        confidence = "HIGH" if user_hint or competition else "MEDIUM"
    elif len(teams) >= 2 or year:
        confidence = "MEDIUM"
    partial = "part" in lower or re.search(r"\bpt\s*\d+\b", lower) is not None
    if partial:
        evidence.append({"type": "source_scope", "value": "partial_source_hint"})
    return MatchCandidate(
        team_a=teams[0] if len(teams) >= 1 else "",
        team_b=teams[1] if len(teams) >= 2 else "",
        competition=competition,
        season=year,
        confidence=confidence,
        evidence=evidence,
    )


class MatchIdentificationService:
    """Identify candidate match identity using cheap deterministic evidence first."""

    def identify(self, project_id: str, source_artifact_id: str, *, user_hint: str | None = None) -> list[MatchCandidate]:
        artifact = get_artifact(source_artifact_id)
        if artifact is None:
            return [identify_match_from_text(user_hint or "", user_hint=user_hint)]
        path = Path(artifact.path)
        parts = [path.name, path.parent.name]
        metadata_title = artifact.metadata.get("title") or artifact.metadata.get("display_name") or ""
        if metadata_title:
            parts.append(str(metadata_title))
        candidate = identify_match_from_text(" ".join(parts), user_hint=user_hint)
        return [candidate]
