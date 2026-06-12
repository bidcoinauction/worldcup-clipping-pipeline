# Windows Capture Box Setup

This guide covers setting up a Windows machine as a capture box for recording Ace Stream broadcasts.

## Prerequisites

1. **Python 3.10+** — Install from [python.org](https://python.org) and check "Add Python to PATH".
2. **FFmpeg** — Download from [ffmpeg.org](https://ffmpeg.org) and add the `bin` folder to your PATH.
3. **Ace Stream** — Install from [acestream.org](http://acestream.org). The desktop app provides the HTTP endpoint for FFmpeg to consume.
4. **Git** — Install from [git-scm.com](https://git-scm.com) to clone the repository.

## Verify Tools

```powershell
python --version
ffmpeg -version
git --version
```

## Clone the Repository

```powershell
git clone <repo-url> C:\StadiumSignal
cd C:\StadiumSignal
```

## Install Python Dependencies

```powershell
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Environment Variables

Copy `.env.example` to `.env` and fill in your API key:

```
OPENAI_API_KEY=sk-...
```

The pipeline does **not** require an OpenAI key for recording — only for transcription and clip detection. For recording-only setups, the `.env` file can be left with a placeholder.

## Archive Directory

The default archive root on Windows is `C:\FootballArchive`. You can override it with the `FOOTBALL_ARCHIVE_ROOT` environment variable:

```powershell
set FOOTBALL_ARCHIVE_ROOT=D:\Archive
```

## Recording a Match

```powershell
.venv\Scripts\activate
python scripts\record_live.py HASH --match-id MY_MATCH --mode full --verbose
```

- Press `q` to stop recording gracefully.
- Do **not** press Play in Ace Stream Player while FFmpeg is recording.
- Output: `C:\FootballArchive\<match_id>_live.ts` (or your custom `FOOTBALL_ARCHIVE_ROOT`).

## Verify Recording

```powershell
ffprobe C:\FootballArchive\<match_id>_live.ts
```

## Processing on Mac

Transfer the `.ts` file from `C:\FootballArchive\` to `FootballArchive/` on the Mac dev box, then use `process_from_manifest.py`:

```bash
python scripts/process_from_manifest.py --manifest data/manifests/<match_id>.json
```

## Manifest Workflow

```powershell
:: Create manifest for a new match
python scripts\create_match_manifest.py ^
  --match-id my_match_2026_06_11 ^
  --match-no 5 ^
  --home Mexico --away "South Africa" ^
  --date 2026-06-11 ^
  --source my_match_live.ts:first_half

:: After recording a second half, add it
python scripts\create_match_manifest.py ^
  --match-id my_match_2026_06_11 ^
  --source my_match_second_half.ts:second_half

:: Transfer .ts files to Mac, then process from manifest
python scripts\process_from_manifest.py ^
  --manifest data\manifests\my_match_2026_06_11.json ^
  --run-detection
```

## Reference

- `AGENTS.md` — full workflow documentation
- `record_live.py` — `--mode full` is the recommended (safest) option
- `create_match_manifest.py` — register recordings in a JSON manifest
- `process_from_manifest.py` — concat sources and run the pipeline
