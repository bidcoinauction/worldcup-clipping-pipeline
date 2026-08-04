# Reference Deployment

## Purpose

This repository is a **local-first football mythology archive and clipping pipeline**. The current **World Cup implementation** is the **reference deployment**: it is the concrete, tested, working instance of the operational platform. The business goal is a managed media-operations service where the World Cup workflow is one case study, not the boundary of the product.

Extracting reusable concepts for a second deployment must be done **without disturbing the reference deployment**. The reference deployment is the source of truth for behavior, and it is the compatibility contract every future change must keep passing.

This document is the guardrail for that work. It records:

- The 12 principles that define how the reference deployment is preserved.
- The current reference deployment flow, with the file behind each stage.
- The behaviors that must be preserved.
- A decision test for deciding whether something is extracted or stays specialized.
- The anti-patterns to avoid.

## Terminology

- **The company / the service / the operational platform** — the future organization and its product. This repo never names that company.
- **The current reference deployment** — the World Cup workflow currently implemented in this repository, including its scripts, config, prompts, and data.
- **Extraction** — separating a reusable concept (configuration, taxonomy, prompt) from the World Cup implementation so a new deployment can reuse it.
- **Specialized** — a concept that is deliberately specific to football or the World Cup case study and should remain where it is.

## The 12 Principles

1. **The reference deployment keeps working.** Every change must keep the World Cup end-to-end workflow operational. `python3 scripts/validate_data.py` and `pytest` must continue to pass.
2. **Local-first and Windows-compatible.** Recorded assets live outside Git in an archive root. Scripts must stay runnable on the Windows capture box (Ace Stream) and the Mac dev/build box.
3. **Script-first, additive migration.** Prefer additive changes: add config/templates/validation before deleting hardcoded logic. Never delete a working behavior before a replacement exists.
4. **Simple JSON configuration.** Prefer plain JSON config files. No new dependencies, no databases, no queues, no multi-tenant systems in Phase 1.
5. **Neutral language.** This documentation and planning never refers to the future company by its brand or product name. Use "the company", "the service", "the operational platform", or "the current reference deployment".
6. **Configuration over code for identity.** Account positioning, hashtags, categories, and naming live in config, not hardcoded in scripts.
7. **Preserved editorial contract.** Every match carries emotional metadata; every moment preserves a narrative function. This mythology framing is preserved, not removed or diluted.
8. **Explicit specialized boundaries.** Football editorial language, the World Cup taxonomy, the World Cup schedule, and football prompts remain specialized and stay on the World Cup case study.
9. **Reference artifact naming stays.** Existing directory names (leagues, categories, clip/export paths) and manifest/template schemas keep working as they are today.
10. **Behavior first, then automation.** Add manual runbooks and validation before adding automation. Never automate an unvalidated stage.
11. **Everything text-based.** Prefer CSV/JSON/YAML that can later migrate elsewhere. Keep binary assets out of Git.
12. **Preserve the case study.** The goal is not to erase the World Cup implementation. It is to keep it as the reference deployment while the platform can support other deployments.

## Current Reference Deployment Flow

The flow below is the World Cup workflow as implemented. Each stage lists the file(s) that implement or hold it.

### 1. Capture (Windows, Ace Stream)

- [ ] `scripts/record_live.py` records a live Ace Stream to a `.ts` file.
- Archive output: `RAW/WORLD_CUP/<match_id>.ts` under `FOOTBALL_ARCHIVE_ROOT`.
- Match-day runbook is in `AGENTS.md`.

### 2. Match manifest

- [ ] `scripts/create_match_manifest.py` registers a recorded source.
- Output: `data/manifests/<match_id>.json` (manifest_version, match metadata, sources, pipeline flags).
- Example: `data/manifests/mexico_south_africa_2026_06_11.json`.

### 3. Schedule + archive organization

- [ ] `data/worldcup_2026_schedule.csv` is the schedule that gates scheduling logic.
- [ ] `scripts/process_scheduled_match.py` reads this schedule and the research template.
- [ ] `scripts/init_archive.py` and `scripts/organize_football_archive.py` create/organize archive folders.
- Directory blueprint: `pipeline/paths.py` (`PROJECT_DIRS`), created by `pipeline/utils.py:ensure_dirs`.

### 4. Concatenate and process

- [ ] `scripts/process_from_manifest.py` concatenates all registered sources into `RAW/WORLD_CUP/<match_id>.ts`, then delegates to the existing pipeline. Supports `--dry-run`.
- [ ] `scripts/process_scheduled_match.py` drives transcription → prompt → detection → manifest.

### 5. Transcription

- [ ] `scripts/transcribe_match.py` produces transcripts via the provider in config (`faster-whisper` or hosted).
- Providers are read from `config/pipeline_config.json:providers`.

### 6. Research windows

- [ ] `scripts/scaffold_research.py` creates a `match_research.json` skeleton.
- [ ] `MATCH_RESEARCH/<LEAGUE>/<match_slug>/match_research.json` holds editorial events.
- Template: `MATCH_RESEARCH/template.json`.

