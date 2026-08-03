# Phase 1 — World Cup Compatibility

The reference deployment is the compatibility contract. Any extraction must keep the World Cup workflow operating exactly as it does today.

## Hard Compatibility Requirements

These must remain true after any change:

1. `python3 scripts/validate_data.py` continues to pass.
2. `pytest` continues to pass (baseline: 541 passed, 1 skipped, 1 warning).
3. `config/pipeline_config.json` remains a valid config source with the current keys and values intact.
4. `get_leagues()` returns the current set: `[PREMIER_LEAGUE, UCL, MLS, LIGA_MX, WORLD_CUP]`.
5. Categories `[EMOTION, AURA, CHAOS, AMERICA]` and platforms `[TikTok, Reels, Shorts]` remain valid as names and path segments.
6. Existing directory paths computed today still resolve: `RAW/WORLD_CUP`, `EXPORTS/<PLATFORM>/<CATEGORY>`, `CLIPS/<CATEGORY>`, `CLIP_MANIFESTS`, `MATCH_RESEARCH/<LEAGUE>`, `TRANSCRIPTS`, `THUMBNAILS`, `CAPTIONS`, `DETECTIONS`.
7. Manifest schemas (`data/manifests/*.json`) and research template (`MATCH_RESEARCH/template.json`) remain readable by current scripts.
8. Existing archive root resolution via `FOOTBALL_ARCHIVE_ROOT` and platform defaults stays intact.
9. Existing World Cup prompts and hashtags remain available to the World Cup workflow.
10. The end-to-end flow — capture → manifest → process → transcribe → research → prompt → detect → clip manifest → export → review → mythology — stays runnable.

## Behavior Lock

The following files represent the reference behavior and must not change as part of extraction:

- `pipeline/config.py`, `pipeline/paths.py`, `pipeline/utils.py`, `pipeline/stadium_signal.py`
- `config/pipeline_config.json` (keys/values)
- All scripts under `scripts/`
- `prompts/thumbnail_prompt_template.txt`
- `data/worldcup_2026_schedule.csv`
- `data/manifests/*.json`
- Existing tests

## Compatibility Verification

After any code change, run:

```bash
python3 scripts/validate_data.py
pytest
```

Additionally verify `git diff --check` passes and that no existing config key, path, or schema is broken by the change.

## Compatibility vs Extraction

Extraction is additive. A new validated config loader may read additional keys and reuse existing keys, but it must not rename, remove, or reinterpret existing keys such that the World Cup flow changes behavior.

## Second-Deployment Compatibility

The basketball model must consume only the extracted, reusable concepts (identity, taxonomy, platform, prompt selection, archive/output roots) and the validated config loading. It must not require changing any World Cup file, including `config/pipeline_config.json`, World Cup prompts, or the schedule.

## Important: Tests

The plan must preserve or extend the existing test baseline. Where new behavior is introduced, the plan must add tests (see `08_TEST_STRATEGY.md`). The expected new test set is listed in `09_IMPLEMENTATION_PLAN.md`.