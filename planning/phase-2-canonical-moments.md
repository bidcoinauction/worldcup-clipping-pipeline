# Phase 2 Canonical Moments

## Canonical Moment Role

Phase 2 introduces a normalized, sport-agnostic Moment layer without replacing
existing detector output.

```text
sport-specific detector
        ↓
canonical Moment
        ↓
future Story Runtime
```

The current analysis JSON, moments JSON, and clip manifest CSV remain valid and
unchanged. Canonical Moments are additional runtime records derived from those
artifacts.

## Universal vs Sport Event Type

Each Moment preserves both a universal event type and the original sport detail.

Examples:

- `SCORE / GOAL`
- `CARD / RED_CARD`
- `SAVE / SAVE`
- `FOUL / FOUL`
- `OTHER / EMOTION`

Unknown sport-specific labels map to `OTHER` while preserving the original
label in `sport_event_type` and provenance metadata.

## Persistence

Canonical Moments are stored in the runtime SQLite `moments` table. They
reference runtime `Project` records and, when available, the source artifact
that produced them, such as the existing analysis moments JSON artifact.

The adapter is idempotent. A stable `moment_id` is derived from project,
source artifact, detector identity, sport event type, and start time so reruns
do not duplicate canonical moments.

## Review State

Supported review states:

- `UNREVIEWED`
- `KEEP`
- `REJECT`
- `STRONG`
- `MUST_USE`

Moments default to `UNREVIEWED`. This slice adds the service-layer update
operation and a simple Operator Console review surface.

Review state lives only on canonical runtime Moment records. Raw detection JSON,
moments JSON, and clip manifests are not updated when an operator reviews a
Moment.

Review metadata is minimal:

- `reviewed_at`
- `reviewed_by`

Changing a Moment back to `UNREVIEWED` clears review metadata. Moving to
`KEEP`, `REJECT`, `STRONG`, or `MUST_USE` sets `reviewed_at`.

Re-normalization preserves existing review state and review metadata when a
stable `moment_id` is produced again. New Moments default to `UNREVIEWED`.

The Console supports basic filters:

- All
- Unreviewed
- Keep
- Reject
- Strong
- Must Use

## Managed Analysis Integration

After managed analysis writes and registers existing artifacts, it normalizes
the produced rows into canonical Moments before marking the run succeeded.

If normalization fails, the runtime analysis run fails with
`MOMENT_NORMALIZATION_FAILED`. Existing raw analysis artifacts are preserved and
not rewritten.

## Compatibility

- Existing detector artifacts remain canonical raw outputs.
- Existing clip manifests remain unchanged.
- Existing CLI detection paths remain unchanged.
- Current managed analysis still writes the same raw artifacts before Moment
  normalization.

## Deferred

- Advanced moment review UI.
- Cross-moment relationships.
- Moment Graph.
- Story Runtime.
- Basketball detector implementation.
- External player/team metadata enrichment.
- Story, render, export, channel, or adapter schemas.

## Phase 2 Exit Status

Phase 2 canonical Moment foundations are complete:

- Normalized Moment model exists.
- Current analysis output adapts into canonical Moments.
- Moment review state exists with service-layer validation.
- Console can show reviewed/unreviewed Moments and update review state.
- Sport-specific event types map into universal event types.

Story Runtime remains deferred to Phase 3.
