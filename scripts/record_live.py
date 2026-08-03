from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.utils import slugify


def build_ace_url(ace_id: str, ace_host: str = "http://localhost:6878") -> str:
    return f"{ace_host}/ace/getstream?id={ace_id}"


def segment_number_from_path(path: Path) -> int:
    return int(path.stem.split("_S")[-1])


def build_output_pattern(staging_dir: Path, match_id: str) -> str:
    return str(staging_dir / f"{match_id}_S%04d.ts")


def build_list_path(staging_dir: Path, match_id: str) -> str:
    return str(staging_dir / f"{match_id}_list.txt")


def build_ffmpeg_cmd(
    ace_url: str,
    output_pattern: str,
    segment_time: int,
    list_path: str,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-i", ace_url,
        "-c", "copy",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        "-segment_list", list_path,
        output_pattern,
    ]


def build_test_ffmpeg_cmd(
    output_pattern: str,
    segment_time: int,
    list_path: str,
    test_duration: int,
) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=duration={test_duration}:size=1280x720:rate=30",
        "-c:v", "libx264",
        "-preset", "ultrafast",
        "-crf", "28",
        "-f", "segment",
        "-segment_time", str(segment_time),
        "-reset_timestamps", "1",
        "-segment_list", list_path,
        output_pattern,
    ]


def build_full_ffmpeg_cmd(ace_url: str, output_path: str) -> list[str]:
    return [
        "ffmpeg",
        "-y",
        "-reconnect", "1",
        "-reconnect_at_eof", "1",
        "-reconnect_streamed", "1",
        "-reconnect_delay_max", "4294",
        "-err_detect", "ignore_err",
        "-i", ace_url,
        "-c", "copy",
        output_path,
    ]


def archive_root() -> str:
    return os.environ.get("FOOTBALL_ARCHIVE_ROOT") or (
        "C:\\FootballArchive" if os.name == "nt" else "FootballArchive"
    )


def archive_path(*parts: str) -> str:
    root = archive_root()
    if "\\" in root or ":" in root:
        from pathlib import PureWindowsPath
        return str(PureWindowsPath(root, *parts))
    return str(Path(root, *parts))


def write_status_json(
    segment_path: Path,
    match_id: str,
    acestream_id: str,
    segment_number: int,
    duration_seconds: float | None,
    exit_code: int | None,
) -> Path:
    size = segment_path.stat().st_size if segment_path.exists() else 0
    status = {
        "match_id": match_id,
        "acestream_id": acestream_id,
        "segment_number": segment_number,
        "filename": segment_path.name,
        "size_bytes": size,
        "duration_seconds": duration_seconds,
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "ffmpeg_exit_code": exit_code,
    }
    status_path = segment_path.with_suffix(".status.json")
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status_path


def finalize_segment(
    src: Path,
    ready_dir: Path,
    match_id: str,
    acestream_id: str,
    segment_number: int,
    duration_seconds: float | None = None,
) -> Path:
    ready_dir.mkdir(parents=True, exist_ok=True)
    dest = ready_dir / src.name
    src.rename(dest)
    write_status_json(dest, match_id, acestream_id, segment_number, duration_seconds, None)
    return dest


