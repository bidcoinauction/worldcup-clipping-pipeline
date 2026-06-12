import sys
import argparse
import json
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.config import get_clip_mode, get_default_clip_mode, load_config
from pipeline.condense_transcript import condense_timestamps
from pipeline.utils import ROOT, slugify

def _build_rules_block(mode: str, duration_seconds: int) -> str:
    cfg = get_clip_mode(mode)
    min_s = cfg["min_seconds"]
    max_s = cfg["max_seconds"]
    min_c = cfg["min_clips"]
    max_c = cfg["max_clips"]

    common = f"""- Favor emotional payoff over play-by-play.
- Avoid generic sports commentary.
- Think like a cinematic storyteller.
- Hook must be understandable to Americans who are still learning global football.
- Return JSON array only. No markdown. No explanation.
- Every start_time and end_time must be in the range [0, {duration_seconds}] in whole seconds (SS format). Do not use HH:MM:SS.
- Do not invent moments, VAR reviews, goals, cards, or reactions not present in the transcript.
- If the transcript mentions rules, VAR, penalties, goals, cards, or officials as explanation/history, do not turn that into a live event.
- Only label a goal, VAR review, card, save, miss, or celebration if the transcript explicitly says it happened in the footage."""

    if mode == "story":
        return f"""Rules:
{common}
- Keep clip length between {min_s} and {max_s} seconds.
- A clip is not one transcript segment. Group adjacent transcript segments into one coherent {min_s}-{max_s} second clip.
- Start time should be the first relevant segment. End time should be {min_s}-{max_s} seconds later, covering the full moment.
- Return exactly {min_c}-{max_c} clips."""

    if mode == "micro":
        return f"""Rules:
{common}
- This mode does NOT guarantee copyright safety. It reduces risk by producing shorter clips that require heavier transformation.
- Return {min_c}-{max_c} source windows.
- Each source window should be 8-25 seconds, covering a full micro-moment with enough context.
- The exporter will cut one or more {min_s}-{max_s} second pieces from inside each source window.
- Do not trim the window — keep enough context for the exporter to find the peak moment.
- Focus on facial reactions, crowd emotion, ball contact, referee gesture, celebration flash, or commentary spike.
- Avoid full highlight sequences."""

    if mode == "package":
        return f"""Rules:
{common}
- Return {min_c}-{max_c} clips in narrative sequence order (001, 002, ..., N).
- Clips should range from {min_s} to {max_s} seconds.
- Group adjacent transcript segments into coherent clips.
- You MUST include at least one crowd reaction clip.
- You MUST include at least one manager/bench reaction clip.
- Cover all major match events (goals, cards, penalties, saves) that appear in the transcript.
- Distribute clips across narrative acts: setup (opening tension), pressure (rising stakes), rupture (decisive moments), aftermath (reactions, consequences).
- Assign each clip a narrative_role from: "setup", "tension_builder", "climax", "reaction", "aftermath"."""

    msg = f"Unknown clip mode: {mode}"
    raise ValueError(msg)


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

GOAL_STORY = """Train TikTok, Reels, and Shorts before the World Cup wave hits. We are not chasing generic highlights. We are identifying emotionally engaging, cinematic, mythological football moments."""

GOAL_MICRO = """Train TikTok, Reels, and Shorts before the World Cup wave hits. We are identifying micro-moments: the fragments within football that can be transformed, reused, and compiled into new narratives without relying on long broadcast sequences."""

GOAL_PACKAGE = """Build a complete story package for this match. We need 8-15 clips that together tell the full emotional narrative — from the build-up to the aftermath. Each clip has a narrative role in the larger story."""


