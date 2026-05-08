import argparse
import csv
import json
from pathlib import Path
from pipeline.utils import ROOT, slugify

FIELDNAMES = [
    "clip_id", "league", "match_name", "source_video", "category",
    "start_time", "end_time", "virality_score", "retention_reason",
    "share_reason", "hook_text", "caption", "thumbnail_idea",
    "manual_scrub_note", "tiktok_note", "reels_note", "shorts_note",
    "status", "editor_notes", "export_tiktok", "export_reels", "export_shorts",
]

def main():
    parser = argparse.ArgumentParser(description="Convert GPT JSON analysis into clip manifest CSV.")
    parser.add_argument("--analysis", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--source-video", default="")
    args = parser.parse_args()

    data = json.loads(Path(args.analysis).read_text(encoding="utf-8"))
    if isinstance(data, dict) and "clips" in data:
        data = data["clips"]

    match_slug = slugify(args.match_name)
    out_file = ROOT / "CLIP_MANIFESTS" / f"{match_slug}_manifest.csv"
    out_file.parent.mkdir(parents=True, exist_ok=True)

    rows = []
    for i, clip in enumerate(data, start=1):
        notes = clip.get("platform_notes", {}) or {}
        rows.append({
            "clip_id": f"{args.league}_{match_slug}_{i:03d}",
            "league": args.league,
            "match_name": args.match_name,
            "source_video": args.source_video,
            "category": str(clip.get("category", "UNSORTED")).upper(),
            "start_time": clip.get("start_time", ""),
            "end_time": clip.get("end_time", ""),
            "virality_score": clip.get("virality_score", ""),
            "retention_reason": clip.get("retention_reason", ""),
            "share_reason": clip.get("share_reason", ""),
            "hook_text": clip.get("hook_text", ""),
            "caption": clip.get("caption", ""),
            "thumbnail_idea": clip.get("thumbnail_idea", ""),
            "manual_scrub_note": clip.get("manual_scrub_note", ""),
            "tiktok_note": notes.get("tiktok", ""),
            "reels_note": notes.get("reels", ""),
            "shorts_note": notes.get("shorts", ""),
            "status": "needs_visual_scrub",
            "editor_notes": "",
            "export_tiktok": "",
            "export_reels": "",
            "export_shorts": "",
        })

    with out_file.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Manifest written: {out_file}")

if __name__ == "__main__":
    main()
