# Phase 2 — Managed Pilot Operations: Intake, Job Records, Transitions, and Output Review (Status)

## Objective

Implement the smallest operational slices for one managed local-file sports
pilot: a validated pilot intake manifest, an explicit rights gate, read-only
source validation, a durable local job record, explicit manual job-state
transitions, and validated output-manifest registration/review.

## Built

- `pipeline/pilot.py` — intake validation (structural / configuration /
  rights / source), the rights gate, read-only source validation, and the job
  record API (atomic writes, append-only event log).
- `scripts/pilot_job.py` — operator CLI: `validate`, `create`, `show`, `list`,
  `transition`, `history`.
- `docs/pilot/` — runbook and intake templates.
- `docs/pilot/JOB_TRANSITIONS.md` — state graph, required metadata, revision
  behavior, rights revalidation, and manual/automated boundaries.
- `docs/pilot/OUTPUT_MANIFESTS.md` — output manifest schema, registration,
  review actions, summary/readiness behavior, and CLI examples.
- `JOB_ID.outputs/MANIFEST_ID.json` runtime structure — reviewed output
  manifests stored beneath the configured job root.
- `docs/pilot/examples/` — a tracked non-production World Cup example plus
  invalid fixtures (unconfirmed rights, missing source, bad reference).

## Boundaries

No database, queue, auth, users, billing, publishing, dashboard, media
processing, model calls, network access, copying, uploading, automatic output
discovery, or deletion.
Existing World Cup manifests, schedule, and clip manifests are unchanged; the
pilot intake/job record is an operational wrapper, not a replacement.

## Manifest / Job Relationship

- **Pilot intake** (`intake JSON`) — the client-facing intake our operator.
- **Existing match manifest** (`data/manifests/*.json`) — records the World Cup
  recorded sources + pipeline flags; unchanged.
- **Existing schedule** (`data/worldcup_2026_schedule.csv`) — unchanged.
- **Existing clip manifest** (`CLIP_MANIFESTS/*.csv`) — unchanged.
- **Job record** (`data/pilot/jobs/<job_id>.json` + `.events.json`) — the
  durable provenance record for one pilot intake; gitignored.

```text
Client intake -> rights gate -> source validation -> job record (READY) ->
  RUNNING (manual pipeline started) -> existing export scripts -> generated files ->
  pilot output manifest -> manual output review -> REVIEW_REQUIRED -> APPROVED ->
  DELIVERY_READY -> DELIVERED
```

Failures and cancellations are recorded through explicit `FAILED` and
`CANCELLED` transitions. Recovery from `FAILED` requires an operator,
recovery reason, and confirmation that the blocking issue was addressed.

## Verification

- `python3 scripts/validate_data.py` passes.
- `pytest` baseline preserved (plus transition/revision/history tests added).
- `python3 scripts/validate_config.py config/pipeline_config.json`,
  `config/examples/basketball.json`, `config/brands/world_cup.json`,
  `config/export/world_cup.json` all pass.
- `git diff --check` clean.
- Manual demonstrations confirm execution-ready, awaiting-rights,
  missing-source, durable READY job creation, deterministic duplicates,
  complete manual transitions, stale revision rejection, rights revalidation,
  output registration/review/readiness, failure recovery, cancellation safety,
  append-only history, and no network / FFmpeg / media mutation.

## Remaining pilot-operation gaps

- The transition CLI records operator state only; it still does not execute,
  inspect, copy, deliver, upload, publish, or delete media/output files.
- Output manifests require explicit operator registration; no automatic output
  discovery, clip inspection, file copying, or delivery automation exists.
- No supported-use check is automated for `RESTRICTED` rights (documented as a
  manual step).
- Delivery/billing/reporting remain out of scope.
