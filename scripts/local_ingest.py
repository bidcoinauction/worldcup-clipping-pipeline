#!/usr/bin/env python3
"""Phase 7 operator CLI — local Mac-native media ingestion.

Local-only: registers one authorized local media file as a project, runs the
smallest genuine FFmpeg-backed processing operation, verifies the artifact,
and emits the versioned Stadium Signal Lite integration contract.

Examples::

    # full run: source -> project -> verified artifact + contract
    python3 scripts/local_ingest.py run \\
        --source ~/FootballArchive/TESTS/capture_test.mp4 \\
        --pilot-id ss_lite_local_001 \\
        --out /tmp/ss_lite_local_001.contract.json

    # full run + opt-in OORT Storage upload (requires OORT_* env vars)
    python3 scripts/local_ingest.py run \\
        --source ~/FootballArchive/TESTS/capture_test.mp4 \\
        --pilot-id ss_lite_local_001 --upload --out /tmp/ss_lite_local_001.contract.json

    # check OORT connectivity/credentials without uploading anything
    python3 scripts/local_ingest.py storage-health

    # validate + register an existing intake only
    python3 scripts/local_ingest.py register --intake path/to/intake.json

    # re-verify a previously emitted contract
    python3 scripts/local_ingest.py verify --contract /tmp/ss_lite_local_001.contract.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import integration_service, local_ingest
from pipeline.config_errors import ConfigurationError
from pipeline.pilot import JobRecordError


def _cmd_run(args: argparse.Namespace) -> int:
    result = local_ingest.run_local_ingest(
        args.source,
        pilot_id=args.pilot_id,
        source_id=args.source_id,
        jobs_dir=args.jobs_dir,
        intake_path=args.intake,
        project=args.project,
        story_id=args.story_id,
        fmt=args.format,
        source_start=args.start,
        source_end=args.end,
        dry_run=args.dry_run,
        upload=args.upload,
        storage_bucket=args.storage_bucket,
        storage_object_prefix=args.object_prefix,
        storage_class=args.storage_class,
        storage_read_back=not args.no_read_back,
        require_storage=args.require_storage,
    )
    contract = result["contract"]
    errors = local_ingest.validate_integration_contract(contract)
    if errors:
        print("ERROR: produced contract failed self-validation:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    if args.out:
        out = Path(args.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(contract, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        print(f"contract written: {out}")

    storage = contract.get("storage") or {}
    print(json.dumps({
        "job_id": result["job_id"],
        "project_state": contract["project"]["current_state"],
        "render_status": contract["processing"]["status"],
        "artifact": contract["artifact"],
        "verified": contract["verification"]["verified"],
        "provenance_links_to_source": contract["provenance"]["links_to_source"],
        "contract_version": contract["contract_version"],
        "storage": {
            "status": storage.get("status"),
            "object_uri": storage.get("object_uri"),
            "verified": storage.get("verified"),
            "error": storage.get("error"),
        },
    }, indent=2))
    return 0 if contract["verification"]["verified"] else 1


def _cmd_register(args: argparse.Namespace) -> int:
    intake = json.loads(Path(args.intake).read_text(encoding="utf-8"))
    registration = local_ingest.register_local_source(
        intake, intake_path=args.intake, jobs_dir=args.jobs_dir, operator=args.operator,
    )
    print(json.dumps({
        "job_id": registration["job_id"],
        "project_id": registration["project_id"],
        "current_state": registration["current_state"],
        "rights_cleared": registration["report"]["rights_cleared"],
        "source": registration["source"],
    }, indent=2))
    return 0


def _cmd_verify(args: argparse.Namespace) -> int:
    contract = json.loads(Path(args.contract).read_text(encoding="utf-8"))
    errors = local_ingest.validate_integration_contract(contract)
    if errors:
        print("INVALID contract:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        return 1

    artifact = contract.get("artifact") or {}
    artifact_path = Path(artifact.get("path", ""))
    exists = artifact_path.exists()
    duration = None
    if exists:
        duration = local_ingest.probe_media(artifact_path).get("duration_seconds")

    source = contract.get("source") or {}
    source_path = Path(source.get("local_file_path", ""))
    source_ok = source_path.exists() and local_ingest.sha256_file(source_path) == source.get("sha256")

    report = {
        "contract_version": contract.get("contract_version"),
        "artifact_exists": exists,
        "artifact_duration_seconds": duration,
        "source_sha256_matches": source_ok,
        "provenance_job_id": (contract.get("provenance") or {}).get("job_id"),
        "storage_status": (contract.get("storage") or {}).get("status"),
        "storage_object_uri": (contract.get("storage") or {}).get("object_uri"),
    }
    print(json.dumps(report, indent=2))
    return 0 if (exists and duration and source_ok) else 1


def _cmd_storage_health(args: argparse.Namespace) -> int:
    adapter = integration_service.build_storage_adapter(timeout=args.timeout)
    if adapter is None:
        print(json.dumps({
            "ok": False,
            "status": "unavailable",
            "reason": "credentials_missing",
            "detail": (
                "Set OORT_ACCESS_KEY_ID and OORT_SECRET_ACCESS_KEY (and OORT_BUCKET) to enable "
                "OORT Storage. No credentials were found in the environment."
            ),
            "config": {
                "endpoint": integration_service.DEFAULT_OORT_ENDPOINT,
                "region": integration_service.DEFAULT_OORT_REGION,
                "bucket": None,
                "access_key_id_set": False,
                "secret_access_key_set": False,
            },
        }, indent=2))
        return 1

    report = adapter.health_check()
    report["config"] = adapter.config.redacted()
    print(json.dumps(report, indent=2))
    return 0 if report.get("ok") else 1


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local Mac-native media ingestion (Phase 7).")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Full run: source -> project -> verified artifact + contract.")
    run.add_argument("--source", required=True, help="Absolute path to the authorized local media file.")
    run.add_argument("--pilot-id", required=True, help="Slug-safe pilot/project identifier.")
    run.add_argument("--source-id", default=None, help="Slug-safe source identifier (default: file stem).")
    run.add_argument("--jobs-dir", default=None, help="Job-record root (default: data/pilot/jobs).")
    run.add_argument("--intake", default=None, help="Where to write the intake manifest.")
    run.add_argument("--project", default=local_ingest.DEFAULT_TEST_PROJECT,
                     help="Registered non-production project profile (default: basketball_sandbox).")
    run.add_argument("--story-id", default=local_ingest.DEFAULT_STORY_ID)
    run.add_argument("--format", default=local_ingest.DEFAULT_FORMAT, choices=["SHORT", "MEDIUM", "LONG"])
    run.add_argument("--start", type=float, default=0.0, help="Source window start (seconds).")
    run.add_argument("--end", type=float, default=2.0, help="Source window end (seconds).")
    run.add_argument("--dry-run", action="store_true", help="Validate and plan without running FFmpeg.")
    run.add_argument("--out", default=None, help="Write the integration contract JSON here.")
    run.add_argument("--upload", action="store_true",
                     help="Opt-in: upload the verified artifact to OORT Storage and verify it.")
    run.add_argument("--storage-bucket", default=None, help="Override OORT_BUCKET for this upload.")
    run.add_argument("--object-prefix", default=local_ingest.DEFAULT_STORAGE_OBJECT_PREFIX,
                     help="Object-key prefix for the upload (default: %(default)s).")
    run.add_argument("--storage-class", default=None, help="Optional OORT storage class header.")
    run.add_argument("--no-read-back", action="store_true",
                     help="Skip the GetObject read-back (content integrity is then unverified unless "
                          "the provider returns object metadata).")
    run.add_argument("--require-storage", action="store_true",
                     help="Fail the run when OORT is unconfigured or the upload fails.")
    run.set_defaults(func=_cmd_run)

    register = sub.add_parser("register", help="Validate + register an existing intake as a project.")
    register.add_argument("--intake", required=True)
    register.add_argument("--jobs-dir", default=None)
    register.add_argument("--operator", default=None)
    register.set_defaults(func=_cmd_register)

    verify = sub.add_parser("verify", help="Re-verify a previously emitted contract.")
    verify.add_argument("--contract", required=True)
    verify.set_defaults(func=_cmd_verify)

    health = sub.add_parser("storage-health", help="Check OORT Storage connectivity + credentials.")
    health.add_argument("--timeout", type=float, default=30.0, help="Request timeout in seconds.")
    health.set_defaults(func=_cmd_storage_health)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (local_ingest.LocalIngestError, JobRecordError, ConfigurationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
