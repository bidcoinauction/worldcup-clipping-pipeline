# Output Manifests

Pilot output manifests link existing exported files to a pilot job so an
operator can review deliverables and calculate delivery readiness. They do not
replace existing CSV clip manifests and do not run export scripts.

```text
Existing clip manifest
-> existing export scripts
-> generated files
-> optional manual pipeline run record
-> pilot output manifest
-> manual output review
-> explicit job approval
-> delivery package/checklist
-> manual delivery confirmation
```

## Runtime Storage

Registered manifests are stored under the configured pilot job root:

```text
JOB_ID.json
JOB_ID.events.json
JOB_ID.outputs/
  MANIFEST_ID.json
```

Runtime job roots remain ignored by Git. Tracked examples live under
`docs/pilot/examples/outputs/`.

## Schema

Top-level fields:

- `schema_version`: currently `1`.
- `manifest_id`: stable identifier using letters, digits, `_`, or `-`.
- `job_id`, `pilot_id`, `project_id`, `source_id`: must match the job context.
- `created_at`, `created_by`: operational provenance.
- `source_clip_manifest_path`: optional reference to an existing CSV/JSON clip manifest.
- `revision`: integer manifest revision, incremented on each output review.
- `run_id`: optional link to a completed manual pipeline run for the same job.
- `outputs`: non-empty list of output records.

Each output includes an `output_id`, `output_type`, `local_path`, `filename`,
`export_profile`, `platform`, `operational_category`, `review_status`, and
`include_in_delivery`. Optional fields include editorial labels, clip ID,
timestamps, duration, caption/thumbnail/metadata paths, checksum, notes,
rejection reason, and approval metadata.

Output types: `VIDEO_CLIP`, `CAPTION`, `THUMBNAIL`, `TRANSCRIPT`,
`CLIP_MANIFEST`, `REVIEW_DASHBOARD`, `METADATA`, `OTHER`.

Review statuses: `PENDING`, `APPROVED`, `REJECTED`, `CHANGES_REQUESTED`,
`EXCLUDED`.

## Validation

Validation is read-only. It checks required fields, unknown keys, duplicate
output IDs, job match when provided, local path existence, readability,
non-empty files, supported extensions, optional checksums, export profiles,
platforms, operational categories, related caption/thumbnail/metadata paths,
and secret-like data.

It rejects network URLs, credential-bearing URLs, path traversal, embedded
base64 media, token/password/payment fields, and unsupported output types or
review statuses. Directories are permitted only for explicitly supported output
types.

Validation never creates directories, mutates files, runs FFmpeg, calls APIs,
uses network services, copies, moves, deletes, uploads, publishes, or processes
media.

## CLI

```bash
python3 scripts/pilot_job.py outputs validate path/to/manifest.json

python3 scripts/pilot_job.py outputs register JOB_ID path/to/manifest.json \
  --expected-revision 2 \
  --operator tyler

python3 scripts/pilot_job.py outputs list JOB_ID

python3 scripts/pilot_job.py outputs show JOB_ID MANIFEST_ID

python3 scripts/pilot_job.py outputs review JOB_ID MANIFEST_ID OUTPUT_ID \
  --status APPROVED \
  --operator reviewer \
  --reason "Approved for delivery" \
  --include-in-delivery \
  --expected-job-revision 3 \
  --expected-manifest-revision 0

python3 scripts/pilot_job.py outputs summary JOB_ID
```

Registration is allowed only for jobs in `RUNNING`, `REVIEW_REQUIRED`,
`APPROVED`, or `DELIVERY_READY`. It validates the manifest, stores it under the
job output directory, links the manifest ID in the job record, increments the
job revision, and appends one job event. Duplicate manifest IDs and stale job
revisions are rejected.

When `run_id` is present, registration verifies that the run exists, belongs to
the same job, and is `SUCCEEDED` or `PARTIALLY_SUCCEEDED`. Existing manifests
without `run_id` remain valid.

Review actions update manifest review state only. Approvals can include the
output in delivery. Rejections, changes requested, exclusions, and reset to
pending all set delivery inclusion to false. Each successful review increments
both job and manifest revisions and appends one job event.

## Readiness

`outputs summary` reports manifest count, output counts, status counts,
delivery-included counts, missing/invalid references, represented platforms,
export profiles, operational categories, job revision, and manifest revisions.

Review completion requires:

- At least one registered manifest.
- At least one output included for delivery.
- Every included output is `APPROVED`.
- Every included output has approval metadata.
- Every included file still validates.
- Rights remain valid.
- Human review is recorded.

The system reports eligibility for `APPROVED` and `DELIVERY_READY`, but it does
not automatically transition the job. The operator must still run
`pilot_job.py delivery generate` followed by `pilot_job.py transition`. See
`docs/pilot/DELIVERY_PACKAGES.md` for package and confirmation records.
