import argparse
from pipeline.claude_client import run_claude_detection

def main():
    parser = argparse.ArgumentParser(description="Run Claude moment detection via API.")
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()

    run_claude_detection(args.prompt, args.output)

if __name__ == "__main__":
    main()
