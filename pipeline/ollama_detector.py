import json
import os
from pathlib import Path

import requests
from pipeline.config import load_config


def run_ollama_detection(
    prompt_path: str | Path,
    output_path: str | Path,
    *,
    model: str = "llama3.1",
    dry_run: bool = False,
) -> None:
    prompt = Path(prompt_path).read_text(encoding="utf-8")
    output_path = Path(output_path)

    if dry_run:
        print(f"[dry-run] Would call Ollama model {model}")
        print(f"[dry-run] Would write: {output_path}")
        print(f"[dry-run] Would write: {output_path.with_suffix('.raw.txt')}")
        return

    url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    resp = requests.post(
        url,
        json={
            "model": model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": 0.2},
        },
        timeout=load_config()["providers"]["timeout"],
    )
    resp.raise_for_status()
    raw = resp.json()["response"]

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
            raise SystemExit(
                f"Ollama returned non-JSON. Raw saved to {raw_path}"
            )

    output_path.write_text(json.dumps(parsed, indent=2), encoding="utf-8")
    print(f"Ollama JSON saved: {output_path}")
    print(f"Raw response saved: {raw_path}")
