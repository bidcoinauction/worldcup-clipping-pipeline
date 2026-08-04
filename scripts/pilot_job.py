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
    DeliveryPackageError,
    JobExistsError,
    JobNotFoundError,
    JobPathError,
    JobRecordError,
    OutputManifestError,
    confirm_delivery,
    create_job,
    generate_delivery_package,
    read_history,
    read_job,
    list_delivery_packages,
    list_jobs,
    list_output_manifests,
    output_summary,
    read_delivery_checklist,
    register_output_manifest,
    review_output,
    show_job,
    show_delivery_package,
    show_output_manifest,
    transition_job,
    validate_delivery_package,
    validate_output_manifest,
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


def _load_json_object(path: Path, label: str) -> dict:
    if not path.is_file():
        raise JobRecordError(f"{label} file not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise JobRecordError(f"{label} file is not valid JSON: {path} ({exc})") from exc
    if not isinstance(data, dict):
        raise JobRecordError(f"{label} root must be a JSON object: {path}")
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
    print(f"  revision: {summary['revision']}")
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
    allowed = ", ".join(summary.get("allowed_next_states", [])) or "(none)"
    print(f"  allowed_next_states: {allowed}")
    if summary.get("latest_event_summary"):
        print(f"  latest_event: {summary['latest_event_summary']}")
    for event in summary["events"]:
        prev = event.get("previous_state") or "-"
        print(f"    {event.get('sequence', '-')} {event['timestamp']} {event['event_type']} "
              f"{prev} -> {event.get('new_state') or '-'} ({event.get('source')})")
    return 0


def _cmd_transition(args: argparse.Namespace) -> int:
    metadata = {
        "operator": args.operator,
        "reason": args.reason,
        "approval_statement": args.approval_statement,
        "confirmation": args.confirmation,
        "delivery_method": args.delivery_method,
        "delivery_destination": args.delivery_destination,
        "delivery_package_id": args.delivery_package_id,
        "failure_category": args.failure_category,
        "retry_allowed": _parse_bool(args.retry_allowed) if args.retry_allowed is not None else None,
        "client_requested": _parse_bool(args.client_requested) if args.client_requested is not None else None,
        "recovery_reason": args.recovery_reason,
        "recovery_confirmed": args.recovery_confirmed if args.recovery_confirmed else None,
    }
    if args.deliverable_count is not None:
        metadata["deliverable_count"] = args.deliverable_count
    if args.delivered_item_count is not None:
        metadata["delivered_item_count"] = args.delivered_item_count
    metadata = {k: v for k, v in metadata.items() if v is not None}

    try:
        job = transition_job(
            args.job_id,
            args.state,
            metadata=metadata,
            artifact_references=args.artifact or [],
            expected_revision=args.expected_revision,
            jobs_dir=args.jobs_dir,
            intake_root=args.intake_root,
            source="pilot_job.transition",
        )
    except JobRecordError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"JOB TRANSITIONED: {job['job_id']}")
    print(f"  state: {job['current_state']}")
    print(f"  revision: {job['revision']}")
    print(f"  events: {job['event_count']}")
    return 0


