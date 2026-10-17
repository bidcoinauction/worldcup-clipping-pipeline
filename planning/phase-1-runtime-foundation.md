# Phase 1 Runtime Foundation

## Purpose

Phase 1 adds an application/runtime spine around the existing Clipper pipeline.
It does not replace current files, manifests, transcripts, analysis outputs, or
media. Existing artifacts remain canonical files; the runtime index references
them so the Operator Console can show durable project and execution state.

## Runtime Records

- `Project`: operator-facing project record linked to an existing pilot job.
- `Artifact`: reference to an existing file such as source media, intake
  manifest, transcript, moments JSON, or analysis manifest CSV.
- `PipelineRun`: current state of one execution attempt, such as analysis.
- `PipelineEvent`: append-only record of what happened during a run.

SQLite is the local runtime index. The default location is
`data/pilot/runtime.sqlite3`, with `STADIUM_RUNTIME_DB` available for tests and
local overrides.

## Compatibility Contract

```text
existing files/manifests
        ↓
remain canonical artifacts
        ↓
runtime index references them
```

The runtime index is additive. It does not migrate or rewrite existing pilot job
records, transcripts, moments files, clip manifests, EDLs, renders, media, or
other artifacts. Legacy projects can be lazily indexed when the console reads
them, and unindexed or partial projects continue to use the previous fallback
read paths.

## Managed Analysis

Operator Console analysis requests are managed through runtime records:

```text
new request
→ new PipelineRun(status=QUEUED, stage=analysis)
→ RUN_QUEUED event
→ PipelineRun(status=RUNNING)
→ stage events
→ artifact registration
→ terminal PipelineRun(status=SUCCEEDED or FAILED)
```

Every retry creates a new `PipelineRun`. Failed runs are preserved and never
mutated back to queued.

The current analysis implementation still performs source validation,
transcript reuse/transcription, detection, and artifact generation. The runtime
layer records the execution and registers produced or reused artifacts.

## Implemented

- Runtime SQLite index.
- `Project`, `Artifact`, `PipelineRun`, and `PipelineEvent` models.
- Idempotent schema initialization and foreign-key enforcement.
- Lazy indexing of existing pilot projects.
- Runtime artifact registration for intake, source media, transcript, analysis
  moments JSON, and analysis manifest CSV.
- Managed Operator Console analysis execution.
- Runtime event stream for analysis lifecycle events.
- Retry history via multiple immutable analysis runs.
- Interrupted analysis recovery using the existing active-analysis registry.
- Operator Console project list, detail, status, and analysis status preference
  runtime records where available.
- Legacy fallback for projects with no runtime analysis run or unavailable
  runtime index.

## Deferred

- Queue or worker architecture.
- Batch execution.
- Granular progress percentages.
- Canonical Moment model and moments table.
- Stories, edit briefs, EDL runtime tables, renders, exports, channels.
- Project duplication and multi-channel reuse.
- Adapter ecosystem.
- Moment Graph.

## Phase 1 Exit Status

Phase 1 is complete when the implementation and tests verify:

- SQLite exists as the operator-facing runtime index.
- `Project`, `Artifact`, `PipelineRun`, and `PipelineEvent` are canonical
  runtime records.
- Existing files and manifests remain valid artifacts.
- Operator Console shows project/runtime status from runtime records where
  available.
- A real managed analysis execution is tracked through queued, running, and a
  terminal succeeded or failed state.
- Interrupted analysis is durably represented as `ANALYSIS_INTERRUPTED` with a
  `RUN_INTERRUPTED` event.
- Retry history is preserved as multiple runs.
- Legacy and partially indexed projects still work.
- Existing tests remain green.

Status: complete for Phase 1 Runtime Foundation.