### 7. Prompt generation

- [ ] `scripts/generate_claude_prompt.py` builds transcription + contract prompts from config and templates.
- [ ] `prompts/thumbnail_prompt_template.txt` provides the image prompt template.

### 8. Detection

- [ ] `scripts/run_gpt_detection.py` runs moment detection via OpenAI or Ollama.
- Choice of provider/model comes from `pipeline/config.py` helpers.

### 9. Research enrichment + clip windows

- [ ] `scripts/process_researched_windows.py` turns researched windows into FFmpeg commands.
- [ ] `scripts/export_research_windows.py` resolves sources and exports actual clips with editorial metadata preserved.

### 10. Clip manifest

- [ ] `scripts/build_clip_manifest.py` writes `CLIP_MANIFESTS/<match_slug>_manifest.csv`.

### 11. Clip encode/export

- [ ] `scripts/export_clips_ffmpeg.py` exports clips to platform/category paths.
- Vertical crop logic lives in `scripts/export_clips_ffmpeg.py` (`_micro_slice`, `_validate_and_clamp`).

### 12. Asset prompts and captions

- [ ] `scripts/generate_asset_prompts.py` writes thumbnails (THUMBNAILS/) and caption prompts (CAPTIONS/), including hashtags.

### 13. Review dashboard

- [ ] `scripts/build_stadium_dashboard.py` builds a local static HTML review dashboard from clips + manifests + research + detections.

### 14. Mythology engine (editorial scoring)

- [ ] `scripts/mythology_engine.py` classifies a match by mythology score via `pipeline.stadium_signal.py`.

### 15. Calendar generation

- [ ] `scripts/export_calendar.py` produces a calendar with prodid string.

## Preserved Behaviors

The following behaviors must remain intact after any extraction:

- `pipeline_config.json` continues to work as the config source (leagues, categories, platforms, clip modes, models, providers, account_positioning, paths).
- Existing CLI choices from `get_leagues()` (PREMIER_LEAGUE, UCL, MLS, LIGA_MX, WORLD_CUP) remain valid.
- Existing category (EMOTION, AURA, CHAOS, AMERICA) and platform (TikTok, Reels, Shorts) names remain valid.
- Existing path computing for archive and project dirs remains intact (RAW/WORLD_CUP, EXPORTS, CLIPS/EMOTION, etc.).
- Manifest schemas, research template schema, and dashboard ingestion remain readable by current scripts.
- `pytest` and `python scripts/validate_data.py` continue to pass.

## Extraction Decision Test

To decide whether a concept should be extracted (made reusable) or remain World Cup-specialized, apply this test:

1. Would a second company/deployment use this exact concept without changing its meaning? If yes, extract.
2. Is the concept the current reference implementation's identity? If it is the brand voice/positioning of the reference, extract it as a value.
3. Does the concept carry football/tournament-only meaning (VAR, goal, card, "group stage", "knockout")? If yes, it stays on the World Cup side.
4. Is the concept a path convention or schema already proven by the reference deployment? Preserve as the reference contract.

Extract when reusable across deployment types. Keep specialized when it is football or World Cup-specific.

## Extraction Progress (Phase 1 — Configuration)

Configuration is the first extracted boundary. It is **additive** — the World Cup reference deployment is preserved unchanged.

- `pipeline/config_errors.py` — `ConfigurationError` for all configuration failures.
- `pipeline/config.py` — strict validation (`validate_config_dict`, `load_validated_config`) over the legacy allowlist `KNOWN_LEGACY_KEYS`; existing accessors unchanged.
- `pipeline/configurator.py` — structured project identity, explicit taxonomy registry, template resolution + rendering, platform/output selection, and canonical archive-root resolution.
- `scripts/validate_config.py` — read-only validator (no network, no file mutation); exits nonzero with the full failing field path on invalid config.
- `config/examples/basketball.json` — non-production structured example for a second sport; never auto-loaded.

### Resolver adoption (complete)

The duplicated archive-root and positioning logic has been consolidated onto the canonical resolvers:

- `pipeline/stadium_signal.py`, `scripts/record_live.py`, `scripts/live_watch.py` all delegate `archive_root`/`archive_path` to `pipeline.configurator.resolve_archive_root`/`resolve_archive_path`. Precedence: explicit CLI argument (`--output`, `--staging-dir`, `--ready-dir`, `--watch-dir`) → `FOOTBALL_ARCHIVE_ROOT` → platform default.
- `scripts/generate_claude_prompt.py` resolves account positioning via `resolve_project_identity()` (explicit config → legacy `account_positioning` → `ACCOUNT_POSITIONING` env → default), removing its independent fallback. Configuration always wins over the environment variable.

### Detection-template boundary

