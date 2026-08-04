import argparse
import csv
import sys
from pathlib import Path
from pipeline.config import get_path
from pipeline.config_errors import ConfigurationError
from pipeline.configurator import resolve_brand_hashtags
from pipeline.utils import ROOT

_THUMB_TEMPLATE_PATH = ROOT / get_path("thumbnail_template")
THUMB_TEMPLATE = _THUMB_TEMPLATE_PATH.read_text(encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Generate thumbnail and caption prompt files from manifest.")
    parser.add_argument("--manifest", required=True)
    parser.add_argument(
        "--brand",
        default="world_cup",
        help="Brand profile whose hashtags are appended to captions (default: world_cup)",
    )
    args = parser.parse_args()

    try:
        hashtags = " ".join(resolve_brand_hashtags(args.brand))
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)

    with Path(args.manifest).open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        clip_id = row["clip_id"]
        angle = row.get("category", "")
        moment = row.get("thumbnail_idea", "") or row.get("manual_scrub_note", "")

        thumb_prompt = THUMB_TEMPLATE.format(angle=angle, moment_description=moment)
        out_dir = ROOT / "THUMBNAILS" / str(angle).upper()
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / f"{clip_id}_thumbnail_prompt.txt").write_text(thumb_prompt, encoding="utf-8")

        cap_dir = ROOT / "CAPTIONS" / str(angle).upper()
        cap_dir.mkdir(parents=True, exist_ok=True)
        caption = f"{row.get('hook_text','')}\n\n{row.get('caption','')}\n\n{hashtags}"
        (cap_dir / f"{clip_id}_caption.txt").write_text(caption, encoding="utf-8")

    print("Asset prompts generated.")

if __name__ == "__main__":
    main()