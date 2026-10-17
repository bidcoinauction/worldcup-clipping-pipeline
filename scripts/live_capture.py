from __future__ import annotations

import argparse
import json
import signal
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import live_capture


_STOP = {"requested": False}


def _install_sigint_handler() -> None:
    def _handler(signum, frame):  # noqa: ARG001
        _STOP["requested"] = True
        print("\nStop requested; finalizing after the current segment...")

    try:
        signal.signal(signal.SIGINT, _handler)
    except (ValueError, OSError):
        pass


def _resolve_cli_source(args) -> dict:
    if args.test:
        return live_capture.resolve_source(live_capture.CAPTURE_KIND_SYNTHETIC)
    if args.ace_id:
        return live_capture.resolve_source(live_capture.CAPTURE_KIND_ACE, ace_id=args.ace_id, ace_host=args.ace_host)
    if args.url:
        kind = live_capture.CAPTURE_KIND_HLS if args.url.split("?")[0].endswith(".m3u8") else live_capture.CAPTURE_KIND_HTTP
        return live_capture.resolve_source(kind, url=args.url)
    if args.file:
        return live_capture.resolve_source(live_capture.CAPTURE_KIND_FILE, path=args.file)
    raise live_capture.LiveCaptureError("no source provided: use --ace-id, --url, --file, or --test")


def _cmd_start(args) -> int:
    try:
        source = _resolve_cli_source(args)
    except live_capture.LiveCaptureError as exc:
        print(f"error: {exc}")
        return 2

    staging_dir = Path(args.staging_dir) if args.staging_dir else live_capture.default_staging_dir()
    ready_dir = Path(args.ready_dir) if args.ready_dir else live_capture.default_ready_dir()
    segment_seconds = args.segment_minutes * 60

    _install_sigint_handler()
    try:
        result = live_capture.run_capture(
            source,
            match_id=args.match_id,
            staging_dir=staging_dir,
            ready_dir=ready_dir,
            segment_seconds=segment_seconds,
            mode=args.mode,
            output=args.output,
            max_attempts=args.max_attempts,
            poll_interval=args.poll_interval,
            dry_run=args.dry_run,
            verbose=args.verbose,
            test_duration=args.test_duration,
            stop_requested=lambda: _STOP["requested"],
        )
    except live_capture.LiveCaptureError as exc:
        print(f"error: {exc}")
        return 2

    if result.get("dry_run"):
        print(f"[dry-run] source: {source['reference']} (kind={source['kind']})")
        print(f"[dry-run] match_id: {live_capture.slugify(args.match_id)}")
        print(f"[dry-run] mode: {args.mode}")
        print(f"[dry-run] staging: {staging_dir}")
        print(f"[dry-run] ready: {ready_dir}")
        print(f"[dry-run] ffmpeg: {' '.join(result.get('argv', []))}")
        return 0

    print(f"session: {result.get('session_id')}")
    if result.get("manifest_path"):
        print(f"manifest: {result['manifest_path']}")
    print(f"state: {result.get('state')}")
    if result.get("mode") == "segment":
        finalized = [e for e in result.get("segments", []) if e.get("state") == live_capture.SEGMENT_FINALIZED]
        partial = [e for e in result.get("segments", []) if e.get("state") == live_capture.SEGMENT_PARTIAL]
        print(f"finalized segments: {len(finalized)}")
        if partial:
            print(f"partial segments (not complete): {len(partial)}")
        if result.get("error"):
            print(f"error: {result['error']}")
            return 1
    else:
        print(f"output: {result.get('output')} (exit {result.get('ffmpeg_exit_code')})")
    return 0


def _load_or_exit(path: str) -> dict:
    manifest = live_capture.load_manifest(path)
    if manifest is None:
        print(f"error: no readable manifest at {path}")
        sys.exit(2)
    return manifest


def _cmd_status(args) -> int:
    manifest = _load_or_exit(args.manifest)
    print(json.dumps(
        {
            "session_id": manifest.get("session_id"),
            "match_id": manifest.get("match_id"),
            "state": manifest.get("state"),
            "source": manifest.get("source"),
            "segment_seconds": manifest.get("segment_seconds"),
            "segments": [
                {"index": e.get("index"), "state": e.get("state"), "size_bytes": e.get("size_bytes"),
                 "duration_seconds": e.get("duration_seconds")}
                for e in manifest.get("segments", [])
            ],
            "recording": manifest.get("recording"),
        },
        indent=2,
    ))
    return 0


