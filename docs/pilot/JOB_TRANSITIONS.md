# Job Transitions

This reference documents manual pilot job-state transitions. The transition CLI
records operator decisions only. It does not run media processing, inspect clips,
copy files, upload files, deliver files, publish posts, or call network services.

## State Definitions

- `READY` — intake, source, configuration, and rights are ready for a manual run.
- `RUNNING` — an operator has started the existing pipeline manually.
- `REVIEW_REQUIRED` — outputs or review artifacts are ready for human review.
- `APPROVED` — a human reviewer approved the deliverables.
- `DELIVERY_READY` — an approved delivery package is ready for manual handoff.
- `DELIVERED` — delivery confirmation was recorded and the job was closed.
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
- `DELIVERY_READY`: `--delivery-package-id` and `--deliverable-count`.
- `DELIVERED`: `--operator`, `--confirmation`, `--delivery-package-id`, and `--delivered-item-count`.
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
`DELIVERY_READY` additionally requires a generated delivery package. `DELIVERED`
requires a prior `delivery confirm` record for that package.

## Revision Guard

Each job has an integer `revision`. New jobs start at `0`. Every successful
transition, execution-plan generation/invalidation, output review, delivery
package generation, and delivery confirmation increments the revision by one.
Operators may pass
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

## Delivery Packages

Delivery packages are generated from approved, delivery-included output
manifests and stored under `JOB_ID.delivery/` with a paired checklist. They are
text/JSON records only and contain the file list, approval metadata, rights
snapshot, destination description, and summary counts. Package generation fails
when approved files are missing, rights are stale, the expected revision is
stale, or the package ID already exists.

```bash
python3 scripts/pilot_job.py delivery generate JOB_ID PACKAGE_ID \
  --operator tyler \
  --delivery-method shared_folder \
  --delivery-destination FootballArchive/EXPORTS/JOB_ID

python3 scripts/pilot_job.py transition JOB_ID DELIVERY_READY \
  --delivery-package-id PACKAGE_ID \
  --deliverable-count 8

python3 scripts/pilot_job.py delivery confirm JOB_ID PACKAGE_ID \
  --operator tyler \
  --confirmation "Manual delivery completed" \
  --delivered-count 8

python3 scripts/pilot_job.py transition JOB_ID DELIVERED \
  --operator tyler \
  --confirmation "Client received package" \
  --delivery-package-id PACKAGE_ID \
  --delivered-item-count 8
```

## Pipeline Run Records

Manual run records live under `JOB_ID.runs/RUN_ID.json`. They describe the
command an operator ran, source/config provenance, stage evidence, output/log
references, and failure summaries. Creating or starting a run never executes the
recorded command. Output manifests can optionally link to a completed run with
`run_id`; delivery packages list represented run IDs when available.

See `docs/pilot/PIPELINE_RUN_RECORDS.md` for the schema and CLI.

## Execution Plans

Execution plans live under `JOB_ID.plans/PLAN_ID.json` with a paired text
checklist. Generation requires a `READY` job, a current expected revision,
current rights/source/config readiness, a supported workflow, a production
football project, a unique plan ID, and existing repository entry-point scripts.
Generation appends exactly one `EXECUTION_PLAN_GENERATED` event and increments
the job revision. Failed generation appends no event and changes no job record.

Invalidation requires operator, reason, expected job revision, and expected plan
revision. It appends exactly one `EXECUTION_PLAN_INVALIDATED` event, increments
both the plan and job revisions, and leaves the invalidated plan readable.

See `docs/pilot/EXECUTION_PLANS.md` for the schema and CLI.

## Event History

Every successful transition appends exactly one event to
`<job_id>.events.json`. Events include schema version, event ID, sequence,
timestamp, previous and new state, operator, summary, structured metadata,
source command, validation codes, and artifact references. Existing events are
preserved.

Pipeline run creation, start, stage updates, and finish also append job events
while keeping the job state unchanged. They increment both job and run revisions
but do not execute processing.
Execution-plan generation and invalidation also append events while keeping the
job state unchanged.

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
- `plans generate`, `plans validate`, `plans show`, `plans checklist`, and `plans invalidate` do not execute commands, invoke FFmpeg, call models/APIs, access networks, process media, copy, move, delete, upload, deliver, or publish files.
- `runs create`, `runs start`, `runs stage`, and `runs finish` do not execute commands, invoke FFmpeg, call models, or process media.
- `APPROVED` records human approval only.
- `delivery generate` does not stage, copy, move, upload, send, publish, or process files.
- `DELIVERY_READY` does not stage or copy files.
- `delivery confirm` does not upload, send, or publish files.
- `DELIVERED` does not upload, send, or publish files.
- `CANCELLED` does not delete source media or outputs.
