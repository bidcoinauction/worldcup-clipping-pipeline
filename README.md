# Stadium Signal World Cup Clipping Pipeline

Stadium Signal is a local-first football mythology and clipping pipeline. The current reference deployment is the World Cup workflow: match metadata, research windows, transcription, prompt-based moment detection, FFmpeg clip export, and static review artifacts.

The repository is script-first. It is not yet a broad SaaS product, dashboard, billing system, or multi-tenant platform.

## Current Capabilities

- CSV/JSON metadata for matches, moments, emotional timelines, clip windows, mythology scores, and match manifests.
- Local archive path resolution through `FOOTBALL_ARCHIVE_ROOT` or platform defaults.
- Ace Stream recording support for Windows capture boxes through `scripts/record_live.py`.
- Local-file processing through manifest and scheduled-match scripts.
- OpenAI, Claude, Ollama, and faster-whisper integration points, depending on the workflow selected.
- FFmpeg/ffprobe-based audio extraction, concat, duration inspection, and clip export.
- Static review dashboard generation.

## Prerequisites

- Python 3.10+ recommended. The current macOS verification also passes under the installed Python 3.9 `pytest` runner after compatibility fixes.
- FFmpeg and ffprobe on `PATH` for recording, transcription audio extraction, duration checks, concat, and clip export.
- Optional: curl for LiveTV resolver fallbacks.
- Optional: Ace Stream on Windows for live capture.
- Optional credentials for hosted model workflows: `OPENAI_API_KEY` and/or `ANTHROPIC_API_KEY`.
- Optional local services: Ollama at `OLLAMA_URL` for local detection.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

On Windows, use `.venv\Scripts\activate` instead of `source`.

If your shell provides `python` instead of `python3`, either command is acceptable. This workspace currently has no `python` alias, so validation was run with `python3`.

## Environment Variables

See `.env.example` for the supported variables:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `FOOTBALL_ARCHIVE_ROOT`
- `DEFAULT_OPENAI_MODEL`
- `DEFAULT_CLAUDE_MODEL`
- `DEFAULT_TRANSCRIBE_MODEL`
- `DEFAULT_WHISPER_MODEL`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
- `ACCOUNT_POSITIONING` (legacy fallback only; ignored when `account_positioning` is set in `config/pipeline_config.json`)

Do not commit `.env` or files under `secrets/`.

## Configuration

`config/pipeline_config.json` is the reference deployment config (World Cup football). It is read by `pipeline/config.py` (legacy accessors) and validated by `pipeline/config.py:validate_config_dict`. Unknown top-level keys are rejected with their full field path instead of being silently ignored.

The additive structured layer in `pipeline/configurator.py` resolves project identity, taxonomy, templates, platforms, and the canonical archive root (`FOOTBALL_ARCHIVE_ROOT`, falling back to `C:\FootballArchive` / `FootballArchive`). Unknown profile, taxonomy, or template selections raise `pipeline.config_errors.ConfigurationError`.

`config/examples/basketball.json` is a **non-production example** proving the structured boundary for a second sport. It is never registered as a default profile and is not loaded at runtime.

Validate any configuration file (read-only, no network, no file mutation):

```bash
python3 scripts/validate_config.py                      # reference config
python3 scripts/validate_config.py config/examples/basketball.json
```

## Validation

```bash
python3 scripts/validate_data.py
pytest
python3 scripts/validate_config.py config/pipeline_config.json
```

Verified Phase 0 baseline on macOS:

- `python3 scripts/validate_data.py`: passed.
- `pytest`: 541 passed, 1 skipped, 2 warnings.

No lint, format, type-check, tox, Makefile, or pre-commit commands are currently configured.

## Common Workflows

Create or update a match manifest:

```bash
python3 scripts/create_match_manifest.py \
  --match-id mexico_south_africa_2026_06_11 \
  --match-no 1 \
  --home Mexico --away "South Africa" \
  --date 2026-06-11 \
  --source mexico_south_africa_live.ts:first_half
```

Dry-run manifest processing:

```bash
python3 scripts/process_from_manifest.py \
  --manifest data/manifests/mexico_south_africa_2026_06_11.json \
  --dry-run
```

Record live Ace Stream on Windows:

```powershell
python scripts\record_live.py HASH --match-id MATCH_ID --mode full --verbose
```

## Important Boundaries

- Keep video/audio assets outside Git in `FootballArchive/` or another ignored archive root.
- Confirm media rights before commercial processing or delivery.
- Treat the World Cup implementation as the current reference deployment, not as a generic commercial platform.
- For the next build phase, prefer a minimum viable paid pilot around local files, manual review, and shared-folder delivery.
