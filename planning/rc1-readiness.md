# RC1 Readiness

Release Candidate 1 hardening. Status reflects only what has actually been
completed; unverified areas remain open.

## Tracked Areas

### Installation

- Supported environment documented in README (Python 3.11/3.12, FFmpeg/ffprobe,
  optional faster-whisper, optional OpenAI).
- First-run `python scripts/init_project.py` creates directories, initializes the
  runtime DB, runs additive migrations, verifies writable storage, and prints the
  next steps. Idempotent.
- No large models are downloaded automatically.

### Startup

- `python scripts/console.py` runs a core preflight (runtime DB migrate with safe
  backup, writable storage, critical imports) before binding.
- Startup failures produce concise operator-safe messages and point to the Doctor.
- Port-in-use produces a clear error with `--port` guidance.

### Migration

- Runtime schema versioning via `schema_metadata` (schema_version, migrated_at).
- Ordered, additive, idempotent migrations (schema v1..v10) recorded only after
  success.
- Existing Phase 1–5 databases upgrade safely; representative pre-Moment, Phase 3,
  and Phase 5 schemas are covered by upgrade tests.
- A failed migration does not advance the schema version and keeps the backup.

### Backup

- Automatic pre-upgrade backup for non-empty existing databases (SQLite online
  backup API).
- Manual `create_runtime_backup()` / `list_runtime_backups()`.
- Backups live under `data/pilot/backups/` (overridable with
  `STADIUM_RUNTIME_BACKUPS`); existing backups are never overwritten.
- Retention automation deferred.

### Doctor

- `scripts/doctor.py` returns structured checks (check_id, status,
  operator_message, technical_detail, recommended_action).
- Statuses: PASS / WARN / FAIL / SKIP.
- Normal mode: core failure fails; missing optional capability warns.
- `--strict` promotes warnings to failures.
- Optional integrations report independently and never fail the core Doctor
  unless explicitly requested (`--include-integrations`).

### Error Safety

- `pipeline/safety.py` sanitizes obvious secret patterns from operator-facing
  messages.
- Core errors are operator-safe; technical detail stays in logs/debug.
- Logging setup writes timestamped/severity/component/message to `LOGS/clipper.log`
  (rotating) plus stderr.

### Console Smoke Test

- Automated smoke test: temp runtime → initialize → migrations → core Console
  load → list projects → system health read. No external API calls.

### Core Workflow Smoke Test

- Full Phase 1–5 test suite green; core workflows independent of integrations.

## Remaining RC1 Blockers / Open

- Packaging (installer/zip distribution) — not started.
- Performance profiling on long real-world matches — not started.
- Real external adapter uploads (OORT/Airtable/Slack) require credentials and are
  not exercised; adapter boundary is unit-tested with mocks.
- UX polish of the Console is ongoing.
- Full manual operator run on a real match as a single RC1 workflow.

## RC1 Slice 2 Status

### Real-Match Workflow

- `scripts/smoke_real_match.py` exercises the canonical workflow
  (source validation → transcription → detection → Moment normalization →
  Story → Edit Brief → EDL → render) with per-stage timing and reuse flags.
- `--fast` runs deterministically without network using real runtime/service
  boundaries; `--full` requires an operator-provided source and real models.
- Smoke reports are written to `data/pilot/smoke/<project_id>/smoke_report.json`.

### Performance Instrumentation

- `pipeline/performance.py` records stage timing (start/finish/duration/status/
  reused) and renders an operator-readable summary.
- Slow-stage info is logged via `pipeline/logging_setup.py`.

### Failure Injection

- `--fail-after <stage>` deterministically fails a chosen stage (used for
  recovery validation, not production randomness).

### Recovery

- Detection failure retry reuses the transcript; the failed PipelineRun is not
  mutated; new runs are created on retry.
- Story/Moments survive analysis failure.
- Interrupted render reconciliation (`reconcile_interrupted_renders`) marks stale
  `RENDERING` renders as `FAILED`/`RENDER_INTERRUPTED` without touching live work.
