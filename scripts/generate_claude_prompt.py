import argparse
import json
from pathlib import Path
from pipeline.config import get_clip_mode, get_default_clip_mode
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
- Return {min_c}-{max_c} microclips.
- Each clip must be {min_s}-{max_s} seconds.
- Focus on facial reactions, crowd emotion, ball contact, referee gesture, celebration flash, or commentary spike.
- Avoid full highlight sequences.
- Do not include continuous broadcast sequence longer than {max_s} seconds."""

    msg = f"Unknown clip mode: {mode}"
    raise ValueError(msg)


GOAL_STORY = """Train TikTok, Reels, and Shorts before the World Cup wave hits. We are not chasing generic highlights. We are identifying emotionally engaging, cinematic, mythological football moments."""

GOAL_MICRO = """Train TikTok, Reels, and Shorts before the World Cup wave hits. We are identifying micro-moments: the fragments within football that can be transformed, reused, and compiled into new narratives without relying on long broadcast sequences."""


PROMPT_TEMPLATE = """You are an elite short-form football clipping strategist for a US-targeted 2026 World Cup account.

Account positioning:
"America Discovers Football"

Goal:
{goal}

Analyze the timestamped transcript below and produce a JSON array of clip candidates.

Prioritize these categories:
1. EMOTION: crowd eruption, anthem moments, crying fans, tunnel walks, national pride, heartbreak, pressure.
2. AURA: Messi, Ronaldo, Mbappe, Bellingham, Vinicius, Neymar, cold reactions, legacy, intimidation.
3. CHAOS: VAR controversy, fights, red cards, meltdowns, tactical collapses.
4. AMERICA: US audience entry point, MLS/Messi, football culture shock, why soccer feels different.

For each clip candidate return:
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

Match name:
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
    args = parser.parse_args()

    mode = args.mode or get_default_clip_mode()
    transcript_path = Path(args.transcript)
    timestamped_transcript, duration = _build_timestamped_transcript(transcript_path)
    rules_block = _build_rules_block(mode, duration)
    goal = GOAL_MICRO if mode == "micro" else GOAL_STORY
    prompt = PROMPT_TEMPLATE.format(
        match_name=args.match_name,
        timestamped_transcript=timestamped_transcript,
        duration_seconds=duration,
        rules_block=rules_block,
        goal=goal,
    )

    out_file = ROOT / "PROMPTS" / f"{slugify(args.match_name)}_claude_prompt.txt"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(prompt, encoding="utf-8")
    print(f"Claude prompt written to: {out_file}")

if __name__ == "__main__":
    main()
