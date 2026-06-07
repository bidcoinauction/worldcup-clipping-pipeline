import argparse
import json
from pathlib import Path
from pipeline.utils import ROOT, get_video_duration, slugify

EVENT_TYPE_LABELS = {
    "goal": "GOAL",
    "penalty": "PENALTY",
    "penalty_miss": "PENALTY MISS",
    "penalty_save": "PENALTY SAVE",
    "yellow_card": "YELLOW CARD",
    "red_card": "RED CARD",
    "substitution": "SUBSTITUTION",
    "injury": "INJURY",
    "var_review": "VAR REVIEW",
    "trophy_lift": "TROPHY LIFT",
    "shootout_goal": "SHOOTOUT GOAL",
    "shootout_miss": "SHOOTOUT MISS",
    "shootout_save": "SHOOTOUT SAVE",
    "celebration": "CELEBRATION",
    "controversy": "CONTROVERSY",
    "half_time": "HALF TIME",
    "full_time": "FULL TIME",
}


def format_event(ev: dict, idx: int, total: int) -> str:
    minute = ev.get("minute_raw", "")
    ev_type = ev.get("type", "").lower()
    label = EVENT_TYPE_LABELS.get(ev_type, ev_type.upper())
    desc = ev.get("description", "")
    player = ev.get("player", "")
    suffix = f" ({player})" if player else ""
    current = ev.get("video_time_seconds")
    current_str = f"{current}s" if current is not None else "(not set)"
    return (
        f"Event {idx}/{total}: [{minute}' {label}] {desc}{suffix}\n"
        f"  Current: {current_str}"
    )


def _load_transcript_context(transcript_path: Path | None) -> str:
    if transcript_path is None:
        return ""
    ts_path = transcript_path.with_name("timestamps.json")
    if not ts_path.exists():
        return ""
    segments = json.loads(ts_path.read_text(encoding="utf-8"))
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "")
        lines.append(f"  [{start:.0f}s-{end:.0f}s] {text}")
    return "\n".join(lines)


def _find_nearby_transcript(video_sec: float, transcript_context: str) -> str:
    if not transcript_context:
        return ""
    nearby = []
    for line in transcript_context.split("\n"):
        try:
            bracket = line.strip().split("]")[0].lstrip("[")
            parts = bracket.split("s-")
            start = float(parts[0])
            end = float(parts[1].rstrip("s"))
            if abs(start - video_sec) < 10 or (start <= video_sec <= end):
                nearby.append(line.strip())
        except (ValueError, IndexError):
            continue
    if nearby:
        return "  Transcript nearby:\n" + "\n".join(nearby[:3])
    return ""


def main():
    parser = argparse.ArgumentParser(
        description="Interactively map match events to video timestamps."
    )
    parser.add_argument("--research", required=True,
                        help="Path to match_research.json")
    parser.add_argument("--source-video", required=True,
                        help="Path to the source video file")
    parser.add_argument("--transcript", default=None,
                        help="Path to transcript.txt (for context)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without modifying file.")
    parser.add_argument("--force", action="store_true",
                        help="Re-prompt events that already have video_time_seconds.")
    args = parser.parse_args()

    research_path = Path(args.research)
    if not research_path.exists():
        print(f"Research file not found: {research_path}")
        return

    source_path = Path(args.source_video)
    if not source_path.exists():
        print(f"Source video not found: {source_path}")
        return

    duration = get_video_duration(source_path)
    print(f"Video duration: {duration:.1f}s\n")

    data = json.loads(research_path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        print("No events found in research file.")
        return

    transcript_context = (
        _load_transcript_context(Path(args.transcript))
        if args.transcript else ""
    )

    modified = False
    for i, ev in enumerate(events, start=1):
        has_value = ev.get("video_time_seconds") is not None
        if has_value and not args.force:
            continue

        print(format_event(ev, i, len(events)))

        if has_value and args.force:
            hint = f" [current: {ev['video_time_seconds']}s]"

        raw = input("  Enter video_time_seconds (or press Enter to skip): ").strip()
        if raw == "":
            print("  Skipped.\n")
            continue

        try:
            val = int(raw)
        except ValueError:
            print(f"  Invalid: '{raw}' is not an integer. Skipped.\n")
            continue

        if val < 0:
            print(f"  Invalid: {val}s is negative. Skipped.\n")
            continue

        if val >= duration:
            print(f"  Invalid: {val}s >= video duration {duration:.1f}s. Skipped.\n")
            continue

        nearby = _find_nearby_transcript(float(val), transcript_context)
        if nearby:
            print(nearby)

        ev["video_time_seconds"] = val
        modified = True
        print(f"  Set to {val}s.\n")

    if not modified:
        if all(ev.get("video_time_seconds") is not None for ev in events):
            print("All events already have video_time_seconds. Use --force to re-prompt.")
        else:
            print("No changes made.")
        return

    if args.dry_run:
        print("[dry-run] Would write updated match_research.json")
        return

    research_path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    print(f"Updated: {research_path}")


if __name__ == "__main__":
    main()
