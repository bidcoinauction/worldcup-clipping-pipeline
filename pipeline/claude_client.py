import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def run_claude_detection(prompt_path: str | Path, output_path: str | Path) -> None:
    """
    Calls Claude directly if ANTHROPIC_API_KEY is set.
    Writes raw text and attempts to parse JSON into output_path.
    """
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise SystemExit("ANTHROPIC_API_KEY missing. Add it to .env or use manual Claude copy/paste.")

    try:
        import anthropic
    except ImportError:
        raise SystemExit("Missing dependency. Run: pip install anthropic")

    model = os.getenv("DEFAULT_CLAUDE_MODEL", "claude-3-5-sonnet-latest")
    prompt = Path(prompt_path).read_text(encoding="utf-8")

    client = anthropic.Anthropic(api_key=api_key)
    message = client.messages.create(
        model=model,
        max_tokens=6000,
        temperature=0.2,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = "\n".join(block.text for block in message.content if getattr(block, "type", "") == "text")

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_path = output_path.with_suffix(".raw.txt")
    raw_path.write_text(raw, encoding="utf-8")

    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("[")
        end = raw.rfind("]")
        if start >= 0 and end > start:
            parsed = json.loads(raw[start:end + 1])
        else:
            raise SystemExit(f"Claude returned non-JSON. Raw saved to {raw_path}")

    output_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    print(f"Claude JSON saved: {output_path}")
    print(f"Raw response saved: {raw_path}")
