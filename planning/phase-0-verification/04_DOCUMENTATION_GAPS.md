# Documentation Gaps

## Documents Reviewed

- All files under `planning/business-architecture/`.
- `RELEASE_READINESS.md`.
- `SECURITY.md`.
- `AGENTS.md`.
- `CONTRIBUTING.md`.
- `requirements.txt`.
- `pytest.ini`.
- `config/`.
- `pipeline/`.
- `scripts/`.
- `tests/`.

## Fixes Completed

- Added missing `README.md` with setup, validation, environment, workflow, and boundary guidance.
- Updated `RELEASE_READINESS.md` to reflect the current branch, test baseline, security evidence, documentation state, and pilot recommendation.
- Replaced unrelated `CONTRIBUTING.md` puzzle content with Stadium Signal contribution guidance.
- Expanded `SECURITY.md` with secret-handling and media-rights guidance.
- Updated `AGENTS.md` validation wording to include `python3` when `python` is unavailable.

## Remaining Documentation Gaps

- No paid pilot runbook exists yet.
- No client source intake template exists yet.
- No rights confirmation checklist exists as an operator-facing template yet.
- No brand intake template exists yet.
- No job log template exists yet.
- No generic local-file source manifest documentation exists yet.
- No architecture diagram exists.
- Windows setup exists, but macOS setup was only added at the README level in Phase 0.

## Stale Or Risky Claims Corrected

- Removed stale test-count claims from release readiness.
- Removed stale branch-ahead claim from release readiness.
- Removed stale missing-README and unrelated-CONTRIBUTING claims after fixing those files.
- Replaced prior local secret-on-disk claims with the verified current finding: no `.env` or `secrets/` files are present in the workspace and neither is tracked.
