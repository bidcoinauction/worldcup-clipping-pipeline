import argparse
import csv
import json
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.utils import ROOT, slugify
from pipeline.stadium_signal import archive_root, archive_path


SCHEDULE = ROOT / "data" / "worldcup_2026_schedule.csv"

STATUS_ORDER = [
    "planned", "recording", "recorded",
    "transcribed", "detected", "exported", "posted",
]

RESEARCH_TEMPLATE = {
    "match": {"home_team": "", "away_team": "", "competition": "FIFA World Cup 2026", "date": ""},
    "events": [],
}


def _read_schedule():
    with SCHEDULE.open("r", encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def _write_schedule(rows):
    with SCHEDULE.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)


def _find_match(rows, match_no):
    for r in rows:
        if int(r["match_no"]) == match_no:
            return r
    return None


def _resolve_video(match):
    path = match.get("source_video_path", "").strip()
    if path:
        p = Path(path)
        if p.exists():
            return str(p.resolve())
        raw = archive_path("RAW", "WORLD_CUP", Path(path).name)
        if Path(raw).exists():
            return raw

    match_id = match["match_id"]
    for ext in (".mp4", ".ts", ".mkv", ".avi", ".mov"):
        candidates = [
            Path(archive_path("RAW", "WORLD_CUP", f"{match_id}{ext}")),
            Path(archive_path("RAW", f"{match_id}{ext}")),
            ROOT / "RAW" / "WORLD_CUP" / f"{match_id}{ext}",
            ROOT / "RAW" / f"{match_id}{ext}",
        ]
        for c in candidates:
            if c.exists():
                return str(c.resolve())

    stream = match.get("stream_url", "").strip()
    if stream:
        return stream

    return None


def _scaffold_research(match, dry_run):
    league = "WORLD_CUP"
    match_slug = match["match_id"]
    out_path = ROOT / "MATCH_RESEARCH" / league / match_slug / "match_research.json"

    if out_path.exists():
        return out_path

    data = RESEARCH_TEMPLATE.copy()
    data["match"] = {
        "home_team": match["home"],
        "away_team": match["away"],
        "competition": "FIFA World Cup 2026",
        "date": match["date"],
    }

    if dry_run:
        print(f"  [dry-run] Would create: {out_path}")
        return out_path

    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"  Created: {out_path}")
    return out_path


def _run(cmd, dry_run, step_name):
    label = " ".join(str(x) for x in cmd)
    if dry_run:
        print(f"  [dry-run] Would run: {label}")
        return True

    print(f"  Running: {label}")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as e:
        print(f"  FAILED: {step_name} (exit {e.returncode})")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Process a scheduled World Cup 2026 match through the clipping pipeline."
    )
    parser.add_argument("--match-no", type=int, required=True, help="Match number from schedule")
    parser.add_argument("--mode", default=None, choices=("story", "micro", "package"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--run-detection", action="store_true",
                        help="Run clip detection after transcription")
    parser.add_argument("--no-condense", action="store_true",
                        help="Disable transcript condensing in package mode")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing")
    args = parser.parse_args()

    rows = _read_schedule()
    match = _find_match(rows, args.match_no)
    if not match:
        print(f"Error: match {args.match_no} not found in schedule")
        raise SystemExit(1)

    match_slug = match["match_id"]
    home = match["home"]
    away = match["away"]
    match_name = f"{home} vs {away}"
    league = "WORLD_CUP"
    status = match.get("pipeline_status", "planned").strip()

    print(f"Match {args.match_no}: {match_name} ({match['date']})")
    print(f"  match_id: {match_slug}")
    print(f"  status: {status}")

    # --- Resolve source video ---
    video_path = _resolve_video(match)
    if not video_path:
        expected = archive_path("RAW", "WORLD_CUP")
        print(f"  source: NOT FOUND — expected at {expected}/{match_slug}.*")
        print(f"  Set source_video_path in CSV or place the file in that directory.")
        if not args.dry_run:
            print("Error: source video required")
            raise SystemExit(1)
    else:
        print(f"  source: {video_path}")

    input_arg = video_path or f"<path_to_{match_slug}.mp4>"

    if video_path and not Path(video_path).exists() and not video_path.startswith("http") and not args.dry_run:
        print("Error: source video file not found")
        raise SystemExit(1)

    # --- Scaffold research ---
    research_path = _scaffold_research(match, args.dry_run)
    has_research = research_path.exists() if not args.dry_run else False

    # --- Build and run process_match.py command ---
    cmd = [
        sys.executable or "python", "scripts/process_match.py",
        "--input", input_arg,
        "--league", league,
        "--match-name", match_name,
    ]
    if args.mode:
        cmd += ["--mode", args.mode]
    if args.run_detection:
        cmd += ["--run-detection"]
    if args.no_condense:
        cmd += ["--no-condense"]
    if has_research and research_path:
        cmd += ["--research", str(research_path)]

    ok = _run(cmd, args.dry_run, "process_match")

    # --- Update pipeline status ---
    if ok and not args.dry_run:
        new_status = "recorded"
        if args.run_detection:
            new_status = "detected"
        else:
            new_status = "transcribed"

        current_idx = STATUS_ORDER.index(status) if status in STATUS_ORDER else -1
        new_idx = STATUS_ORDER.index(new_status)
        if new_idx > current_idx:
            match["pipeline_status"] = new_status
            _write_schedule(rows)
            print(f"  pipeline_status: {status} → {new_status}")

    print(f"Done: match {args.match_no}")


if __name__ == "__main__":
    main()