The World Cup detection prompt body is extracted to a registered, tracked template `prompts/world_cup_detection_prompt.txt`, rendered by `pipeline.configurator.render_template()` (standard library, deterministic, read-only). Registered templates only; unknown IDs, missing files, missing variables, and path traversal raise `ConfigurationError`. The `prompts/claude_detection_prompt.stub` reference has been removed from active configuration. The basketball example template (`prompts/basketball_detection_prompt.txt`) resolves for validation but is not registered for rendering.

Reference contract rules 1-4 above determine what is extracted vs. kept. The basketball example exists only to prove the structured boundary; it is not selected by default.

### Brand-profile boundary

Brand language is extracted to validated data files under `config/brands/`:

- `config/brands/world_cup.json` — production brand (display name, positioning, caption tone, language, hashtags, optional per-platform hashtag overrides, optional asset references). Values preserve the reference deployment language exactly; hashtags are stored with leading `#` so joining with spaces reproduces the historical output.
- `config/brands/basketball_example.json` — clearly labeled non-production example, referenced only by `config/examples/basketball.json`.

`pipeline/configurator.py` exposes `resolve_brand_profile`, `resolve_brand_hashtags`, `resolve_brand_positioning`, `resolve_brand_language`, `resolve_brand_caption_tone`, and `validate_brand_profile`. Brand positioning precedence: explicit override → brand profile → legacy `account_positioning` → `ACCOUNT_POSITIONING` env (fallback only) → default. Configuration always wins over the environment variable. `scripts/generate_asset_prompts.py` is the migrated consumer: caption hashtags come from the selected brand instead of a hardcoded list, with identical output for the World Cup brand.

### Export-profile boundary

Export behavior is extracted to a validated data file `config/export/world_cup.json` with two namespaces:

- `platforms` (TikTok/Reels/Shorts) — consumed by `scripts/export_clips_ffmpeg.py` through `resolve_platform_export_profile()` and `resolve_export_destination()`.
- `profiles` (vertical_*, goal_context, source) — consumed by `scripts/export_research_windows.py` for the encoding arguments through `resolve_export_profile()`.

Values encode the historical reference behavior (1080x1920, libx264 veryfast CRF 20, AAC, `EXPORTS/<PLATFORM>/<CATEGORY>/<clip_id>_<platform>.mp4`, `CLIPS/<match>/<clip>.mp4`). The crop/fit filter chains remain in the exporter scripts; profiles describe dimensions, codecs, naming, and destinations. Unknown profiles/platforms, invalid dimensions/codecs, and unsafe destinations raise `ConfigurationError` with no silent fallback.

The affected CLI scripts (`generate_asset_prompts.py`, `export_clips_ffmpeg.py`, `export_research_windows.py`) surface `ConfigurationError` as a concise actionable message with a nonzero exit and no traceback for expected configuration mistakes; unexpected programming errors still raise normally.

## Managed Pilot Operations (Phase 2)

Phase 2 adds an operational wrapper around the reference deployment for one
managed local-file sports pilot. It is additive: every World Cup manifest,
schedule, clip manifest, and command keeps working independently.

- `pipeline/pilot.py` — validated pilot intake (structural / configuration /
  rights / source), the rights gate, read-only source validation, the job
  record API (atomic writes, append-only event log), and explicit manual
  state transitions with revision checks. It also validates and registers
  manual pipeline run records and pilot output manifests for already-generated
  files without replacing the existing CSV clip-manifest/export workflow.
- `scripts/pilot_job.py` — operator CLI (`validate`, `create`, `show`, `list`,
  `transition`, `history`).
- `docs/pilot/` — runbook and intake templates; tracked examples under
  `docs/pilot/examples/` (non-production, fictitious data).
- Runtime intake/job files live under `data/pilot/` and are gitignored.

Boundaries preserved:

- Only `CONFIRMED`, unexpired rights pass the execution-ready gate;
  `RESTRICTED` requires an explicit supported-use check.
- Source validation is read-only; network URLs are rejected; no media is
  modified, moved, copied, or transcoded; no network requests occur.
- The CLI never runs the pipeline, invokes models, or processes media.
- Run records capture operator-entered command/stage/provenance metadata only;
  they do not execute commands or start background work.
- Manual transitions record operator state only: `RUNNING` does not run media
  processing, and `DELIVERED` does not upload or send files.
- Job records contain identifiers and readiness only — never intake
  confirmation or personal data.
- No database, queue, auth, users, billing, publishing, or dashboards are
  added.

## Anti-Patterns

Avoid these in extraction work:

- Renaming repository parts (scripts, dirs, config file, taxonomy names) as part of extraction; that breaks the reference contract.
- Deleting working World Cup logic before a replacement exists, without keeping the paths/behavior intact.
- Adding new runtime dependencies, a database, a queue, auth, billing, or multi-tenant concepts during Phase 1.
- Hardcoding values in scripts again after they have been extracted to config.
- Naming the future platform/company in this documentation or planning. Use neutral terms.
- Building new dashboard/auth/billing/publishing infrastructure during the platform build. The static local review dashboard is the current, preserved practice.
- Automating a stage before validating its inputs and outputs manually.
