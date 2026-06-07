import argparse
import json
from pathlib import Path
from pipeline.utils import ROOT, slugify

TEMPLATE = {
    "match": {
        "home_team": "",
        "away_team": "",
        "competition": "",
        "date": "",
    },
    "events": [],
}


def main():
    parser = argparse.ArgumentParser(
        description="Scaffold a match_research.json file for a match."
    )
    parser.add_argument("--league", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--home-team", default="")
    parser.add_argument("--away-team", default="")
    parser.add_argument("--competition", default="")
    parser.add_argument("--date", default="")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print target path without writing.")
    parser.add_argument("--force", action="store_true",
                        help="Overwrite existing file.")
    args = parser.parse_args()

    match_slug = slugify(args.match_name)
    out_path = ROOT / "MATCH_RESEARCH" / args.league / match_slug / "match_research.json"

    if not args.dry_run:
        if out_path.exists() and not args.force:
            print(f"File already exists: {out_path}")
            print("Use --force to overwrite.")
            return

        out_path.parent.mkdir(parents=True, exist_ok=True)
        data = TEMPLATE.copy()
        data["match"] = {
            "home_team": args.home_team,
            "away_team": args.away_team,
            "competition": args.competition,
            "date": args.date,
        }
        out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
        print(f"Created: {out_path}")
        print("Edit the file and add events to the 'events' array.")
    else:
        print(f"[dry-run] Would create: {out_path}")


if __name__ == "__main__":
    main()
