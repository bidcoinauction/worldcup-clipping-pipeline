# Phase 1 — Assumption Register

This register classifies every concept in the reference deployment as either **EXTRACT NOW** (reusable across deployments), **EXTRACT LATER** (reusable but not required for the first slice), or **KEEP SPECIALIZED** (football/World Cup-specific and stays on the case study).

Each row names the concrete artifact from the repository so the classification is grounded in real code.

## EXTRACT NOW

These are reusable platform concepts the first implementation slice should lift into config and validated loading, while the reference deployment continues to work.

| Concept | Current location | Why extract |
|---|---|---|
| Project identity / account positioning | `config/pipeline_config.json:account_positioning` = `"America Discovers Football"`; also backed by `ACCOUNT_POSITIONING` env (see `.env.example`); read in `scripts/generate_claude_prompt.py:344` | A second deployment has different positioning. This is a value, not a behavior. |
| Taxonomy set (categories) | `config/pipeline_config.json:categories` = `[EMOTION, AURA, CHAOS, AMERICA]`; mirrored as clip dirs in `pipeline/paths.py` (`CLIPS/EMOTION`, `CLIPS/AURA`, `CLIPS/CHAOS`, `CLIPS/AMERICA`) | Deployment type defines which categories apply. |
| Platform set | `config/pipeline_config.json:platforms` = `[TikTok, Reels, Shorts]`; mirrored as `EXPORTS/TIKTOK`, `EXPORTS/REELS`, `EXPORTS/SHORTS` in `pipeline/paths.py` and CLI choices in `scripts/export_clips_ffmpeg.py:58` | Deployment determines target platforms. |
| Clip mode profiles | `config/pipeline_config.json:clip_modes` + `default_clip_mode` | Profiles are reusable formatting rules. |
| Prompt template selection | `prompts/thumbnail_prompt_template.txt` referenced by `config/pipeline_config.json:paths.thumbnail_template` | Which prompt template to use is deployment/identity-specific. |
| Catalog/provider/model selection | `config/pipeline_config.json:leagues`, `models`, `providers`, `get_leagues()` in `pipeline/config.py` | Model/provider and catalog membership are deployment choices. |
| Archive/output roots | `FOOTBALL_ARCHIVE_ROOT` (see `AGENTS.md`, `README.md`), platform defaults in `scripts/record_live.py:90` and `pipeline/utils.py:ROOT` | Root resolution is a general path rule. |

## EXTRACT NOW — without this, config load has no validation

| Concept | Current Repository | Why extractable |
|---|---|---|
| Config schema / validation | `pipeline/config.py` has no schema validation and uses a cached global `_config`; `CONFIG_PATH` is hardcoded | A validated config loader is a reusable prerequisite for safe extraction. |

## EXTRACT LATER

| Concept | Current Repository | When |
|---|---|---|
| Mythology scoring / emotional metadata contract | `pipeline/stadium_signal.py`, `scripts/mythology_engine.py` | Editorial contract is preserved; only generalize scoring inputs after identity extraction |
| Clip/export/review artifacts | `scripts/build_stadium_dashboard.py`, `scripts/export_research_windows.py`, `scripts/export_clips_ffmpeg.py` | Reusable later; first slice does not touch exports/review. |

## KEEP SPECIALIZED

These stay on the World Cup reference deployment and are deliberately **not** extracted.

| Concept | Current repository | Why specialized |
|---|---|---|
| Football editorial series | `config/emotions.yml`, `config/series.yml` | Football-specific emotional vocabulary and series arcs. |
| World Cup scheduling | `data/worldcup_2026_schedule.csv` | Tournament schedule is case-study data. |
| Football/World Cup prompt language | `scripts/generate_claude_prompt.py`, `prompts/thumbnail_prompt_template.txt` | Football/American-audience language; VAR/goal/card detection. |
| World Cup league name + paths | `WORLD_CUP` in `config/pipeline_config.json`, `RAW/WORLD_CUP`, `scripts/process_scheduled_match.py` | Tournament-specific. |
| World Cup hashtags | `scripts/generate_caption_bank.py`, hashtags in prompts | Tournament/brand voice for the case study. |

## EXTRACT NOW — first implementation slice (candidate)

Proposed first slice from the assumption register: extract **project identity, taxonomy selection, prompt selection, and archive/output roots into validated configuration** while preserving current `pipeline_config.json` behavior.

These four dimensions are config values already (or near-values), not code behavior, so extracting them imposes the lowest risk to the reference deployment.

## Reference Deployment Constraints

For every classification, an extraction must meet the compatibility contract in `03_COMPATIBILITY.md` — the World Cup flow keeps working with the existing values.