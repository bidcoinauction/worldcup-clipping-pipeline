# Phase 0 Baseline

Date: 2026-08-03

## Git State Before Work

- Branch: `main`
- Tracking: `origin/main`
- Initial status: clean

## Scope

Phase 0 verifies the existing World Cup clipping pipeline. It does not start broad product implementation.

The current reference deployment remains the World Cup/Stadium Signal workflow: local-first metadata, match manifests, transcription, model-assisted detection, FFmpeg export, and static review artifacts.

## Repository Shape

- `planning/business-architecture/`: completed business architecture blueprint.
- `data/`: CSV datasets and match manifests.
- `MATCH_RESEARCH/`: match research JSON fixtures.
- `config/`: pipeline, series, emotion, and match URL config.
- `pipeline/`: shared Python modules.
- `scripts/`: CLI workflow entry points.
- `tests/`: pytest suite.
- `FootballArchive/`: tracked reference assets and dashboard HTML, but large video/audio assets remain ignored by extension and should live outside Git.

## Configured Tooling

- `pytest.ini` configures `pythonpath = .`.
- No `pyproject.toml`, `setup.cfg`, `tox.ini`, `Makefile`, or pre-commit config exists.
- No lint, format, or type-check command is configured.

## Environment Observations

- `python` command is not available in this macOS workspace.
- `python3` is available as Python 3.14.5 but does not have `pytest` installed.
- `python3.12` is available but does not have `pytest` installed.
- The `pytest` console command runs under Python 3.9.6 and has pytest 8.3.4 installed.

## Initial Blockers Found

- `python scripts/validate_data.py` failed because `python` is not on `PATH`.
- Initial `pytest` collection failed under Python 3.9.6 because modules and two tests used Python 3.10 union annotation syntax without postponed annotations.
- One test depended on missing local media: `FootballArchive/SAMPLES/psg_arsenal_2min.mp4`.
- One test polluted `sys.modules` with a fake `pipeline.whisper_transcriber` module, affecting later tests.
- `pipeline.whisper_transcriber` imported `faster_whisper` at module import time, making it hard to import and mock when the optional local Whisper dependency is absent from the active test interpreter.

## Phase 0 Fixes Applied

- Added postponed annotations to affected modules and tests so the installed Python 3.9 pytest runner can collect the suite.
- Made `pipeline.whisper_transcriber` importable without `faster_whisper` installed and fail clearly only when local Whisper transcription is invoked.
- Exposed `pipeline.whisper_transcriber` from `pipeline/__init__.py` for stable test mocking.
- Scoped live-watch test `sys.modules` mocking with `patch.dict`.
- Skipped the duration test when its local media sample is missing.
- Registered the `network` pytest marker.
- Added missing dependency declarations for directly imported `anthropic` and `urllib3`.
- Expanded `.env.example`, security guidance, contributing guidance, release readiness, and README documentation.

## Current Verified Baseline

- `python3 scripts/validate_data.py`: passed.
- `pytest`: 541 passed, 1 skipped, 2 warnings.
