"""Clip-manifest construction service.

This module is the application/service boundary for converting detected clip
candidates into the existing clip manifest CSV artifact. CLI scripts and a
future Operator Console should call these functions rather than reimplementing
CSV row construction.
"""

from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

from .config_errors import ConfigurationError
from .configurator import resolve_project_profile
from .utils import ROOT, slugify


FIELDNAMES = [
    "clip_id", "league", "match_name", "source_video", "category",
    "start_time", "end_time", "virality_score", "retention_reason",
    "share_reason", "hook_text", "caption", "thumbnail_idea",
    "manual_scrub_note", "tiktok_note", "reels_note", "shorts_note",
    "status", "editor_notes", "export_tiktok", "export_reels", "export_shorts",
]


def _require_profile(profile: str) -> dict:
    resolved = resolve_project_profile(profile)
    if not resolved.get("production_capable"):
        raise ConfigurationError(f"profile '{profile}' is registered but is not production-capable")
    return resolved


def _load_analysis(analysis: str | Path | list | dict) -> tuple[Any, str | None]:
    if isinstance(analysis, (str, Path)):
        path = Path(analysis)
        return json.loads(path.read_text(encoding="utf-8")), str(path)
    return analysis, None


def _clips_from_analysis(data: Any) -> list[dict]:
    if isinstance(data, dict) and "clips" in data:
        data = data["clips"]
    if not isinstance(data, list):
        raise ValueError("analysis must be a JSON array or an object with a 'clips' array")
    if not all(isinstance(item, dict) for item in data):
        raise ValueError("analysis clips must be objects")
    return list(data)


def _parse_seconds(value: Any) -> float | None:
    if value in (None, ""):
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, str):
        return None
    raw = value.strip()
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        pass
    parts = raw.split(":")
    if not 2 <= len(parts) <= 3:
        return None
    try:
        numbers = [float(part) for part in parts]
    except ValueError:
        return None
    if len(numbers) == 2:
        minutes, seconds = numbers
        return minutes * 60 + seconds
    hours, minutes, seconds = numbers
    return hours * 3600 + minutes * 60 + seconds


def _coverage(rows: list[dict]) -> dict:
    starts: list[float] = []
    ends: list[float] = []
    total = 0.0
    parsed_durations = 0
    for row in rows:
        start = _parse_seconds(row.get("start_time"))
        end = _parse_seconds(row.get("end_time"))
        if start is None or end is None or end < start:
            continue
        starts.append(start)
        ends.append(end)
        total += end - start
        parsed_durations += 1
    return {
        "parsed_windows": parsed_durations,
        "total_clip_seconds": total if parsed_durations else None,
        "first_start_seconds": min(starts) if starts else None,
        "last_end_seconds": max(ends) if ends else None,
    }


def build_clip_manifest(
    analysis: str | Path | list | dict,
    *,
    league: str,
    match_name: str,
    source_video: str = "",
    profile: str = "football",
) -> dict:
    """Build clip manifest rows and structured metadata without writing files."""
    profile_data = _require_profile(profile)
    data, analysis_path = _load_analysis(analysis)
    clips = _clips_from_analysis(data)
    match_slug = slugify(match_name)

    rows = []
    for index, clip in enumerate(clips, start=1):
        notes = clip.get("platform_notes", {}) or {}
        rows.append({
            "clip_id": f"{league}_{match_slug}_{index:03d}",
            "league": league,
            "match_name": match_name,
            "source_video": source_video,
            "category": str(clip.get("category", "UNSORTED")).upper(),
            "start_time": clip.get("start_time", ""),
            "end_time": clip.get("end_time", ""),
            "virality_score": clip.get("virality_score", ""),
            "retention_reason": clip.get("retention_reason", ""),
            "share_reason": clip.get("share_reason", ""),
            "hook_text": clip.get("hook_text", ""),
            "caption": clip.get("caption", ""),
            "thumbnail_idea": clip.get("thumbnail_idea", ""),
            "manual_scrub_note": clip.get("manual_scrub_note", ""),
            "tiktok_note": notes.get("tiktok", ""),
            "reels_note": notes.get("reels", ""),
            "shorts_note": notes.get("shorts", ""),
            "status": "needs_visual_scrub",
            "editor_notes": "",
            "export_tiktok": "",
            "export_reels": "",
            "export_shorts": "",
        })

    return {
        "profile": profile_data["profile_id"],
        "sport": profile_data["sport"],
        "analysis_input": analysis_path,
        "output_path": None,
        "league": league,
        "match_name": match_name,
        "source_video": source_video,
        "clip_window_count": len(rows),
        "fieldnames": list(FIELDNAMES),
        "coverage": _coverage(rows),
        "warnings": [],
        "rows": rows,
    }


def write_clip_manifest(
    analysis: str | Path | list | dict,
    *,
    league: str,
    match_name: str,
    source_video: str = "",
    profile: str = "football",
    output_root: str | Path | None = None,
    output_path: str | Path | None = None,
) -> dict:
    """Build and write the existing clip manifest CSV artifact."""
    result = build_clip_manifest(
        analysis,
        league=league,
        match_name=match_name,
        source_video=source_video,
        profile=profile,
    )
    if output_path is not None:
        out_file = Path(output_path)
    else:
        if output_root is None:
            root = ROOT
        elif isinstance(output_root, (str, Path)):
            root = Path(output_root)
        else:
            root = output_root
        out_file = root / "CLIP_MANIFESTS" / f"{slugify(match_name)}_manifest.csv"
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with out_file.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(result["rows"])
    return {**result, "output_path": out_file}
