import argparse
import json
from pathlib import Path

from pipeline.stadium_signal import validate_package


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a story package detection JSON.")
    parser.add_argument("--input", required=True, help="Path to package detection JSON")
    parser.add_argument("--min-clips", type=int, default=8, help="Minimum clip count (default: 8)")
    parser.add_argument("--max-clips", type=int, default=15, help="Maximum clip count (default: 15)")
    args = parser.parse_args()

    clips = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if isinstance(clips, dict) and "clips" in clips:
        clips = clips["clips"]

    report = validate_package(clips, min_clips=args.min_clips, max_clips=args.max_clips)

    print(json.dumps(report, indent=2))

    if report["valid"]:
        print("\nPASS")
    else:
        print("\nFAIL")
        for w in report["warnings"]:
            print(f"  - {w}")


if __name__ == "__main__":
    main()
