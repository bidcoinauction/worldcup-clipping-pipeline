#!/usr/bin/env bash
set -e

python scripts/init_project.py
python scripts/generate_caption_bank.py

echo ""
echo "GPT-connected daily flow:"
echo "1. Drop legal match/highlight file into MATCHES/[LEAGUE]/"
echo "2. Run: python scripts/process_match.py --input MATCHES/MLS/file.mp4 --league MLS --match-name 'Team A vs Team B'"
echo "3. Use --run-gpt if OPENAI_API_KEY is set"
echo "4. Build manifest"
echo "5. Generate thumbnail/caption prompts"
echo "6. Export rough clips with export_clips_ffmpeg.py"
echo "7. Manual polish in CapCut/Premiere"
echo "8. Log posts and update metrics"
