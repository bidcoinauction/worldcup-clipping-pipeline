# Pilot Readiness Gaps

## Verified Capabilities

- Data validation works.
- Pytest suite passes with 541 passed, 1 skipped, and 2 warnings.
- CSV and JSON metadata conventions exist.
- Match manifests exist and are tested.
- Archive root resolution exists through `FOOTBALL_ARCHIVE_ROOT` and platform defaults.
- FFmpeg export and processing commands are covered by tests, mostly with mocks.
- OpenAI, Claude, Ollama, and faster-whisper integration points exist.
- Static review dashboard generation is tested.
- The World Cup implementation is a functioning reference deployment.

## Critical Gaps For Paid Work

- No documented rights intake and approval record.
- No paid pilot runbook.
- No job log that records source, operator, command, status, errors, outputs, and delivery handoff.
- No client/project config boundary.
- No brand intake template or brand-safe output checklist.

## High Gaps

- `shell=True` remains in one FFmpeg execution path.
- Error handling for API and subprocess failures is inconsistent.
- Local environment requirements are not machine-verifiable beyond tests and docs.
- Test coverage is mostly mocked; real FFmpeg/local-media integration is not fully exercised in this workspace.

## Medium Gaps

- World Cup and football assumptions remain in prompts, categories, hashtags, and config.
- No generic source manifest for non-World Cup local files.
- No delivery manifest for client handoff.
- No reporting beyond manifests and generated files.

## What Can Responsibly Be Offered Now

A managed pilot, not a platform:

- Client supplies local media files and confirms rights.
- Operator runs the existing local pipeline with documented manual steps.
- Outputs are reviewed manually before delivery.
- Delivery happens through a shared folder or manual handoff.
- Scope stays close to sports/game highlight production, where the current reference deployment is strongest.

Do not offer yet:

- Self-serve upload or dashboard.
- Authentication or multi-tenancy.
- Billing automation.
- Direct publishing integrations.
- Guaranteed live capture.
- Generic support for all media verticals.
