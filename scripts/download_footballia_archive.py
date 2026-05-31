import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import MATCH_FIELDS, ROOT, planned_match_rows_from_config, read_csv, write_csv


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Queue Footballia source URLs into the local archive ledger. Downloading is intentionally manual/browser-led."
    )
    parser.add_argument("--config", default=ROOT / "config/match_urls.json")
    parser.add_argument("--write", action="store_true", help="Append queued rows to data/matches.csv.")
    args = parser.parse_args()

    planned_rows = planned_match_rows_from_config(args.config)
    if not planned_rows:
        print("No Footballia matches found in config/match_urls.json.")
        return

    if args.write:
        existing = read_csv(ROOT / "data/matches.csv")
        existing_ids = {row.get("match_id") for row in existing}
        merged = existing + [row for row in planned_rows if row.get("match_id") not in existing_ids]
        write_csv(ROOT / "data/matches.csv", merged, MATCH_FIELDS)
        print(f"Queued {len(merged) - len(existing)} new source rows in data/matches.csv.")
    else:
        for row in planned_rows:
            print(f"{row['match_id']}: {row['source_url']}")


if __name__ == "__main__":
    main()
