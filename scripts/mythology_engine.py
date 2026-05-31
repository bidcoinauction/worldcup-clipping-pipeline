import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import mythology_for_match


def main() -> None:
    parser = argparse.ArgumentParser(description="Classify a match by Stadium Signal mythology score.")
    parser.add_argument("--match-id", required=True)
    args = parser.parse_args()

    print(json.dumps(mythology_for_match(args.match_id), indent=2))


if __name__ == "__main__":
    main()
