# Delivery Packages

Delivery packages are the final text/JSON handoff records for approved pilot
outputs. They package metadata, not media. Generation never copies, moves,
uploads, sends, publishes, compresses, deletes, or processes files.

## Runtime Storage

Runtime delivery records live under the configured pilot job root:

```text
JOB_ID.delivery/
  PACKAGE_ID.json
  PACKAGE_ID.checklist.txt
  PACKAGE_ID.confirmation.json
```

Runtime job roots remain ignored by Git. Tracked fictitious examples live under
`docs/pilot/examples/delivery/`.

## Workflow

```bash
python3 scripts/pilot_job.py delivery generate JOB_ID PACKAGE_ID \
  --operator tyler \
  --delivery-method shared_folder \
  --delivery-destination FootballArchive/EXPORTS/JOB_ID \
  --expected-revision REVISION_FROM_SHOW

python3 scripts/pilot_job.py delivery validate data/pilot/jobs/JOB_ID.delivery/PACKAGE_ID.json \
  --job-id JOB_ID

python3 scripts/pilot_job.py delivery checklist JOB_ID PACKAGE_ID

python3 scripts/pilot_job.py transition JOB_ID DELIVERY_READY \
  --delivery-package-id PACKAGE_ID \
  --deliverable-count 8

python3 scripts/pilot_job.py delivery confirm JOB_ID PACKAGE_ID \
  --operator tyler \
  --confirmation "Manual handoff completed" \
  --delivered-count 8

python3 scripts/pilot_job.py transition JOB_ID DELIVERED \
  --operator tyler \
  --confirmation "Client received package" \
  --delivery-package-id PACKAGE_ID \
  --delivered-item-count 8
```

## Package Rules

- Generation is allowed only from `APPROVED` or `DELIVERY_READY` jobs.
- Only outputs with `review_status: APPROVED` and `include_in_delivery: true` are included.
- Rejected, excluded, pending, and changes-requested outputs are omitted.
- If included outputs come from run-linked output manifests, the package records
  `represented_run_ids` and each deliverable's `run_id`.
- Every included file path is revalidated, including optional checksum checks.
- Current rights are revalidated from the stored intake.
- Duplicate package IDs and stale expected revisions are rejected.
- `DELIVERY_READY` requires a valid package and matching deliverable count.
- `DELIVERED` requires a recorded confirmation for that package.

## Safety

Package, checklist, and confirmation fields are scanned for credential-like
keys/values, credential URLs, path traversal, shell-command-like secrets, and
embedded base64 media. The privacy-safe `list`, `show`, and `checklist` commands
avoid exposing full rights statements or intake details.
