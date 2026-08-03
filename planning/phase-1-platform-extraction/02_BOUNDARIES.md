# Phase 1 — Boundaries

Explicit limits for Phase 1 and for later implementation phases. Every boundary exists to preserve the reference deployment.

## Phase 1 Boundaries (this track)

- **Documentation only.** No production code, tests, config values, scripts, or repository names change.
- **No new runtime dependencies.**
- **No company/product names.** This track uses "the company", "the service", "the operational platform", and "the current reference deployment". It never refers to the future company by name.
- **No repository renames.** `pipeline/`, `scripts/`, `config/`, `data/`, `MATCH_RESEARCH/`, `CLIP_MANIFESTS/`, `CLIPS/`, `EXPORTS/`, `THUMBNAILS/`, `CAPTIONS/`, `DETECTIONS/`, `FootballArchive/` keep their current names.
- **No new automation.** This phase produces no new scripts or scheduled jobs.

## Technical Boundaries (Phase 1 → later)

- The first implementation slice must be additive and reversible, and must keep `python3 scripts/validate_data.py` and `pytest` green.
- Config loading may be validated, but existing config keys keep working; no existing key is renamed.
- Existing CLI choices produced by `get_leagues()` keep returning the current values.
- Taxonomy and platform names remain usable as path segments exactly as today.

## Editorial Boundaries

- Every match keeps emotional metadata; every moment keeps a narrative function. The mythology/editorial contract is preserved.
- Football-specific editorial language (emotions, series arcs, football detection language) stays specialized.

## Content / Rights Boundaries

- Keep video/audio assets outside Git in `FootballArchive/` or another ignored archive root.
- Confirm media rights before commercial processing or delivery.
- The reference deployment remains a local-first archive, not a hosted publishing platform.

## Out-of-Boundary Concepts (explicitly deferred)

These are explicitly out of scope for Phase 1 and the first implementation slice:

- Dashboards beyond the current static local review dashboard.
- Auth, billing, publishing, database, queue, and multi-tenant systems.
- Renaming the repo or choosing repository/company/product names.
- Building the basketball deployment itself (model only).