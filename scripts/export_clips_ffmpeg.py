import argparse
import csv
from pathlib import Path
import subprocess
from pipeline.config import get_clip_mode
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


def export_clip(source, start, end, output):
    duration = max(0.1, timestamp_to_seconds(end) - timestamp_to_seconds(start))
    output.parent.mkdir(parents=True, exist_ok=True)

    vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"

    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(source),
        "-t", str(duration),
        "-vf", vf,
        "-r", "30",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "192k",
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

        output = ROOT / "EXPORTS" / args.platform / category / f"{clip_id}_{args.platform.lower()}.mp4"
        if not args.dry_run:
            print(f"Exporting {clip_id} -> {output}")
            export_clip(source, start, end, output)
        else:
            print(f"[dry-run] Would export: {clip_id} -> {output}")

    print("Exports complete.")

if __name__ == "__main__":
    main()