def _build_research_block(research_path: Path | None) -> str:
    if research_path is None or not research_path.exists():
        return ""
    data = json.loads(research_path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        return ""
    header = (
        "Match events (research anchors -- use them to look for nearby moments, "
        "but only generate clips supported by the transcript/timestamps):"
    )
    lines = [header]
    for ev in events:
        minute = ev.get("minute_raw", "")
        video_sec = ev.get("video_time_seconds")
        ev_type = ev.get("type", "").lower()
        label = EVENT_TYPE_LABELS.get(ev_type, ev_type.upper())
        desc = ev.get("description", "")
        player = ev.get("player", "")
        suffix = f" ({player})" if player else ""

        ts = f"{minute}' / {video_sec}s" if video_sec is not None else f"{minute}'"
        lines.append(f"- [{ts} {label}] {desc}{suffix}")
    return "\n".join(lines) + "\n"


CATEGORY_RULE_RESEARCH = (
    "\n"
    "Research events are provided with specific match events. "
    "Prioritize EMOTION, AURA, and CHAOS categories that match "
    "the event types. Do not default to AMERICA framing when "
    "concrete football events (goals, cards, saves, penalties) "
    "are available.\n"
)


def _build_story_targets_block(research_path: Path | None) -> str:
    if research_path is None or not research_path.exists():
        return ""
    data = json.loads(research_path.read_text(encoding="utf-8"))
    targets = data.get("story_targets", {})
    if not targets:
        return ""
    lines = ["Story package targets:"]
    arc_type = targets.get("arc_type", "")
    if arc_type:
        lines.append(f"  - Arc type: {arc_type}")
    acts = targets.get("acts", [])
    if acts:
        lines.append(f"  - Acts to distribute across: {', '.join(acts)}")
    coverage = targets.get("required_coverage", {})
    types = coverage.get("types", [])
    if types:
        lines.append(f"  - Must cover event types: {', '.join(types)}")
    diversity = coverage.get("diversity", [])
    if diversity:
        lines.append(f"  - Must include: {', '.join(diversity)}")
    hook = targets.get("narrative_hook", "")
    if hook:
        lines.append(f"  - Narrative hook: {hook}")
    return "\n".join(lines) + "\n\n"


CLIP_SCHEMA_BASE = """  "clip_id": "001",
  "category": "EMOTION | AURA | CHAOS | AMERICA",
  "start_time": "SS",
  "end_time": "SS",
  "virality_score": 1-10,
  "retention_reason": "why people keep watching",
  "share_reason": "why people share it",
  "hook_text": "0-2 sec hook",
  "caption": "short caption",
  "editorial_thesis": "why this clip exists in the package",
  "emotional_angle": "how the viewer should feel",
  "legacy_value": 1,
  "thumbnail_idea": "specific frame or visual idea",
  "manual_scrub_note": "what visual moment to look for",
  "platform_notes": {
    "tiktok": "how to package",
    "reels": "how to package",
    "shorts": "how to package"
  }"""


def _build_clip_schema(mode: str) -> str:
    extra = ""
    if mode == "package":
        extra = (
            '  "sequence_order": 1,\n'
            '  "narrative_role": "setup | tension_builder | climax | reaction | aftermath",\n'
        )
    schema = extra + CLIP_SCHEMA_BASE
    return "For each clip candidate return:\n{\n" + schema + "\n}"


def _build_category_rule(research_path: Path | None) -> str:
    if research_path is None or not research_path.exists():
        return ""
    data = json.loads(research_path.read_text(encoding="utf-8"))
    if not data.get("events"):
        return ""
    return CATEGORY_RULE_RESEARCH


def _build_match_brief_block(research_path: Path | None) -> str:
    if research_path is None or not research_path.exists():
        return ""
    brief_path = research_path.parent / "match_brief.json"
    if not brief_path.exists():
        return ""
    try:
        data = json.loads(brief_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, IOError):
        print(f"Warning: malformed match_brief.json at {brief_path}, skipping")
        return ""
    lines = ["Match context (brief):"]
    storylines = data.get("narrative_storylines", [])
    if storylines:
        lines.append("  Narrative storylines:")
        for s in storylines:
            lines.append(f"    - {s}")
    implications = data.get("tournament_implications", {})
    if implications and any(v for v in implications.values()):
        lines.append("  Tournament implications:")
        for key, val in implications.items():
            if val:
                label = key.replace("_", " ").title()
                lines.append(f"    - {label}: {val}")
    standings = data.get("standings_entering_match", {})
    table = standings.get("table", [])
    if table:
        lines.append("  Group standings entering match:")
        for row in table:
            lines.append(
                f"    - {row['team']}: {row['pts']} pts "
                f"({row['w']}W {row['d']}D {row['l']}L, GD {row['gd']:+d})"
            )
    key_players = data.get("key_players", [])
    if key_players:
        lines.append("  Key players:")
        for p in key_players:
            notable = f" ({p['notable']})" if p.get("notable") else ""
            lines.append(f"    - {p['team']}: {p['name']} ({p['position']}){notable}")
    if len(lines) == 1:
        return ""
    return "\n".join(lines) + "\n\n"


PROMPT_TEMPLATE = """You are an elite short-form football clipping strategist for a US-targeted 2026 World Cup account.

Account positioning:
"{account_positioning}"

Goal:
{goal}

Analyze the timestamped transcript below and produce a JSON array of clip candidates.

Prioritize these categories:
1. EMOTION: crowd eruption, anthem moments, crying fans, tunnel walks, national pride, heartbreak, pressure.
2. AURA: Messi, Ronaldo, Mbappe, Bellingham, Vinicius, Neymar, cold reactions, legacy, intimidation.
3. CHAOS: VAR controversy, fights, red cards, meltdowns, tactical collapses.
4. AMERICA: US audience entry point, MLS/Messi, football culture shock, why soccer feels different.

{category_rule}{clip_schema}

{rules_block}

{story_targets_block}{research_block}{brief_block}Match name:
{match_name}

Timestamped transcript (video duration: {duration_seconds}s):
\"\"\"
{timestamped_transcript}
\"\"\"
"""

def _segments_to_transcript(segments):
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "")
        lines.append(f"[{start:.0f}s - {end:.0f}s] {text}")
    duration = int(segments[-1]["end"]) if segments else 0
    return "\n".join(lines), duration