- Interrupted analysis recovery remains a hard RC requirement and is covered by
  existing regression tests.

### Reuse Verification

- Re-running the same smoke input reuses the transcript, keeps stable canonical
  Moment IDs, and does not create duplicate Moments/Stories/Renders.

### Temp Cleanup / Partial Output

- Fixture artifacts are namespaced under smoke projects; source media is never
  deleted. Full temp-cleanup audit of every renderer path is not complete.

### Long-Media Profiling

- Instrumentation is in place; a real long-media run still requires an
  operator-provided file and was not executed in this slice.

## RC1 Final Matrix (Slice 3)

| Area | Status |
| --- | --- |
| Code architecture (Phases 1–5) | PASS |
| Install (documented, venv + `init_project`) | PASS |
| Startup (preflight + operator-safe errors) | PASS |
| Schema migration (versioned, additive, backup) | PASS |
| Backup / recovery | PASS |
| Health diagnostics (doctor / release_check / /system) | PASS |
| Automated workflow smoke | PASS |
| Failure / interrupted-run recovery | PASS |
| Console UX (workflow strip + next action + empty states) | PASS |
| Packaging (pyproject + entry points, editable) | PASS |
| Security hygiene (secrets sanitized, gitignore) | PASS |
| Real long-media run | BLOCKED — `OLLAMA_NOT_INSTALLED` (Ollama binary absent). Provider-completion code now supports Ollama for detection, Story, and Edit Brief (per-stage selection); real transcription validated and reused. See rc1-operator-validation.md |
| Live optional integrations (OORT/Airtable/Slack) | PENDING — contract tests pass, no credentials |

## Provider Completion (Story + Edit via Ollama)

- `STORY_PROVIDER` (default `openai`) and `EDIT_PROVIDER` (default = `STORY_PROVIDER`)
  select the provider per stage; detection keeps `providers.detection`.
- `pipeline/provider_service.py` exposes `ollama_generate()` (shared helper) and
  independent readiness for Detection / Story / Edit (Ollama model-presence check,
  or OpenAI key).
- Doctor and the Console `/system` Providers card report each stage separately
  (`Detection Ollama · Ready`, etc.). A fully-Ollama workflow no longer warns about
  a missing OpenAI key.
- Story/Edit parser contracts, artifact formats, and downstream EDL behavior are
  unchanged; OpenAI remains the default and is fully preserved.
- Provider-unavailable Story/Edit fail safely and preserve the prior stage
  (Moments / approved Story).
- Automated coverage: `tests/test_provider_story_edit.py` (20 tests) plus
  `tests/test_provider_service.py`. The fully-local path
  (`faster-whisper + Ollama + FFmpeg`) is code-complete and unit-tested; real-media
  validation of the full chain still requires Ollama to be installed on the
  validation machine.

## RC Status

RC1 CODE COMPLETE — REAL-MEDIA VALIDATION BLOCKED (Ollama not installed on the
validation machine; detection provider `ollama`, model `llama3.1`). Real
transcription validated on a real match; the full editorial chain requires a
running Ollama with the configured model pulled.

## Frontend / Backend Integration (RC1 Slice 3)

- Project dashboard shows per-project Attention + Next action and a welcome
  empty state.
- Project detail shows a derived Workflow strip, an attention badge, and one
  primary CTA (Analyze / Review Moments / Approve Story / Generate Edit /
  Prepare Cut / Generate Rough Cut / Review Rough Cut / Create Export).
- Moment review shows filter counts and review actions; Story builder exposes
  Move Up / Move Down / role dropdown / Add Moment / Remove.
- Rough-cut review shows an inline video player, Download Video, and
  Ready to Export; platform presets show aspect ratio and dimensions.
- Export offers Create Export Package and Download Video.
- Navigation (Projects / Batches / System), breadcrumbs, and a small CSS design
  system (attention badges, danger buttons, empty states) added.
- Full operator journey integration test passes end-to-end through the real
  Console API and service boundaries (mock external/model/media only).