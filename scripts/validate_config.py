#!/usr/bin/env python3
"""Read-only configuration validator.

Usage:
    python3 scripts/validate_config.py [PATH]

Validates either the legacy reference JSON (config/pipeline_config.json) or a
structured configuration file (e.g. config/examples/basketball.json). Exits
nonzero and prints the full failing field path when configuration is invalid.

Performs no network calls and mutates no files.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.config import CONFIG_PATH, validate_config_dict  # noqa: E402
from pipeline.config_errors import ConfigurationError  # noqa: E402
from pipeline.configurator import (  # noqa: E402
    validate_brand_profile,
    validate_export_profiles,
    validate_structured_profile,
)

_STRUCTURED_MARKERS = ("name", "profile", "project")


def validate_path(path: Path) -> int:
    """Validate a single file; returns 0 on success, raises ConfigurationError."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ConfigurationError(f"{path.name}: configuration root must be an object")

    parent = path.resolve().parent.name
    if parent == "brands":
        validate_brand_profile(data, source=path.name)
        print(f"OK: {path} is a valid brand profile")
    elif parent == "export":
        validate_export_profiles(data, source=path.name)
        print(f"OK: {path} is a valid export-profiles file")
    elif any(key in data for key in _STRUCTURED_MARKERS):
        validate_structured_profile(data, source=path.name)
        print(f"OK: {path} is a valid structured configuration")
    else:
        validate_config_dict(data, source=path.name)
        print(f"OK: {path} is a valid legacy configuration")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a configuration file (read-only).")
    parser.add_argument(
        "path",
        nargs="?",
        default=str(CONFIG_PATH),
        help="Configuration file to validate (default: the reference pipeline_config.json)",
    )
    args = parser.parse_args(argv)
    path = Path(args.path)
    if not path.is_file():
        print(f"INVALID: configuration file not found: {path}", file=sys.stderr)
        return 1
    try:
        return validate_path(path)
    except ConfigurationError as exc:
        print(f"INVALID: {exc}", file=sys.stderr)
        return 1
    except (json.JSONDecodeError, OSError) as exc:
        print(f"INVALID: {path} could not be read as JSON: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())