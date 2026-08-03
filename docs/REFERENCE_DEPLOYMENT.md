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

## Anti-Patterns

Avoid these in extraction work:

- Renaming repository parts (scripts, dirs, config file, taxonomy names) as part of extraction; that breaks the reference contract.
- Deleting working World Cup logic before a replacement exists, without keeping the paths/behavior intact.
- Adding new runtime dependencies, a database, a queue, auth, billing, or multi-tenant concepts during Phase 1.
- Hardcoding values in scripts again after they have been extracted to config.
- Naming the future platform/company in this documentation or planning. Use neutral terms.
- Building new dashboard/auth/billing/publishing infrastructure during the platform build. The static local review dashboard is the current, preserved practice.
- Automating a stage before validating its inputs and outputs manually.