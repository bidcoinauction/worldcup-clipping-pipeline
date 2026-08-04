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

### Archive-root precedence

The canonical `resolve_archive_root`/`resolve_output_root` in `pipeline/configurator.py` resolve the output root in this order:

1. Explicit override (e.g. CLI output argument)
2. Structured project `outputs.directory`, when a profile configures it
3. `FOOTBALL_ARCHIVE_ROOT`
4. Platform default (`C:\FootballArchive` on Windows, `FootballArchive` elsewhere)

The built-in World Cup profile does not set `outputs.directory`, so its resolution stays environment/default. Structured output roots accept absolute or repository-relative paths; invalid types and `..` traversal are rejected with `ConfigurationError`. Resolution is read-only: it never creates directories and makes no network calls. The canonical `resolve_archive_path` is used by `pipeline/stadium_signal.py`, `scripts/record_live.py`, and `scripts/live_watch.py` (each keeps a thin `archive_root`/`archive_path` wrapper that delegates to the canonical functions).

### Editorial taxonomy

The World Cup editorial language is separated from the operational `categories` list as a data-backed taxonomy:

```text
config/editorial/world_cup.json
```

It holds `emotional_kinds`, `narrative_functions`, and `story_targets` (arc roles and narrative roles). `pipeline/configurator.py` resolves it via `resolve_editorial_taxonomy()`, `resolve_story_targets()`, and `resolve_operational_categories()`. Unknown keys or wrong types raise `ConfigurationError` with the full field path. This is a distinct surface from the legacy operational `categories` in `config/pipeline_config.json`, which remains intact. `config/examples/basketball.json` demonstrates the same separated taxonomy structure for a second sport (non-production).

### Brand profiles

Brand language is extracted to validated data files under `config/brands/`:

```text
config/brands/world_cup.json            # production (reference deployment)
config/brands/basketball_example.json   # non-production example
```

A brand profile carries `id`, `display_name`, `positioning`, `caption_tone`, `language`, default `hashtags` (with leading `#`), optional per-platform `platforms` hashtag overrides, and optional `assets` metadata (thumbnail guidance, plus `logo`/`font` path references only if a workflow ever uses them). Unknown keys, wrong types, invalid hashtags, and unsafe asset paths raise `ConfigurationError` with the full field path.

`pipeline/configurator.py` resolves brands via `resolve_brand_profile()`, `resolve_brand_hashtags()`, `resolve_brand_positioning()`, `resolve_brand_language()`, and `resolve_brand_caption_tone()`. Brand positioning precedence:

1. Explicit override (function/CLI argument)
2. Selected brand profile `positioning`
3. Legacy `account_positioning` in `config/pipeline_config.json`
4. `ACCOUNT_POSITIONING` environment variable (legacy fallback only)
5. Historical default `America Discovers Football`

Configuration always wins over the environment variable. `scripts/generate_asset_prompts.py` resolves caption hashtags from the selected brand (default `world_cup`; `--brand` to override) instead of embedding them, and exits nonzero with a concise error on an invalid brand selection. The World Cup hashtags remain byte-identical (`#worldcup #football #soccer`).

### Export profiles

Export behavior is extracted to a validated data file:

```text
config/export/world_cup.json
```

It contains two namespaces:

- `platforms` — TikTok / Reels / Shorts profiles (width, height, frame rate, codecs, bitrate, extension, filename suffix, and destination template) consumed by `scripts/export_clips_ffmpeg.py` via `resolve_platform_export_profile()` and `resolve_export_destination()`.
- `profiles` — research window profiles (`vertical_clean`, `vertical_blur`, `vertical_review`, `vertical_safe`, `vertical_zoom`, `vertical_social`, `vertical_social_dynamic`, `goal_context`, `source`) consumed by `scripts/export_research_windows.py` via `resolve_export_profile()` for the encoding arguments.

Values reproduce the historical behavior exactly (1080x1920, libx264/veryfast/CRF 20, AAC, `EXPORTS/<PLATFORM>/<CATEGORY>/<clip_id>_<platform>.mp4`, `CLIPS/<match>/<clip>.mp4`). The crop/fit filter chains themselves remain in the exporter scripts; profiles describe dimensions, codecs, naming, and destinations. Unknown profiles, unknown platforms, invalid dimensions/codecs, and unsafe destinations raise `ConfigurationError`; `resolve_export_profile()` and `resolve_platform_export_profile()` fail loudly rather than silently falling back.

### Positioning precedence

`pipeline/configurator.resolve_project_identity()` resolves account positioning in this order:

1. Explicit structured project configuration
2. Legacy `account_positioning` in `config/pipeline_config.json`
3. `ACCOUNT_POSITIONING` environment variable (legacy fallback)
4. Historical default `America Discovers Football`

Configuration always wins over the environment variable. `scripts/generate_claude_prompt.py` routes through this resolver and no longer carries its own fallback.

### Detection template

The World Cup detection prompt is a tracked, registered template:

```text
prompts/world_cup_detection_prompt.txt
```

