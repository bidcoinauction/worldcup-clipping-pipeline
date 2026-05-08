# Transcription Migration Notes (OpenAI API -> faster-whisper)

## What changed
- `scripts/transcribe_match.py` now uses local `faster-whisper` instead of OpenAI transcription API.
- Outputs now include word-level timestamps and speaker-style tags.

## Backward compatibility
- `transcript.txt` is still generated in the same transcript folder.
- `timestamps.json` still exists but now contains real segment data instead of an empty array placeholder.
- New `segments.json` provides versioned schema for downstream consumers.

## New outputs
- `transcript.txt`: flattened transcript text.
- `timestamps.json`: segment list with `start`, `end`, `text`, `speaker_style`, and `words`.
- `segments.json`: schema wrapper with metadata + segments.
- `metadata.json`: run metadata, model, engine, and timestamp.

## Installation
```bash
pip install -r requirements.txt
brew install ffmpeg
```

## Example run
```bash
PYTHONPATH=. python scripts/transcribe_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --model large-v3 \
  --device auto
```

## Dry run
```bash
PYTHONPATH=. python scripts/transcribe_match.py \
  --input "MATCHES/MLS/LAFC vs. Inter Miami CF.mp4" \
  --league MLS \
  --dry-run
```
