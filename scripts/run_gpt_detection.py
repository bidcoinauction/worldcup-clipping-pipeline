import argparse
from pipeline.openai_client import run_gpt_detection

def main():
    parser = argparse.ArgumentParser(description="Run GPT moment detection via OpenAI API.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing.")
    args = parser.parse_args()

    run_gpt_detection(args.prompt, args.output, dry_run=args.dry_run)

if __name__ == "__main__":
    main()
