---
name: stadium-editorial
description: Rules for editorial metadata — research window schema, narrative functions, emotional angles, match_research.json structure, row-level CSV fields, and validation
compatibility: opencode
metadata:
  project: worldcup-clipping-pipeline
---

## What I do

I standardize how editorial metadata is structured, generated, and validated.

## Rules

- Every match gets 3+ research windows, 3+ narrative acts, and 1 editorial thesis.
- Required per-clip fields: `clip_id`, `moment_label`, `emotional_angle`, `narrative_function`, `start_time`, `end_time`.
- `narrative_function` must be one of: `inciting_incident`, `rising_action`, `climax`, `falling_action`, `resolution`, `character_moment`.
- `emotional_angle` is a one-line hook, not a category — e.g. "Pirlo releases Super Mario into mythology", not "excitement".
- Research windows are stored in `MATCH_RESEARCH/<TOURNAMENT>/<MATCH_SLUG>/`.
- Tournament dirs are UPPER_CASE: `WORLD_CUP/`, `EURO/`, `CHAMPIONS_LEAGUE/`.
- Validate against `MATCH_RESEARCH/template.json` before committing (run `python scripts/validate_data.py`).
- CSV columns map 1:1 to Supabase schema: `clip_id`, `match_title`, `source_file`, `start_time`, `end_time`, `moment_label`, `emotional_angle`, `platform`, `export_profile`.
- `validate_data.py` must pass before committing any editorial changes.

## When to use me

Use this skill when editing `generate_claude_prompt.py`, `mythology_engine.py`, `scaffold_research.py`, `generate_story_arcs.py`, `generate_caption_bank.py`, `validate_data.py`, any JSON/YAML under `MATCH_RESEARCH/`, or editorial fields in CSVs.
