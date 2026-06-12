#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.livetv_resolver import resolve_event_url


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Resolve the best Ace Stream hash from a LiveTV event URL."
    )
    parser.add_argument("url", help="LiveTV eventinfo URL")
    parser.add_argument("--json", action="store_true", help="Output as JSON")
    args = parser.parse_args()

    result = resolve_event_url(args.url)

    if args.json:
        output = {
            "best_hash": result.best_hash,
            "acestream_url": f"acestream://{result.best_hash}" if result.best_hash else None,
            "bitrate": result.ranked[0].get("bitrate", "") if result.ranked else "",
            "language": result.ranked[0].get("language", "") if result.ranked else "",
            "rating": result.ranked[0].get("rating", "") if result.ranked else "",
            "availability": result.availability,
            "fetch_method": result.fetch_method,
            "total_hashes": result.metadata.get("total_hashes", 0),
        }
        print(json.dumps(output))
        sys.exit(0 if result.best_hash else 1)

    if not result.best_hash:
        print(f"Availability: {result.availability}")
        print(f"Fetch method: {result.fetch_method}")
        print("No Ace Stream hashes found on this page.")
        sys.exit(1)

    print(f"Best hash:     {result.best_hash}")
    print(f"AceStream URL: acestream://{result.best_hash}")
    if result.ranked:
        r = result.ranked[0]
        print(f"Bitrate:       {r.get('bitrate', '') or 'N/A'} Kbps")
        print(f"Language:      {r.get('language', '') or 'N/A'}")
        print(f"Rating:        {r.get('rating', '') or 'N/A'}%")
    print(f"Availability:  {result.availability}")
    print(f"Fetch method:  {result.fetch_method}")


if __name__ == "__main__":
    main()
