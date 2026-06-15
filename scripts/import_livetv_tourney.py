from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from pipeline import showvideo_resolver
from pipeline.utils import ROOT

logger = logging.getLogger(__name__)

DEFAULT_OUTPUT_ROOT = ROOT / "FootballArchive" / "RAW_HIGHLIGHTS"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import videos from a LiveTV tournament video index page."
    )
    parser.add_argument("--url", required=True, help="LiveTV videotourney page URL")
    parser.add_argument("--league", default="WORLD_CUP", help="Tournament/league name (default: WORLD_CUP)")
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT),
                        help="Base output directory (default: FootballArchive/RAW_HIGHLIGHTS)")
    parser.add_argument("--type", default="all",
                        help='Comma-separated video types: highlights,goals,long_highlights,short_highlights,full_match (default: all)')
    parser.add_argument("--match-filter", default=None,
                        help="Case-insensitive substring filter on match name")
    parser.add_argument("--limit", type=int, default=None,
                        help="Maximum number of videos to import")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would be done without downloading")
    parser.add_argument("--execute", action="store_true",
                        help="Actually download the discovered videos")
    parser.add_argument("--force", action="store_true",
                        help="Re-download even if already present")
    parser.add_argument("--add-to-clip-windows", action="store_true",
                        help="Append a candidate row to data/clip_windows.csv for each download")
    parser.add_argument("--verbose", action="store_true",
                        help="Show detailed debug output")
    return parser.parse_args(argv)


def _parse_type_filter(raw: str) -> set[str]:
    types = {t.strip().lower() for t in raw.split(",") if t.strip()}
    if "all" in types:
        return {"all"}
    for t in types:
        if t not in showvideo_resolver.VALID_TOURNEY_TYPES:
            raise ValueError(f"Invalid video type: {t!r}. Valid: {', '.join(sorted(showvideo_resolver.VALID_TOURNEY_TYPES))}")
    return types


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.dry_run and not args.execute:
        print("Error: must specify --dry-run or --execute")
        return 1

    if args.verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    output_root = Path(args.output_root)

    # Fetch tournament page
    html, fetch_method = showvideo_resolver.fetch_page(args.url)
    if not html:
        print(f"Error: could not fetch tournament page: {args.url}")
        return 1

    # Parse entries
    entries = showvideo_resolver.parse_tourney_page(html, league=args.league)
    if not entries:
        print("No videos found on tournament page.")
        return 0

    # Apply type filter
    type_filter = _parse_type_filter(args.type)
    if "all" not in type_filter:
        entries = [e for e in entries if e.video_type in type_filter]

    # Apply match filter
    if args.match_filter:
        mf = args.match_filter.lower()
        entries = [e for e in entries if mf in e.match_name.lower()]

    # Apply limit
    if args.limit is not None and args.limit < len(entries):
        entries = entries[:args.limit]

    if not entries:
        print("No matching entries after filters.")
        return 0

    # Print discovery
    print()
    print(f"Tournament: videotourney/ (league={args.league}, type={args.type})")
    print(f"Entries:    {len(entries)} video(s) discovered")
    if args.match_filter:
        print(f"Filter:    match={args.match_filter}")
    print()
    for i, e in enumerate(entries, 1):
        flag = ""
        if args.execute:
            match_dir = output_root / showvideo_resolver.slugify(e.match_name)
            sidecars = showvideo_resolver.read_sidecars(match_dir)
            existing = any(
                e.showvideo_url in sc.get("source_page_url", "")
                for sc in sidecars.values()
            )
            if existing and not args.force:
                flag = " [skip-sidecar]"
        print(f"  {i:3d}. [{e.video_type:20s}] {e.match_name:30s} {e.label:20s}{flag}")
        if args.verbose:
            print(f"       {e.showvideo_url}")

    if args.dry_run:
        print()
        print(f"[dry-run] Would download {len(entries)} video(s) to {output_root}")
        print("[dry-run] Use --execute to perform the download.")
        return 0

    # Execute imports
    print()
    print("Downloading...")
    print()

    results: list[dict[str, object]] = []
    errors = 0
    successes = 0
    skips = 0

    for i, entry in enumerate(entries, 1):
        match_slug = showvideo_resolver.slugify(entry.match_name)
        match_dir = output_root / match_slug

        # Sequence number: existing mp4 files + 1
        existing_files = list(match_dir.glob(f"{match_slug}_livetv_*.mp4")) if match_dir.exists() else []
        seq = len(existing_files) + 1

        # Dedup check: skip if any sidecar references this showvideo URL
        sidecars = showvideo_resolver.read_sidecars(match_dir)
        already_done = any(
            entry.showvideo_url in sc.get("source_page_url", "")
            for sc in sidecars.values()
        )
        if already_done and not args.force:
            print(f"  [{i}/{len(entries)}] Skipping {entry.match_name} ({entry.label}) — sidecar exists")
            skips += 1
            continue

        print(f"  [{i}/{len(entries)}] {entry.match_name} ({entry.label})...", end=" ", flush=True)

        try:
            result = showvideo_resolver.resolve_and_download(
                showvideo_url=entry.showvideo_url,
                match_name=entry.match_name,
                league=entry.league,
                output_root=args.output_root,
                dry_run=False,
                force=args.force,
                verbose=args.verbose,
                sequence_num=seq,
            )
        except Exception as exc:
            print(f"ERROR: {exc}")
            errors += 1
            continue

        if result.get("error"):
            if result.get("skipped"):
                print("skipped (already downloaded)")
                skips += 1
            else:
                print(f"FAILED: {result['error']}")
                errors += 1
        else:
            out = result.get("output_path", "?")
            print(f"OK -> {out}")
            successes += 1
            results.append(result)

    # Summary
    print()
    print("=" * 50)
    print(f"  Total:   {len(entries)}")
    print(f"  OK:      {successes}")
    print(f"  Skipped: {skips}")
    print(f"  Errors:  {errors}")
    print("=" * 50)

    if errors > 0:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