def read_segment_list(list_path: Path) -> set[str]:
    if not list_path.exists():
        return set()
    lines = list_path.read_text(encoding="utf-8").splitlines()
    return {Path(line.strip().replace("\\", "/")).name for line in lines if line.strip()}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Record a live Acestream match. Default mode writes a single .ts file with reconnect flags. "
                    "Use --mode segment for fixed-duration .ts segments.",
    )
    parser.add_argument("acestream_id", help="Acestream content hash / ID")
    parser.add_argument(
        "--match-id",
        required=True,
        help="Match slug for naming (e.g. argentina_france_2022_final)",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "segment"),
        default="full",
        help="Recording mode: full (single .ts, default) or segment (fixed-duration chunks)",
    )
    parser.add_argument(
        "--ace-host",
        default="http://localhost:6878",
        help="Acestream proxy base URL (default: http://localhost:6878)",
    )
    parser.add_argument(
        "--segment-minutes",
        type=int,
        default=15,
        help="Segment duration in minutes when --mode segment (default: 15)",
    )
    parser.add_argument(
        "--output",
        help="Output path for --mode full (default: FOOTBALL_ARCHIVE/<match_id>_live.ts)",
    )
    parser.add_argument(
        "--staging-dir",
        help="Override staging directory when --mode segment (default: FOOTBALL_ARCHIVE/LIVE_SEGMENTS)",
    )
    parser.add_argument(
        "--ready-dir",
        help="Override ready directory when --mode segment (default: FOOTBALL_ARCHIVE/LIVE_READY)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without executing",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print full ffmpeg command and show ffmpeg output on console",
    )
    parser.add_argument(
        "--test",
        action="store_true",
        help="Use synthetic video source (testsrc2) instead of Acestream",
    )
    parser.add_argument(
        "--test-duration",
        type=int,
        default=120,
        help="Total test duration in seconds (default: 120, only used with --test)",
    )

    args = parser.parse_args()
    match_id = slugify(args.match_id)
    ace_url = build_ace_url(args.acestream_id, args.ace_host)
    segment_seconds = args.segment_minutes * 60

    if args.mode == "full":
        output_path = Path(args.output or archive_path(f"{match_id}_live.ts"))
        cmd = build_full_ffmpeg_cmd(ace_url, str(output_path))

        if args.dry_run:
            print(f"[dry-run] Ace URL: {ace_url}")
            print(f"[dry-run] Match ID: {match_id}")
            print(f"[dry-run] Mode: full")
            print(f"[dry-run] Output: {output_path}")
            print(f"[dry-run] Would start ffmpeg recording")
            return

        output_path.parent.mkdir(parents=True, exist_ok=True)
        print(f"Recording {match_id} (full) -> {output_path}")
        print("Press Ctrl+C to stop gracefully.")

        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        signal.signal(signal.SIGINT, signal.SIG_IGN)

        try:
            process.wait()
        except KeyboardInterrupt:
            print("\nShutting down gracefully...")
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()

        ret = process.poll()
        if ret is not None:
            print(f"ffmpeg exited with code {ret}")
        return

    segment_seconds = args.segment_minutes * 60
    staging_dir = Path(args.staging_dir or archive_path("LIVE_SEGMENTS"))
    ready_dir = Path(args.ready_dir or archive_path("LIVE_READY"))

    if args.test:
        segment_seconds = 10

    if args.dry_run:
        print(f"[dry-run] Match ID: {match_id}")
        print(f"[dry-run] Mode: segment")
        print(f"[dry-run] Staging dir: {staging_dir}")
        print(f"[dry-run] Ready dir: {ready_dir}")
        if args.test:
            print(f"[dry-run] Test mode enabled (synthetic video via testsrc2)")
            print(f"[dry-run] Test duration: {args.test_duration}s")
            print(f"[dry-run] Segment duration: {segment_seconds}s")
            print(f"[dry-run] ffmpeg log: {staging_dir / f'{match_id}_ffmpeg.log'}")
        else:
            ace_url = build_ace_url(args.acestream_id, args.ace_host)
            print(f"[dry-run] Ace URL: {ace_url}")
            print(f"[dry-run] Segment duration: {segment_seconds}s ({args.segment_minutes} min)")
        if args.verbose:
            print(f"[dry-run] Verbose mode: full ffmpeg output shown on console")
        print(f"[dry-run] Would start ffmpeg recording")
        print(f"[dry-run] Would watch segment list for completed segments")
        print(f"[dry-run] Would move completed segments to {ready_dir}")
        print(f"[dry-run] Would write status JSON per segment")
        return

    staging_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)

    output_pattern = build_output_pattern(staging_dir, match_id)
    list_path = build_list_path(staging_dir, match_id)

    if args.test:
        cmd = build_test_ffmpeg_cmd(output_pattern, segment_seconds, list_path, args.test_duration)
        print(f"Test recording (synthetic video) as {match_id}")
    else:
        cmd = build_ffmpeg_cmd(ace_url, output_pattern, segment_seconds, list_path)
        print(f"Recording {match_id} (segments) from {ace_url}")
    print(f"Segments ({segment_seconds}s) -> {staging_dir} -> {ready_dir}")
    print("Press Ctrl+C to stop gracefully.")

    if args.verbose:
        print(f"[ffmpeg] {' '.join(cmd)}")

    stderr_fh = None
    if not args.verbose:
        log_path = staging_dir / f"{match_id}_ffmpeg.log"
        print(f"ffmpeg log: {log_path}")
        stderr_fh = open(log_path, "w", encoding="utf-8")

    process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=stderr_fh,
    )

    signal.signal(signal.SIGINT, signal.SIG_IGN)

    known_segments: set[str] = set()

    try:
        while True:
            ret = process.poll()
            if ret is not None:
                print(f"ffmpeg exited with code {ret}")
                break
            current = read_segment_list(Path(list_path))
            new = current - known_segments
            if new and args.verbose:
                print(f"[verbose] Segment list has {len(current)} entries, {len(new)} new")
            for name in sorted(new):
                src = staging_dir / name
                if not src.exists():
                    if args.verbose:
                        print(f"[verbose] {name} listed but not found at {src}")
                    continue
                seg_num = segment_number_from_path(src)
                print(f"Segment {seg_num:04d} complete: {name}")
                finalize_segment(src, ready_dir, match_id, args.acestream_id, seg_num)
            known_segments = current
            time.sleep(5)
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
    finally:
        remaining = sorted(staging_dir.glob(f"{match_id}_S*.ts"))
        for src in remaining:
            seg_num = segment_number_from_path(src)
            print(f"Finalizing remaining segment {seg_num:04d}: {src.name}")
            finalize_segment(src, ready_dir, match_id, args.acestream_id, seg_num)

        ret = process.poll()
        if ret is not None:
            print(f"ffmpeg exited with code {ret}")

        if stderr_fh is not None:
            stderr_fh.close()


if __name__ == "__main__":
    main()
