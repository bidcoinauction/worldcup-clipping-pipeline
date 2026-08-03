# Phase 1 — Migration Sequence

Extraction is additive and staged. The sequence below orders changes so the reference deployment keeps working at every step. Phase 1 itself creates no code; this document describes the later implementation ordering and is documentation only.

## Migration Principles

- Add before removing. No behavior is removed before a replacement exists.
- Validate before automating. Runbooks and validation come before automation.
- Keep paths/schemas stable. No directory or manifest schema changes in Phase 1.
- Each step ends green: `python3 scripts/validate_data.py` and `pytest` pass.

## Staged Sequence

### Stage 0 — Documentation (this phase, DONE)

- `docs/REFERENCE_DEPLOYMENT.md`.
- This planning track under `planning/phase-1-platform-extraction/`.

### Stage 1 — Validated config loading (later implementation slice, additive)

- Add schema/validation to the config loader while preserving all existing keys and behavior.
- No new keys required; current `config/pipeline_config.json` must pass validation unchanged.
- Add tests proving current config loads identically.

### Stage 2 — Extract identity and taxonomy sets

- Project identity (account positioning) and taxonomy/platform sets are read as validated config values, still sourced from `config/pipeline_config.json` defaults.
- Existing names remain the same; no file renames.

### Stage 3 — Prompt selection

- Prompt template selection (`paths.thumbnail_template` and caption/detection template pointers) becomes config-driven, defaulting to current World Cup templates.

### Stage 4 — Archive/output root resolution

- Formalize root resolution as a reusable adapter contract (see `06_ADAPTER_MODEL.md`) with `FOOTBALL_ARCHIVE_ROOT` and platform defaults unchanged.

### Stage 5 — Second-deployment pilot (model)

- A regional basketball league deployment consumes the extracted concepts with its own identity, taxonomy, platform set, and prompt selection — without editing any World Cup file.
- This is the acceptance test that extraction is real.

### Stage 6 — Manual runbook + validation

- Add a pilot runbook and input/output validation before any further automation (boundary rule: no new automation before validation).

## What Stays Behind

The following remain specialized and are never migrated in Phase 1:

- `config/emotions.yml`, `config/series.yml` (football editorial).
- `data/worldcup_2026_schedule.csv` (tournament data).
- Football prompt language and World Cup hashtags.
- `RAW/WORLD_CUP` league naming and World Cup manifests.

## Gate per Stage

Before moving to the next stage:

- `git diff --check` clean.
- `python3 scripts/validate_data.py` passes.
- `pytest` passes (baseline maintained or extended).
- No existing config key, path, schema, or taxonomy name changed.