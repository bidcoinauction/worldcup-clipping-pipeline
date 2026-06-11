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

### Platform Notes

- **Windows (Ace Stream):** Run `record_live.py --mode full` (default). Records a single `.ts` file with reconnect flags for stream resilience. Output: `C:\FootballArchive\<match_id>_live.ts`.
- **macOS / Linux:** Can also use `record_live.py --mode full` after recording, or process the recorded file from any platform.
- **Segment mode:** Use `--mode segment` for fixed-duration chunks consumed by `live_watch.py`.

### Typical Windows Live Flow

1. Get Ace Stream ID for the match.
2. `python scripts/record_live.py <ACE_ID> --match-id <slug>`
3. Press Ctrl+C to stop recording.
4. Process the resulting `C:\FootballArchive\<slug>_live.ts` with `process_match.py` or `transcribe_match.py`.

### Cross-Platform Processing

Recorded `.ts` files from Windows can be transcribed and clipped on macOS/Linux — no RAW import required.

## Required Validation

After code changes, run:

```bash
python scripts/validate_data.py
pytest
```
