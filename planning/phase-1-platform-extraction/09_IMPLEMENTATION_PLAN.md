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

## Status — Phase 1 First Slice (Built)

The first config-extraction slice is implemented and merged:

- `pipeline/config_errors.py` — dedicated `ConfigurationError` used for all configuration failures.
- `pipeline/config.py` — additive strict validation (`validate_config_dict`, `load_validated_config`) plus the legacy allowlist `KNOWN_LEGACY_KEYS`; all existing public accessors unchanged.
- `pipeline/configurator.py` — structured resolution: project identity (`ACCOUNT_POSITIONING` kept as legacy fallback only), explicit taxonomy/profile registry, repo-relative template resolution, platform/output selection, and the canonical `resolve_archive_root`/`resolve_archive_path`.
- `pipeline/stadium_signal.py` — `archive_root`/`archive_path` now delegate to the canonical resolver (smallest approved call-site update; other archive-path callers are unchanged for later slices).
- `scripts/validate_config.py` — read-only config validator CLI (no network, no file mutation).
- `config/examples/basketball.json` — non-production structured example for a second sport; never loaded by default.
- Tests: `test_config_validation.py`, `test_configurator.py`, `test_validate_config.py`, `test_basketball_example.py`.

Verification: `python3 scripts/validate_data.py` passes; `pytest` 570 passed, 1 skipped (baseline 541 passed / 1 skipped preserved); both `validate_config.py` fixtures pass; an intentionally invalid fixture exits nonzero with the complete field path and without file mutation.

## Status — Phase 1 Second Slice (Complete: Resolver Adoption + Prompt Boundary)

The remaining duplicated archive-root and positioning logic is consolidated, and the missing detection-template stub is replaced with a real registered template:

- `scripts/record_live.py` and `scripts/live_watch.py` — local `archive_root`/`archive_path` replaced with thin delegates to the canonical `pipeline.configurator.resolve_archive_root`/`resolve_archive_path`; explicit CLI args (`--output`, `--staging-dir`, `--ready-dir`, `--watch-dir`) still win.
- `scripts/generate_claude_prompt.py` — account positioning now resolved via `resolve_project_identity()` (explicit config → legacy `account_positioning` → `ACCOUNT_POSITIONING` env → default); the independent `load_config().get("account_positioning", ...)` fallback is removed.
- `prompts/world_cup_detection_prompt.txt` — new tracked, registered World Cup detection template, byte-identical to the previous `PROMPT_TEMPLATE` body; the nonexistent `prompts/claude_detection_prompt.stub` reference is gone.
- `pipeline/configurator.py` — added `render_template()` (standard library, deterministic, read-only), the registered-template variable registry, and traversal-safe `resolve_profile_template_path()`/`_resolve_template_path()`.
- `prompts/basketball_detection_prompt.txt` — example-only template that resolves for validation but is not registered for rendering.
- Tests: `test_template_resolver.py` (byte-for-byte equivalence fixture, section preservation, error handling, traversal), `test_resolver_adoption.py` (canonical adoption, precedence, no independent fallback).

Verification: `pytest` 597 passed, 1 skipped (baseline 570 / 1 preserved); `validate_data.py` passes; both config fixtures validate; invalid template selection raises `ConfigurationError` with the template identifier and no silent fallback; render performs no network access and no file mutation.

## Status — Phase 1 Third Slice (Output Resolution + Editorial Taxonomy)

The structured output root now carries full precedence, and the football editorial language is separated from the operational `categories` surface as data:

- `pipeline/configurator.py` — output-root resolution with precedence override -> structured `outputs.directory` -> `FOOTBALL_ARCHIVE_ROOT` -> platform default, exposed as `resolve_output_root()`, `resolve_structured_output_root()`, `resolve_archive_root()`, `resolve_archive_path()`. Roots accept absolute or repository-relative paths; invalid types and `..` traversal raise `ConfigurationError`; resolution is read-only (no mkdir, no network).
- `pipeline/configurator.py` — editorial taxonomy tier (`operational`, `editorial` under `taxonomies`) validated in `validate_structured_profile()`; `_football_profile()` sources `emotional_kinds`/`operational` from the data-backed editorial file while keeping `get_taxonomy()` backward compatible.
- `config/editorial/world_cup.json` — the World Cup editorial vocabulary (`emotional_kinds` EMOTION/AURA/CHAOS/AMERICA, `narrative_functions`, `story_targets` arc/narrative roles), loaded via `_load_editorial_taxonomy()` and validated by `validate_editorial_taxonomy()`.
- `pipeline/configurator.py` — resolvers `resolve_operational_categories()`, `resolve_editorial_taxonomy()`, `resolve_story_targets()` (strict, full field paths).
- `config/examples/basketball.json` — moved to the separated structure (`operational.categories` distinct from `editorial.emotional_kinds`); not registered, not loaded by default.
- New tests `test_output_resolution.py` and `test_editorial_taxonomy.py` covering precedence, type/path validation, no-directory-creation, no-network, backward compatibility, and editorial-file-as-source-of-truth.

