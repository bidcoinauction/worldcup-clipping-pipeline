# Phase 1 — Configuration Model

The reference deployment is script-first and already carries most identity in `config/pipeline_config.json`. The extraction proposes an **additive, validated configuration model** that generalizes identity, taxonomy, platform, prompt, and archive root selection while leaving the World Cup behavior intact.

## Current Configuration Baseline

- `pipeline/config.py` reads `config/pipeline_config.json` with a cached global and no schema validation.
- Existing config keys: `account_positioning`, `leagues`, `categories`, `platforms`, `daily_targets`, `default_clip_mode`, `clip_modes`, `scoring_weights`, `models`, `paths` (`thumbnail_template`), `providers`.
- Env surface in `.env.example`: `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `FOOTBALL_ARCHIVE_ROOT`, `DEFAULT_OPENAI_MODEL`, `DEFAULT_CLAUDE_MODEL`, `DEFAULT_TRANSCRIBE_MODEL`, `DEFAULT_WHISPER_MODEL`, `OLLAMA_URL`, `OLLAMA_MODEL`, `ACCOUNT_POSITIONING`.

## Principles for the Proposed Model

1. **Additive only.** New config files/keys are added; existing keys in `pipeline_config.json` keep working.
2. **Plain JSON.** No new dependencies; simple JSON loading with validation.
3. **Identity separated from code.** Project identity (positioning/hashtags/voice) is config data, not script constants.
4. **Taxonomy and platform sets are deployment-owned.** Categories/platforms are selected per deployment, not hardcoded.
5. **Prompt selection is config.** Which templates apply (thumbnail, caption, detection) is a deployment choice.
6. **Archive/output roots are resolved consistently** via `FOOTBALL_ARCHIVE_ROOT` and platform defaults, as today.

## Proposed Structure (documentation only — not created in Phase 1)

A future additive layout, alongside the current files:

- `config/pipeline_config.json` — unchanged, remains the World Cup reference config.
- `config/schema.json` — optional future validation schema (not required in Phase 1).
- Per-deployment identity/taxonomy blocks could later live in new config files, **without renaming or removing** the current `pipeline_config.json` keys.

Nothing in this phase creates these files. This section only records the direction.

## Validation Rules (proposed for the later implementation slice)

- Config load validates required keys exist and have expected types.
- Unknown keys are warnings, not errors (additive compatibility).
- League/category/platform names are validated as strings usable as path segments.
- `get_leagues()` and friends keep returning current behavior from `pipeline_config.json`.

## Config Compatibility

The World Cup reference config must keep loading with identical behavior. If the later slice adds a validation layer, the current `pipeline_config.json` must pass it without edits.

## Second-Deployment Model Fit

The basketball deployment model provides a different identity, taxonomy, platform set, and prompt selection, but consumes the same validated loading contract and the same archive/output root resolution. It must not require editing any World Cup file.