---
name: dry-run-safety
description: Add or verify dry-run behavior for expensive, destructive, or side-effect-heavy scripts
compatibility: opencode
metadata:
  project: worldcup-clipping-pipeline
---

## What I do

I protect scripts from accidental expensive or destructive actions.

## Dry-run rules

When `--dry-run` is enabled:

- Do not call OpenAI.
- Do not call Anthropic.
- Do not run ffmpeg.
- Do not write files.
- Do not overwrite files.
- Do not create directories unless explicitly allowed.
- Print clear `[dry-run]` messages.
- Exit successfully if the planned actions are valid.

## Target operations

Guard these first:

- API calls
- ffmpeg calls
- file writes
- directory creation
- CSV append operations
- bulk export loops

## When to use me

Use this skill when adding, auditing, or testing `--dry-run` behavior.
