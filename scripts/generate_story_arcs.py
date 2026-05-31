import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import write_story_arc


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate mythology-first story arc manifests from clip windows.")
    parser.add_argument("--match-id", required=True)
    args = parser.parse_args()

    out_path = write_story_arc(args.match_id)
    print(f"Story arc written: {out_path}")


if __name__ == "__main__":
    main()
