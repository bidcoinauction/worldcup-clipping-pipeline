import argparse
import csv
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import ROOT


def build_vertical_blur_filter(width: int, height: int) -> str:
    return (
        "[0:v]split=2[bg][fg];"
        f"[bg]scale={width}:{height}:force_original_aspect_ratio=increase,"
        f"crop={width}:{height},gblur=sigma=28,eq=brightness=-0.08:saturation=1.12[bg];"
        f"[fg]scale={width}:{height}:force_original_aspect_ratio=decrease[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,format=yuv420p"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Create vertical blurred-background copies for clip windows.")
    parser.add_argument("--match-id", default="", help="Limit to one match id.")
    parser.add_argument("--width", type=int, default=720, help="Output width. Defaults to 720.")
    parser.add_argument("--height", type=int, default=1280, help="Output height. Defaults to 1280.")
    parser.add_argument("--overwrite", action="store_true")
    parser.add_argument("--execute", action="store_true", help="Run FFmpeg. Without this, only print planned outputs.")
    args = parser.parse_args()

    rows = list(csv.DictReader((ROOT / "data/clip_windows.csv").open(newline="", encoding="utf-8")))
    if args.match_id:
        rows = [row for row in rows if row.get("match_id") == args.match_id]

    out_dir = ROOT / "FootballArchive/CLIPS/VERTICAL_BLUR"
    out_dir.mkdir(parents=True, exist_ok=True)

    for row in rows:
        clip_id = row["clip_id"]
        source = find_source_clip(clip_id)
        if source is None:
            print(f"Missing source clip: {clip_id}")
            continue

        output = out_dir / f"{clip_id}_vertical_blur.mp4"
        if output.exists() and not args.overwrite:
            print(f"Skipping existing vertical clip: {output}")
            continue

        cmd = [
            "ffmpeg",
            "-y" if args.overwrite else "-n",
            "-i",
            str(source),
            "-vf",
            build_vertical_blur_filter(args.width, args.height),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-crf",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "160k",
            "-movflags",
            "+faststart",
            str(output),
        ]

        print(f"{source} -> {output}")
        if args.execute:
            subprocess.run(cmd, check=True)


def find_source_clip(clip_id: str) -> Path | None:
    candidates = [
        ROOT / "FootballArchive/CLIPS/PLAYABLE" / f"{clip_id}_playable.mp4",
        ROOT / "FootballArchive/CLIPS" / f"{clip_id}.mp4",
    ]
    return next((path for path in candidates if path.exists()), None)


if __name__ == "__main__":
    main()
