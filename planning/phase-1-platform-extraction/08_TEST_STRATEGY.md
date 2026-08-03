# Phase 1 — Test Strategy

The reference deployment has a strong test baseline (`pytest`: 541 passed, 1 skipped, 1 warning). Extraction must preserve it and extend it where behavior is added.

## Strategy

1. **Preserve.** No existing test is removed or weakened. `python3 scripts/validate_data.py` and `pytest` must keep passing after any change.
2. **Prove compatibility.** New tests demonstrate the World Cup config still loads identically after extraction.
3. **Test validation.** New config validation gets unit tests (valid config passes, invalid config fails clearly, unknown keys warn).
4. **Test the decision.** Tests cover that specialized (football) content is not treated as extracted platform content.

## What Tests Today Cover

Existing tests cover config loading (`tests/test_config.py`), export (`tests/test_export_clips_ffmpeg.py`, `tests/test_export_research_windows.py`), prompt generation (`tests/test_generate_claude_prompt.py`, `tests/test_generate_asset_prompts.py`), manifest building (`tests/test_build_clip_manifest.py`), research windows (`tests/test_map_research_timestamps.py`), and more (see `tests/`).

## Proposed New Tests (for the later implementation slice)

- **Config validation tests**: current `config/pipeline_config.json` loads identically; required keys validated; unknown keys warn.
- **Identity extraction tests**: account positioning and taxonomy/platform sets resolve to config values, with World Cup defaults preserved.
- **Prompt selection tests**: thumbnail template path resolves from config and defaults to the World Cup template.
- **Root resolution tests**: `FOOTBALL_ARCHIVE_ROOT` and platform defaults resolve as today (covered by existing record_live/export tests).
- **Specialization tests**: assert football-only content (emotions, series, schedule) is not required by the generic extraction contract.

## Validation Commands

```bash
python3 scripts/validate_data.py
pytest
git diff --check
```

## When Tests Are Rerun

Phase 1 is documentation-only: **tests are not rerun** because no code or config changes. Tests are rerun only if a later change touches validation rules, config loading, or any production behavior.