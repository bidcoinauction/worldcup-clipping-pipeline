# Stadium Signal Release Readiness

Updated: 2026-10-05. Documentation review against `main` at `b30cd8e`.

This replaces the current-status conclusions of the 2026-08-03 audit. Historical Phase 0 evidence remains in `planning/phase-0-verification/`; its 541-test count and then-missing pilot/configuration features are not the present release baseline.

## Current readiness

Stadium Signal supports a managed, local-first football story and clipping workflow through CLI scripts and a working local browser Operator Console. Operators must provide cleared local media, Python dependencies, FFmpeg/ffprobe, and the selected model credentials or local services.

The console supports project/source setup, rights confirmation, football analysis, moments, story suggestions, short-/medium-/long-form edit briefs, deterministic EDLs, and FFmpeg rough cuts. A rough cut is an editorial review artifact, not automatic approval or delivery.

The repository is not a self-serve hosted platform. Authentication, billing, multi-tenancy, automated onboarding, direct publishing, and durable distributed/background execution are not included.

## Validation evidence

The README records the latest macOS baseline as:

- `python3 scripts/validate_data.py`: passed.
- `pytest`: 1128 passed, 1 skipped, 1 warning.

This is a previously recorded baseline, not a claim that this documentation review reran the suite on macOS or validated a real-media production job. Reproduce checks in the intended operating environment with:

```bash
python3 scripts/validate_data.py
pytest
python3 scripts/validate_config.py config/pipeline_config.json
```

Unit/service test results do not replace source-specific rights confirmation, environment readiness, or human review of real-media exports.

## Implemented since the August audit

| Former gap | Current implementation / evidence |
| --- | --- |
| No paid-pilot runbook or rights/brand/source intake templates | `docs/pilot/PILOT_RUNBOOK.md`, `RIGHTS_CONFIRMATION.md`, `BRAND_INTAKE.md`, and `SOURCE_INTAKE.md` |
| No job log or pilot lifecycle | `pipeline.pilot`: durable job records, append-only events, revision-guarded transitions, output review, readiness reports, execution plans, manual run records, delivery packages/checklists/confirmations |
| No config schema validation | `pipeline.config.validate_config_dict`, structured/profile validators in `pipeline.configurator`, and `scripts/validate_config.py` |
| Generated FFmpeg commands executed through a shell | `pipeline.stadium_signal.execute_ffmpeg_commands` now uses parsed argument lists with `subprocess.run(..., check=True)`; the cited shell execution gap is closed |
| CLI-only operator surface | `scripts/console.py`, `pipeline.console_server`, and `pipeline.operator_console` provide a local browser surface over reusable services |
| Story/edit structure only proposed | Story suggestions, edit briefs, deterministic EDLs, and reference/editorial rough-cut services and tests exist |

The local pilot model is implemented; a generalized hosted organization/account/multi-tenant model is still outside scope. Execution plans and manual run records describe operations without executing them, while supported console analysis and rendering do execute their corresponding services.

## Operating scope

Football/World Cup remains the reference deployment. Basketball is a registered additional profile for supported configuration/intake/prompt/planning workflows, with `production_capable=True` but `analysis_supported=False`. Do not sell registration as a working basketball detector or a validated multi-sport production service. The separate `basketball_sandbox` profile is non-production.

Windows is the established Ace Stream capture box; macOS is the development/post-processing box. Processing may remain on Windows or use recordings transferred to macOS. Archive/output/export/provenance paths have portable Windows/POSIX handling, but this does not prove every external dependency is installed or every media workflow has been exercised on both platforms.

Use full-file live recording (`--mode full`), stop with `q`, validate with ffprobe, and avoid reopening Play while FFmpeg owns the stream. The Mexico–South Africa capture is recorded as a validated workflow in `AGENTS.md`; segment mode remains experimental, not a guaranteed live-processing offer.

## Remaining release gates and limitations

- Confirm permitted source-by-source processing and delivery uses before commercial work. Public availability, possession, or stream access does not establish permission; the pilot readiness gate requires current confirmed rights.
- Verify required tools, dependencies, source readability, model availability, and credentials on the actual processing machine. Console analysis preflight reuses valid transcripts or checks FFmpeg and the selected faster-whisper dependency before new transcription.
- Review real-media narrative accuracy, clip bounds, audio, framing, effect execution, and final outputs. Automated tests and model suggestions do not establish editorial quality.
- Keep full live capture operator-controlled; segment automation remains experimental.
- Respect renderer capability reports: partial/deferred effects, music, typography, stem separation, and shot-aware selection are not complete editing automation.
- Treat console disconnect handling and interrupted-analysis recovery as local resilience. They do not provide durable workers, automatic partial-run resumption, or multi-user operation. Error handling across legacy scripts still needs evaluation per workflow.
- Preserve football-specific prompts/defaults where appropriate; universal event normalization, complete basketball analysis, and full channel packaging remain future work.

## Responsible managed pilot

1. Accept client-supplied local media and record source/rights/brand requirements.
2. Validate intake and execution readiness; create the durable job.
3. Run supported console services or the existing CLI workflow, preserving provenance.
4. Review moments, story treatment, edit structure, and rendered outputs manually.
5. Register/review outputs, create the delivery package/checklist, and confirm the manual/shared-folder handoff.

The commercial boundary remains a managed sports pilot with explicit review and delivery. Do not promise self-serve accounts, automated billing, client portals, autonomous publishing, guaranteed live capture, or broad non-sports support.

## Next verification priorities

Run and document a representative cleared-media pilot on the intended Windows/macOS setup, recording actual dependency/model versions, artifacts, review results, and handoff. Use that evidence to address specific failures before expanding scope. Extend new sport, rendering, or console capabilities behind tested services and report their actual capability flags.

[README](README.md) is the operator overview; [console architecture](docs/OPERATOR_CONSOLE_ARCHITECTURE.md) describes implementation; [AGENTS.md](AGENTS.md) defines development/agent rules. Dated planning audits remain historical evidence.
