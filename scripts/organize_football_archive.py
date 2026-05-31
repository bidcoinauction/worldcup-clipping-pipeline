import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import MATCH_FIELDS, ROOT, archive_root, read_csv, write_csv
from pipeline.utils import slugify


VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4v"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Organize raw football videos into MATCHES/<competition>/ and update matches.csv.")
    parser.add_argument("--source-dir", default=Path(archive_root()) / "RAW")
    parser.add_argument("--competition", default="WORLD_CUP")
    parser.add_argument("--move", action="store_true", help="Move files instead of copying them.")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    source_dir = Path(args.source_dir)
    files = [path for path in source_dir.glob("*") if path.suffix.lower() in VIDEO_EXTENSIONS]
    destination_dir = ROOT / "MATCHES" / args.competition
    destination_dir.mkdir(parents=True, exist_ok=True)

    rows = read_csv(ROOT / "data/matches.csv")
    by_path = {row.get("local_path"): row for row in rows}
    new_rows = []

    for path in files:
        match_id = slugify(path.stem)
        destination = destination_dir / path.name
        print(f"{'Move' if args.move else 'Copy'} {path} -> {destination}")
        if not args.dry_run:
            if args.move:
                shutil.move(str(path), destination)
            else:
                shutil.copy2(path, destination)
        local_path = str(destination.relative_to(ROOT))
        if local_path not in by_path:
            new_rows.append(
                {
                    "match_id": match_id,
                    "title": path.stem.replace("_", " "),
                    "date": "",
                    "competition": args.competition,
                    "stage": "",
                    "venue": "",
                    "teams": "",
                    "primary_emotion": "",
                    "secondary_emotions": "",
                    "mythology_score": "",
                    "status": "organized",
                    "source_url": "",
                    "local_path": local_path,
                    "notes": "Registered from local source file.",
                }
            )

    if not args.dry_run and new_rows:
        write_csv(ROOT / "data/matches.csv", rows + new_rows, MATCH_FIELDS)
    print(f"Organized {len(files)} files; registered {len(new_rows)} new matches.")


if __name__ == "__main__":
    main()
