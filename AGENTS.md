# Stadium Signal Agent Instructions

## Mission

Stadium Signal is a football mythology archive. Prioritize emotional narrative, historical context, and cinematic structure over raw highlight clipping.

## Rules

- Treat full matches as primary source material.
- Do not reduce clips to goals only.
- Every match should include emotional metadata.
- Every moment should include narrative function.
- Every output should support short-form, medium-form, or long-form editing.
- Prefer CSV/JSON/YAML files that can later migrate to Supabase.
- Keep scripts local-first and Windows-compatible.
- Use `FOOTBALL_ARCHIVE_ROOT` when available.
- Default Windows archive root: `C:\FootballArchive`.
- Default local/macOS archive root: `FootballArchive/`.

## Live Recording Workflow

### Platform Roles

- **Windows (Ace Stream):** Capture box. Ace Stream only runs on Windows. Run `record_live.py` here to record live broadcasts.
- **Mac:** Dev/build box. OpenCode, Git, pipeline development, and post-processing. Recorded files from Windows can be transcribed and clipped here.

### Proven Match-Day Workflow

This workflow was validated during the Mexico vs South Africa (World Cup 2026) test.

1. Get the Ace Stream link (e.g., `acestream://HASH`).
2. Extract the hash from the URL.
3. On Windows, record the live stream:
   ```
   python scripts\record_live.py HASH --match-id MATCH_ID --mode full --verbose
   ```
   - Output: `C:\FootballArchive\<match_id>_live.ts`
   - Stop recording by pressing `q` in the terminal (not Ctrl+C).
   - **Do not press Play again in Ace Stream Player while FFmpeg owns the stream** — only one consumer can read the stream at a time.
4. Verify the recording with `ffprobe`:
   ```
   ffprobe C:\FootballArchive\<match_id>_live.ts
   ```
5. Transfer the `.ts` file to Mac (or keep on Windows) for processing.

Full-file recording (`--mode full`) is the safest match-day workflow. It uses reconnect flags (`-reconnect 1 -reconnect_delay_max 4294`) and ignores corrupt packets (`-err_detect ignore_err`).

### First Production Recording — Mexico vs South Africa

```
File:   mexico_south_africa_live.ts
Duration: 38m31s
Size:    ~334 MB
Resolution: 1024x576
```

### Segment Mode (Experimental)

`--mode segment` splits the stream into fixed-duration `.ts` chunks and is consumed by `live_watch.py`. It is **not** part of the current match-day workflow. Segment mode is under development for future automated processing and is less reliable than full-file recording for live Ace Stream captures.

## Archive Architecture

### Git Repository (this repo)
Stores metadata, workflow code, CSV schedules, **match manifests**, transcripts,
clip manifests, and prompts. Everything here is text — safe to version control and share.

### FootballArchive (C:\FootballArchive)
Stores video assets: raw recordings (.ts), exported clips (.mp4), and other
large binary files. This directory is outside the repo and never committed to Git.

### How they connect
Match manifests in `data/manifests/*.json` reference video files by filename.
At runtime, filenames are resolved against `FOOTBALL_ARCHIVE_ROOT` or the
platform default (`C:\FootballArchive` on Windows, `FootballArchive/` on Mac).

## Match Manifest Workflow

Manifests track each match's source recordings and pipeline progress. They are
the bridge between the capture box (Windows) and the processing pipeline.

### Creating a manifest

```bash
# Register the first recording
python scripts/create_match_manifest.py \
  --match-id mexico_south_africa_2026_06_11 \
  --match-no 1 \
  --home Mexico --away "South Africa" \
  --date 2026-06-11 \
  --source mexico_south_africa_live.ts:first_half

# Add a second recording
python scripts/create_match_manifest.py \
  --match-id mexico_south_africa_2026_06_11 \
  --source mexico_south_africa_second_half.ts:second_half
```

### Processing a manifest

`process_from_manifest.py` concatenates all recorded sources into a single
`.ts` file in `RAW/WORLD_CUP/<match_id>.ts`, then delegates to the existing
pipeline. It does **not** reimplement transcription, detection, or manifest
building — those are handled by `process_scheduled_match.py`.

```bash
# Dry-run first to see every subprocess command
python scripts/process_from_manifest.py \
  --manifest data/manifests/mexico_south_africa_2026_06_11.json \
  --dry-run

# Full processing with clip detection
python scripts/process_from_manifest.py \
  --manifest data/manifests/mexico_south_africa_2026_06_11.json \
  --run-detection
```

### Pipeline (delegated entirely to existing scripts)

```
Manifest sources
  -> ffmpeg concat -> RAW/WORLD_CUP/<match_id>.ts
  -> update_match.py        (register in schedule CSV)
  -> process_scheduled_match.py  (transcribe -> prompt -> detection -> manifest)
```

## Required Validation

After code changes, run:

```bash
python scripts/validate_data.py  # or python3 scripts/validate_data.py on systems without a python alias
pytest
```
