import argparse
import csv
from pathlib import Path
import subprocess
from pipeline.utils import ROOT, timestamp_to_seconds

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
    args = parser.parse_args()

    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if args.limit:
        rows = rows[:args.limit]

    source = Path(args.source_video)

    for row in rows:
        clip_id = row["clip_id"]
        category = str(row.get("category", "UNSORTED")).upper()
        output = ROOT / "EXPORTS" / args.platform / category / f"{clip_id}_{args.platform.lower()}.mp4"
        print(f"Exporting {clip_id} -> {output}")
        export_clip(source, row["start_time"], row["end_time"], output)

    print("Exports complete.")

if __name__ == "__main__":
    main()
