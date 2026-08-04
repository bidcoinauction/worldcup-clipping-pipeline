# Pipeline Run Records

Pipeline run records are durable operator logs for manual execution attempts.
They describe what a human ran, which source/configuration was used, which
stages completed, and what artifacts or failures resulted. They do not execute
commands, invoke FFmpeg, call models, process media, start background work, copy
files, upload files, publish files, or delete files.

## Runtime Storage

Run records live under the configured pilot job root:

```text
JOB_ID.runs/
  RUN_ID.json
```

Runtime job roots remain ignored by Git.

## Statuses

Run statuses are independent from job states:

- `PLANNED`
- `STARTED`
- `SUCCEEDED`
- `PARTIALLY_SUCCEEDED`
- `FAILED`
- `ABORTED`

Stage statuses are:

- `NOT_STARTED`
- `RUNNING`
- `SUCCEEDED`
- `SKIPPED`
- `FAILED`

## Recognized Entry Points

- `process-match` -> `scripts/process_match.py`
- `process-from-manifest` -> `scripts/process_from_manifest.py`
- `process-scheduled-match` -> `scripts/process_scheduled_match.py`
- `transcribe-match` -> `scripts/transcribe_match.py`
- `generate-claude-prompt` -> `scripts/generate_claude_prompt.py`
- `run-gpt-detection` -> `scripts/run_gpt_detection.py`
- `build-clip-manifest` -> `scripts/build_clip_manifest.py`
- `generate-asset-prompts` -> `scripts/generate_asset_prompts.py`
- `export-clips-ffmpeg` -> `scripts/export_clips_ffmpeg.py`
- `export-research-windows` -> `scripts/export_research_windows.py`

Commands are stored as structured arguments. The CLI records them with repeated
`--command-arg` values; it never executes them.

## Stages

Supported stages mirror the current repository workflow:

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

Each stage can record timestamps, command/function references, input/output/log
references, warnings, metrics, failure category/summary, operator, and notes.
Stage updates are operator records only.

## CLI

```bash
python3 scripts/pilot_job.py runs create JOB_ID \
  --run-id run-001 \
  --operator tyler \
  --entry-point process-match \
  --command-arg scripts/process_match.py \
  --command-arg path/to/source.mp4 \
  --manual-confirmed \
  --expected-job-revision 1

python3 scripts/pilot_job.py runs start JOB_ID run-001 \
  --operator tyler \
  --expected-job-revision 2 \
  --expected-run-revision 0

python3 scripts/pilot_job.py runs stage JOB_ID run-001 TRANSCRIPTION \
  --status RUNNING \
  --operator tyler \
  --expected-job-revision 3 \
  --expected-run-revision 1

python3 scripts/pilot_job.py runs stage JOB_ID run-001 TRANSCRIPTION \
  --status SUCCEEDED \
  --operator tyler \
  --output path/to/transcript.json \
  --expected-job-revision 4 \
  --expected-run-revision 2

python3 scripts/pilot_job.py runs finish JOB_ID run-001 \
  --status SUCCEEDED \
  --operator tyler \
  --summary "Manual pipeline run completed" \
  --expected-job-revision 5 \
  --expected-run-revision 3

python3 scripts/pilot_job.py runs list JOB_ID
python3 scripts/pilot_job.py runs show JOB_ID run-001
python3 scripts/pilot_job.py runs summary JOB_ID run-001
```

## Provenance

Run creation captures references and file metadata for the intake manifest,
source media, project config, brand profile, editorial taxonomy, export config,
detection template, optional recording/research files, optional match/schedule
references, repository commit, and model/provider identifiers. File references
store path, size, modified timestamp, optional SHA-256, and validation outcome.

The record stores identifiers and file references only. It does not embed full
configuration files, API keys, environment dumps, model responses, transcript
contents, or raw subprocess output.

## Output Linkage

Output manifests may include optional `run_id`. When present, registration
requires the run to belong to the same job and be `SUCCEEDED` or
`PARTIALLY_SUCCEEDED`. Output manifests without `run_id` remain valid.

Delivery packages list represented run IDs when approved deliverables came from
run-linked output manifests.

## Failure Handling

Failed stages require an error category and summary. Failed runs require failure
category and summary. Partial success requires an explanation. Failed lifecycle
updates append no event and do not mutate files.

## Security

Run records reject credential-like values, credential URLs, shell substitutions,
command separators, pipes, redirections, environment dumps, base64 media, and
unrecognized entry-point scripts. `show` and `summary` remain privacy-safe.
