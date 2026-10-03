"""CLI adapter for match transcription.

Thin wrapper over pipeline.transcription.transcribe_source. Preserves
existing CLI behavior: --input, --league, --provider, --model, --dry-run.
"""

import argparse
import os
import sys
from pathlib import Path

from pipeline.config import get_leagues, get_model as _get_model, get_provider


def main():
    parser = argparse.ArgumentParser(
        description="Transcribe a match using OpenAI API or local faster-whisper."
    )
    parser.add_argument("--input", required=True, help="Path to match video/audio file")
    parser.add_argument("--league", required=True, choices=get_leagues())
    parser.add_argument("--provider", default=get_provider("transcription"),
                        choices=["openai", "faster-whisper"])
    parser.add_argument("--model", default=os.getenv("DEFAULT_TRANSCRIBE_MODEL") or _get_model("transcription"))
    parser.add_argument("--force", action="store_true",
                        help="Force retranscription even if transcript exists")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing.")
    args = parser.parse_args()

    from pipeline.transcription import transcribe_source

    result = transcribe_source(
        args.input,
        league=args.league,
        provider=args.provider,
        model=args.model,
        force=args.force,
        dry_run=args.dry_run,
    )

    if result["reused"]:
        print(f"Existing transcript reused: {result['transcript_path']}")
    elif result["dry_run"]:
        print(f"[dry-run] Would transcribe: {args.input}")
        print(f"[dry-run] Would write: {result['transcript_path']}")
    else:
        print(f"Transcript written: {result['transcript_path']}")
        print(f"Segments: {result['segments']}")


if __name__ == "__main__":
    main()
