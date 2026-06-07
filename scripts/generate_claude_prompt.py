import argparse
import json
from pathlib import Path
from pipeline.config import get_clip_mode, get_default_clip_mode, load_config
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


def _build_category_rule(research_path: Path | None) -> str:
    if research_path is None or not research_path.exists():
        return ""
    data = json.loads(research_path.read_text(encoding="utf-8"))
    if not data.get("events"):
        return ""
    return CATEGORY_RULE_RESEARCH


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

{category_rule}For each clip candidate return:
{{
  "clip_id": "001",
  "category": "EMOTION | AURA | CHAOS | AMERICA",
  "start_time": "SS",
  "end_time": "SS",
  "virality_score": 1-10,
  "retention_reason": "why people keep watching",
  "share_reason": "why people share it",
  "hook_text": "0-2 sec hook",
  "caption": "short caption",
  "thumbnail_idea": "specific frame or visual idea",
  "manual_scrub_note": "what visual moment to look for",
  "platform_notes": {{
    "tiktok": "how to package",
    "reels": "how to package",
    "shorts": "how to package"
  }}
}}

{rules_block}

{research_block}Match name:
{match_name}

Timestamped transcript (video duration: {duration_seconds}s):
\"\"\"
{timestamped_transcript}
\"\"\"
"""

def _build_timestamped_transcript(transcript_path):
    ts_path = transcript_path.with_name("timestamps.json")
    if not ts_path.exists():
        return transcript_path.read_text(encoding="utf-8"), 0
    with open(ts_path, encoding="utf-8") as f:
        segments = json.load(f)
    lines = []
    for seg in segments:
        start = seg.get("start", 0)
        end = seg.get("end", 0)
        text = seg.get("text", "")
        lines.append(f"[{start:.0f}s - {end:.0f}s] {text}")
    duration = int(segments[-1]["end"]) if segments else 0
    return "\n".join(lines), duration

def main():
    parser = argparse.ArgumentParser(description="Generate Claude prompt from transcript.")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--mode", default=None, choices=("story", "micro"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--research", default=None,
                        help="Path to match_research.json with known events")
    args = parser.parse_args()

    mode = args.mode or get_default_clip_mode()
    transcript_path = Path(args.transcript)
    timestamped_transcript, duration = _build_timestamped_transcript(transcript_path)
    rules_block = _build_rules_block(mode, duration)
    goal = GOAL_MICRO if mode == "micro" else GOAL_STORY
    research_path = Path(args.research) if args.research else None
    research_block = _build_research_block(research_path)
    category_rule = _build_category_rule(research_path)
    account_positioning = load_config().get("account_positioning", "America Discovers Football")
    prompt = PROMPT_TEMPLATE.format(
        match_name=args.match_name,
        timestamped_transcript=timestamped_transcript,
        duration_seconds=duration,
        rules_block=rules_block,
        goal=goal,
        research_block=research_block,
        category_rule=category_rule,
        account_positioning=account_positioning,
    )

    out_file = ROOT / "PROMPTS" / f"{slugify(args.match_name)}_claude_prompt.txt"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(prompt, encoding="utf-8")
    print(f"Claude prompt written to: {out_file}")

if __name__ == "__main__":
    main()
