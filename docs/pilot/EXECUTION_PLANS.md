# Execution Plan Manifests

Execution plans are frozen operator manifests for jobs that are already `READY`.
They turn current readiness, source/configuration provenance, and existing
repository entry points into a JSON plan plus a human-readable checklist.

Plans never execute commands, invoke subprocesses, run FFmpeg, call models/APIs,
access network services, process media, copy/move/delete/upload/publish files,
deliver outputs, or discover outputs automatically. Operators run commands
manually and record execution through pipeline-run records.

## Runtime Storage

```text
JOB_ID.plans/
  PLAN_ID.json
  PLAN_ID.txt
```

Runtime job roots remain ignored by Git. Writes are atomic and contained inside
the configured job-record root.

## Schema

Execution-plan schema version: `1`.

Plan statuses:

- `DRAFT`
- `READY`
- `SUPERSEDED`
- `INVALIDATED`

Generation writes `READY` plans only. `DRAFT` and `SUPERSEDED` are reserved by
the schema; superseding is deferred in this slice to keep lifecycle writes simple
and append-only. Invalidated plans stay readable and stored.

Supported workflows:

- `local-match-file`
- `recording-manifest`

Production generation is limited to the registered football project. The
non-production basketball example is rejected for production execution plans.

## Generation Gates

`plans generate` requires:

- Existing job in state `READY`
- Expected job revision matches the current revision
- Current readiness report has no blockers
- Rights remain valid
- Source remains ready
- Configuration references remain valid
- Supported workflow
- Supported production project
- Unique plan ID
- Every referenced repository entry point exists

Failed generation creates no plan, appends no event, and does not change the job
record.

## Stage Model

Plans contain the complete ordered stage list:

- `SOURCE_INTAKE`
- `CONCATENATION`
- `TRANSCRIPTION`
- `RESEARCH`
- `PROMPT_GENERATION`
- `DETECTION`
- `CLIP_MANIFEST`
- `ASSET_PROMPTS`
- `CLIP_EXPORT`
- `REVIEW_DASHBOARD`
- `OUTPUT_REGISTRATION`

Each stage records sequence, stage ID, required/optional classification, enabled
status, skip reason when disabled, recognized entry point, script path,
structured argument array, working directory, inputs, expected outputs,
configuration references, required tools, required environment-variable names,
completion evidence, and a human-readable command preview.

The structured argument array is the source of truth. Command previews are only
for operators.

## CLI

```bash
python3 scripts/pilot_job.py plans generate JOB_ID \
  --plan-id plan-001 \
  --operator tyler \
  --expected-job-revision 1

python3 scripts/pilot_job.py plans validate JOB_ID plan-001
python3 scripts/pilot_job.py plans list JOB_ID
python3 scripts/pilot_job.py plans show JOB_ID plan-001
python3 scripts/pilot_job.py plans checklist JOB_ID plan-001

python3 scripts/pilot_job.py plans invalidate JOB_ID plan-001 \
  --operator tyler \
  --reason "Source file was replaced" \
  --expected-job-revision 2 \
  --expected-plan-revision 0
```

Recording-manifest plans add:

```bash
python3 scripts/pilot_job.py plans generate JOB_ID \
  --plan-id plan-recording-001 \
  --operator tyler \
  --expected-job-revision 1 \
  --workflow recording-manifest \
  --recording-manifest data/manifests/example_match.json
```

## Run Linkage

Run records may optionally include `plan_id`:

```bash
python3 scripts/pilot_job.py runs create JOB_ID \
  --run-id run-001 \
  --operator tyler \
  --plan-id plan-001 \
  --entry-point process-match \
  --command-arg scripts/process_match.py \
  --command-arg --input \
  --command-arg path/to/source.mp4 \
  --manual-confirmed \
  --expected-job-revision 2
```

When `plan_id` is present, the plan must exist, belong to the same job, be
`READY`, validate against current job revision, and have a matching structured
entry point and argument array. Runs without `plan_id` remain valid for legacy
manual records.

Run summaries include plan ID, plan revision, workflow, planned stages, recorded
stages, and manually recorded deviations when provided.

## Security

Plans and linked runs reject shell separators, pipes, redirection, command
substitution, backticks, arbitrary executable paths, credential-bearing URLs,
API keys, tokens, passwords, cookies, signed URLs, full environment dumps,
base64 media, embedded binaries, path traversal, and secret-like values.

Environment variables are stored by name only. Values are never stored.

## Examples

Tracked fictitious examples live under `docs/pilot/examples/plans/`.
