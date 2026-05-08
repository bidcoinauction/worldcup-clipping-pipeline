import argparse
from pathlib import Path
from pipeline.utils import ROOT, slugify

PROMPT_TEMPLATE = """You are an elite short-form football clipping strategist for a US-targeted 2026 World Cup account.

Account positioning:
"America Discovers Football"

Goal:
Train TikTok, Reels, and Shorts before the World Cup wave hits. We are not chasing generic highlights. We are identifying emotionally engaging, cinematic, mythological football moments.

Analyze the transcript below and produce a JSON array of clip candidates.

Prioritize these categories:
1. EMOTION: crowd eruption, anthem moments, crying fans, tunnel walks, national pride, heartbreak, pressure.
2. AURA: Messi, Ronaldo, Mbappe, Bellingham, Vinicius, Neymar, cold reactions, legacy, intimidation.
3. CHAOS: VAR controversy, fights, red cards, meltdowns, tactical collapses.
4. AMERICA: US audience entry point, MLS/Messi, football culture shock, why soccer feels different.

For each clip candidate return:
{{
  "clip_id": "001",
  "category": "EMOTION | AURA | CHAOS | AMERICA",
  "start_time": "00:00:00",
  "end_time": "00:00:00",
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

Rules:
- Favor emotional payoff over play-by-play.
- Avoid generic sports commentary.
- Think like a cinematic storyteller.
- Hook must be understandable to Americans who are still learning global football.
- Keep clip length between 12 and 28 seconds unless the moment needs longer.
- Return valid JSON only.

Match name:
{match_name}

Transcript:
\"\"\"
{transcript}
\"\"\"
"""

def main():
    parser = argparse.ArgumentParser(description="Generate Claude prompt from transcript.")
    parser.add_argument("--transcript", required=True)
    parser.add_argument("--match-name", required=True)
    args = parser.parse_args()

    transcript_path = Path(args.transcript)
    transcript = transcript_path.read_text(encoding="utf-8")
    prompt = PROMPT_TEMPLATE.format(match_name=args.match_name, transcript=transcript)

    out_file = ROOT / "PROMPTS" / f"{slugify(args.match_name)}_claude_prompt.txt"
    out_file.parent.mkdir(exist_ok=True)
    out_file.write_text(prompt, encoding="utf-8")
    print(f"Claude prompt written to: {out_file}")

if __name__ == "__main__":
    main()