It is rendered by `pipeline/configurator.render_template()` (standard library only, no Jinja). Only registered templates can be rendered; unknown template IDs, missing files, missing required variables, and path-traversal attempts raise `ConfigurationError` with the template identifier and no silent fallback. Rendering is read-only (no network access, no file mutation) and deterministic.

`config/examples/basketball.json` is a **non-production example** proving the structured boundary for a second sport. It is never registered as a default profile and is not loaded at runtime. Its detection template (`prompts/basketball_detection_prompt.txt`) resolves for validation but is not registered for rendering.

Validate any configuration file (read-only, no network, no file mutation):

```bash
python3 scripts/validate_config.py                      # reference config
python3 scripts/validate_config.py config/examples/basketball.json
python3 scripts/validate_config.py config/brands/world_cup.json
python3 scripts/validate_config.py config/export/world_cup.json
```

## Managed Pilot Operations

Phase 2 adds a validated **pilot intake manifest**, an explicit **rights gate**,
read-only **source validation**, and a durable **job record** for one managed
local-file sports pilot. It wraps the existing pipeline; it does not replace
or modify any World Cup manifest, schedule, or clip workflow.

### Intake manifest

A pilot intake is a JSON file (schema version 1) describing the pilot, the
client-supplied media, rights confirmation, configuration references, and
review/delivery settings. Start from the tracked non-production example:

```text
docs/pilot/examples/world_cup_pilot_example.json
```

Configuration is **referenced** by identifier (project, brand, editorial
taxonomy, detection template, export profiles, delivery destination) and
resolved through `pipeline/configurator.py` — never duplicated. Unknown keys,
wrong types, credential-like keys/values, and unsafe paths are rejected with
full field paths.

### Rights gate

Rights states: `UNCONFIRMED`, `CONFIRMED`, `RESTRICTED`, `EXPIRED`, `REJECTED`.
Only `CONFIRMED` (unexpired) rights pass the execution-readiness gate.
`RESTRICTED` validate structurally but require an explicit supported-use check.
The gate never infers permission from public availability, stream access, file
possession, or prior clipping. See `docs/pilot/RIGHTS_CONFIRMATION.md`.
Transitions into `READY`, `RUNNING`, `DELIVERY_READY`, and `DELIVERED` re-read
the stored intake and revalidate current rights status and expiration.

### Source validation

Read-only validation of the local media file: exists, regular, readable,
non-empty, allowed extension, optional SHA-256 checksum, optional duration via
ffprobe (reported as an environmental limitation when ffprobe is unavailable),
and containment within a configured intake root (`STADIUM_PILOT_INTAKE_ROOT`).
Network URLs are rejected. Validation never modifies, moves, copies, or
transcodes media.

### Job records

`create` writes a durable job record plus an append-only event log under
`data/pilot/jobs/` (gitignored). Deterministic initial state:
`READY` (execution-ready), `AWAITING_RIGHTS` (source-ready but rights not
cleared), or `VALIDATION_FAILED` (structural/config/source validation failure).
Duplicate job identifiers are refused. Writes are atomic and never leave the
configured job root. The record stores identifiers and readiness only — never
intake confirmation or personal data.

Manual transitions are explicit and revision-guarded. New jobs start at
revision `0`; each successful transition increments the revision and appends
one event. Existing jobs without a revision remain readable and gain a revision
on their next successful transition. See `docs/pilot/JOB_TRANSITIONS.md`.

### CLI

```bash
python3 scripts/pilot_job.py validate path/to/intake.json
python3 scripts/pilot_job.py create   path/to/intake.json --operator YOUR_NAME
python3 scripts/pilot_job.py show     JOB_ID
python3 scripts/pilot_job.py transition JOB_ID RUNNING --operator YOUR_NAME
python3 scripts/pilot_job.py history  JOB_ID
python3 scripts/pilot_job.py list
```

`validate` makes no file changes and exits zero only when structurally valid.
Expected operational errors print concise messages to stderr without
tracebacks. The CLI never processes media, calls models, or touches the
network. `transition` records manual operations only: `RUNNING` does not run
the pipeline, and `DELIVERED` does not upload or send files.

### Relationship to existing manifests

```text
Pilot intake -> rights gate -> source validation -> job record (READY)
    -> RUNNING (manual pipeline started) -> REVIEW_REQUIRED -> APPROVED
    -> DELIVERY_READY -> DELIVERED
```

The existing match manifest (`data/manifests/*.json`), schedule CSV, and clip
manifests are unchanged and remain independently usable. Full runbook and
intake templates: `docs/pilot/PILOT_RUNBOOK.md`.

## Validation

```bash
python3 scripts/validate_data.py
pytest
python3 scripts/validate_config.py config/pipeline_config.json
```

Verified baseline on macOS:

- `python3 scripts/validate_data.py`: passed.
- `pytest`: 746 passed, 1 skipped, 1 warning.

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
- Managed pilot operations are the current build phase: validated intake, rights gate, source validation, and job records exist; media processing, review, approval, and delivery remain manual operator steps.
