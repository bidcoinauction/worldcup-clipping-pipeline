#!/usr/bin/env bash
set -e

PYTHON_BIN="${PYTHON_BIN:-.venv/bin/python}"
export PYTHONPATH="${PYTHONPATH:-.}"

"$PYTHON_BIN" scripts/init_project.py
"$PYTHON_BIN" scripts/generate_caption_bank.py

echo ""
echo "Lean local daily flow:"
echo "0. Check readiness: $PYTHON_BIN scripts/doctor.py"
echo "1. Download/save the match file locally, then drop it into MATCHES/[LEAGUE]/"
echo "2. For Footballia, use your normal browser/account flow and keep the page URL for source tracking"
echo "3. Run: $PYTHON_BIN scripts/process_match.py --input MATCHES/MLS/file.mp4 --league MLS --match-name 'Team A vs Team B' --source footballia --source-url 'https://footballia.eu/...' --run-signals --run-gpt"
echo "4. If you skip --run-gpt, paste the generated PROMPTS file into ChatGPT, save JSON, then run build_clip_manifest.py"
echo "5. Export rough clips with export_clips_ffmpeg.py"
echo "6. Manual polish in CapCut/Premiere"
echo "7. Log posts and update metrics"
