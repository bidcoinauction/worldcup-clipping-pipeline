# Brand Intake

## Purpose

Collect the minimum brand language needed to route a pilot through the
existing **brand profile model** (`pipeline/configurator.py` + `config/brands/`).
This is not a design questionnaire and not a broad branding exercise.

## What to collect

For a new brand, create a validated profile under `config/brands/` following
`config/brands/world_cup.json` (the reference structure) and reference it by
identifier in the intake (`configuration.brand`). Collect only:

- **Display name** — the public-facing brand name.
- **Positioning** — the one-line brand positioning (used by the prompt layer).
- **Tone** — caption tone guidance.
- **Hashtags** — the default hashtag set (written with a leading `#`).
- **Platform overrides** — per-platform hashtags where they differ from the
  default (optional).
- **Logo / font / color references** — existing references only if a workflow
  will use them (optional; the reference deployment does not use them yet).
- **Approval requirements** — who must approve brand-safe output before
  delivery.

## What is NOT collected here

- Broad visual identity systems
- Full brand guidelines
- Detailed design assets
- Anything outside the brand profile schema

## Referencing instead of duplicating

The intake **references** brand, taxonomy, template, and export profiles by
identifier. It never duplicates the referenced configuration. Registered
profiles resolve through the existing configuration layer; unknown references
fail validation with their full field path.

## Existing profiles

- `config/brands/world_cup.json` — production reference brand (`world_cup`).
- `config/brands/basketball_example.json` — non-production example, never the
  default.

## Validate

```bash
python3 scripts/validate_config.py config/brands/your_brand.json
```