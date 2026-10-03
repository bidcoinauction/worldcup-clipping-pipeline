import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import DATASETS, ROOT, validate_data


def _status_icons() -> tuple[str, str]:
    encoding = sys.stdout.encoding or ""
    try:
        "✅❌".encode(encoding)
    except (LookupError, UnicodeEncodeError):
        return "[OK]", "[FAIL]"
    return "✅", "❌"


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate Stadium Signal OS CSV datasets.")
    parser.add_argument("--root", default=ROOT, help="Project root to validate.")
    args = parser.parse_args()

    result = validate_data(Path(args.root))
    ok_icon, fail_icon = _status_icons()
    for name, (rel_path, _) in DATASETS.items():
        icon = ok_icon if result.dataset_status.get(name) else fail_icon
        print(f"{icon} {Path(rel_path).name} valid")

    for warning in result.warnings:
        print(f"WARNING: {warning}")
    for error in result.errors:
        print(f"ERROR: {error}")

    if result.ok:
        print(f"{ok_icon} Stadium Signal data validation passed")
        return
    sys.exit(1)


if __name__ == "__main__":
    main()
