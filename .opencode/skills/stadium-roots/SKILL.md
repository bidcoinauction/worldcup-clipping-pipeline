---
name: stadium-roots
description: Rules for archive structure — FOOTBALL_ARCHIVE_ROOT, cross-platform path resolution, directory conventions, slugify, STADIUM volume fallback, and path utilities
compatibility: opencode
metadata:
  project: worldcup-clipping-pipeline
---

## What I do

I enforce archive structure conventions and path resolution.

## Rules

- Every path resolves through the `ROOT` constant (`pipeline/utils.py`) or `FOOTBALL_ARCHIVE_ROOT` env var.
- Default macOS root: `FootballArchive/` (relative to repo root).
- Default Windows root: `C:\FootballArchive`.
- STADIUM volume (external): `/Volumes/STADIUM/FootballArchive/RAW/` — always searched last.
- Archive subdirectory structure is fixed:
  - `RAW/` — source match videos
  - `CLIPS/<match_slug>/` — exported clips
  - `MATCH_RESEARCH/<TOURNAMENT>/<match_slug>/` — research metadata
  - `DETECTIONS/` — detection JSON outputs
  - `CLIP_MANIFESTS/` — export manifest CSVs
- Match slugs use `slugify()`: "Germany vs Italy 2012" → `germany_vs_italy_2012`.
- Tournament dirs are UPPER_CASE: `WORLD_CUP/`, `EURO/`, `CHAMPIONS_LEAGUE/`.
- Never hardcode absolute paths outside `pipeline/utils.py` or `pipeline/paths.py`.
- CSV manifests always go in `CLIP_MANIFESTS/` root (not per-match).
- When adding a new archive directory, follow the `DEFAULT_*` constant pattern.

## When to use me

Use this skill before editing `pipeline/utils.py`, `pipeline/paths.py`, `pipeline/config.py`, `init_archive.py`, `init_project.py`, `organize_football_archive.py`, or any script that resolves archive paths.
