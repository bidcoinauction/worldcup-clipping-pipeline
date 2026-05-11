# 2026 World Cup Clipping Pipeline — Lean Local Setup

This setup keeps the expensive parts small:

- **FFmpeg:** local/free audio extraction and clip export, supplied by `imageio-ffmpeg` if no system FFmpeg is installed
- **faster-whisper:** local/free transcription
- **GPT:** optional clip detection only, usually cents to around $1 per match depending on model and transcript length
- **Export/rendering:** local/free

## Setup

```bash
cd ~/Downloads/worldcup_clipping_pipeline_openai_api

brew install python@3.12
python3.12 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

cp .env.example .env
PYTHONPATH=. python scripts/init_project.py
PYTHONPATH=. python scripts/doctor.py
```

Use Python 3.11 or 3.12 for the venv. The current system Python 3.14 does not have compatible `onnxruntime` wheels yet, which blocks `faster-whisper`.

## Add OpenAI Key

Open `.env`:

```bash
nano .env
```

Add:

```bash
OPENAI_API_KEY=your_key_here
DEFAULT_OPENAI_MODEL=gpt-4.1
```

## FFmpeg

Transcription and clipping both need FFmpeg. If `ffmpeg` is already installed on your system, the scripts use it. Otherwise, `imageio-ffmpeg` supplies a local FFmpeg binary through `pip install -r requirements.txt`.

## Add Videos

Save downloaded match files locally, then put them under `MATCHES/<LEAGUE>/`.

```bash
mkdir -p MATCHES/MLS
mkdir -p MATCHES/UCL

mv "LAFC vs. Inter Miami CF.mp4" MATCHES/MLS/
mv "Borussia Dortmund vs. Eintracht Frankfurt.mp4" MATCHES/UCL/
```

For Footballia videos, download through your normal browser/account flow, keep the file local, and pass the source page URL into the pipeline for tracking. The pipeline does not need a Footballia scraper or downloader.

## Run Full GPT Pipeline

```bash
PYTHONPATH=. python scripts/process_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --match-name "LAFC vs Inter Miami CF" \
  --source footballia \
  --source-url "https://footballia.eu/..." \
  --run-signals \
  --run-gpt
```

This runs:

1. FFmpeg audio extraction
2. local `faster-whisper` transcription with timestamps
3. local signal detection from transcript keywords and crowd/audio spikes
4. GPT clip detection, if `--run-gpt` is included
5. manifest + caption/thumbnail prompt generation

If anything feels off, run:

```bash
PYTHONPATH=. python scripts/doctor.py
```

Use `--strict` when you want warnings, like a missing `OPENAI_API_KEY`, to fail the check.

Scoreboard/OCR sampling is optional because it can be slow on some Footballia files. Use it only when you want to tune the scoreboard crop:

```bash
PYTHONPATH=. python scripts/process_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --match-name "LAFC vs Inter Miami CF" \
  --source footballia \
  --source-url "https://footballia.eu/..." \
  --run-signals \
  --run-scoreboard \
  --scoreboard-duration-seconds 180
```

## Export Rough Clips

```bash
PYTHONPATH=. python scripts/export_clips_ffmpeg.py \
  --manifest CLIP_MANIFESTS/lafc_vs_inter_miami_cf_manifest.csv \
  --source-video "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --platform TIKTOK \
  --layout hybrid \
  --output-set TIKTOK_HYBRID
```

By default exports are clean, with no text burned in. Use `--burn-text` only when the overlay style is ready for production.
