#!/usr/bin/env python3
"""Phase 7.4 operator CLI — end-to-end media workflow.

One command drives an authorized local (or synthetic) media source through the
existing Phase 7 services: register → render → verify → optional OORT upload →
catalog build → Stadium Signal Lite import + local preview, and writes a
durable per-pilot status document.

Examples::

    # full local workflow + Lite import/playback verification
    python3 scripts/media_workflow.py run \\
        --source ~/FootballArchive/TESTS/capture_test.mp4 \\
        --pilot-id ss_lite_wf_001 \\
        --lite-root ~/FootballArchive/ESPN/stadium-signal-lite \\
        --preview-roots ~/FootballArchive/ESPN/data

    # same run, opt-in OORT upload
    python3 scripts/media_workflow.py run \\
        --source ~/FootballArchive/TESTS/capture_test.mp4 \\
        --pilot-id ss_lite_wf_001 --upload \\
        --lite-root ~/FootballArchive/ESPN/stadium-signal-lite

    # inspect the durable status of the last run
    python3 scripts/media_workflow.py status \
        --pilot-id ss_lite_wf_001 --source-id capture_test
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import local_ingest, media_catalog, media_workflow
from pipeline.config_errors import ConfigurationError
from pipeline.pilot import JobRecordError


def _cmd_run(args: argparse.Namespace) -> int:
    try:
        report = media_workflow.run_media_workflow(
            args.source,
            pilot_id=args.pilot_id,
            source_id=args.source_id,
            jobs_dir=args.jobs_dir,
            contracts_dir=args.contracts_dir,
            catalog_out=args.catalog_out,
            sessions_dir=args.sessions_dir,
            reference_deployment=args.reference_deployment,
            intake_root=args.intake_root,
            operator=args.operator,
            project=args.project,
            story_id=args.story_id,
            fmt=args.format,
            source_start=args.start,
            source_end=args.end,
            dry_run=args.dry_run,
            reuse=not args.no_reuse,
            upload=args.upload,
            storage_bucket=args.storage_bucket,
            storage_object_prefix=args.object_prefix,
            storage_class=args.storage_class,
            storage_read_back=not args.no_read_back,
            require_storage=args.require_storage,
            lite_root=args.lite_root,
            lite_python=args.lite_python,
            preview_roots=args.preview_roots.split(":") if args.preview_roots else None,
            preview_enabled=args.preview_enabled,
            verify_library=not args.no_verify_library,
        )
    except (media_workflow.MediaWorkflowError, local_ingest.LocalIngestError,
            media_catalog.MediaCatalogError, JobRecordError, ConfigurationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        if args.no_reuse is False:
            print("hint: an interrupted run can usually be retried as-is (resume is automatic).", file=sys.stderr)
        return 1

    print(json.dumps({
        "workflow_schema": report["workflow_schema"],
        "pilot_id": report["pilot_id"],
        "job_id": report["job_id"],
        "reused": report["reused"],
        "resumed": report["resumed"],
        "verified": report["verified"],
        "artifact": report["artifact"],
        "storage": report["storage"],
        "catalog": report["catalog"],
        "library": report["library"],
        "status_path": str(media_workflow.workflow_status_path(
            args.pilot_id, args.jobs_dir, source_id=report["request"]["source_id"],
        )),
        "recommendations": report["recommendations"],
    }, indent=2))

    if report["dry_run"]:
        return 0
    if not report["verified"]:
        return 1
    library = report["library"]
    if library is not None and library.get("ran") and not library.get("ok"):
        return 1
    return 0


def _cmd_status(args: argparse.Namespace) -> int:
    try:
        path = media_workflow.workflow_status_path(
            args.pilot_id, args.jobs_dir, source_id=args.source_id,
        )
    except media_workflow.MediaWorkflowError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not path.is_file():
        print(f"no workflow status found: {path}", file=sys.stderr)
        return 1
    print(path.read_text(encoding="utf-8"), end="")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 7.4 end-to-end media workflow (source -> catalog -> Lite preview).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="Run the full workflow for one source.")
    run.add_argument("--source", required=True, help="Absolute path to the authorized local media file.")
    run.add_argument("--pilot-id", required=True, help="Slug-safe pilot/project identifier.")
    run.add_argument("--source-id", default=None, help="Slug-safe source identifier (default: file stem).")
    run.add_argument("--jobs-dir", default=None, help="Job-record root (default: data/pilot/jobs).")
    run.add_argument("--contracts-dir", default=None,
                     help="Where contracts are persisted/read (default: <jobs-dir>/contracts).")
    run.add_argument("--catalog-out", default=None,
                     help="Catalog output path (default: <jobs-dir>/library.catalog.json).")
    run.add_argument("--sessions-dir", default=None,
                     help="Live-capture staging dir to include in the catalog (*.manifest.json).")
    run.add_argument("--reference-deployment", default=media_catalog.DEFAULT_REFERENCE_DEPLOYMENT)
    run.add_argument("--intake-root", default=None,
                     help="Allowed source intake root for pilot confinement (default: none).")
    run.add_argument("--operator", default=None)
    run.add_argument("--project", default=local_ingest.DEFAULT_TEST_PROJECT)
    run.add_argument("--story-id", default=local_ingest.DEFAULT_STORY_ID)
    run.add_argument("--format", default=local_ingest.DEFAULT_FORMAT, choices=["SHORT", "MEDIUM", "LONG"])
    run.add_argument("--start", type=float, default=0.0)
    run.add_argument("--end", type=float, default=2.0)
    run.add_argument("--dry-run", action="store_true", help="Validate + plan only; no FFmpeg, no artifacts.")
    run.add_argument("--no-reuse", action="store_true",
                     help="Do not reuse an existing verified contract (forces ingest).")
    run.add_argument("--upload", action="store_true",
                     help="Opt-in: upload the verified artifact to OORT Storage and verify it.")
    run.add_argument("--storage-bucket", default=None)
    run.add_argument("--object-prefix", default=local_ingest.DEFAULT_STORAGE_OBJECT_PREFIX)
    run.add_argument("--storage-class", default=None)
    run.add_argument("--no-read-back", action="store_true")
    run.add_argument("--require-storage", action="store_true")
    run.add_argument("--lite-root", default=None,
                     help="Stadium Signal Lite checkout, to verify import + local preview.")
    run.add_argument("--lite-python", default=None,
                     help="Python interpreter for the Lite verification tool (default: this interpreter).")
    run.add_argument("--preview-roots", default=None,
                     help="Colon-separated media roots for Lite preview (default: <jobs-dir>).")
    run.add_argument("--preview-enabled", action="store_true",
                     help="Enable Lite local preview during verification.")
    run.add_argument("--no-verify-library", action="store_true",
                     help="Skip the Lite import/playback verification step.")
    run.set_defaults(func=_cmd_run)

    status = sub.add_parser("status", help="Print durable workflow status for a pilot/source pair.")
    status.add_argument("--pilot-id", required=True)
    status.add_argument("--source-id", required=True)
    status.add_argument("--jobs-dir", default=None)
    status.set_defaults(func=_cmd_status)

    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
