# Managed Pilot Runbook

## Purpose

This runbook describes the **manual, operator-driven** flow for one managed
local-file sports pilot. It wraps the existing World Cup clipping pipeline
with a validated intake manifest, an explicit rights gate, read-only source
validation, and a durable local job record.

This is **not** an automated end-to-end pipeline. Every step below is executed
by a human operator. The new CLI only records and validates; it never processes
media.

## Terminology

- **Intake manifest** — the validated JSON describing pilot, media, rights,
  configuration references, and delivery. See `SOURCE_INTAKE.md`.
- **Job record** — a durable local file (JSON) plus an append-only event log
  under `data/pilot/jobs/` (gitignored). See below.
- **Execution-ready** — the intake passed structural, configuration, source,
  and rights validation, so the pipeline may be run.

## The 15 Steps

### 1. Receive source media

Accept only local files (no stream URLs, no network paths). Confirm the file
arrives through an agreed channel (shared folder, direct transfer, USB, etc.).
Record the original filename and any client-supplied metadata in the intake
manifest. Do not store passwords, API keys, payment details, or unnecessary
personal data.

### 2. Confirm rights

Use `RIGHTS_CONFIRMATION.md` to collect an affirmative confirmation from the
client: permitted uses, distribution limits, territory, expiration (if any),
and whether publishing is included. Record this in the intake `rights`
section. **No rights confirmation means no commercial processing.**

### 3. Collect project and brand requirements

Confirm the project/profile, brand, taxonomy, detection template, export
profiles, and delivery destination. Use `BRAND_INTAKE.md` for any new brand
language. Existing registered profiles are referenced by identifier, never
duplicated inside the intake.

### 4. Create the intake manifest

Write a JSON intake manifest. Start from
`docs/pilot/examples/world_cup_pilot_example.json` (clearly marked
non-production). Replace the placeholder `media.local_file_path` with the
absolute path to the cleared source file. Optionally set
`STADIUM_PILOT_INTAKE_ROOT` to restrict where source files may live.

### 5. Validate the intake

```bash
python3 scripts/pilot_job.py validate path/to/intake.json
```

The command exits zero only when the intake is **structurally valid**. It
reports four distinct layers: `structurally_valid`, `config_references_valid`,
`source_ready`, `rights_cleared`, and the combined `execution_ready`. Fix any
reported issues before continuing.

### 6. Create the job

```bash
python3 scripts/pilot_job.py create path/to/intake.json --operator YOUR_NAME
```

`create` validates first, then writes a durable job record and its initial
event. Deterministic initial state:

- Execution-ready intake -> `READY`
- Structurally valid and source-ready but rights not cleared -> `AWAITING_RIGHTS`
- Structurally invalid, invalid configuration, or source validation failure
  (identifiers derivable) -> `VALIDATION_FAILED`

Duplicate job identifiers are refused. No media is processed.

### 7. Confirm `READY`

```bash
python3 scripts/pilot_job.py show JOB_ID
```

A job may only be run when its state is `READY` (rights confirmed and source
valid). If the state is `AWAITING_RIGHTS`, resolve rights first; if
`VALIDATION_FAILED`, fix the intake.

### 8. Run the existing pipeline manually

Record the manual run start, then use the existing World Cup workflows against
the cleared source file. For a manifest-driven match this is
`process_from_manifest.py`; for a single file it is `process_match.py` /
`process_scheduled_match.py`. The job CLI does **not** run the pipeline.

```bash
python3 scripts/pilot_job.py transition JOB_ID RUNNING \
  --operator YOUR_NAME \
  --reason "Started manual clipping run" \
  --expected-revision REVISION_FROM_SHOW
```

### 9. Review outputs

Review every generated clip, caption, and thumbnail before any client sees it.
Use the existing static review dashboard or direct file review. The intake
requires `human_review_required`; do not skip it.

