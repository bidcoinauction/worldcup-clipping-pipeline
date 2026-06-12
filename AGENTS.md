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

## Next Build Phase

Create `process_live_recording.py` to automate the post-recording pipeline:

Input: `<match_id>_live.ts`
Pipeline: transcription -> timestamps.json -> clip detection -> clip manifest -> exports

Target architecture:

```
Ace Stream
  -> record_live.py --mode full
  -> <match_id>_live.ts
  -> process_live_recording.py
  -> transcripts
  -> clips
  -> exports
```

## Required Validation

After code changes, run:

```bash
python scripts/validate_data.py
pytest
```
