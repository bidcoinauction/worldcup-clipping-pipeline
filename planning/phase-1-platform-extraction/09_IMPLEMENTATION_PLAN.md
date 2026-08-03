# Phase 1 — Implementation Plan

This document records the **proposed first implementation slice** for a later phase and the owner decisions still required. Phase 1 itself implements nothing.

## First Implementation Slice (proposed)

Extract **project identity, taxonomy selection, prompt selection, and archive/output roots into validated configuration** while preserving current `pipeline_config.json` behavior.

Grounding in the repository:

- Identity: `account_positioning` in `config/pipeline_config.json` (read in `scripts/generate_claude_prompt.py:344`), backed by `ACCOUNT_POSITIONING` env.
- Taxonomy/platform sets: `categories` and `platforms` in config, mirrored as clip/export dirs in `pipeline/paths.py` and CLI choices in `scripts/export_clips_ffmpeg.py:58`.
- Prompt selection: `paths.thumbnail_template` in config → `prompts/thumbnail_prompt_template.txt`.
- Archive/output roots: `FOOTBALL_ARCHIVE_ROOT` + platform defaults in `scripts/record_live.py` and `pipeline/utils.py:ROOT`.
- Config validation: `pipeline/config.py` currently has no schema validation and caches a global; a validated loader is the prerequisite.

This slice is low-risk because it moves existing config values into a validated loading contract without renaming files, keys, or paths.

## EXTRACT NOW Assumptions

These are the assumptions this slice relies on:

1. Existing `config/pipeline_config.json` values are the World Cup defaults and must remain valid.
2. Categories/platforms are deployment-owned sets usable as path-safe strings.
3. Prompt template selection is a config pointer to an existing template file.
4. Archive root resolution (`FOOTBALL_ARCHIVE_ROOT` + platform defaults) is the reusable contract.
5. Validated config loading can be added without changing existing key names or behavior.

## Deliberately Specialized Assumptions (KEEP)

These are **not** extracted and stay on the World Cup case study:

1. Football editorial series/emotions (`config/emotions.yml`, `config/series.yml`).
2. World Cup schedule and league naming (`data/worldcup_2026_schedule.csv`, `WORLD_CUP`).
3. Football detection prompt language and World Cup hashtags.
4. `RAW/WORLD_CUP` league paths and World Cup manifests.

## World Cup Compatibility Requirements

1. `python3 scripts/validate_data.py` and `pytest` pass (baseline: 541 passed, 1 skipped, 1 warning).
2. `config/pipeline_config.json` keys/values unchanged and valid.
3. `get_leagues()` returns current set.
4. Categories/platforms remain valid names and path segments.
5. Existing directory paths and manifest/research schemas unchanged.
6. Archive root resolution unchanged.
7. No renaming, no new deps, no auth/billing/dashboard/db/queue/multi-tenant.

## Proposed Implementation Outline (later phase, not built now)

1. Add a validation layer to the config loader (additive; current config passes).
2. Read identity, taxonomy, platform, and prompt-selection as validated config values, defaulting to the World Cup config.
3. Keep archive/output root resolution as the reusable adapter contract.
4. Add tests described in `08_TEST_STRATEGY.md`.
5. Verify compatibility gates in `03_COMPATIBILITY.md`.

## Expected New Tests

- Config validation: current config loads identically; invalid config fails clearly; unknown keys warn.
- Identity/taxonomy/platform resolve to config values with World Cup defaults preserved.
- Prompt template path resolves from config and defaults to the World Cup template.
- Root resolution keeps `FOOTBALL_ARCHIVE_ROOT` + platform-default behavior.
- Specialization: football-only content is not required by the generic contract.

## Remaining Owner Decisions

- Which validated-config error severity to adopt (warn vs hard-fail on unknown keys).
- Whether a schema file is introduced in the slice (default: no, additively later).
- Whether `ACCOUNT_POSITIONING` env should override the config value (today it is documented as env-backed).
- When the basketball pilot is run (after Stage 6 in `07_MIGRATION_SEQUENCE.md`).
- Whether the later slice touches `scripts/generate_asset_prompts.py` hashtags (currently specialized; likely KEEP).

## Recommended Next OpenCode Build Prompt

> "Implement the Phase 1 first slice: add a validated, additive config loader that reads project identity, taxonomy, platform, and prompt-selection from `config/pipeline_config.json` (and optional env overrides), while preserving all existing keys and behavior. Keep the World Cup reference deployment intact: `python3 scripts/validate_data.py` and `pytest` must pass (baseline 541 passed, 1 skipped). Add tests for config validation, identity/taxonomy/platform resolution with World Cup defaults preserved, prompt-template path resolution, and archive-root resolution. Do not rename any file, key, path, or schema; do not add dependencies, auth, billing, dashboards, databases, queues, or multi-tenant logic."

## Gate Before Merge

- `git diff --check` clean.
- Validator + pytest pass.
- No production code/test/config changed in Phase 1 (documentation-only).

## Competitive Validation (ChatCut)

The competitive analysis (`planning/competitive-analysis/`) does not change this implementation plan. It confirms the first slice should extract **operational config** (identity, taxonomy, platform, prompt selection, archive roots), not editing features.

- No editing-product capabilities are added to the slice.
- Commodity editing features (captions, filler removal, motion graphics) remain out of scope and are delegated or deferred per `planning/competitive-analysis/03_BUILD_BUY_PARTNER.md`.
- The recommended next build prompt remains the config-extraction prompt in this document; it is unchanged by the competitive benchmark.