from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.config import get_default_clip_mode
from pipeline.prompt_generation import write_prompt_file
from pipeline.utils import ROOT


def main():
    parser = argparse.ArgumentParser(description="Generate Claude prompt from transcript.")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--profile", default="football",
                        help="Registered sport/project profile (default: football)")
    parser.add_argument("--mode", default=None, choices=("story", "micro", "package"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--research", default=None,
                        help="Path to match_research.json with known events")
    parser.add_argument("--condensed-windows", action="store_true",
                        help="Reduce transcript to football-relevant windows for long matches")
    parser.add_argument("--condense-interval", type=int, default=360,
                        help="Seconds between fallback sampling windows (default: 360)")
    parser.add_argument("--anchor-padding", type=int, default=25,
                        help="Seconds each side of research anchor (default: 25)")
    args = parser.parse_args()

    result = write_prompt_file(
        transcript=args.transcript,
        match_name=args.match_name,
        profile=args.profile,
        mode=args.mode or get_default_clip_mode(),
        research=args.research,
        condensed_windows=args.condensed_windows,
        condense_interval=args.condense_interval,
        anchor_padding=args.anchor_padding,
        output_root=ROOT,
    )
    print(f"Claude prompt written to: {result['output_path']}")


if __name__ == "__main__":
    main()
