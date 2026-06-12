import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.stadium_signal import archive_path


def _print_cmd(cmd: list[str]) -> str:
    return " ".join(str(x) for x in cmd)


def _run(cmd: list[str], dry_run: bool, step: str) -> bool:
    label = _print_cmd(cmd)
    if dry_run:
        print(f"  [dry-run] Would run: {label}")
        return True
    print(f"  Running: {label}")
    try:
        subprocess.run(cmd, check=True)
        return True
    except subprocess.CalledProcessError as exc:
        print(f"  FAILED: {step} (exit {exc.returncode})")
        return False


def main():
    parser = argparse.ArgumentParser(
        description="Concatenate recorded match sources and process through the existing pipeline."
    )
    parser.add_argument("--manifest", required=True,
                        help="Path to match manifest JSON")
    parser.add_argument("--run-detection", action="store_true",
                        help="Run clip detection after transcription (passed to process_scheduled_match.py)")
    parser.add_argument("--no-condense", action="store_true",
                        help="Disable transcript condensing in package mode")
    parser.add_argument("--overwrite", action="store_true",
                        help="Re-concat and re-process even if already verified")
    parser.add_argument("--mode", default=None, choices=("story", "micro", "package"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing")
    args = parser.parse_args()

    manifest_path = Path(args.manifest)
    if not manifest_path.exists():
        print(f"Error: manifest not found: {manifest_path}")
        raise SystemExit(1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    match_id = manifest["match_id"]
    match_no = manifest["match_no"]

    print(f"Manifest: {match_id} (match {match_no})")

    # ── Step 0: Collect recorded sources ──
    sources = [s for s in manifest["sources"] if s.get("status") == "recorded"]
    if not sources:
        print("Error: no sources with status 'recorded' found in manifest")
        raise SystemExit(1)

    print(f"  Sources to concat: {len(sources)}")
    for s in sources:
        print(f"    {s['label']}: {s['filename']}")

    if manifest.get("pipeline", {}).get("verified") and not args.overwrite:
        print("  Already verified. Use --overwrite to re-process.")

    # ── Step 1: Concatenate to RAW/WORLD_CUP ──
    concat_output = archive_path("RAW", "WORLD_CUP", f"{match_id}.ts")
    concat_output_path = Path(concat_output)

    # Build concat file listing all source paths
    concat_lines = []
    missing = []
    for s in sources:
        src_path = archive_path(s["filename"])
        if not Path(src_path).exists():
            missing.append(s["filename"])
            if not args.dry_run:
                continue
        concat_lines.append(f"file '{src_path}'")

    if missing and not args.dry_run:
        print(f"Error: source files not found: {', '.join(missing)}")
        raise SystemExit(1)

    concat_txt_path = None
    if not args.dry_run:
        tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".txt", delete=False, encoding="utf-8"
        )
        tmp.write("\n".join(concat_lines) + "\n")
        concat_txt_path = tmp.name
        tmp.close()
    else:
        print(f"  [dry-run] Concat sources to: {concat_output}")
        print(f"  [dry-run] Concat file would contain:")
        for line in concat_lines:
            print(f"    {line}")
        if missing:
            print(f"  [dry-run] WARNING: source files not found: {', '.join(missing)}")

    ffmpeg_cmd = [
        "ffmpeg", "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", concat_txt_path or "CONCAT.txt",
        "-c", "copy",
        concat_output,
    ]
    ok = _run(ffmpeg_cmd, args.dry_run, "concat")
    if not ok:
        raise SystemExit(1)

    if not args.dry_run:
        print(f"  Output: {concat_output} ({concat_output_path.stat().st_size} bytes)")

    # ── Step 2: Register in schedule CSV ──
    update_cmd = [
        sys.executable or "python", "scripts/update_match.py",
        "--match", str(match_no),
        "--source-video-path", concat_output,
        "--pipeline-status", "recorded",
    ]
    ok = _run(update_cmd, args.dry_run, "update_match")
    if not ok:
        raise SystemExit(1)

    # ── Step 3: Run pipeline ──
    process_cmd = [
        sys.executable or "python", "scripts/process_scheduled_match.py",
        "--match-no", str(match_no),
    ]
    if args.run_detection:
        process_cmd.append("--run-detection")
    if args.no_condense:
        process_cmd.append("--no-condense")
    if args.mode:
        process_cmd += ["--mode", args.mode]

    ok = _run(process_cmd, args.dry_run, "process_scheduled_match")
    if not ok:
        raise SystemExit(1)

    # ── Step 4: Update manifest status ──
    if not args.dry_run:
        manifest["pipeline"]["verified"] = True
        if args.run_detection:
            manifest["pipeline"]["transcribed"] = True
            manifest["pipeline"]["researched"] = True
            manifest["pipeline"]["clipped"] = True
        else:
            manifest["pipeline"]["transcribed"] = True
        manifest_path.write_text(
            json.dumps(manifest, indent=2) + "\n", encoding="utf-8"
        )
        print(f"  Manifest updated: {manifest_path}")
    else:
        print(f"  [dry-run] Would update manifest pipeline status")

    print(f"Done: match {match_no}")


if __name__ == "__main__":
    main()
