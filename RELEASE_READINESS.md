# Stadium Signal Release Readiness Audit

Date: 2026-08-03

Branch: `main` tracking `origin/main`

Verified baseline:

- `python3 scripts/validate_data.py`: passed.
- `pytest`: 541 passed, 1 skipped, 1 warning.

Detailed Phase 0 evidence is recorded in `planning/phase-0-verification/`.

## Current Readiness

The repository is ready to operate as a local-first World Cup clipping pipeline reference deployment when the operator has the required Python packages, FFmpeg/ffprobe, local media, and any selected model credentials or local services.

The repository is not ready to be sold as a self-serve platform. It does not include authentication, billing, multi-tenancy, direct publishing, generalized client onboarding, or a hosted dashboard.

## What Works

- CSV and JSON metadata validation passes.
- The pytest suite passes after Phase 0 environment/test reliability fixes.
- World Cup match, manifest, archive-root, transcription, detection, export, and static review workflows are represented in scripts and tests.
- Dry-run behavior exists on several expensive workflows.
- `.env.example` documents the environment variables used by current code.
- `README.md`, `CONTRIBUTING.md`, and `SECURITY.md` now describe the current repository rather than unrelated or stale project state.

## Main Gaps

Critical:

- Commercial use requires source-by-source rights confirmation before processing or delivering clips.

High:

- A generated FFmpeg command execution path still uses shell execution in `pipeline/stadium_signal.py`.
- API and subprocess error handling is inconsistent across scripts.
- Paid work depends on local environment readiness: FFmpeg/ffprobe, optional curl, local media, optional Ace Stream, optional Ollama, and hosted API credentials.
- No paid-pilot runbook, rights checklist, brand intake, or job log exists yet.

Medium:

- Football and World Cup assumptions remain in prompts, config, categories, and workflow defaults.
- Integration coverage with real media is limited in this workspace; one media-duration test is skipped when the sample file is absent.
- No schema validation exists for `config/pipeline_config.json`.
- No generic organization, project, workflow, source, brand, or job model exists yet.

## Responsible Paid Pilot Offer

The responsible offer is a managed pilot, not a product launch:

- Client supplies local media files.
- Client confirms rights and permitted delivery use.
- Operator runs the local pipeline.
- Outputs are reviewed manually.
- Delivery happens through a shared folder or manual handoff.
- Scope stays close to sports/game highlight production, where the current World Cup deployment is strongest.

Do not offer self-serve onboarding, automated billing, multi-client portals, direct publishing, guaranteed live capture, or broad non-sports workflows yet.

## Recommended Next Sprint

Build only the minimum viable paid pilot layer:

- Pilot runbook.
- Source intake template.
- Rights checklist.
- Brand intake template.
- Basic job log.
- Local-file source manifest.
- Safer FFmpeg command execution and clearer operator-facing errors.

Keep the World Cup implementation working as the reference deployment.
