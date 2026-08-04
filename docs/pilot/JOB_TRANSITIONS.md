# Job Transitions

This reference documents manual pilot job-state transitions. The transition CLI
records operator decisions only. It does not run media processing, inspect clips,
copy files, upload files, deliver files, publish posts, or call network services.

## State Definitions

- `READY` — intake, source, configuration, and rights are ready for a manual run.
- `RUNNING` — an operator has started the existing pipeline manually.
- `REVIEW_REQUIRED` — outputs or review artifacts are ready for human review.
- `APPROVED` — a human reviewer approved the deliverables.
- `DELIVERY_READY` — approved deliverables are staged for manual delivery.
- `DELIVERED` — delivery completion was recorded.
- `FAILED` — a blocking failure was recorded.
- `CANCELLED` — the pilot job was cancelled.
- `AWAITING_RIGHTS` — source is ready but rights are not cleared.
- `VALIDATION_FAILED` — structure, configuration, or source validation failed.
- `INTAKE_RECEIVED` — reserved state for future intake tracking.

`DELIVERED` and `CANCELLED` are terminal in this slice.

## Allowed Transitions

```text
READY -> RUNNING | CANCELLED | FAILED
RUNNING -> REVIEW_REQUIRED | FAILED | CANCELLED
REVIEW_REQUIRED -> APPROVED | RUNNING | FAILED | CANCELLED
APPROVED -> DELIVERY_READY | REVIEW_REQUIRED | FAILED | CANCELLED
DELIVERY_READY -> DELIVERED | REVIEW_REQUIRED | FAILED | CANCELLED
AWAITING_RIGHTS -> READY | VALIDATION_FAILED | CANCELLED
VALIDATION_FAILED -> READY | AWAITING_RIGHTS | CANCELLED
FAILED -> READY | RUNNING (explicit recovery only)
DELIVERED -> terminal
CANCELLED -> terminal
```

Invalid transitions fail with the job ID, current state, requested state, and
allowed next states. Failed transitions append no events.

## Required Metadata

- `RUNNING`: `--operator`; from `FAILED` also `--recovery-reason` and `--recovery-confirmed`.
- `REVIEW_REQUIRED`: `--operator`, `--reason`, and at least one `--artifact`.
- `APPROVED`: `--operator`, `--approval-statement`, and `--deliverable-count`.
- `DELIVERY_READY`: `--delivery-method`, `--delivery-destination`, and `--deliverable-count`.
- `DELIVERED`: `--operator`, `--confirmation`, `--delivery-destination`, and `--delivered-item-count`.
- `FAILED`: `--operator`, `--reason`, `--failure-category`, and `--retry-allowed yes|no`.
- `CANCELLED`: `--operator`, `--reason`, and `--client-requested yes|no`.
- `READY` from `FAILED`: `--operator`, `--recovery-reason`, and `--recovery-confirmed`.
- `READY` from `AWAITING_RIGHTS` or `VALIDATION_FAILED`: stored intake must now validate as execution-ready.

Failure categories: `SOURCE`, `CONFIGURATION`, `RIGHTS`, `PROCESSING`,
`REVIEW`, `DELIVERY`, `OPERATOR`, `UNKNOWN`.

`APPROVED`, `DELIVERY_READY`, and `DELIVERED` also check registered output
manifests. Jobs without output manifests cannot pass these delivery-related
checks. The deliverable count must equal the number of approved,
delivery-included outputs, and included output files must still validate.

## Revision Guard

Each job has an integer `revision`. New jobs start at `0`. Every successful
transition increments the revision by one. Operators may pass
`--expected-revision N`; stale revisions are rejected without appending events
or changing files.

```bash
python3 scripts/pilot_job.py show JOB_ID
python3 scripts/pilot_job.py transition JOB_ID RUNNING \
  --operator tyler \
  --reason "Started manual clipping run" \
  --expected-revision 0
```

## Rights Revalidation

Transitions into `READY`, `RUNNING`, `DELIVERY_READY`, and `DELIVERED` re-read
the stored intake manifest and revalidate rights status and expiration. `READY`
and `RUNNING` also require the source file to remain valid. The intake manifest
is never modified by transition validation.

## Event History

Every successful transition appends exactly one event to
`<job_id>.events.json`. Events include schema version, event ID, sequence,
timestamp, previous and new state, operator, summary, structured metadata,
source command, validation codes, and artifact references. Existing events are
preserved.

```bash
python3 scripts/pilot_job.py history JOB_ID
```

History output is privacy-safe and does not expose rights confirmation text or
stored intake details.

## Artifact References

Transition events may reference review folders, clip manifests, caption or
thumbnail folders, export directories, delivery checklists, or shared-folder
paths. The system validates references for string type, path traversal, and
credential-like values. It does not create, copy, upload, or delete artifacts.

## What The System Does Not Perform

- `RUNNING` does not execute media processing.
- `APPROVED` records human approval only.
- `DELIVERY_READY` does not stage or copy files.
- `DELIVERED` does not upload, send, or publish files.
- `CANCELLED` does not delete source media or outputs.
