import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import init_archive


def main() -> None:
    init_archive()
    print("Stadium Signal archive folders, CSV datasets, and seed matches are ready.")


if __name__ == "__main__":
    main()
