#!/usr/bin/env python3
"""Import a video from a LiveTV showvideo URL into the local archive.

Resolves the underlying public MP4/HLS URL, downloads it into
FootballArchive/RAW_HIGHLIGHTS/<match_slug>/, writes a sidecar .import.json,
and optionally appends a candidate row to data/clip_windows.csv.

Usage:
  python scripts/import_livetv_showvideo.py \\
    --url https://livetv.sx/enx/showvideo/123456/ \\
    --league WORLD_CUP \\
    --match-name "Haiti vs Scotland" \\
    --dry-run

  python scripts/import_livetv_showvideo.py \\
    --url https://livetv.sx/enx/showvideo/123456/ \\
    --league WORLD_CUP \\
    --match-name "Haiti vs Scotland" \\
    --execute

  python scripts/import_livetv_showvideo.py \\
    --url https://livetv.sx/enx/showvideo/123456/ \\
    --league WORLD_CUP \\
    --match-name "Haiti vs Scotland" \\
    --execute --add-to-clip-windows --verbose
"""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.showvideo_resolver import resolve_and_download, logger
from pipeline.utils import ROOT, slugify


CLIP_WINDOWS_CSV = ROOT / "data" / "clip_windows.csv"

CLIP_WINDOWS_FIELDS = [
    "clip_id",
    "match_id",
    "moment_id",
    "clip_type",
    "start_time",
    "end_time",
    "duration_seconds",
    "series",
    "hook",
    "caption",
    "status",
]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Import a video from a LiveTV showvideo URL into the local archive."
    )
    parser.add_argument("--url", required=True, help="LiveTV showvideo URL")
    parser.add_argument("--league", default="WORLD_CUP", help="Tournament/league name (default: WORLD_CUP)")
    parser.add_argument("--match-name", required=True, help='Match display name, e.g. "Haiti vs Scotland"')
    parser.add_argument(
        "--output-root",
        default=str(ROOT / "FootballArchive" / "RAW_HIGHLIGHTS"),
        help="Root directory for downloaded highlights (default: FootballArchive/RAW_HIGHLIGHTS)",
    )
    parser.add_argument("--dry-run", action="store_true", help="Show what would be done without downloading")
    parser.add_argument("--execute", action="store_true", help="Actually download the media")
    parser.add_argument("--force", action="store_true", help="Re-download even if already present")
    parser.add_argument("--add-to-clip-windows", action="store_true", help="Append a candidate row to data/clip_windows.csv")
    parser.add_argument("--verbose", action="store_true", help="Show detailed debug output")
    return parser.parse_args(argv)


def append_clip_window_row(match_name: str, match_slug: str, output_path: str) -> None:
    clip_id = f"{match_slug}_livetv_import"

    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", output_path],
            capture_output=True, text=True, timeout=30,
        )
        duration_seconds = int(float(result.stdout.strip())) if result.returncode == 0 else 0
    except Exception:
        duration_seconds = 0

    new_row = {
        "clip_id": clip_id,
        "match_id": match_slug,
        "moment_id": "",
        "clip_type": "highlight_import",
        "start_time": "00:00:00",
        "end_time": f"{duration_seconds // 60:02d}:{duration_seconds % 60:02d}:00" if duration_seconds else "",
        "duration_seconds": str(duration_seconds) if duration_seconds else "",
        "series": "LiveTV Import",
        "hook": match_name,
        "caption": f"Imported from LiveTV showvideo — {match_name}",
        "status": "candidate",
    }

    existing: dict[str, dict[str, str]] = {}
    fieldnames = list(CLIP_WINDOWS_FIELDS)

    if CLIP_WINDOWS_CSV.exists():
        with CLIP_WINDOWS_CSV.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            if reader.fieldnames:
                fieldnames = list(reader.fieldnames)
            for row in reader:
                existing[row.get("clip_id", "")] = row

    if clip_id in existing:
        print(f"  clip_windows.csv: row {clip_id} already exists (use --force to overwrite)")
        return

    existing[clip_id] = new_row
    for fn in new_row:
        if fn not in fieldnames:
            fieldnames.append(fn)

    with CLIP_WINDOWS_CSV.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing.values())

    print(f"  clip_windows.csv: added row {clip_id}")


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)

    if not args.dry_run and not args.execute:
        print("Error: Use --dry-run to preview, or --execute to download.", file=sys.stderr)
        sys.exit(1)

    result = resolve_and_download(
        showvideo_url=args.url,
        match_name=args.match_name,
        league=args.league,
        output_root=args.output_root,
        dry_run=args.dry_run,
        force=args.force,
        verbose=args.verbose,
    )

    print()
    print(f"Source:      {result['source_page_url']}")
    print(f"Match:       {result['match_name']} ({result['league']})")
    print(f"Slug:        {result['match_slug']}")

    for step in result.get("steps", []):
        s = step.get("step", "")
        st = step.get("status", "")
        detail = step.get("detail") or step.get("method") or ""
        if s == "fetch":
            print(f"Fetch:       {st} ({detail})" if detail else f"Fetch:       {st}")
        elif s == "iframe":
            print(f"Iframe:      {step.get('url', '')}")
        elif s == "discover":
            print(f"Discovery:   {step.get('candidates_found', 0)} candidate(s)")
        elif s == "select":
            print(f"Selected:    {step.get('url', '')}")
        elif s == "validate":
            v = step.get("detail", {})
            ct = v.get("content_type", "?") if isinstance(v, dict) else "?"
            print(f"Validation:  {st} ({ct})")
        elif s == "dedup":
            print(f"Dedup:       {detail}")
        elif s == "download":
            print(f"Download:    {st}")

    if result.get("error"):
        print(f"Error:       {result['error']}")
        sys.exit(1)

    if result.get("dry_run"):
        print()
        print("[dry-run] No files were downloaded.")
        print(f"[dry-run] Would write to: {ROOT / 'FootballArchive' / 'RAW_HIGHLIGHTS' / result['match_slug']}")
        return

    if result.get("skipped"):
        print(f"Skipped:     file already at {result.get('existing_sidecar', {}).get('output_filename', '?')}")
        return

    output_path = result.get("output_path", "")
    sidecar = result.get("sidecar", {})
    print(f"Output:      {output_path}")
    print(f"Size:        {sidecar.get('downloaded_bytes', 0)} bytes")
    print(f"Type:        {sidecar.get('content_type', '?')}")

    if args.add_to_clip_windows and output_path:
        append_clip_window_row(args.match_name, result["match_slug"], output_path)


if __name__ == "__main__":
    main()
