# Phase 7.4 - End-to-End Media Workflow

## Purpose

Provide one repeatable operator entry point for the complete local media path:

```text
authorized local or synthetic source
  -> managed pilot registration and rights gate
  -> deterministic EDL and genuine FFmpeg reference render
  -> artifact and source provenance verification
  -> optional OORT upload and read-back verification
  -> persisted local-ingest contract
  -> unified media catalog with stable asset IDs
  -> Stadium Signal Lite import and loopback Range playback verification
  -> durable workflow status
```

The command orchestrates existing Phase 7 services. It does not introduce a
worker, database, hosted media proxy, or second rendering implementation.

## Operator Command

From the Clipper repository:

```bash
.venv/bin/python scripts/media_workflow.py run \
  --source ~/FootballArchive/TESTS/capture_test.mp4 \
  --pilot-id ss_lite_wf_001 \
  --jobs-dir data/pilot/jobs \
  --lite-root ~/FootballArchive/ESPN/stadium-signal-lite \
  --preview-enabled
```

`--source` must name an authorized local or synthetic media file. The default
project remains the registered, non-production `basketball_sandbox` profile;
the World Cup reference deployment is not selected or modified.

Default runtime outputs:

| Output | Default |
| --- | --- |
| Intake | `<jobs-dir>/intakes/<pilot-id>_<source-id>.json` |
| Render | `<jobs-dir>/RENDERS/<job-id>/.../REFERENCE/reference.mp4` |
| Contract | `<jobs-dir>/contracts/<pilot-id>_<source-id>.contract.json` |
| Catalog | `<jobs-dir>/library.catalog.json` |
| Status | `<jobs-dir>/workflow/<pilot-id>_<source-id>.workflow.json` |

Inspect the durable result without rerunning media work:

```bash
.venv/bin/python scripts/media_workflow.py status \
  --pilot-id ss_lite_wf_001 \
  --source-id capture_test \
  --jobs-dir data/pilot/jobs
```

## Lite Verification

Pass `--lite-root` or set `STADIUM_SIGNAL_LITE_ROOT` to the Lite checkout. The
workflow calls Lite's `tools/verify_library.py` as a subprocess and verifies:

- `GET /api/library` imports the catalog and contains the expected asset.
- The browser payload contains no source path, artifact path, or storage URI.
- Preview uses an opaque `/preview?asset=...` reference.
- An arbitrary filesystem path is rejected.
- A `Range: bytes=0-1023` request returns HTTP 206 when preview is enabled.

Preview remains disabled unless `--preview-enabled` is supplied. Preview roots
default to `<jobs-dir>`; use `--preview-roots` only when artifacts live under a
different explicit root. If Lite is not configured, Clipper still completes the
local contract and catalog stages and records an actionable recommendation.

## Retry And Resume

- A rerun first validates the existing contract schema, source checksum, and
  artifact checksum. If all match, rendering is skipped and the catalog/Lite
  checks are repeated.
- If a pilot job exists but no reusable contract was written, registration is
  resumed and the deterministic EDL/render/verify stages run again.
- Catalog entries are deduplicated by stable `asset_id`; a successful workflow
  requires the processed artifact to occur exactly once.
- Every valid-ID run writes a versioned status document with stage state and
  recovery recommendations. Pilot and source IDs jointly key runtime records,
  so multiple sources under one pilot do not overwrite each other.
- `--no-reuse` forces ingest work but still resumes the matching durable job;
  use a new pilot/source ID pair when intentionally creating a distinct job.

## Optional OORT Storage

Uploads are never automatic. Add `--upload` only when OORT credentials and a
bucket are configured. Upload verification uses the existing Phase 7.1 adapter.
An optional-upload failure is recorded while the verified local artifact is
preserved; `--require-storage` makes that failure fatal.

```bash
.venv/bin/python scripts/media_workflow.py run \
  --source ~/FootballArchive/TESTS/capture_test.mp4 \
  --pilot-id ss_lite_wf_001 \
  --upload --require-storage \
  --lite-root ~/FootballArchive/ESPN/stadium-signal-lite \
  --preview-enabled
```

The workflow never uploads a full-length source automatically. Only the
verified processed artifact is eligible after the explicit `--upload` action.

## Failure Guidance

| Failure | Operator action |
| --- | --- |
| Source missing or outside the configured intake root | Correct `--source`/`--intake-root`; do not bypass validation. |
| Rights or intake validation fails | Correct the intake/rights facts before retrying. |
| Render or provenance verification fails | Inspect `<jobs-dir>/RENDERS`, preserve the source, then retry. |
| Existing job with interrupted output | Retry the same command; resume is automatic. |
| OORT unavailable | Retry without `--require-storage`, or restore credentials/connectivity and rerun with `--upload`. |
| Lite tool not found | Correct `--lite-root` or set `STADIUM_SIGNAL_LITE_ROOT`. |
| Preview returns no 206 | Confirm `--preview-enabled`, preview roots, artifact existence, and browser codec support. |

## Validation

Focused Clipper tests:

```bash
STADIUM_SIGNAL_LITE_ROOT=/path/to/stadium-signal-lite \
  .venv/bin/python -m pytest tests/test_media_workflow.py -q
```

Without `STADIUM_SIGNAL_LITE_ROOT`, the cross-repository case skips cleanly and
the repository remains independently testable. The test suite covers first
run, checksum-verified reuse, interruption resume, forced rerun, dry run,
duplicate suppression, optional storage failure, contract re-verification, and
the real Lite import/Range boundary.

See `RELEASE_READINESS.md` for security findings, test evidence, rollout order,
deployment boundaries, and rollback.
