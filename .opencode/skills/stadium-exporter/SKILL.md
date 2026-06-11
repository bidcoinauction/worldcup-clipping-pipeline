---
name: stadium-exporter
description: Rules for the ffmpeg clip export pipeline — CSV reading, source resolution, ffmpeg invocation, manifest writing, and profile resolution
compatibility: opencode
metadata:
  project: worldcup-clipping-pipeline
---

## What I do

I help produce video clips from research window CSVs.

## Rules

- Always use list-form subprocess, never shell strings or `shell=True`.
- Preserve the CSV-first pipeline — every clip must trace back to a `clip_windows_*.csv` row.
- Source resolution order: absolute path → raw_dir + basename → raw_dir + glob match; raise `FileNotFoundError` with context.
- `--force` uses `-y`; without it, use `-n` to skip existing outputs.
- Export profiles are resolved: CSV row's `export_profile` column → CLI `--profile` default.
- The manifest (`CLIP_MANIFESTS/researched_clip_exports.csv`) is append-only, merging by `clip_id`.
- Tests must mock `subprocess.run` — never call real ffmpeg in tests.
- Add `--dry-run` for any new CLI command that calls ffmpeg or writes files.
- Output is always 1080x1920, libx264 veryfast, CRF 20, AAC 160k, +faststart.

## When to use me

Use this skill when editing `export_research_windows.py`, `export_clips_ffmpeg.py`, `export_vertical_blur.py`, `build_clip_manifest.py`, or their test files.
