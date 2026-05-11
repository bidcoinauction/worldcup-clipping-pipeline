import argparse
import json
import shutil
import subprocess
from pathlib import Path
from pipeline.utils import ROOT, ffmpeg_executable, seconds_to_timestamp, slugify


def run(cmd: list[str]) -> None:
    subprocess.run(cmd, check=True)


def ocr_frame(frame_path: Path) -> str:
    tesseract = shutil.which("tesseract")
    if not tesseract:
        return ""
    result = subprocess.run(
        [tesseract, str(frame_path), "stdout", "--psm", "6"],
        check=False,
        capture_output=True,
        text=True,
    )
    return " ".join(result.stdout.split())


def ffmpeg_crop(crop: str) -> str:
    parts = crop.split(":")
    if len(parts) != 4:
        raise SystemExit("--crop must be x:y:w:h")
    x, y, width, height = parts
    return f"{width}:{height}:{x}:{y}"


def main():
    parser = argparse.ArgumentParser(description="Sample scoreboard-region frames and optionally OCR them if tesseract exists.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--match-slug", default="")
    parser.add_argument("--interval-seconds", type=int, default=15)
    parser.add_argument("--duration-seconds", type=int, default=0)
    parser.add_argument("--crop", default="0:0:620:140", help="x:y:w:h scoreboard crop in source pixels")
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    source = Path(args.input)
    match_slug = args.match_slug or slugify(source.stem)
    out_dir = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "scoreboard_samples"
    out_dir.mkdir(parents=True, exist_ok=True)
    for stale_frame in out_dir.glob("scoreboard_*.jpg"):
        stale_frame.unlink()

    vf = f"crop={ffmpeg_crop(args.crop)},scale=1240:-2"
    frame_pattern = out_dir / "scoreboard_%04d.jpg"
    cmd = [
        ffmpeg_executable(),
        "-y",
        "-i",
        str(source),
        "-vf",
        f"fps=1/{args.interval_seconds},{vf}",
    ]
    if args.duration_seconds:
        cmd.extend(["-t", str(args.duration_seconds)])
    cmd.append(str(frame_pattern))
    run(cmd)

    samples = []
    for index, frame in enumerate(sorted(out_dir.glob("scoreboard_*.jpg"))):
        timestamp = index * args.interval_seconds
        samples.append({
            "type": "scoreboard_sample",
            "frame": str(frame),
            "timestamp": timestamp,
            "timestamp_text": seconds_to_timestamp(timestamp),
            "ocr_text": ocr_frame(frame),
        })

    output = Path(args.output) if args.output else ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "scoreboard_samples.json"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "source": str(source),
        "crop": args.crop,
        "interval_seconds": args.interval_seconds,
        "tesseract_available": bool(shutil.which("tesseract")),
        "samples": samples,
    }, indent=2), encoding="utf-8")
    print(f"Scoreboard samples written: {output}")
    print(f"Sample frames: {len(samples)}")


if __name__ == "__main__":
    main()
