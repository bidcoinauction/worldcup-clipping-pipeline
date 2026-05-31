<<<<<<< ours
<<<<<<< ours
# Stadium Signal Operating System

Stadium Signal is a local-first football mythology archive. It turns full matches into emotional metadata, researched moments, story arcs, candidate clip windows, captions, and export-ready editing commands.

The system is designed for football cinema, not generic highlight scraping. Goals matter only when they carry emotion, pressure, atmosphere, memory, or mythology.

## Folder Setup

```text
config/      YAML and JSON configuration
data/        Supabase-ready CSV ledgers
docs/        Operating notes
outputs/     Generated manifests, scripts, captions, and exports
scripts/     Local-first workflow commands
MATCHES/     Existing local match folders
FootballArchive/
  RAW/       Primary source video files
  CLIPS/     FFmpeg clip outputs
```

Archive paths use `FOOTBALL_ARCHIVE_ROOT` when set. Defaults are `C:\FootballArchive` on Windows and `FootballArchive/` on macOS/local runs.

## Install

```bash
python -m pip install -r requirements.txt
```

## Seed Data

The repository includes seeded rows for ten mythology-grade matches in `data/matches.csv` and `data/mythology_scores.csv`.

To recreate missing folders and seed rows:

```bash
python scripts/init_archive.py
```

## Validate Data

```bash
python scripts/validate_data.py
pytest
```

Expected validation shape:

```text
✅ matches.csv valid
✅ moments.csv valid
✅ emotional_timelines.csv valid
✅ clip_windows.csv valid
✅ mythology_scores.csv valid
✅ Stadium Signal data validation passed
```

## Mythology Classification

```bash
python scripts/mythology_engine.py --match-id brazil_germany_2014
```

Example output:

```json
{
  "match_id": "brazil_germany_2014",
  "total_score": 99,
  "tier": "S",
  "classification": "Football Mythology",
  "recommended_series": ["The Collapse", "National Trauma", "Football Cinema"]
}
```

Tier logic:

```text
95-100 = S
85-94  = A
75-84  = B
60-74  = C
below 60 = Archive
```

## Story Arcs

```bash
python scripts/generate_story_arcs.py --match-id brazil_germany_2014
```

This writes:

```text
outputs/scripts/brazil_germany_2014_story_arc.json
```

Supported arc types:

- Collapse Arc
- Miracle Arc
- Madness Arc
- Legacy Arc
- Revenge Arc
- Aura Arc
- Straight Rise Arc

## FFmpeg Command Export

Add clip candidates to `data/clip_windows.csv`, then run:

```bash
python scripts/process_researched_windows.py
```

This writes FFmpeg-ready commands to:

```text
outputs/manifests/ffmpeg_clip_commands.txt
```

Commands are not executed by default. To run them:

```bash
python scripts/process_researched_windows.py --execute
```

## Example Workflow

1. Put a source match at `FootballArchive/RAW/brazil_germany_2014.mp4`.
2. Confirm or add the match row in `data/matches.csv`.
3. Add researched emotional beats to `data/moments.csv`.
4. Add timeline phases to `data/emotional_timelines.csv`.
5. Add candidate clip rows to `data/clip_windows.csv`.
6. Run `python scripts/validate_data.py`.
7. Run `python scripts/mythology_engine.py --match-id brazil_germany_2014`.
8. Run `python scripts/generate_story_arcs.py --match-id brazil_germany_2014`.
9. Run `python scripts/process_researched_windows.py`.

## Existing OpenAI Clipping Path

The earlier OpenAI-assisted clipping scripts remain available for transcription, prompt generation, caption generation, and rough platform exports. Those scripts write to `TRANSCRIPTS`, `MATCH_ANALYSIS`, `CLIP_MANIFESTS`, `CAPTIONS`, `THUMBNAILS`, and `EXPORTS`.
=======
=======
>>>>>>> theirs
# gsmg.io 5 BTC Puzzle Kit

This repository is a working kit for the GSMG.io 5 BTC puzzle challenge. It aggregates research notes, decoding utilities, intermediate datasets, and progress logs for the multi-phase puzzle.

## Quick links

- Puzzle progress log: `GSMG_Puzzle_README.md`
- Consolidated roadmap: `GSMG_IO_5BTC_Puzzle.md`
- Master manifest of artifacts: `GSMG_Manifest_README.md`
- Historical hint archive: `What Was Originally Shared.md`
- Level 5 hypothesis reset: `LEVEL5_HYPOTHESIS_RESET.md`

## Repository layout (high level)

- `*.py`: scripts for decoding, validation, and brute-force experiments.
- `*.md`: progress notes, writeups, and reference material.
- `*.csv`, `*.txt`, `*.bin`: intermediate data and solver output.
- `manifest/`: a curated export of key assets and summaries.

## Safety & data handling

This repo includes candidate mnemonics and intermediate solver output. **Never publish real private keys or sensitive credentials.** Treat any mnemonic candidates or derived addresses as sensitive until confirmed.

## Getting started

Most scripts are standalone. If you want to reproduce a specific phase, start with the progress log and follow the referenced scripts. For Level 5 work, read `LEVEL5_HYPOTHESIS_RESET.md` before adding more candidate-generation code so new work targets unresolved ambiguities instead of expanding the search space blindly.

```bash
python gsmg.py
```

## Contributing

If you want to help, please read `CONTRIBUTING.md` for workflow notes and suggested ways to share findings.
<<<<<<< ours
>>>>>>> theirs
=======
>>>>>>> theirs
