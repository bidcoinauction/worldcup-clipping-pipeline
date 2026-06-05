import argparse
import os
from pipeline.openai_client import run_gpt_detection
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

    if args.provider == "openai":
        run_gpt_detection(args.prompt, args.output, dry_run=args.dry_run)
    else:
        from pipeline.ollama_detector import run_ollama_detection
        run_ollama_detection(
            args.prompt, args.output, model=args.model, dry_run=args.dry_run
        )

if __name__ == "__main__":
    main()