Verification: `pytest` 620 passed, 1 skipped (baseline 597 / 1 preserved, 1 warning); `validate_data.py` passes; `validate_config.py` passes on the reference config and `config/examples/basketball.json`.

## Status — Phase 1 Fourth Slice (Brand + Export Profile Boundaries)

Brand language and export behavior are extracted into validated profiles, and the smallest set of asset-generation and export consumers routes through them:

- `config/brands/world_cup.json` — production brand profile (id, display_name, positioning, caption_tone, language, hashtags with `#` prefix, optional per-platform overrides, optional asset references). `config/brands/basketball_example.json` is a clearly labeled non-production example.
- `config/export/world_cup.json` — export profiles with two namespaces: `platforms` (TikTok/Reels/Shorts) and `profiles` (vertical_*, goal_context, source). Values reproduce the historical reference behavior (1080x1920, libx264 veryfast CRF 20, AAC, `EXPORTS/<PLATFORM>/<CATEGORY>/<clip_id>_<platform>.mp4`, `CLIPS/<match>/<clip>.mp4`).
- `pipeline/configurator.py` — `validate_brand_profile`, `load_brand_profile`, `resolve_brand_profile`, `resolve_brand_positioning`, `resolve_brand_hashtags`, `resolve_brand_language`, `resolve_brand_caption_tone`, `validate_export_profiles`, `load_export_profiles`, `resolve_export_profile`, `resolve_platform_export_profile`, `resolve_export_destination`. Brand positioning precedence: override -> brand -> legacy `account_positioning` -> `ACCOUNT_POSITIONING` env (fallback only) -> default. Structured profiles may declare `brand.profile` and `exports.profiles` references (validated, referenced files must exist).
- `scripts/generate_asset_prompts.py` — caption hashtags resolved from the selected brand (`--brand`, default `world_cup`) instead of a hardcoded list; invalid brand selections exit nonzero with a concise error.
- `scripts/export_clips_ffmpeg.py` — resolves the platform export profile and builds destinations via `resolve_export_destination`; `export_clip` consumes the resolved profile.
- `scripts/export_research_windows.py` — `ffmpeg_filter()` consumes the resolved export profile for encoding arguments; unknown profiles raise `ConfigurationError`.
- `scripts/validate_config.py` — dispatches `config/brands/*` and `config/export/*` files to the brand/export validators.
- New tests `test_brand_profiles.py` and `test_export_profiles.py` plus consumer tests (brand hashtag equivalence, no hardcoded hashtags in `generate_asset_prompts.py`, resolved platform destinations, profile-driven encoding args).

Verification: `pytest` 660 passed, 1 skipped (baseline 620 / 1 preserved, 1 warning); `validate_data.py` passes; `validate_config.py` passes on the reference config, basketball example, brand files, and export file; `git diff --check` clean.

Remaining embedded assumptions (documented, not in the migrated call set): `scripts/generate_caption_bank.py` carries a static caption bank with per-category hashtags (manual bank generator, not the clipping asset path); the crop/fit filter chains live in the exporter scripts (described by profile `crop` metadata, not reproduced in config); `scripts/export_vertical_blur.py` duplicates vertical export logic and is out of the approved call set.

## Gate Before Merge

- `git diff --check` clean.
- Validator + pytest pass.
- World Cup reference deployment and all existing keys/behaviors preserved (additive only).

## Competitive Validation (ChatCut)

The competitive analysis (`planning/competitive-analysis/`) does not change this implementation plan. It confirms the first slice should extract **operational config** (identity, taxonomy, platform, prompt selection, archive roots), not editing features.

- No editing-product capabilities are added to the slice.
- Commodity editing features (captions, filler removal, motion graphics) remain out of scope and are delegated or deferred per `planning/competitive-analysis/03_BUILD_BUY_PARTNER.md`.
- The recommended next build prompt remains the config-extraction prompt in this document; it is unchanged by the competitive benchmark.