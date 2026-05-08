# 2026 World Cup Clipping Pipeline — OpenAI API Version

This version avoids local Whisper and avoids pandas.

It is built for your current Mac setup where `python3` is Python 3.14 and `openai-whisper` will not build cleanly.

## Setup

```bash
cd ~/Downloads/worldcup_clipping_pipeline_openai_api

python3 -m venv .venv
source .venv/bin/activate

python -m pip install --upgrade pip setuptools wheel
pip install -r requirements.txt

cp .env.example .env
PYTHONPATH=. python scripts/init_project.py
```

## Add OpenAI Key

Open `.env`:

```bash
nano .env
```

Add:

```bash
OPENAI_API_KEY=your_key_here
DEFAULT_OPENAI_MODEL=gpt-4.1
DEFAULT_TRANSCRIBE_MODEL=gpt-4o-transcribe
```

## Install FFmpeg

```bash
brew install ffmpeg
```

If Homebrew is slow, you can install FFmpeg later. Transcription and clipping both need FFmpeg for video audio extraction.

## Move Videos

```bash
mkdir -p MATCHES/MLS
mkdir -p MATCHES/UCL

mv "LAFC vs. Inter Miami CF.mp4" MATCHES/MLS/
mv "Borussia Dortmund vs. Eintracht Frankfurt.mp4" MATCHES/UCL/
```

## Run Full GPT Pipeline

```bash
PYTHONPATH=. python scripts/process_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --match-name "LAFC vs Inter Miami CF" \
  --run-gpt
```

## Export Rough Clips

```bash
PYTHONPATH=. python scripts/export_clips_ffmpeg.py \
  --manifest CLIP_MANIFESTS/lafc_vs_inter_miami_cf_manifest.csv \
  --source-video "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --platform TIKTOK
```


## Timestamped Transcription (faster-whisper)

This pipeline now uses local `faster-whisper` for word-level timestamps and structured segments.

```bash
PYTHONPATH=. python scripts/transcribe_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --model large-v3 \
  --device auto
```

Use `--dry-run` to validate paths/commands without running inference.

See `docs_transcription_migration.md` for migration details.
