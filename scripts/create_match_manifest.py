import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.utils import slugify
from pipeline.stadium_signal import archive_path


MANIFESTS_DIR = Path(__file__).resolve().parents[1] / "data" / "manifests"


def _manifest_path(match_id: str) -> Path:
    return MANIFESTS_DIR / f"{match_id}.json"


def _load(match_id: str) -> dict:
    p = _manifest_path(match_id)
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return None


def _save(data: dict) -> None:
    p = _manifest_path(data["match_id"])
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  Created: {p}")


def main():
    parser = argparse.ArgumentParser(
        description="Create or update a match recording manifest."
    )
    parser.add_argument("--match-id", required=True, help="Canonical match slug")
    parser.add_argument("--match-no", type=int, help="Match number from schedule")
    parser.add_argument("--home", help="Home team name")
    parser.add_argument("--away", help="Away team name")
    parser.add_argument("--date", help="Match date (YYYY-MM-DD)")
    parser.add_argument("--source", action="append", dest="sources",
                        help="filename:label (e.g. file.ts:first_half)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without writing")
    args = parser.parse_args()

    match_id = slugify(args.match_id)

    existing = _load(match_id)
    if existing:
        data = existing
    else:
        if not args.match_no:
            print("Error: --match-no required for new manifests")
            raise SystemExit(1)
        data = {
            "manifest_version": 1,
            "match_id": match_id,
            "match_no": args.match_no,
            "home_team": args.home or "",
            "away_team": args.away or "",
            "date": args.date or "",
            "sources": [],
            "pipeline": {
                "recorded": False,
                "verified": False,
                "transcribed": False,
                "researched": False,
                "clipped": False,
                "exported": False,
            },
        }

    if args.sources:
        existing_filenames = {s["filename"] for s in data["sources"]}
        for src in args.sources:
            if ":" not in src:
                print(f"Error: source must be filename:label, got {src}")
                raise SystemExit(1)
            filename, label = src.split(":", 1)
            if filename in existing_filenames:
                print(f"  Source already exists: {filename}")
                continue
            source_entry = {
                "label": label,
                "filename": filename,
                "status": "recorded",
            }
            data["sources"].append(source_entry)
            data["pipeline"]["recorded"] = True

    if args.dry_run:
        print(f"[dry-run] Would write manifest for {match_id}")
        print(json.dumps(data, indent=2))
        return

    _save(data)


if __name__ == "__main__":
    main()