def _cmd_recover(args) -> int:
    manifest = _load_or_exit(args.manifest)
    staging_dir = Path(args.staging_dir) if args.staging_dir else Path(manifest.get("staging_dir"))
    ready_dir = Path(args.ready_dir) if args.ready_dir else Path(manifest.get("ready_dir"))
    report = live_capture.recover_session(manifest, staging_dir=staging_dir, ready_dir=ready_dir)
    live_capture.save_manifest(args.manifest, manifest)
    print(f"state: {report['state']}")
    print(f"finalized: {len(report['finalized'])}, partial: {len(report['partial'])}, skipped: {len(report['skipped'])}")
    for entry in report["partial"]:
        print(f"  partial (not complete): {entry['filename']} ({entry.get('reason')})")
    return 0


def _cmd_assemble(args) -> int:
    manifest = _load_or_exit(args.manifest)
    out_path = args.out or (live_capture.default_recordings_dir() / f"{manifest.get('match_id')}_{manifest.get('session_id')}.mp4")
    try:
        recording = live_capture.assemble_recording(manifest, out_path=out_path, dry_run=args.dry_run)
    except live_capture.LiveCaptureError as exc:
        print(f"error: {exc}")
        return 2
    if args.dry_run:
        print(f"[dry-run] {' '.join(recording['argv'])}")
        return 0
    live_capture.save_manifest(args.manifest, manifest)
    print(f"recording: {recording['path']}")
    print(f"segments: {recording['segment_count']}, duration: {recording['duration_seconds']}s, sha256: {recording['sha256']}")
    return 0


def _cmd_register(args) -> int:
    recording = {"path": args.recording, "filename": Path(args.recording).name}
    try:
        result = live_capture.register_recording(
            recording,
            pilot_id=args.pilot_id,
            jobs_dir=args.jobs_dir,
            intake_path=args.intake,
            upload=args.upload,
            source_id=args.source_id,
        )
    except live_capture.LiveCaptureError as exc:
        print(f"error: {exc}")
        return 2
    contract = result.get("contract", {})
    print(f"job_id: {result.get('job_id')}")
    print(f"contract verified: {contract.get('verification', {}).get('verified')}")
    print(f"artifact: {contract.get('artifact', {}).get('path')}")
    print(f"storage status: {contract.get('storage', {}).get('status')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Phase 7.2 reliable Mac-native live capture (segmented, durable, recoverable).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    start = sub.add_parser("start", help="Start a capture session (segmented by default).")
    start.add_argument("--match-id", required=True, help="Match slug for naming")
    src = start.add_mutually_exclusive_group()
    src.add_argument("--ace-id", help="Optional Ace Stream content id")
    src.add_argument("--url", help="Authorized HLS (.m3u8) or HTTP input URL")
    src.add_argument("--file", help="Local file input")
    src.add_argument("--test", action="store_true", help="Use a synthetic testsrc2 source")
    start.add_argument("--ace-host", default=live_capture.DEFAULT_ACE_HOST, help="Ace Stream proxy base URL")
    start.add_argument("--mode", choices=("segment", "full"), default="segment")
    start.add_argument("--segment-minutes", type=int, default=15)
    start.add_argument("--test-duration", type=int, default=120, help="Synthetic duration in seconds (--test)")
    start.add_argument("--staging-dir", help="Override staging dir (default: archive root/LIVE_SEGMENTS)")
    start.add_argument("--ready-dir", help="Override ready dir (default: archive root/LIVE_READY)")
    start.add_argument("--output", help="Output path for --mode full")
    start.add_argument("--max-attempts", type=int, default=live_capture.DEFAULT_MAX_ATTEMPTS)
    start.add_argument("--poll-interval", type=float, default=5.0)
    start.add_argument("--dry-run", action="store_true")
    start.add_argument("--verbose", action="store_true")
    start.set_defaults(func=_cmd_start)

    status = sub.add_parser("status", help="Print a capture session manifest summary.")
    status.add_argument("--manifest", required=True)
    status.set_defaults(func=_cmd_status)

    recover = sub.add_parser("recover", help="Recover a capture session after interruption.")
    recover.add_argument("--manifest", required=True)
    recover.add_argument("--staging-dir")
    recover.add_argument("--ready-dir")
    recover.set_defaults(func=_cmd_recover)

    assemble = sub.add_parser("assemble", help="Concatenate finalized segments into one recording.")
    assemble.add_argument("--manifest", required=True)
    assemble.add_argument("--out", help="Output recording path")
    assemble.add_argument("--dry-run", action="store_true")
    assemble.set_defaults(func=_cmd_assemble)

    register = sub.add_parser("register", help="Register a recording through Phase 7 local_ingest.")
    register.add_argument("--recording", required=True)
    register.add_argument("--pilot-id", required=True)
    register.add_argument("--source-id")
    register.add_argument("--jobs-dir")
    register.add_argument("--intake")
    register.add_argument("--upload", action="store_true", help="Opt-in OORT upload (off by default; never automatic)")
    register.set_defaults(func=_cmd_register)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