```bash
python3 scripts/pilot_job.py transition JOB_ID REVIEW_REQUIRED \
  --operator YOUR_NAME \
  --reason "Exports ready for review" \
  --artifact outputs/review/JOB_ID
```

### 10. Record approval

Only after the client (or an internal approver) approves, record approval.
Approval is a manual human action; the CLI only records it.

```bash
python3 scripts/pilot_job.py transition JOB_ID APPROVED \
  --operator REVIEWER_NAME \
  --approval-statement "Approved for delivery" \
  --deliverable-count 8
```

### 11. Prepare delivery

Stage the approved deliverables and verify them against
`DELIVERY_CHECKLIST.md`. Confirm the export profile, naming, and expected clip
count.

```bash
python3 scripts/pilot_job.py transition JOB_ID DELIVERY_READY \
  --delivery-method shared_folder \
  --delivery-destination FootballArchive/EXPORTS/JOB_ID \
  --deliverable-count 8 \
  --artifact docs/pilot/DELIVERY_CHECKLIST.md
```

### 12. Deliver through the agreed folder

Copy approved deliverables into the agreed shared folder or local directory
(`review_and_delivery.delivery_directory`). Do not publish unless
`publishing_included` is true and the rights permit it.

### 13. Record delivery

Update the job record with the delivery event (approximate or exact delivery
time and destination). This is a manual operator action.

```bash
python3 scripts/pilot_job.py transition JOB_ID DELIVERED \
  --operator YOUR_NAME \
  --confirmation "Delivered through agreed shared folder" \
  --delivery-destination FootballArchive/EXPORTS/JOB_ID \
  --delivered-item-count 8
```

### 14. Archive operational records

Keep the intake manifest and job record on disk under `data/pilot/` (gitignored).
These are the provenance record: intake -> rights -> validation -> job -> outputs.

### 15. Handle failure or cancellation

- **Failure** — record `FAILED` with a failure category and retry decision.
- **Recovery** — from `FAILED`, transition to `READY` or `RUNNING` with a
  recovery reason and `--recovery-confirmed`.
- **Rights revoked or expired** — stop processing immediately; do not deliver
  anything new; record `FAILED` or `CANCELLED` as appropriate.
- **Cancelled** — record `CANCELLED`; this does not delete source or outputs.

## Job Record and Event Log

Each job produces two files under `data/pilot/jobs/`:

- `<job_id>.json` — current job state, identifiers, readiness summary,
  timestamps, expected output root, intake path.
- `<job_id>.events.json` — append-only event history (every state decision:
  schema version, event ID, sequence, timestamp, event type, previous/new state,
  message, metadata, validation codes, operator, source, artifact references).

Writes are atomic (temp file + rename) and stay inside the configured
job-record root. `data/pilot/` is ignored by Git.

## States

`INTAKE_RECEIVED`, `VALIDATION_FAILED`, `AWAITING_RIGHTS`, `READY`,
`RUNNING`, `REVIEW_REQUIRED`, `APPROVED`, `DELIVERY_READY`, `DELIVERED`,
`FAILED`, `CANCELLED`.

This slice implements creation into `READY`, `AWAITING_RIGHTS`, and
`VALIDATION_FAILED`, plus explicit manual transitions through running, review,
approval, delivery, failure, recovery, and cancellation. `show` displays the
current revision and allowed next states. `history` displays the append-only
event sequence.

```bash
python3 scripts/pilot_job.py show JOB_ID
python3 scripts/pilot_job.py history JOB_ID
```

Every successful transition increments the integer job revision. Use
`--expected-revision` to prevent accidental overwrites when two operators are
working from the same job record.

## What Is NOT Automated

- Media processing
- Rights confirmation and approvals
- Review
- Delivery
- Publishing
- Billing
- Client onboarding

## First Paid Pilot Boundaries

One managed local-file sports pilot only. Local files, manual review,
shared-folder delivery. No publishing, no platform accounts, no automation
beyond the record-keeping in this runbook.
