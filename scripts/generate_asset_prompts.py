import argparse
import csv
from pathlib import Path
from pipeline.config import get_path
from pipeline.utils import ROOT

_THUMB_TEMPLATE_PATH = ROOT / get_path("thumbnail_template")
THUMB_TEMPLATE = _THUMB_TEMPLATE_PATH.read_text(encoding="utf-8")

def main():
    parser = argparse.ArgumentParser(description="Generate thumbnail and caption prompt files from manifest.")
    parser.add_argument("--manifest", required=True)
    args = parser.parse_args()

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
        caption = f"{row.get('hook_text','')}\n\n{row.get('caption','')}\n\n#worldcup #football #soccer"
        (cap_dir / f"{clip_id}_caption.txt").write_text(caption, encoding="utf-8")

    print("Asset prompts generated.")

if __name__ == "__main__":
    main()
