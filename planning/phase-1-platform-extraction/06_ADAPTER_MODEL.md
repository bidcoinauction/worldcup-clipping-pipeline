# Phase 1 — Adapter Model

The reference deployment is **local-first**. The extraction must make source and path resolution reusable across deployments without forcing a database, queue, or multi-tenant system. This document describes the adapter model that the current implementation already implies, so extraction can rely on proven behavior.

## What "Adapter" Means Here

An adapter is a thin, validated resolution point between a named concept (source, archive root, league, platform) and concrete behavior (a file path, a directory, a CLI provider choice). The reference deployment already does this implicitly. Extraction formalizes it in config without new runtime infrastructure.

## Source Resolution (already proven)

- `pipeline/utils.py:ROOT` is the repo root.
- `scripts/record_live.py:archive_root()` resolves `FOOTBALL_ARCHIVE_ROOT`, else platform default (`C:\FootballArchive` on Windows, `FootballArchive/` otherwise).
- `pipeline/utils.py` + `scripts/export_research_windows.py` also reference `/Volumes/STADIUM/FootballArchive/RAW` as a fallback source directory.
- `scripts/build_stadium_dashboard.py:resolve_raw_video_path` tries several candidate archive paths for a match.

These are the reusable blueprint: **archive root is configurable and has platform defaults and fallbacks**. Extraction keeps this contract.

## Path Model (current)

- Project dir blueprint lives in `pipeline/paths.py` (`PROJECT_DIRS`).
- Clip/export dirs derive from category/platform names from config (`CLIPS/<CATEGORY>`, `EXPORTS/<PLATFORM>/<CATEGORY>`).
- Archive dirs derive from league name (`RAW/WORLD_CUP`).
- League-specific research dirs: `MATCH_RESEARCH/<LEAGUE>`.

## Proposed Adapter Contract (documentation only)

A future adapter layer would make these points explicit:

1. **Archive root** — resolved from `FOOTBALL_ARCHIVE_ROOT` or platform default (unchanged).
2. **League/category/platform → path** — derived from config-owned sets, validated as path-safe (unchanged derivation, now validated).
3. **Source type** — local file (manifest source) currently; the model keeps local-file sources and does **not** add cloud source adapters in Phase 1.

Nothing in this phase changes these resolutions.

## Second-Deployment Fit (basketball model)

A regional basketball league deployment would provide its own league name and platform set through the same validated path derivation. It adds **no** new infrastructure. It reuses `FOOTBALL_ARCHIVE_ROOT` resolution and the local-file source path.

## Non-Actions

- No cloud source adapters.
- No queue/database-based adapters.
- No renaming of existing directories or config keys.
- No repository/company name selection.