def _cmd_history(args: argparse.Namespace) -> int:
    try:
        events = read_history(args.job_id, jobs_dir=args.jobs_dir)
    except (JobNotFoundError, JobPathError, JobRecordError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not events:
        print(f"No history for {args.job_id}.")
        return 0
    print(f"HISTORY: {args.job_id}")
    for event in events:
        prev = event.get("previous_state") or "-"
        new = event.get("new_state") or "-"
        operator = event.get("operator") or "-"
        message = event.get("message") or ""
        print(f"  {event.get('sequence', '-')} {event.get('timestamp', '')} {prev} -> {new} operator={operator} {message}")
    return 0


def _cmd_outputs_validate(args: argparse.Namespace) -> int:
    data = _load_json_object(Path(args.manifest), "output manifest")
    report = validate_output_manifest(data)
    print(f"OUTPUT MANIFEST: valid={_yesno(report['valid'])}")
    if report["issues"]:
        print("ISSUES:")
        for issue in report["issues"]:
            print(f"  {issue['path']} [{issue['code']}]: {issue['message']}")
    return 0 if report["valid"] else 1


def _cmd_outputs_register(args: argparse.Namespace) -> int:
    data = _load_json_object(Path(args.manifest), "output manifest")
    try:
        result = register_output_manifest(
            args.job_id,
            data,
            jobs_dir=args.jobs_dir,
            expected_revision=args.expected_revision,
            operator=args.operator,
        )
    except OutputManifestError as exc:
        _print_output_error(exc)
        return 1
    except JobRecordError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    job = result["job"]
    manifest = result["manifest"]
    print(f"OUTPUT MANIFEST REGISTERED: {manifest['manifest_id']}")
    print(f"  job: {job['job_id']}")
    print(f"  job_revision: {job['revision']}")
    print(f"  manifest_revision: {manifest.get('revision', 0)}")
    print(f"  outputs: {len(manifest.get('outputs', []))}")
    return 0


def _cmd_outputs_list(args: argparse.Namespace) -> int:
    rows = list_output_manifests(args.job_id, jobs_dir=args.jobs_dir)
    if not rows:
        print(f"No output manifests registered for {args.job_id}.")
        return 0
    for row in rows:
        print(f"{row['manifest_id']}\trevision={row['revision']}\toutputs={row['output_count']}")
    return 0


def _cmd_outputs_show(args: argparse.Namespace) -> int:
    data = show_output_manifest(args.job_id, args.manifest_id, jobs_dir=args.jobs_dir)
    print(f"OUTPUT MANIFEST: {data['manifest_id']}")
    print(f"  job: {data['job_id']}")
    print(f"  revision: {data['revision']}")
    print(f"  outputs: {data['output_count']}")
    for output in data["outputs"]:
        include = "include" if output["include_in_delivery"] else "exclude"
        print(f"    {output['output_id']} {output['output_type']} {output['review_status']} {include} {output['filename']}")
    return 0


def _cmd_outputs_review(args: argparse.Namespace) -> int:
    try:
        result = review_output(
            args.job_id,
            args.manifest_id,
            args.output_id,
            status=args.status,
            operator=args.operator,
            reason=args.reason,
            include_in_delivery=args.include_in_delivery,
            jobs_dir=args.jobs_dir,
            expected_job_revision=args.expected_job_revision,
            expected_manifest_revision=args.expected_manifest_revision,
        )
    except OutputManifestError as exc:
        _print_output_error(exc)
        return 1
    except JobRecordError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OUTPUT REVIEWED: {args.output_id}")
    print(f"  status: {args.status}")
    print(f"  job_revision: {result['job']['revision']}")
    print(f"  manifest_revision: {result['manifest']['revision']}")
    return 0


def _cmd_outputs_summary(args: argparse.Namespace) -> int:
    summary = output_summary(args.job_id, jobs_dir=args.jobs_dir, intake_root=args.intake_root)
    print(f"OUTPUT SUMMARY: {args.job_id}")
    for key in ("manifest_count", "total_outputs", "video_count", "delivery_included_count",
                "approved_delivery_included_count", "missing_file_count", "invalid_reference_count"):
        print(f"  {key}: {summary[key]}")
    print(f"  review_complete: {_yesno(summary['review_complete'])}")
    print(f"  eligible_for_approved: {_yesno(summary['eligible_for_approved'])}")
    print(f"  eligible_for_delivery_ready: {_yesno(summary['eligible_for_delivery_ready'])}")
    print(f"  job_revision: {summary['job_revision']}")
    print(f"  manifest_revisions: {json.dumps(summary['manifest_revisions'], sort_keys=True)}")
    print(f"  statuses: {json.dumps(summary['counts_by_review_status'], sort_keys=True)}")
    return 0


def _cmd_delivery_generate(args: argparse.Namespace) -> int:
    try:
        result = generate_delivery_package(
            args.job_id,
            package_id=args.package_id,
            operator=args.operator,
            delivery_method=args.delivery_method,
            delivery_destination=args.delivery_destination,
            package_label=args.package_label,
            internal_notes=args.internal_notes,
            expected_revision=args.expected_revision,
            jobs_dir=args.jobs_dir,
            intake_root=args.intake_root,
        )
    except (DeliveryPackageError, JobRecordError, JobPathError) as exc:
        _print_delivery_error(exc)
        return 1
    package = result["package"]
    print(f"DELIVERY PACKAGE GENERATED: {package['package_id']}")
    print(f"  job: {package['job_id']}")
    print(f"  job_revision: {result['job']['revision']}")
    print(f"  deliverables: {len(package.get('deliverables', []))}")
    print(f"  package_path: {result['package_path']}")
    print(f"  checklist_path: {result['checklist_path']}")
    return 0


def _cmd_delivery_validate(args: argparse.Namespace) -> int:
    data = _load_json_object(Path(args.package), "delivery package")
    job = None
    if args.job_id:
        try:
            job = read_job(args.job_id, jobs_dir=args.jobs_dir)
        except (JobRecordError, JobPathError) as exc:
            _print_delivery_error(exc)
            return 1
    report = validate_delivery_package(data, job=job, jobs_dir=args.jobs_dir, intake_root=args.intake_root)
    print(f"DELIVERY PACKAGE: valid={_yesno(report['valid'])}")
    if report["issues"]:
        print("ISSUES:")
        for issue in report["issues"]:
            print(f"  {issue['path']} [{issue['code']}]: {issue['message']}")
    return 0 if report["valid"] else 1


def _cmd_delivery_list(args: argparse.Namespace) -> int:
    try:
        rows = list_delivery_packages(args.job_id, jobs_dir=args.jobs_dir)
    except (DeliveryPackageError, JobRecordError, JobPathError) as exc:
        _print_delivery_error(exc)
        return 1
    if not rows:
        print(f"No delivery packages recorded for {args.job_id}.")
        return 0
    for row in rows:
        print(f"{row['package_id']}\trevision={row['package_revision']}\tdeliverables={row['deliverable_count']}\tcreated={row['created_at']}")
    return 0


def _cmd_delivery_show(args: argparse.Namespace) -> int:
    try:
        data = show_delivery_package(args.job_id, args.package_id, jobs_dir=args.jobs_dir)
    except (DeliveryPackageError, JobRecordError, JobPathError) as exc:
        _print_delivery_error(exc)
        return 1
    print(f"DELIVERY PACKAGE: {data['package_id']}")
    print(f"  job: {data['job_id']}")
    print(f"  revision: {data['package_revision']}")
    print(f"  delivery_method: {data['delivery_method']}")
    print(f"  delivery_destination: {data['delivery_destination']}")
    print(f"  deliverables: {data['deliverable_count']}")
    for deliverable in data["deliverables"]:
        print(f"    {deliverable['delivery_sequence']} {deliverable['deliverable_id']} {deliverable['output_type']} {deliverable['platform']} {deliverable['filename']}")
    return 0


def _cmd_delivery_checklist(args: argparse.Namespace) -> int:
    try:
        print(read_delivery_checklist(args.job_id, args.package_id, jobs_dir=args.jobs_dir), end="")
    except (DeliveryPackageError, JobRecordError, JobPathError) as exc:
        _print_delivery_error(exc)
        return 1
    return 0


def _cmd_delivery_confirm(args: argparse.Namespace) -> int:
    try:
        result = confirm_delivery(
            args.job_id,
            args.package_id,
            operator=args.operator,
            confirmation=args.confirmation,
            delivered_count=args.delivered_count,
            client_acknowledgment=args.client_acknowledgment,
            notes=args.notes,
            expected_revision=args.expected_revision,
            jobs_dir=args.jobs_dir,
            intake_root=args.intake_root,
        )
    except (DeliveryPackageError, JobRecordError, JobPathError) as exc:
        _print_delivery_error(exc)
        return 1
    print(f"DELIVERY CONFIRMED: {result['confirmation']['package_id']}")
    print(f"  job: {result['confirmation']['job_id']}")
    print(f"  job_revision: {result['job']['revision']}")
    print(f"  delivered_item_count: {result['confirmation']['delivered_item_count']}")
    print(f"  confirmation_path: {result['confirmation_path']}")
    return 0


def _print_output_error(exc: OutputManifestError) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)
    for issue in getattr(exc, "issues", [])[:20]:
        print(f"  {issue['path']} [{issue['code']}]: {issue['message']}", file=sys.stderr)


