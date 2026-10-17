# Phase 5 Scale

## Purpose

Phase 5 adds scale capabilities. Slice 1 introduces safe project duplication and
reuse from a shared source, so one analyzed match can produce multiple
independent editorial projects without repeating ingestion, transcription, or
detection work.

```text
Analyze once
     ↓
Reuse source intelligence
     ↓
Independent editorial projects
```

## Project Lineage

Projects carry explicit lineage:

- `parent_project_id` — the project this one was duplicated from.
- `source_project_id` — the project owning the original source intelligence.
- `reuse_mode` — how much intelligence was reused.

For an original project:

- `parent_project_id = null`
- `source_project_id = self`

Nested duplication preserves the original source:

```text
A original

A → B
B.parent = A
B.source = A

B → C
C.parent = B
C.source = A
```

## Reuse Modes

- `SOURCE_ONLY`
- `SOURCE_AND_ANALYSIS`
- `SOURCE_ANALYSIS_AND_MOMENTS`

The default operator duplication path uses `SOURCE_ANALYSIS_AND_MOMENTS` when
compatible data exists.

## Reuse Semantics

Reusable (by reference, never physically copied):

- source media artifact
- transcript artifact
- analysis artifacts
- detected intelligence (cloned canonical Moments with lineage)

Independent per project (not duplicated):

- Moment review state
- Stories
- StoryMoment relationships
- Edit Briefs
- EDLs
- Renders
- Export Packages
- channel selections / operator decisions

## Artifact Reuse

Duplicated projects create project-scoped Artifact records pointing to the same
immutable file paths as the source. Reuse provenance is recorded:

- `reused = true`
- `reused_from_project_id`
- `reused_from_artifact_id`
- `reuse_reason`

Underlying files are never modified or copied.

## Moment Cloning / Lineage

Canonical Moments remain project-scoped because review state is mutable.
Duplication clones each source Moment into the new project with:

- deterministic new `moment_id` (`new_project_id + origin_moment_id`)
- `origin_moment_id` and `origin_project_id` lineage
- identical sporting data (types, timestamps, signals, importance, confidence)
- `review_state = UNREVIEWED`
- review metadata cleared

Original Moments are never mutated.

## Review-State Reset

Editorial judgement belongs to the project. A `MUST_USE` in the source becomes
`UNREVIEWED` in the duplicate by default. `reviewed_at` / `reviewed_by` are not
copied.

## Analysis Reuse

A duplicated project that reuses analysis is operator-visible as analysis-ready
without running transcription or detection. No fake PipelineRun is created. The
runtime read model reports `analysis_reused = true` via lineage metadata.

## Downstream Editorial Isolation

A duplicated project starts with zero Stories, Edit Briefs, EDLs, Renders, and
Export Packages. Later editorial changes in the duplicate never mutate the
original project.

## Compatibility Validation

Reuse fails safely when:

- source project is missing
- target sport is incompatible with source sport
- target profile is incompatible
- canonical Moments are unavailable for the requested reuse mode

## Console

A small `Duplicate Project` action creates a derived project. The duplicated
project page communicates:

```text
Derived from: Germany vs Portugal
Reused: Source + analysis + moments
Analysis reused — no retranscription required
```

## Deferred

- Batch processing / batch duplication.
- Story cloning.
- Moment Graph.
- Queue/workers.
- Direct publishing.

Phase 5 is not complete; this slice satisfies only the project
duplication/reuse exit criterion.

## Batch Analysis

A batch is a container of independent project operations.

```text
Batch
  ↓
independent Project operations
  ↓
independent PipelineRuns
```

Execution is sequential for now. A failure in one project does not erase the
results of other projects.

Batch statuses:

- `QUEUED` — created, no item started.
- `RUNNING` — one or more items in progress.
- `SUCCEEDED` — all items succeeded.
- `FAILED` — all executable items failed.
- `PARTIAL` — mixture of succeeded and failed/blocked/cancelled.
- `CANCELLED` — explicitly cancelled before completion.

Item statuses reuse execution concepts: `QUEUED`, `RUNNING`, `SUCCEEDED`,
`FAILED`, `BLOCKED`, `CANCELLED`.

## Batch Semantics

- Each ANALYZE item runs the existing managed analysis entry point and records
  the created PipelineRun id on the item. No fake aggregate PipelineRun.
- Duplicate project IDs in a batch request are deduplicated preserving order.
- A project with a genuinely active analysis is blocked
  (`ANALYSIS_ALREADY_RUNNING`).
- A duplicated project that already reuses analysis is blocked
  (`ANALYSIS_ALREADY_AVAILABLE`) instead of retranscribing.
- Aggregate counts are always recalculated from items.
- Cancellation and automatic retry are deferred. Failed items can be retried
  individually through normal project analysis.

## Phase 5 Progress

After Slice 2:

- ✓ project duplication/reuse
- ✓ batch analysis foundation
- ✓ multi-channel reuse (Phase 4 + duplication)

Still deferred:

- integration adapter boundary
- Moment Graph

## Integration Adapter Boundary

External integrations are optional adapters around the Clipper core.

```text
Clipper Core
   ↓ optional adapters
OORT / Airtable / Slack
```

The core (Project runtime, analysis, Moments, Stories, Render, Export) never
depends on OORT, Airtable, or Slack. Adapters declare capabilities and provide
health checks; operations fail safely and never corrupt core runtime records.

Capabilities:

- `ARTIFACT_STORAGE`
- `RECORD_SYNC`
- `NOTIFICATION`
- `REVIEW_CARD`
- `DELIVERY`

Health statuses: `READY`, `NOT_CONFIGURED`, `UNAVAILABLE`, `ERROR`. No secrets
are exposed. There is no dynamic plugin discovery, OAuth, or marketplace.

## Moment Graph Foundation

First-class directed relationships between canonical Moments.

```text
Moment
  ↓ typed directed relationship
Moment
```

Vocabulary: `CAUSES`, `ESCALATES`, `CALLBACK_TO`, `CONTRASTS`, `RESPONDS_TO`,
`LEADS_TO`, `SAME_SEQUENCE`.

Relations are stored in SQLite (`moment_relations`), project-scoped, directed,
with deterministic ids. Self-relations and cross-project edges are rejected.
Relationships are created through the runtime service; no automatic inference
and no graph database.

Project duplication does not copy relations in this slice (cloned Moments start
with no edges).

## Phase 5 Exit Status

All Phase 5 exit criteria are satisfied:

- ✓ batch analysis exists
- ✓ project duplication/reuse exists
- ✓ multi-channel reuse works from same source/moments
- ✓ external integrations are adapters
- ✓ Moment Graph relationships are first-class

Phase 5 is complete.