def _load_segments(transcript_path):
    ts_path = transcript_path.with_name("timestamps.json")
    if not ts_path.exists():
        return None
    with open(ts_path, encoding="utf-8") as f:
        return json.load(f)


def _build_timestamped_transcript(transcript_path):
    segments = _load_segments(transcript_path)
    if segments is None:
        return transcript_path.read_text(encoding="utf-8"), 0
    return _segments_to_transcript(segments)

def main():
    parser = argparse.ArgumentParser(description="Generate Claude prompt from transcript.")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--mode", default=None, choices=("story", "micro", "package"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--research", default=None,
                        help="Path to match_research.json with known events")
    parser.add_argument("--condensed-windows", action="store_true",
                        help="Reduce transcript to football-relevant windows for long matches")
    parser.add_argument("--condense-interval", type=int, default=360,
                        help="Seconds between fallback sampling windows (default: 360)")
    parser.add_argument("--anchor-padding", type=int, default=25,
                        help="Seconds each side of research anchor (default: 25)")
    args = parser.parse_args()

    mode = args.mode or get_default_clip_mode()
    transcript_path = Path(args.transcript)

    if args.condensed_windows:
        segments = _load_segments(transcript_path)
        if segments is None:
            timestamped_transcript, duration = transcript_path.read_text(encoding="utf-8"), 0
        else:
            research_events = None
            research_path = Path(args.research) if args.research else None
            if research_path and research_path.exists():
                research_data = json.loads(research_path.read_text(encoding="utf-8"))
                research_events = research_data.get("events")
            segments = condense_timestamps(
                segments,
                research_events=research_events,
                coverage_interval=args.condense_interval,
                anchor_padding=args.anchor_padding,
            )
            timestamped_transcript, duration = _segments_to_transcript(segments)
    else:
        timestamped_transcript, duration = _build_timestamped_transcript(transcript_path)
    rules_block = _build_rules_block(mode, duration)
    if mode == "package":
        goal = GOAL_PACKAGE
    elif mode == "micro":
        goal = GOAL_MICRO
    else:
        goal = GOAL_STORY
    research_path = Path(args.research) if args.research else None
    research_block = _build_research_block(research_path)
    category_rule = _build_category_rule(research_path)
    story_targets_block = _build_story_targets_block(research_path)
    brief_block = _build_match_brief_block(research_path)
    clip_schema = _build_clip_schema(mode)
    account_positioning = load_config().get("account_positioning", "America Discovers Football")
    prompt = PROMPT_TEMPLATE.format(
        match_name=args.match_name,
        timestamped_transcript=timestamped_transcript,
        duration_seconds=duration,
        rules_block=rules_block,
        goal=goal,
        research_block=research_block,
        category_rule=category_rule,
        story_targets_block=story_targets_block,
        brief_block=brief_block,
        clip_schema=clip_schema,
        account_positioning=account_positioning,
    )

    out_file = ROOT / "PROMPTS" / f"{slugify(args.match_name)}_claude_prompt.txt"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(prompt, encoding="utf-8")
    print(f"Claude prompt written to: {out_file}")

if __name__ == "__main__":
    main()