def _print_delivery_error(exc: Exception) -> None:
    print(f"ERROR: {exc}", file=sys.stderr)
    for issue in getattr(exc, "issues", [])[:20]:
        print(f"  {issue['path']} [{issue['code']}]: {issue['message']}", file=sys.stderr)


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


def _parse_bool(value: str) -> bool:
    if value.lower() in {"yes", "true", "1"}:
        return True
    if value.lower() in {"no", "false", "0"}:
        return False
    raise JobRecordError(f"expected boolean value yes/no, got '{value}'")


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

    transition_p = subparsers.add_parser("transition", help="Record a validated manual job-state transition.")
    transition_p.add_argument("job_id", help="Job identifier")
    transition_p.add_argument("state", help="Destination state")
    transition_p.add_argument("--operator", default=None, help="Operator or reviewer identifier")
    transition_p.add_argument("--reason", default=None, help="Human-readable reason or summary")
    transition_p.add_argument("--expected-revision", type=int, default=None, help="Optimistic concurrency revision")
    transition_p.add_argument("--artifact", action="append", default=[], help="Output/review/delivery artifact reference")
    transition_p.add_argument("--deliverable-count", type=int, default=None, help="Approved or delivery-ready item count")
    transition_p.add_argument("--delivered-item-count", type=int, default=None, help="Delivered item count")
    transition_p.add_argument("--delivery-method", default=None, help="Delivery method")
    transition_p.add_argument("--delivery-destination", default=None, help="Delivery destination")
    transition_p.add_argument("--delivery-package-id", default=None, help="Delivery package identifier")
    transition_p.add_argument("--failure-category", default=None, help="Failure category")
    transition_p.add_argument("--retry-allowed", default=None, help="Whether retry is allowed: yes/no")
    transition_p.add_argument("--client-requested", default=None, help="Whether cancellation was client-requested: yes/no")
    transition_p.add_argument("--approval-statement", default=None, help="Approval statement")
    transition_p.add_argument("--confirmation", default=None, help="Delivery confirmation statement")
    transition_p.add_argument("--recovery-reason", default=None, help="Required when recovering from FAILED")
    transition_p.add_argument("--recovery-confirmed", action="store_true", help="Confirm the blocking issue was addressed")
    transition_p.add_argument("--jobs-dir", default=None, help="Job-record directory")
    transition_p.add_argument("--intake-root", default=None, help="Allowed source intake root")
    transition_p.set_defaults(func=_cmd_transition)

    history_p = subparsers.add_parser("history", help="Show privacy-safe append-only job event history.")
    history_p.add_argument("job_id", help="Job identifier")
    history_p.add_argument("--jobs-dir", default=None, help="Job-record directory")
    history_p.set_defaults(func=_cmd_history)

    outputs_p = subparsers.add_parser("outputs", help="Validate, register, review, and summarize output manifests.")
    outputs_sub = outputs_p.add_subparsers(dest="outputs_command", required=True)

    outputs_validate = outputs_sub.add_parser("validate", help="Validate an output manifest JSON (read-only).")
    outputs_validate.add_argument("manifest", help="Path to output manifest JSON")
    outputs_validate.set_defaults(func=_cmd_outputs_validate)

    outputs_register = outputs_sub.add_parser("register", help="Register an output manifest against a job.")
    outputs_register.add_argument("job_id", help="Job identifier")
    outputs_register.add_argument("manifest", help="Path to output manifest JSON")
    outputs_register.add_argument("--expected-revision", type=int, default=None, help="Expected job revision")
    outputs_register.add_argument("--operator", default=None, help="Operator identifier")
    outputs_register.add_argument("--jobs-dir", default=None, help="Job-record directory")
    outputs_register.set_defaults(func=_cmd_outputs_register)

    outputs_list = outputs_sub.add_parser("list", help="List registered output manifests for a job.")
    outputs_list.add_argument("job_id", help="Job identifier")
    outputs_list.add_argument("--jobs-dir", default=None, help="Job-record directory")
    outputs_list.set_defaults(func=_cmd_outputs_list)

    outputs_show = outputs_sub.add_parser("show", help="Show a privacy-safe output manifest summary.")
    outputs_show.add_argument("job_id", help="Job identifier")
    outputs_show.add_argument("manifest_id", help="Output manifest identifier")
    outputs_show.add_argument("--jobs-dir", default=None, help="Job-record directory")
    outputs_show.set_defaults(func=_cmd_outputs_show)

    outputs_review = outputs_sub.add_parser("review", help="Record a manual review action for one output.")
    outputs_review.add_argument("job_id", help="Job identifier")
    outputs_review.add_argument("manifest_id", help="Output manifest identifier")
    outputs_review.add_argument("output_id", help="Output identifier")
    outputs_review.add_argument("--status", required=True, help="Review status")
    outputs_review.add_argument("--operator", required=True, help="Reviewer/operator identifier")
    outputs_review.add_argument("--reason", required=True, help="Review reason or statement")
    outputs_review.add_argument("--include-in-delivery", action="store_true", help="Include approved output in delivery")
    outputs_review.add_argument("--expected-job-revision", type=int, default=None, help="Expected job revision")
    outputs_review.add_argument("--expected-manifest-revision", type=int, default=None, help="Expected output manifest revision")
    outputs_review.add_argument("--jobs-dir", default=None, help="Job-record directory")
    outputs_review.set_defaults(func=_cmd_outputs_review)

    outputs_summary = outputs_sub.add_parser("summary", help="Show output summary and readiness for a job.")
    outputs_summary.add_argument("job_id", help="Job identifier")
    outputs_summary.add_argument("--jobs-dir", default=None, help="Job-record directory")
    outputs_summary.add_argument("--intake-root", default=None, help="Allowed source intake root")
    outputs_summary.set_defaults(func=_cmd_outputs_summary)

    delivery_p = subparsers.add_parser("delivery", help="Generate, inspect, and confirm delivery packages.")
    delivery_sub = delivery_p.add_subparsers(dest="delivery_command", required=True)

    delivery_generate = delivery_sub.add_parser("generate", help="Generate a text/JSON delivery package from approved outputs.")
    delivery_generate.add_argument("job_id", help="Job identifier")
    delivery_generate.add_argument("package_id", help="Delivery package identifier")
    delivery_generate.add_argument("--operator", required=True, help="Operator identifier")
    delivery_generate.add_argument("--delivery-method", required=True, help="Delivery method")
    delivery_generate.add_argument("--delivery-destination", required=True, help="Delivery destination")
    delivery_generate.add_argument("--package-label", default=None, help="Optional package label")
    delivery_generate.add_argument("--internal-notes", default=None, help="Optional internal notes")
    delivery_generate.add_argument("--expected-revision", type=int, default=None, help="Expected job revision")
    delivery_generate.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_generate.add_argument("--intake-root", default=None, help="Allowed source intake root")
    delivery_generate.set_defaults(func=_cmd_delivery_generate)

    delivery_validate = delivery_sub.add_parser("validate", help="Validate a delivery package JSON (read-only).")
    delivery_validate.add_argument("package", help="Path to delivery package JSON")
    delivery_validate.add_argument("--job-id", default=None, help="Job identifier for current-state validation")
    delivery_validate.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_validate.add_argument("--intake-root", default=None, help="Allowed source intake root")
    delivery_validate.set_defaults(func=_cmd_delivery_validate)

    delivery_list = delivery_sub.add_parser("list", help="List delivery packages for a job.")
    delivery_list.add_argument("job_id", help="Job identifier")
    delivery_list.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_list.set_defaults(func=_cmd_delivery_list)

    delivery_show = delivery_sub.add_parser("show", help="Show a privacy-safe delivery package summary.")
    delivery_show.add_argument("job_id", help="Job identifier")
    delivery_show.add_argument("package_id", help="Delivery package identifier")
    delivery_show.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_show.set_defaults(func=_cmd_delivery_show)

    delivery_checklist = delivery_sub.add_parser("checklist", help="Print the generated handoff checklist.")
    delivery_checklist.add_argument("job_id", help="Job identifier")
    delivery_checklist.add_argument("package_id", help="Delivery package identifier")
    delivery_checklist.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_checklist.set_defaults(func=_cmd_delivery_checklist)

    delivery_confirm = delivery_sub.add_parser("confirm", help="Record manual delivery confirmation for a package.")
    delivery_confirm.add_argument("job_id", help="Job identifier")
    delivery_confirm.add_argument("package_id", help="Delivery package identifier")
    delivery_confirm.add_argument("--operator", required=True, help="Operator identifier")
    delivery_confirm.add_argument("--confirmation", required=True, help="Confirmation statement")
    delivery_confirm.add_argument("--delivered-count", type=int, required=True, help="Delivered item count")
    delivery_confirm.add_argument("--client-acknowledgment", default=None, help="Optional client acknowledgment reference")
    delivery_confirm.add_argument("--notes", default=None, help="Optional confirmation notes")
    delivery_confirm.add_argument("--expected-revision", type=int, default=None, help="Expected job revision")
    delivery_confirm.add_argument("--jobs-dir", default=None, help="Job-record directory")
    delivery_confirm.add_argument("--intake-root", default=None, help="Allowed source intake root")
    delivery_confirm.set_defaults(func=_cmd_delivery_confirm)

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
