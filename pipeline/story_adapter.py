"""Adapter from existing story-engine suggestions to canonical runtime Stories."""

from __future__ import annotations

import hashlib
from typing import Any

from .moment_models import Moment
from .story_engine import NARRATIVE_ROLES
from .story_models import Story, StoryMoment

ROLE_ORDER = ("HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH")


def canonical_story_id(project_id: str, original_story_id: str) -> str:
    raw = f"{project_id}|{original_story_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"story_{digest}"


def _resolve_moment(moments_by_original_id: dict[str, Moment], reference: str) -> Moment | None:
    return moments_by_original_id.get(str(reference))


def build_moment_resolution_map(moments: list[Moment]) -> dict[str, Moment]:
    lookup: dict[str, Moment] = {}
    for moment in moments:
        original = moment.metadata.get("original_event_id")
        if original:
            lookup[str(original)] = moment
        lookup[moment.moment_id] = moment
    return lookup


def adapt_story_suggestions(
    suggestions: list[dict[str, Any]],
    *,
    project_id: str,
    moments: list[Moment],
    created_at: str = "",
) -> list[Story]:
    """Normalize story-engine suggestions into canonical Stories + StoryMoments.

    StoryMoments are written to the runtime index via ``runtime_service``; the
    returned canonical Story list includes ``story_moments`` metadata for caller
    convenience. This function is idempotent for stable inputs.
    """
    from .runtime_service import add_story_moment, upsert_story

    resolution = build_moment_resolution_map(moments)
    canonical_stories: list[Story] = []

    for suggestion in suggestions:
        if not isinstance(suggestion, dict):
            raise ValueError("story suggestion must be an object")
        original_id = suggestion.get("story_id") or suggestion.get("id") or ""
        if not original_id:
            raise ValueError("story suggestion is missing story_id")
        story_id = canonical_story_id(project_id, str(original_id))
        archetype = str(suggestion.get("archetype") or "")

        story = upsert_story(
            project_id=project_id,
            story_id=story_id,
            title=str(suggestion.get("title") or original_id),
            summary=str(suggestion.get("summary") or ""),
            archetype=archetype,
            hook=str(suggestion.get("hook") or suggestion.get("why_this_story") or ""),
            emotional_arc=list(suggestion.get("emotional_arc") or []),
            estimated_duration=suggestion.get("estimated_duration"),
            recommended_formats=list(suggestion.get("recommended_formats") or []),
            metadata={
                "original_story_id": original_id,
                "original_archetype": archetype,
                "original_recommended_formats": list(suggestion.get("recommended_formats") or []),
                "source_suggestion": dict(suggestion),
            },
            created_at=created_at,
        )
        canonical_stories.append(story)

        moment_ids = list(suggestion.get("moment_ids") or [])
        roles = suggestion.get("narrative_roles") if isinstance(suggestion.get("narrative_roles"), dict) else {}
        ordered_moments: list[tuple[str, str, int]] = []
        sequence = 0
        for role in ROLE_ORDER:
            if role not in NARRATIVE_ROLES:
                continue
            for ref in roles.get(role, []) or []:
                ordered_moments.append((str(ref), role, sequence))
                sequence += 1
        for ref in moment_ids:
            if ref not in [entry[0] for entry in ordered_moments]:
                ordered_moments.append((str(ref), "SETUP", sequence))
                sequence += 1

        for ref, role, seq in ordered_moments:
            moment = _resolve_moment(resolution, ref)
            if moment is None:
                raise ValueError(
                    f"story '{original_id}' references moment '{ref}' that cannot be resolved to a canonical moment"
                )
            add_story_moment(story_id, moment.moment_id, role, seq, metadata={"original_reference": ref})

    return canonical_stories