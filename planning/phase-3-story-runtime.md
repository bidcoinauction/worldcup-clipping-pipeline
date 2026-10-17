# Phase 3 Story Runtime

## Purpose

Phase 3 introduces first-class canonical Story runtime records and explicit
Story ↔ Moment relationships without replacing the existing story engine or
artifact formats.

```text
Canonical Moments
       ↓
StoryMoment
       ↓
Story
```

A canonical Moment is reusable across many Stories. A Story references many
Moments. The relationship is a real relational table, not a JSON array on the
Story.

## Canonical Story Model

- `story_id`
- `project_id`
- `title`
- `summary`
- `archetype`
- `status`
- `hook`
- `emotional_arc`
- `estimated_duration`
- `recommended_formats`
- `created_at`
- `updated_at`
- `metadata`

Archetypes reuse the existing `story_engine.ARCHETYPES` vocabulary.

## Story Statuses

- `DRAFT`
- `SUGGESTED`
- `APPROVED`
- `REJECTED`
- `ARCHIVED`

Existing story suggestions normalize to `SUGGESTED` by default.

## Narrative Roles

Canonical StoryMoment relationships reuse the existing
`story_engine.NARRATIVE_ROLES` vocabulary:

- `HOOK`
- `SETUP`
- `ESCALATION`
- `CLIMAX`
- `AFTERMATH`

## Editorial Sequence

`sequence_order` is the editorial order of a Moment within a Story. It is
independent from canonical Moment source chronology (`start_seconds`). Future
editing logic depends on this distinction, so story_moments are never
auto-sorted by `start_seconds`.

## Many-to-Many Behavior

- One Moment → many Stories.
- One Story → many Moments.
- The same Moment may appear more than once in a Story at different editorial
  positions; the relationship uses its own deterministic `story_moment_id`
  derived from story, moment, and sequence order, so no premature uniqueness
  rule prevents editorial reuse.

## Cross-Project Safety

Story ↔ Moment relationships are validated explicitly in the service layer.
A Story from one project cannot reference a Moment from another project.

## Provenance

Canonical Stories preserve:

- original story id
- original archetype
- original recommended formats
- original suggestion artifact metadata

StoryMoment relationships preserve the original reference used by the story
engine.

## Legacy Story Compatibility

The existing story engine, story suggestion artifacts, edit brief generation,
and EDL generation remain unchanged. Canonical Story runtime records are
additive.

## Idempotency and Operator State

Canonical Story IDs are deterministic (derived from project + original story
id), so repeated normalization does not duplicate Stories. If an operator has
changed a Story status to `APPROVED`, `REJECTED`, or `ARCHIVED`, re-normalization
preserves that status instead of resetting it to `SUGGESTED`.

## Deferred

- Edit Brief runtime model.
- EDL runtime model.
- Full Story builder / review UI.
- Beat editor.
- Moment Graph.
- Story ranking redesign.
- Batch processing, project duplication, queues/workers, new sport detectors.

## Story Review Workflow

Canonical Story status supports `SUGGESTED`, `APPROVED`, `REJECTED`, `ARCHIVED`.
The operator workflow supports:

- `SUGGESTED` → `APPROVED`
- `SUGGESTED` → `REJECTED`
- `APPROVED` → `ARCHIVED`

Status transitions are validated centrally in the runtime service. The Console
project detail and Story detail pages expose small Approve / Reject / Archive
actions.

## Story Building Operations

Minimal operator Story ↔ Moment editing is supported through the runtime
service:

- add a canonical Moment to a Story
- remove a Moment from a Story
- change a Moment's narrative role
- change a Moment's sequence order

Sequence positions are normalized to contiguous `1..N` values after every edit
so ordering remains deterministic. Cross-project relationships are rejected in
the service layer. A `REJECT` Moment remains visible and can be intentionally
added to a Story; it is marked with a warning indicator in the Console.

## Canonical Edit Brief Association

```text
Moment
→ Story
→ Edit Brief
```

The existing Edit Brief engine remains the generator. When it succeeds, the
existing Edit Brief artifact is written unchanged and registered as a runtime
artifact, and a canonical `edit_briefs` runtime record is created referencing:

- the canonical Story
- the artifact
- the format treatment (`SHORT`, `MEDIUM`, or `LONG`)

Runtime status becomes `READY`. On generation failure the runtime record becomes
`FAILED`; no misleading `READY` is recorded.

Multiple Edit Briefs per Story are supported, one per format treatment
(`story_id + format_treatment` is unique). Re-adapting the same artifact is
idempotent via a deterministic `edit_brief_id`.

Only `APPROVED` Stories can generate an Edit Brief through the normal operator
flow.

## Edit Brief Statuses

- `DRAFT`
- `GENERATING`
- `READY`
- `FAILED`
- `ARCHIVED`

## EDL Runtime

The canonical EDL runtime model is now part of Phase 3. The existing EDL
engine remains the generator and its artifact format is unchanged. A canonical
`edls` runtime record references the canonical Story, Edit Brief, and the EDL
artifact.

```text
Moment
  ↓
Story
  ↓
Edit Brief
  ↓
EDL
```

EDL generation through the operator flow requires a `READY` canonical Edit
Brief for the same format treatment. Format consistency is enforced at the
runtime boundary. On success the runtime EDL becomes `READY`; on failure it
becomes `FAILED` with no misleading `READY`.

EDL statuses:

- `DRAFT`
- `GENERATING`
- `READY`
- `FAILED`
- `ARCHIVED`

## Runtime Records vs Detailed Artifacts

Runtime records in SQLite:

- Moment
- Story
- Edit Brief
- EDL

Detailed artifacts remain files:

- raw analysis JSON / moments JSON / clip manifest CSV
- story suggestion artifact
- edit brief artifact
- EDL artifact

Files remain the canonical detailed source of truth. SQLite provides runtime
relationships, status, provenance, and operator visibility.

## Phase 3 Exit Status

Phase 3 Story Runtime is complete:

- Stories are first-class runtime records.
- Stories reference canonical Moments.
- One Moment can participate in multiple Stories.
- Story editorial ordering is independent of source chronology.
- Story review/status workflow exists.
- Story ↔ Moment building operations exist.
- Edit Brief runtime records are tied to canonical Stories.
- Existing Edit Brief artifacts remain unchanged.
- EDL runtime records are tied to canonical Edit Briefs and Stories.
- Existing EDL artifacts remain unchanged.
- Console moves through Moment review, Story review/building, Edit Brief
  generation, and EDL generation.
- Legacy callers remain functional.
- Tests remain green.

## Deferred To Phase 4

- Render runtime model.
- Rough-cut review.
- Render lifecycle.
- Platform variants.
- Export packages.
- Channel presets.

Also still deferred:

- Moment Graph.
- Batch execution.
- Project duplication.
- Queue/workers.