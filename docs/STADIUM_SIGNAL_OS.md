# Stadium Signal OS

Stadium Signal is a football mythology archive. The data layer is intentionally CSV-first so it can stay local today and migrate to Supabase later.

## Canonical Workflow

```text
full match source
  -> match metadata
  -> researched emotional moments
  -> emotional timeline
  -> mythology score
  -> story arc
  -> clip windows
  -> FFmpeg commands
```

## Required CSVs

`data/matches.csv`

```csv
match_id,title,date,competition,stage,venue,teams,primary_emotion,secondary_emotions,mythology_score,status,source_url,local_path,notes
```

`data/moments.csv`

```csv
moment_id,match_id,match_minute,video_timestamp,title,event_type,emotion,narrative_function,importance_score,clip_start,clip_end,notes
```

`data/emotional_timelines.csv`

```csv
timeline_id,match_id,sequence_order,match_minute,video_timestamp,emotion,label,description
```

`data/clip_windows.csv`

```csv
clip_id,match_id,moment_id,clip_type,start_time,end_time,duration_seconds,series,hook,caption,status
```

`data/mythology_scores.csv`

```csv
match_id,stakes,crowd_emotion,historical_impact,narrative_arc,cultural_memory,total_score,tier
```

## Match Manifest Workflow

For multi-source recordings (e.g. first half, second half recorded separately), the pipeline uses JSON manifests to track sources and pipeline progress.

### Creating a Manifest

```bash
python scripts/create_match_manifest.py \
  --match-id mexico_south_africa_2026_06_11 \
  --match-no 1 \
  --home Mexico --away "South Africa" \
  --date 2026-06-11 \
  --source mexico_south_africa_live.ts:first_half

# Add a second recording source
python scripts/create_match_manifest.py \
  --match-id mexico_south_africa_2026_06_11 \
  --source mexico_south_africa_second_half.ts:second_half
```

### Processing from a Manifest

```bash
# Dry-run to verify commands
python scripts/process_from_manifest.py \
  --manifest data/manifests/mexico_south_africa_2026_06_11.json \
  --dry-run

# Full processing (concat -> register -> transcribe -> detect -> clip)
python scripts/process_from_manifest.py \
  --manifest data/manifests/mexico_south_africa_2026_06_11.json \
  --run-detection
```

The `process_from_manifest.py` script:
- Concatenates all recorded sources into `RAW/WORLD_CUP/<match_id>.ts`
- Auto-creates the output directory if it does not exist
- Runs child processes (`update_match.py`, `process_scheduled_match.py`) with `cwd` set to the repository root
- Updates the manifest pipeline status on success

See `data/manifests/` for available manifests. See `AGENTS.md` for full workflow details.

## Live Recording Workflow

For live matches (World Cup 2026 and later), the pipeline starts with an Ace Stream broadcast instead of an archived file.

```text
Ace Stream ID
  -> record_live.py --mode full (Windows)
  -> <match_id>_live.ts
  -> [future] process_live_recording.py
       -> transcription
       -> timestamps.json
       -> clip detection
       -> clip manifest
       -> exports
```

### Platform Roles

- **Windows:** Capture box. Ace Stream only runs on Windows. `record_live.py` records the stream to a single `.ts` file with reconnect flags and corrupt-packet tolerance.
- **Mac:** Dev/build box. Pipeline development, OpenCode, Git. Transcribing and clipping can happen here after the `.ts` file is transferred.

### Match-Day Steps

1. Extract the Ace Stream hash from `acestream://HASH`.
2. On Windows: `python scripts\record_live.py HASH --match-id MATCH_ID --mode full --verbose`
3. Stop with `q`. Do not press Play in Ace Stream Player while FFmpeg owns the stream.
4. Verify with `ffprobe C:\FootballArchive\<match_id>_live.ts`.
5. Transfer for processing.

See `data/worldcup_2026_schedule.csv` for all 104 match schedules.

## Validation

```bash
python scripts/validate_data.py
pytest
```

Validation confirms required files, required columns, match references, clip references, and mythology score ranges.

## Mythology Engine

```bash
python scripts/mythology_engine.py --match-id brazil_germany_2014
```

The engine reads `data/mythology_scores.csv`, applies tier logic, and returns a JSON classification with recommended series.

## Story Arc Generator

```bash
python scripts/generate_story_arcs.py --match-id brazil_germany_2014
```

The generator writes `outputs/scripts/<match_id>_story_arc.json` using one of the supported arc types:

- Collapse Arc
- Miracle Arc
- Madness Arc
- Legacy Arc
- Revenge Arc
- Aura Arc
- Straight Rise Arc

## Researched Window Processor

```bash
python scripts/process_researched_windows.py
```

This reads `data/clip_windows.csv` and writes `outputs/manifests/ffmpeg_clip_commands.txt`. Use `--execute` only when ready to run FFmpeg.
