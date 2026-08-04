from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path
import subprocess
from pipeline.config import get_clip_mode
from pipeline.configurator import resolve_export_destination, resolve_platform_export_profile
from pipeline.config_errors import ConfigurationError
from pipeline.utils import ROOT, get_video_duration, seconds_to_timestamp, timestamp_to_seconds


def _micro_slice(start: str, end: str, max_seconds: float) -> tuple[str, str]:
    """Return (slice_start, slice_end) centered within the source window."""
    duration = timestamp_to_seconds(end) - timestamp_to_seconds(start)
    if duration <= max_seconds:
        return start, end
    half_excess = (duration - max_seconds) / 2
    slice_start = timestamp_to_seconds(start) + half_excess
    slice_end = slice_start + max_seconds
    return seconds_to_timestamp(slice_start), seconds_to_timestamp(slice_end)


def _validate_and_clamp(start: str, end: str, video_duration: float) -> tuple[str, str] | None:
    s = timestamp_to_seconds(start)
    e = timestamp_to_seconds(end)
    if s >= video_duration:
        return None
    if e > video_duration:
        e = video_duration
    return seconds_to_timestamp(s), seconds_to_timestamp(e)


def export_clip(source, start, end, output, profile=None):
    duration = max(0.1, timestamp_to_seconds(end) - timestamp_to_seconds(start))
    output.parent.mkdir(parents=True, exist_ok=True)

    if profile is None:
        profile = resolve_platform_export_profile("TIKTOK")
    width = profile["width"]
    height = profile["height"]
    vf = f"scale={width}:{height}:force_original_aspect_ratio=increase,crop={width}:{height}"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-vf", vf,
        "-r", str(profile["frame_rate"]),
        "-c:v", profile["video_codec"],
        "-preset", profile["preset"],
        "-crf", profile["crf"],
        "-c:a", profile["audio_codec"],
        "-b:a", profile["audio_bitrate"],
        str(output),
    ]
    subprocess.run(cmd, check=True)

def main():
    parser = argparse.ArgumentParser(description="Export rough vertical clips from a manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-video", required=True)
    parser.add_argument("--platform", default="TIKTOK", choices=["TIKTOK", "REELS", "SHORTS"])
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--mode", default="story", choices=("story", "micro"),
                        help="Clip mode (default: story)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing.")
    args = parser.parse_args()

    export_profile = resolve_platform_export_profile(args.platform)

    max_seconds = get_clip_mode(args.mode)["max_seconds"] if args.mode == "micro" else None

    source = Path(args.source_video)

    video_duration = None
    if args.mode == "micro":
        video_duration = get_video_duration(source)

    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.limit:
        rows = rows[:args.limit]

    for row in rows:
        clip_id = row["clip_id"]
        category = str(row.get("category", "UNSORTED")).upper()
        start = row["start_time"]
        end = row["end_time"]

        if args.mode == "micro":
            clamped = _validate_and_clamp(start, end, video_duration)
            if clamped is None:
                print(f"[skip] {clip_id}: start_time {start} >= video duration {video_duration:.1f}s")
                continue
            clamped_start, clamped_end = clamped
            if timestamp_to_seconds(end) > video_duration:
                print(f"[clamp] {clip_id}: end_time {end} > video duration {video_duration:.1f}s")
            start, end = _micro_slice(clamped_start, clamped_end, max_seconds)

        output = resolve_export_destination(
            profile=export_profile,
            platform=args.platform,
            clip_id=clip_id,
            category=category,
            root=ROOT,
        )
        if not args.dry_run:
            print(f"Exporting {clip_id} -> {output}")
            export_clip(source, start, end, output, profile=export_profile)
        else:
            print(f"[dry-run] Would export: {clip_id} -> {output}")

    print("Exports complete.")

if __name__ == "__main__":
    try:
        main()
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
