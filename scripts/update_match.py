import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.utils import ROOT


def main():
    parser = argparse.ArgumentParser(
        description="Update a World Cup 2026 match in the schedule CSV."
    )
    parser.add_argument("--match", type=int, required=True, help="Match number")
    parser.add_argument("--home", help="Home team")
    parser.add_argument("--away", help="Away team")
    parser.add_argument("--venue", help="Venue")
    parser.add_argument("--notes", help="Notes (e.g. group stage)")
    parser.add_argument("--stream-url", dest="stream_url", help="Stream URL")
    parser.add_argument("--pipeline-status", dest="pipeline_status",
                        choices=["planned", "recording", "recorded",
                                 "transcribed", "detected", "exported", "posted"],
                        help="Pipeline status")
    parser.add_argument("--source-video-path", dest="source_video_path",
                        help="Local path to source video file")
    parser.add_argument("--file", default=ROOT / "data" / "worldcup_2026_schedule.csv",
                        help="Schedule CSV path")
    args = parser.parse_args()

    path = Path(args.file)
    if not path.exists():
        print(f"Error: file not found: {path}")
        raise SystemExit(1)

    with path.open("r", encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f))

    match = None
    for row in rows:
        if int(row["match_no"]) == args.match:
            match = row
            break

    if match is None:
        print(f"Error: match {args.match} not found")
        raise SystemExit(1)

    updated = []
    for key in ("home", "away", "venue", "notes", "stream_url", "pipeline_status", "source_video_path"):
        val = getattr(args, key, None)
        if val is not None:
            match[key] = val
            updated.append(key)

    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

    print(f"Match {args.match}: updated {', '.join(updated)}")


if __name__ == "__main__":
    main()
