import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import DATASETS, ROOT, validate_data


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Stadium Signal OS CSV datasets.")
    parser.add_argument("--root", default=ROOT, help="Project root to validate.")
    args = parser.parse_args()

    result = validate_data(Path(args.root))
    for name, (rel_path, _) in DATASETS.items():
        icon = "✅" if result.dataset_status.get(name) else "❌"
        print(f"{icon} {Path(rel_path).name} valid")

    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")

    if result.ok:
        print("✅ Stadium Signal data validation passed")
        return
    sys.exit(1)


if __name__ == "__main__":
    main()
