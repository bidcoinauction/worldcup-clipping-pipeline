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
