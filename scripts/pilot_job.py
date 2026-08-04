#!/usr/bin/env python3
"""Managed pilot operations CLI.

Usage:
    python3 scripts/pilot_job.py validate path/to/intake.json [--intake-root ROOT]
    python3 scripts/pilot_job.py create   path/to/intake.json [--operator NAME]
                                                          [--jobs-dir DIR]
                                                          [--intake-root ROOT]
    python3 scripts/pilot_job.py show     JOB_ID [--jobs-dir DIR]
    python3 scripts/pilot_job.py list     [--jobs-dir DIR]

* ``validate`` performs read-only analysis and makes no file changes.
* ``create``   validates first, then writes a durable job record + events.
* ``show``     prints a privacy-safe job summary.
* ``list``     lists job records.

Expected operational errors print a concise message to stderr and exit
nonzero without a traceback; unexpected programming errors still raise
normally.

Never processes media, invokes models, or makes network requests.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # noqa: E402

from pipeline.config_errors import ConfigurationError  # noqa: E402
from pipeline.pilot import (  # noqa: E402
    JobExistsError,
    JobNotFoundError,
    JobPathError,
    JobRecordError,
    create_job,
    list_jobs,
    show_job,
    validate_intake,
)


def _load_intake(path: Path) -> dict:
    if not path.is_file():
        raise JobRecordError(f"intake file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobRecordError(f"intake file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise JobRecordError(f"intake root must be a JSON object: {path}")
    return data


def _cmd_validate(args: argparse.Namespace) -> int:
    data = _load_intake(Path(args.intake))
    report = validate_intake(data, intake_root=args.intake_root)

    print(f"INTAKE: structurally_valid={_yesno(report['structurally_valid'])} "
          f"source_ready={_yesno(report['source_ready'])} "
          f"rights_cleared={_yesno(report['rights_cleared'])} "
          f"execution_ready={_yesno(report['execution_ready'])} "
          f"rights_status={report['rights_status'] or 'n/a'}")
    if report["duration_limitation"]:
        print(f"WARNING: {report['duration_limitation']}")

    if report["issues"]:
        print("ISSUES:")
        for issue in report["issues"]:
            print(f"  {issue['path']} [{issue['code']}]: {issue['message']}")

    print("READINESS: " + ("execution-ready" if report["execution_ready"] else "not execution-ready"))
    return 0 if report["structurally_valid"] else 1


def _cmd_create(args: argparse.Namespace) -> int:
    data = _load_intake(Path(args.intake))
    try:
        job = create_job(
            data,
            intake_path=args.intake,
            jobs_dir=args.jobs_dir,
            operator=args.operator,
            intake_root=args.intake_root,
        )
    except JobExistsError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except (JobRecordError, JobPathError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    state = job["current_state"]
    print(f"JOB CREATED: {job['job_id']}")
    print(f"  state: {state}")
    print(f"  project: {job['project_id']}")
    print(f"  expected_output_root: {job['expected_output_root']}")
    print(f"  readiness: execution_ready={_yesno(job['readiness_summary']['execution_ready'])}")
    return 0 if state == "READY" else 2


def _cmd_show(args: argparse.Namespace) -> int:
    try:
        summary = show_job(args.job_id, jobs_dir=args.jobs_dir)
    except (JobNotFoundError, JobPathError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"JOB: {summary['job_id']}")
    print(f"  state: {summary['current_state']}")
    print(f"  pilot: {summary['pilot_id']}")
    print(f"  source: {summary['source_id']}")
    print(f"  project: {summary['project_id']}")
    print(f"  created: {summary['created_at']}")
    print(f"  output_root: {summary['expected_output_root']}")
    rs = summary["readiness_summary"]
    print(f"  readiness: structural={_yesno(rs.get('structurally_valid'))} "
          f"source={_yesno(rs.get('source_ready'))} "
          f"rights={_yesno(rs.get('rights_cleared'))} "
          f"execution_ready={_yesno(rs.get('execution_ready'))}")
    print(f"  events: {summary['event_count']}")
    for event in summary["events"]:
        prev = event.get("previous_state") or "-"
        print(f"    {event['timestamp']} {event['event_type']} {prev} -> {event.get('new_state') or '-'} ({event.get('source')})")
    return 0


def _cmd_list(args: argparse.Namespace) -> int:
    rows = list_jobs(jobs_dir=args.jobs_dir)
    if not rows:
        print("No pilot jobs recorded.")
        return 0
    for row in rows:
        print(f"{row['job_id']}\t{row['current_state']}")
    return 0


def _yesno(value: bool) -> str:
    return "yes" if value else "no"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Managed pilot operations (validate/create/show/list).")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate_p = subparsers.add_parser("validate", help="Validate an intake manifest (read-only).")
    validate_p.add_argument("intake", help="Path to intake JSON")
    validate_p.add_argument("--intake-root", default=None, help="Allowed source intake root")
    validate_p.set_defaults(func=_cmd_validate)

    create_p = subparsers.add_parser("create", help="Validate then create a durable job record.")
    create_p.add_argument("intake", help="Path to intake JSON")
    create_p.add_argument("--operator", default=None, help="Operator identifier")
    create_p.add_argument("--jobs-dir", default=None, help="Job-record root")
    create_p.add_argument("--intake-root", default=None, help="Allowed source intake root")
    create_p.set_defaults(func=_cmd_create)

    show_p = subparsers.add_parser("show", help="Show a privacy-safe job summary.")
    show_p.add_argument("job_id", help="Job identifier")
    show_p.add_argument("--jobs-dir", default=None, help="Job-record directory")
    show_p.set_defaults(func=_cmd_show)

    list_p = subparsers.add_parser("list", help="List job records.")
    list_p.add_argument("--jobs-dir", default=None, help="Job-record directory")
    list_p.set_defaults(func=_cmd_list)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    try:
        code = main()
    except ConfigurationError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    except (JobRecordError, JobPathError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        code = 1
    raise SystemExit(code)