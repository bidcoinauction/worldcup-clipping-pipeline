# Phase 1 — Template and Taxonomy Model

This document separates **reusable template/taxonomy concepts** from **football-specialized content** in the reference deployment.

## Current State

- Taxonomy lives in config and directories:
  - Categories: `EMOTION`, `AURA`, `CHAOS`, `AMERICA` in `config/pipeline_config.json`.
  - Platforms: `TikTok`, `Reels`, `Shorts` in config, mirrored as `EXPORTS/<PLATFORM>`.
  - League catalog: `PREMIER_LEAGUE`, `UCL`, `MLS`, `LIGA_MX`, `WORLD_CUP` via `get_leagues()`.
- Football editorial series/emotions live in `config/series.yml` and `config/emotions.yml` (specialized).
- Prompt templates:
  - `prompts/thumbnail_prompt_template.txt` — image prompt template (referenced by config `paths.thumbnail_template`).
  - Detection/transcription prompt language is built in `scripts/generate_claude_prompt.py` (football-specific).
- Research template: `MATCH_RESEARCH/template.json` (event taxonomy).

## Template Model

### Reusable (extractable)

| Template | Current Location | Reuse |
|---|---|---|
| Prompt template selection | `config/pipeline_config.json:paths.thumbnail_template` | Deployment picks which prompt template applies. |
| Clip-mode profiles | `clip_modes` + `default_clip_mode` in config | Reusable formatting rules. |

### Specialized (KEEP)

| Template | Why specialized |
|---|---|
| Football detection prompt language (VAR/goal/card, American audience) | Football-specific rules in `scripts/generate_claude_prompt.py`. |
| World Cup hashtags and captions | Tournament/brand voice in `scripts/generate_caption_bank.py`. |
| Football emotion/series vocabulary | `config/emotions.yml`, `config/series.yml` are football-specific. |

## Taxonomy Model

### Reusable

- Categories and platforms are deployment-owned sets (chosen per deployment), validated as path-safe names.
- The editorial contract — every moment has a narrative function and emotional metadata — is preserved as a contract, even though its vocabulary stays specialized.

### Specialized

- Football event types (goal, VAR, card, celebration, full-time) and research windows remain World Cup-side.
- World Cup schedule and league naming remain specialized.

## Decision Rule

A taxonomy/template entry is reusable if a second deployment (basketball model) can apply it without changing its meaning. Football-specific vocabulary and tournament voice are KEEP SPECIALIZED.

## Preserved Behavior

The World Cup workflow keeps using its existing templates and taxonomy names; no template or taxonomy is renamed or moved in Phase 1.