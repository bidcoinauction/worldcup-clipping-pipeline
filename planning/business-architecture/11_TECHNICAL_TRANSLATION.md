# Technical Translation

## Purpose

This document maps the business architecture to future repository changes. It is not an implementation plan by itself.

## Current Technical Baseline

The repository is currently script-first and football-focused.

Reusable parts:

- Local file processing.
- Transcription.
- Timestamp handling.
- FFmpeg export.
- CSV/JSON manifests.
- Static review dashboard.

Specialized parts:

- World Cup schedule processing.
- Football prompt templates.
- Soccer event taxonomy.
- Football archive naming.
- World Cup hashtags and positioning.

## Business Concept To Technical Concept

| Business Concept | Future Technical Shape |
|---|---|
| Organization | `organizations/*.json` or database table later |
| Project | Project config and folder root |
| Workflow | Template config selecting source, analysis, transformation, review, and delivery steps |
| Job | Durable job manifest with status, timestamps, errors, costs |
| Output | Generated transcript, clip, caption, thumbnail prompt, metadata package, or review artifact |
| Asset | Asset manifest with type, path, provenance, rights status |
| Brand system | Brand config with captions, hashtags, export rules, visual assets |
| Content source | Source adapter interface or source manifest schema |
| Offerings | Operating runbooks and later workflow presets |

## Likely Files To Modify Later

Do not modify these during the business architecture phase. These are likely future touchpoints:

- `config/pipeline_config.json`.
- `.env.example`.
- `scripts/process_match.py`.
- `scripts/process_scheduled_match.py`.
- `scripts/process_from_manifest.py`.
- `scripts/generate_claude_prompt.py`.
- `scripts/generate_asset_prompts.py`.
- `scripts/export_research_windows.py`.
- `scripts/build_stadium_dashboard.py`.
- `pipeline/stadium_signal.py`.
- `pipeline/config.py`.

## Likely New Files Later

Potential future files after approval:

- `config/organizations/example.json`.
- `config/projects/example.json`.
- `config/brands/example.json`.
- `config/workflows/game_highlights.json`.
- `config/workflows/podcast_quotes.json`.
- `config/source_types/local_file.json`.
- `docs/pilot_runbook.md`.
- `docs/brand_intake_template.md`.
- `docs/source_intake_template.md`.

## First Technical Boundary

The first implementation phase should not create a full platform. It should make one pilot easier to run.

Minimum useful technical changes:

- Add explicit pilot configuration.
- Move hardcoded account positioning and hashtags into config.
- Create a generic local-file source manifest.
- Create a simple job log.
- Add validation around expected inputs and outputs.
- Keep World Cup paths working.

## What Should Remain Specialized

Some current files can remain case-study-specific:

- `data/worldcup_2026_schedule.csv`.
- `MATCH_RESEARCH/WORLD_CUP/*`.
- Existing World Cup manifests.
- Football-specific prompts and seed data.

The goal is not to erase the case study. The goal is to prevent it from blocking other deployments.

## Migration Principle

Prefer additive changes:

- Add generic config alongside existing World Cup data.
- Add templates before deleting hardcoded logic.
- Add validation before adding automation.
- Add manual runbooks before dashboards.

## Validation Commands For Future Technical Phases

After code changes, run:

```bash
python scripts/validate_data.py
pytest
```

If a future phase adds linting, formatting, type checking, or CI, document those commands here.
