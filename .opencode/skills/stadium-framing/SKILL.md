---
name: stadium-framing
description: Rules for crop/zoom export profiles — ffmpeg filter chain architecture, profile registry, test patterns, promotion workflow, and vertical reframe conventions
compatibility: opencode
metadata:
  project: worldcup-clipping-pipeline
---

## What I do

I govern the crop/zoom export profile system — how profiles are defined, tested, named, and refactored.

## Rules

- Profiles never modify each other — add, never edit existing profiles (until final promotion).
- Profile name pattern: `vertical_<descriptor>` (e.g. `vertical_safe`, `vertical_social`, `vertical_zoom`).
- Every new profile gets: one entry in `--profile` choices, one `elif` in `ffmpeg_filter()`, 3+ tests (crop values, zoom values, crop-before-split order).
- Filter chain architecture is always:
  ```
  crop → split=2[clean_a][clean_b];
  [clean_a]scale to fill 1080:1920, crop, boxblur → [bg];
  [clean_b]scale to fit 1080 width [, optional zoom] → [fg];
  [bg][fg] overlay centered
  ```
- When promoting a test profile to production: remove the test name from choices, rename the `elif` block, update zoom value, rename tests, run full suite.
- The blur background (`boxblur=28:2`) is shared by all vertical profiles.
- Output is always 1080x1920, libx264 veryfast, CRF 20, AAC 160k, +faststart.
- Audio is always mapped with `-map 0:a?`.
- Profile crop uses four variables: `top`, `bottom`, `left`, `right` — converted to `keep_h` and `keep_w`.
- The zoom step is always a second `scale` after the normal width scale: `scale=iw*<factor>:ih*<factor>[fg]`.

## When to use me

Use this skill before adding, modifying, or removing an export profile in `ffmpeg_filter()`, or when editing the `--profile` choices list. Also before editing the filter chain for any vertical reframe.
