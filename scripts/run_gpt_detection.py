"""CLI adapter for clip-moment detection.

Thin wrapper over pipeline.detection.run_detection_call. Preserves existing
CLI behavior: --prompt, --output, --provider, --model, --dry-run.
"""

import argparse
import json
import os
from pathlib import Path

from pipeline.config import get_provider


def main():
    parser = argparse.ArgumentParser(
        description="Run clip moment detection via OpenAI API or local Ollama."
    )
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--provider", default=get_provider("detection"),
                        choices=["openai", "ollama"])
    parser.add_argument("--model", default=os.getenv("OLLAMA_MODEL", "llama3.1"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing.")
    args = parser.parse_args()

    if args.dry_run:
        print(f"[dry-run] Would call {args.provider} with prompt from {args.prompt}")
        print(f"[dry-run] Would write: {args.output}")
        print(f"[dry-run] Would write: {Path(args.output).with_suffix('.raw.txt')}")
        return

    from pipeline.detection import run_detection_call

    prompt_text = Path(args.prompt).read_text(encoding="utf-8")
    clips = run_detection_call(prompt_text, provider=args.provider, model=args.model)

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(clips, indent=2), encoding="utf-8")
    print(f"Detection JSON saved: {output_path}")


if __name__ == "__main__":
    main()
