"""Release readiness check — fast deterministic checks only.

Does not execute the pytest suite from inside Python.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import runtime_db
from pipeline.system_health import core_health_report, doctor_ok
from pipeline.stadium_signal import ROOT, validate_data
from pipeline.version import application_version


def scan_smoke_reports(smoke_dir: Path) -> tuple[bool, bool]:
    """Return (automated_smoke_pass, real_media_smoke_pass) from smoke reports."""
    import json
    automated = False
    real_media = False
    if not smoke_dir.exists():
        return automated, real_media
    for report_path in smoke_dir.glob("*/smoke_report.json"):
        try:
            data = json.loads(report_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if data.get("timings", {}).get("result") != "PASS":
            continue
        classification = data.get("classification", {})
        if classification.get("real_media"):
            real_media = True
        else:
            automated = True
    return automated, real_media


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clipper release readiness check.")
    parser.add_argument("--strict", action="store_true")
    args = parser.parse_args(argv)

    print(f"Clipper {application_version()} - release check")
    print(f"Runtime DB schema v{runtime_db.current_schema_version()} "
          f"({'current' if runtime_db.schema_is_current() else 'OUT OF DATE'})")

    report = {"core": core_health_report(), "integrations": []}
    ok = doctor_ok(report, strict=args.strict)
    for check in report["core"]:
        print(f"[{check['status']:<7}] {check['operator_message']}")

    validation = validate_data(ROOT)
    print(f"[{'PASS' if validation.ok else 'FAIL':<7}] data validation ({len(validation.errors)} errors)")

    smoke_dir = runtime_db.default_runtime_db_path().parent / "smoke"
    automated_pass, real_media_pass = scan_smoke_reports(smoke_dir)

    print(f"[{'PASS' if automated_pass else 'WARN':<7}] automated workflow smoke "
          f"({'recorded' if automated_pass else 'not recorded'}). Run: python scripts/smoke_real_match.py --fast")
    if real_media_pass:
        print("[PASS   ] real-media validation recorded")
    else:
        print("[WARN   ] real-media validation not yet completed (RC blocker until a real long-media run passes)")

    return 0 if (ok and validation.ok) else 1


if __name__ == "__main__":
    raise SystemExit(main())