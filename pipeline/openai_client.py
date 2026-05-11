from __future__ import annotations

import os
import json
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

def parse_json_response(raw: str):
    """
    Parse model output that should be JSON, tolerating common fenced-code wrappers.
    """
    raw = raw.strip()
    if raw.startswith("```"):
        lines = raw.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        raw = "\n".join(lines).strip()

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        candidates = [
            (raw.find("["), raw.rfind("]")),
            (raw.find("{"), raw.rfind("}")),
        ]
        for start, end in candidates:
            if start >= 0 and end > start:
                try:
                    return json.loads(raw[start:end + 1])
                except json.JSONDecodeError:
                    continue
        raise

def run_gpt_detection(prompt_path: str | Path, output_path: str | Path) -> None:
    """
    Calls OpenAI directly if OPENAI_API_KEY is set.
    Writes raw text and attempts to parse JSON into output_path.
    """
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise SystemExit("OPENAI_API_KEY missing. Add it to .env or use manual ChatGPT copy/paste.")

    try:
        from openai import OpenAI
    except ImportError:
        raise SystemExit("Missing dependency. Run: pip install openai")

    model = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4.1")
    prompt = Path(prompt_path).read_text(encoding="utf-8")

    client = OpenAI(api_key=api_key)
    response = client.responses.create(
        model=model,
        input=prompt,
        temperature=0.2,
    )

    raw = response.output_text

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    raw_path = output_path.with_suffix(".raw.txt")
    raw_path.write_text(raw, encoding="utf-8")

    try:
        parsed = parse_json_response(raw)
    except json.JSONDecodeError:
        raise SystemExit(f"GPT returned non-JSON. Raw saved to {raw_path}")

    output_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    print(f"GPT JSON saved: {output_path}")
    print(f"Raw response saved: {raw_path}")
