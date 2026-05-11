import argparse
import csv
import os
from pathlib import Path
import subprocess
import tempfile
import textwrap
from pipeline.utils import ROOT, ffmpeg_executable, timestamp_to_seconds

FONT_FILE = "/System/Library/Fonts/SFNS.ttf"

def wrap_overlay_text(text: str, width: int = 24) -> str:
    return "\n".join(textwrap.wrap(str(text).strip(), width=width)) if text else ""

def write_temp_text(text: str) -> str:
    handle = tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".txt", delete=False)
    with handle:
        handle.write(text)
    return handle.name

def text_overlay_filters(hook_file: str, caption_file: str) -> str:
    filters = []
    if hook_file:
        filters.append(
            "drawtext=fontfile='{}':textfile='{}':fontcolor=white:fontsize=70:"
            "line_spacing=10:box=1:boxcolor=black@0.58:boxborderw=28:"
            "x=(w-text_w)/2:y=92".format(FONT_FILE, hook_file)
        )
    if caption_file:
        filters.append(
            "drawtext=fontfile='{}':textfile='{}':fontcolor=white:fontsize=44:"
            "line_spacing=8:box=1:boxcolor=black@0.48:boxborderw=22:"
            "x=(w-text_w)/2:y=h-text_h-120".format(FONT_FILE, caption_file)
        )
    return ",".join(filters)

def video_filter(layout: str, hook_file: str = "", caption_file: str = "") -> str:
    overlays = text_overlay_filters(hook_file, caption_file)

    if layout == "center_crop":
        vf = "scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920"
        return f"{vf},{overlays}" if overlays else vf

    if layout == "fit_blur":
        vf = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=24:2,eq=brightness=-0.08:saturation=0.85[bg];"
            "[0:v]scale=1080:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v]"
        )
        return f"{vf};[v]{overlays}" if overlays else f"{vf};[v]null"

    raise ValueError(f"Unknown layout: {layout}")

def choose_hybrid_layout(row: dict) -> str:
    category = str(row.get("category", "")).upper()
    start = timestamp_to_seconds(row.get("start_time", 0))
    note = f"{row.get('manual_scrub_note', '')} {row.get('thumbnail_idea', '')}".lower()

    punch_in_clues = ["crowd", "stadium", "atmosphere", "closeup", "hero shot", "players entering"]
    if start < 15 or category in {"EMOTION", "AMERICA"}:
        return "center_crop"
    if any(clue in note for clue in punch_in_clues) and category != "CHAOS":
        return "center_crop"
    return "fit_blur"

def export_clip(source, start, end, output, layout, hook_text="", caption="", burn_text=False):
    duration = max(0.1, timestamp_to_seconds(end) - timestamp_to_seconds(start))
    output.parent.mkdir(parents=True, exist_ok=True)

    hook_file = write_temp_text(wrap_overlay_text(hook_text, width=22)) if burn_text and hook_text else ""
    caption_file = write_temp_text(wrap_overlay_text(caption, width=30)) if burn_text and caption else ""
    try:
        cmd = [
            ffmpeg_executable(), "-y",
            "-ss", str(start),
            "-i", str(source),
            "-t", str(duration),
            "-filter_complex", video_filter(layout, hook_file, caption_file),
            "-r", "30",
            "-c:v", "libx264",
            "-preset", "veryfast",
            "-crf", "20",
            "-c:a", "aac",
            "-b:a", "192k",
            str(output),
        ]
        subprocess.run(cmd, check=True)
    finally:
        for text_file in [hook_file, caption_file]:
            if text_file:
                os.unlink(text_file)

def main():
    parser = argparse.ArgumentParser(description="Export rough vertical clips from a manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--source-video", required=True)
    parser.add_argument("--platform", default="TIKTOK", choices=["TIKTOK", "REELS", "SHORTS"])
    parser.add_argument("--layout", default="fit_blur", choices=["fit_blur", "center_crop", "hybrid"])
    parser.add_argument("--output-set", default="", help="Optional export folder name, e.g. TIKTOK_HYBRID")
    parser.add_argument("--burn-text", action="store_true", help="Burn hook/caption text into exports")
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
        output_set = args.output_set or args.platform
        output = ROOT / "EXPORTS" / output_set / category / f"{clip_id}_{output_set.lower()}.mp4"
        clip_layout = choose_hybrid_layout(row) if args.layout == "hybrid" else args.layout
        print(f"Exporting {clip_id} ({clip_layout}) -> {output}")
        export_clip(
            source,
            row["start_time"],
            row["end_time"],
            output,
            clip_layout,
            row.get("hook_text", ""),
            row.get("caption", ""),
            args.burn_text,
        )

    print("Exports complete.")

if __name__ == "__main__":
    main()
