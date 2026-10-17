"""Lightweight project metadata trust resolution.

This module resolves creator-facing match identity from already-persisted local
data. It does not fetch new data, mutate project records, or migrate history.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
import re
from typing import Any


TRUSTED = "TRUSTED"
INCOMPLETE = "INCOMPLETE"
CONFLICT = "CONFLICT"
UNKNOWN = "UNKNOWN"

HIGH = "HIGH"
MEDIUM = "MEDIUM"
LOW = "LOW"


@dataclass(frozen=True)
class MetadataField:
    value: str
    source: str
    trust: str
    original_value: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def _clean(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _year(value: object) -> str:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _slug(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _norm_competition(value: object) -> str:
    text = _clean(value)
    text = re.sub(r"^(?:19|20)\d{2}\s+", "", text).strip()
    return text


def _norm_title(value: object) -> str:
    text = _slug(value)
    text = re.sub(r"\b\d{10,}\b", " ", text)
    text = re.sub(r"\b(source|raw|rf|v\d+|align|status|evidence|part\d+|first|second|half|1st|2nd|pilot)\b", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _team_set(title: str) -> set[str]:
    text = _slug(title)
    teams = set()
    aliases = {
        "argentina": ["argentina", "arg", "argc", "argcro"],
        "croatia": ["croatia", "cro", "argc"],
        "portugal": ["portugal", "por"],
        "netherlands": ["netherlands", "ned"],
        "italy": ["italy", "ita"],
        "germany": ["germany", "ger"],
        "belgium": ["belgium"],
        "egypt": ["egypt"],
        "japan": ["japan"],
    }
    tokens = set(text.split())
    compact = text.replace(" ", "")
    for team, names in aliases.items():
        if any(name in tokens or name in compact for name in names):
            teams.add(team)
    return teams


def _parse_known_match(value: object) -> tuple[str, str]:
    text = _slug(value)
    compact = text.replace(" ", "")
    year = _year(value)
    known = [
        (("argentina", "arg"), ("croatia", "cro"), "Argentina vs Croatia"),
        (("portugal", "por"), ("netherlands", "ned"), "Portugal vs Netherlands"),
        (("italy", "ita"), ("germany", "ger"), "Italy vs Germany"),
        (("germany",), ("italy",), "Germany vs Italy"),
        (("belgium",), ("egypt",), "Belgium vs Egypt"),
        (("netherlands",), ("japan",), "Netherlands vs Japan"),
    ]
    tokens = set(text.split())
    for left, right, title in known:
        if (any(name in tokens or name in compact for name in left)
                and any(name in tokens or name in compact for name in right)):
            return title, year
    cleaned = _norm_title(value)
    if cleaned and cleaned not in {"project", "source", "match"}:
        return cleaned.title(), year
    return "", year


def _add_candidate(candidates: dict[str, list[MetadataField]], key: str, value: object, source: str, trust: str, *, original: object = "") -> None:
    text = _clean(value)
    if not text:
        return
    if key == "title" and text.lower() in {"project", "source", "match", "untitled match"}:
        return
    candidates.setdefault(key, []).append(MetadataField(text, source, trust, _clean(original) or text))


def _choose(candidates: dict[str, list[MetadataField]], key: str) -> MetadataField:
    values = candidates.get(key) or []
    return values[0] if values else MetadataField("", "FALLBACK", LOW, "")


def _field_dict(fields: dict[str, MetadataField]) -> dict[str, dict[str, str]]:
    return {key: value.to_dict() for key, value in fields.items()}


def resolve_metadata(
    project: dict[str, Any],
    research: dict[str, Any] | None = None,
    intake: dict[str, Any] | None = None,
    *,
    source_paths: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve trusted display metadata without mutating persisted data."""
    research_items = (research or {}).get("research") or []
    latest = research_items[-1] if research_items else {}
    media = (intake or {}).get("media") if isinstance(intake, dict) else {}
    source_paths = source_paths or []
    candidates: dict[str, list[MetadataField]] = {}

    home = _clean(latest.get("home_team"))
    away = _clean(latest.get("away_team"))
    if home and away:
        _add_candidate(candidates, "title", f"{home} vs {away}", "RESEARCH", HIGH)
        _add_candidate(candidates, "home", home, "RESEARCH", HIGH)
        _add_candidate(candidates, "away", away, "RESEARCH", HIGH)
    _add_candidate(candidates, "competition", _norm_competition(latest.get("competition")), "RESEARCH", HIGH, original=latest.get("competition"))
    _add_candidate(candidates, "stage", latest.get("stage"), "RESEARCH", HIGH)
    _add_candidate(candidates, "date", latest.get("match_date"), "RESEARCH", HIGH)
    if _year(latest.get("competition")):
        _add_candidate(candidates, "year", _year(latest.get("competition")), "RESEARCH", HIGH, original=latest.get("competition"))
    if _year(latest.get("match_date")):
        _add_candidate(candidates, "year", _year(latest.get("match_date")), "RESEARCH", HIGH, original=latest.get("match_date"))

    hint = _clean((media or {}).get("match_or_event_name") or (media or {}).get("event_name"))
    title, hint_year = _parse_known_match(hint)
    _add_candidate(candidates, "title", title or hint, "INTAKE_HINT", MEDIUM, original=hint)
    _add_candidate(candidates, "year", hint_year, "INTAKE_HINT", MEDIUM, original=hint)

    display = _clean(project.get("display_name") or project.get("pilot_id"))
    title, display_year = _parse_known_match(display)
    _add_candidate(candidates, "title", title or display, "PERSISTED_DISPLAY", MEDIUM, original=display)
    _add_candidate(candidates, "year", display_year, "PERSISTED_DISPLAY", MEDIUM, original=display)

    for path in source_paths:
        title, path_year = _parse_known_match(path)
        _add_candidate(candidates, "title", title, "SOURCE_FILENAME", MEDIUM, original=path)
        _add_candidate(candidates, "year", path_year, "SOURCE_FILENAME", MEDIUM, original=path)

    raw = " ".join(_clean(project.get(key)) for key in ("job_id", "source_id") if project.get(key))
    title, legacy_year = _parse_known_match(raw)
    _add_candidate(candidates, "title", title, "LEGACY", LOW, original=raw)
    _add_candidate(candidates, "year", legacy_year, "LEGACY", LOW, original=raw)

    fields = {
        "title": _choose(candidates, "title"),
        "home": _choose(candidates, "home"),
        "away": _choose(candidates, "away"),
        "competition": _choose(candidates, "competition"),
        "year": _choose(candidates, "year"),
        "stage": _choose(candidates, "stage"),
        "date": _choose(candidates, "date"),
    }
    conflicts: list[dict[str, Any]] = []

    years = [field for field in candidates.get("year", []) if field.value]
    year_values = {field.value for field in years}
    if len(year_values) > 1:
        conflicts.append({"field": "year", "values": [field.to_dict() for field in years]})
        fields["year"] = MetadataField("", "FALLBACK", LOW, "")

    title_candidates = candidates.get("title", [])
    research_title = next((field for field in title_candidates if field.source == "RESEARCH"), None)
    if research_title:
        research_teams = _team_set(research_title.value)
        for field in title_candidates:
            if field.source == "RESEARCH":
                continue
            teams = _team_set(field.value)
            if teams and research_teams and teams != research_teams:
                conflicts.append({"field": "title", "values": [research_title.to_dict(), field.to_dict()]})

    missing_core = [name for name in ("title", "competition", "year", "stage") if not fields[name].value]
    if conflicts:
        health = CONFLICT
    elif not fields["title"].value:
        health = UNKNOWN
    elif missing_core:
        health = INCOMPLETE
    else:
        health = TRUSTED

    repairable = []
    for name in ("competition", "year", "stage"):
        field = fields[name]
        if field.value and field.source == "RESEARCH" and not conflicts:
            repairable.append({
                "field": name,
                "normalized_value": field.value,
                "source": field.source,
                "reason": "research provides deterministic presentation metadata",
            })

    return {
        "title": fields["title"].value or "Untitled Match",
        "home": fields["home"].value,
        "away": fields["away"].value,
        "competition": fields["competition"].value,
        "year": fields["year"].value,
        "stage": fields["stage"].value,
        "date": fields["date"].value,
        "fields": _field_dict(fields),
        "candidates": {key: [field.to_dict() for field in value] for key, value in candidates.items()},
        "health": health,
        "conflicts": conflicts,
        "repair": {
            "safe": bool(repairable and health in {TRUSTED, INCOMPLETE}),
            "recommended": repairable if health in {TRUSTED, INCOMPLETE} else [],
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "mode": "preview_only",
        },
    }
