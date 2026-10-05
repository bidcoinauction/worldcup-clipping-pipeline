"""Operator Doctor — structured core + optional integration health checks.

Exit code 0 when healthy. Optional integrations never fail the core Doctor
unless explicitly requested via --include-integrations.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import runtime_db
from pipeline.integration_service import integration_health_report
from pipeline.system_health import core_health_report, doctor_ok
from pipeline.version import application_version


def _print_report(checks: list[dict]) -> None:
    for check in checks:
        status = check["status"]
        line = f"[{status:<7}] {check['operator_message']}"
        if check.get("recommended_action"):
            line += f"  -> {check['recommended_action']}"
        print(line)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clipper/Stadium Signal health checks.")
    parser.add_argument("--strict", action="store_true",
                        help="Promote warnings to failures (fails on missing optional capability).")
    parser.add_argument("--include-integrations", action="store_true",
                        help="Include optional integration adapter health.")
    args = parser.parse_args(argv)

    print(f"Clipper {application_version()}")
    print(f"Runtime DB: {runtime_db.default_runtime_db_path()}")
    core = core_health_report()
    _print_report(core)

    integrations = []
    if args.include_integrations:
        print("\nOptional integrations:")
        integrations = integration_health_report()
        _print_report(integrations)

    report = {"core": core, "integrations": integrations}
    ok = doctor_ok(report, strict=args.strict)
    print("\n" + ("OK" if ok else "ISSUES DETECTED"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())