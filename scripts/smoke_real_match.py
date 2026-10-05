"""Real-match smoke command for RC validation.

Exercises the canonical workflow across real service boundaries with stage
timing and reuse flags. ``--fast`` uses deterministic executors (real runtime
writes, no network). ``--full`` requires an operator-provided source and real
model/media dependencies; it reports blockers when they are unavailable.

Exit codes:
  0 = smoke success
  1 = workflow failure
  2 = environment/preflight failure
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline import runtime_db
from pipeline.smoke_service import run_smoke


def _fixture_source(output_dir: Path) -> Path:
    source = output_dir / "fixture_source.mp4"
    if not source.exists():
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(b"clipper smoke fixture media")
    return source


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Clipper real-match smoke.")
    parser.add_argument("--input", help="Local media file (optional; a fixture is used for --fast).")
    parser.add_argument("--profile", default="football")
    parser.add_argument("--fast", action="store_true", help="Deterministic fast smoke (default).")
    parser.add_argument("--full", action="store_true", help="Real long-media path (needs media + models).")
    parser.add_argument("--fail-after", choices=["source_validation", "transcription", "detection",
                                                "moment_normalization", "story", "edit_brief", "edl", "render"],
                        help="Deterministically fail after this stage (recovery validation).")
    parser.add_argument("--output-dir", default=str(runtime_db.default_runtime_db_path().parent / "smoke"))
    args = parser.parse_args(argv)

    if not args.input:
        if args.full:
            print("ERROR: --full requires --input (local media file).", file=sys.stderr)
            return 2
        source = _fixture_source(Path(args.output_dir))
        mode = "fast"
        smoke_mode = "FAST"
        source_type = "FIXTURE"
        real_media = False
    else:
        source = Path(args.input)
        if not source.exists():
            print(f"ERROR: source not found: {source}", file=sys.stderr)
            return 2
        mode = "full" if args.full else "fast"
        smoke_mode = "FULL" if args.full else "FAST"
        source_type = "OPERATOR_MEDIA"
        real_media = bool(args.full)

    jobs_dir = runtime_db.default_runtime_db_path().parent / "jobs"
    try:
        report = run_smoke(source_file=source, profile=args.profile, jobs_dir=jobs_dir,
                           mode=mode, fail_after=args.fail_after,
                           smoke_mode=smoke_mode, source_type=source_type, real_media=real_media)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: smoke preflight failed: {exc}", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) / report["smoke_run_id"]
    output_dir.mkdir(parents=True, exist_ok=True)
    report_path = output_dir / "smoke_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    print(report["report_text"])
    print(f"Report: {report_path}")
    result = report["timings"]["result"]
    if result == "PASS":
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())