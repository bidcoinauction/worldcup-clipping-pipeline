# Phase 4 Media Workflow

## Purpose

Phase 4 makes rendered rough cuts first-class runtime objects and introduces a
rough-cut review workflow around the existing rendering pipeline.

```text
Moment
  ↓
Story
  ↓
Edit Brief
  ↓
EDL
  ↓
Render
```

The rendered video file remains the media artifact and source of truth. The
runtime Render record tracks identity, execution status, and operator review
state.

## Canonical Render Model

- `render_id`
- `project_id`
- `story_id`
- `edit_brief_id`
- `edl_id`
- `artifact_id`
- `format_treatment`
- `render_profile`
- `status`
- `review_state`
- `duration_seconds`, `width`, `height`, `fps`
- `reviewed_at`, `reviewed_by`, `review_note`
- timestamps and metadata

## Execution Status vs Review State

Execution status and operator review state are separate:

- Execution status: `QUEUED`, `RENDERING`, `READY`, `FAILED`, `ARCHIVED`.
- Review state: `UNREVIEWED`, `APPROVED`, `NEEDS_CHANGES`, `REJECTED`.

A successful new rough cut defaults to `status = READY`, `review_state = UNREVIEWED`.

## Rough-Cut Review Workflow

- `UNREVIEWED → APPROVED`
- `UNREVIEWED → NEEDS_CHANGES`
- `UNREVIEWED → REJECTED`
- `NEEDS_CHANGES → APPROVED`
- `APPROVED → NEEDS_CHANGES`

Moving to a non-UNREVIEWED state sets `reviewed_at`; returning to `UNREVIEWED`
clears review metadata. An optional plain-text `review_note` is supported.

## Multiple Render Profiles Per EDL

One EDL can produce multiple variants distinguished by `render_profile`
(`REFERENCE`, `EDITORIAL` for this slice). Uniqueness is
`edl_id + render_profile`. Render/export platform presets are deferred to the
next slice.

## Render Generation

Rough-cut generation through the operator flow requires a `READY` canonical
EDL for the same format treatment. The existing renderer writes the video
unchanged; the runtime Render record references the canonical Story, Edit
Brief, EDL, and the registered `render_video` artifact.

On success: `status = READY`, `review_state = UNREVIEWED`.
On failure: `status = FAILED` with no misleading artifact.

## Video Preview

The Console serves registered render artifacts through a safe route that:

- only serves artifacts registered in the runtime index
- verifies the render belongs to the current project
- rejects unknown or cross-project render ids

No custom video editor, timeline, waveform, or annotation canvas.

## Deferred To Phase 4 Slice 2+

- Export packages.
- Channel/platform presets.
- Platform render variants beyond reference profiles.
- Publishing APIs.
- Batch rendering.
- Render versioning (re-renders currently upsert per profile).

Phase 4 is not complete yet.

## Channel / Platform Presets

A small static preset registry in `pipeline/channel_models.py` defines platform
delivery targets. Platform vocabulary: `TIKTOK`, `INSTAGRAM_REELS`,
`YOUTUBE_SHORTS`, `X`, `GENERIC_VERTICAL`, `GENERIC_HORIZONTAL`.

Each preset carries dimensions, fps, max duration, render profile, format
treatment, caption style, and safe-area profile. Presets are validated against
the EDL format treatment and render profile before use.

## Render Variants

A canonical Render can be associated with a `channel_preset_id` and `platform`.
One EDL can produce multiple independently reviewable platform variants
(`edl_id + render_profile + channel_preset_id` unique). Generating a new variant
never mutates the review state of existing renders.

## Export Packages

An Export Package is a runtime deliverable bundle that references an approved
render video artifact plus delivery metadata:

```text
EDL
 ↓
Render Variant
 ↓
Review
 ↓
Export Package
```

Only `status = READY` + `review_state = APPROVED` renders can create an Export
Package through the normal operator flow. Export status vocabulary:
`DRAFT`, `READY`, `DELIVERED`, `FAILED`, `ARCHIVED`.

Export idempotency: `render_id + channel_preset_id` is unique, so re-creating
the same package updates the record rather than duplicating it. Versioned
exports are deferred.

## Runtime vs File Artifacts

The rendered video file remains the media artifact. The runtime Render and
Export Package records provide identity, status, review, platform, and delivery
relationships.

## Phase 4 Exit Status

Phase 4 Slice 2 completes the following exit criteria:

- Renders are first-class runtime records.
- Rough-cut review states exist.
- Export packages exist.
- Channel/platform presets exist.
- Same Story/Edit can produce platform variants.
- Console supports review video → export.

Direct publishing, scheduled posting, batch rendering, and export versioning
remain deferred.