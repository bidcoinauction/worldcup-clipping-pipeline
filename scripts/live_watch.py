from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.utils import ROOT, slugify


REGISTRY_FILENAME = ".live_registry.json"
REGISTRY_VERSION = 1


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


def parse_segment_name(name: str) -> tuple[str, int] | None:
    stem = Path(name).stem
    if "_S" not in stem:
        return None
    match_id, num_str = stem.rsplit("_S", 1)
    if not num_str.isdigit():
        return None
    return match_id, int(num_str)


def load_registry(registry_path: Path) -> dict:
    if not registry_path.exists():
        return {"version": REGISTRY_VERSION, "segments": {}}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        if "segments" not in data:
            data["segments"] = {}
        data.setdefault("version", REGISTRY_VERSION)
        return data
    except (json.JSONDecodeError, OSError):
        return {"version": REGISTRY_VERSION, "segments": {}}


def save_registry(registry_path: Path, data: dict) -> None:
    data["version"] = REGISTRY_VERSION
    tmp = registry_path.with_suffix(".tmp.json")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(str(tmp), str(registry_path))


def find_ready_segments(
    watch_dir: Path,
    match_id_filter: str | None = None,
) -> list[Path]:
    segments = []
    for ts_path in sorted(watch_dir.glob("*.ts")):
        status_path = ts_path.with_suffix(".status.json")
        if not status_path.exists():
            continue
        parsed = parse_segment_name(ts_path.name)
        if parsed is None:
            continue
        seg_match_id, _seg_num = parsed
        if match_id_filter and seg_match_id != match_id_filter:
            continue
        segments.append(ts_path)
    return segments


def extract_audio(segment_path: Path, out_audio: Path) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(segment_path),
        "-vn",
        "-acodec", "aac",
        "-b:a", "192k",
        str(out_audio),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return out_audio


def write_transcript_outputs(
    transcript_dir: Path,
    full_text: str,
    segments: list[dict],
    metadata: dict,
) -> tuple[Path, Path, Path]:
    transcript_dir.mkdir(parents=True, exist_ok=True)
    txt_path = transcript_dir / "transcript.txt"
    txt_path.write_text(full_text, encoding="utf-8")
    ts_path = transcript_dir / "timestamps.json"
    ts_path.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    md_path = transcript_dir / "metadata.json"
    md_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return txt_path, ts_path, md_path


def transcribe_segment(
    segment_path: Path,
    status: dict,
    transcript_dir: Path,
    model_size: str,
    dry_run: bool = False,
) -> tuple[Path, Path, Path] | None:
    match_id = status.get("match_id", "unknown")
    seg_num = status.get("segment_number", 0)

    if dry_run:
        print(f"[dry-run] Would transcribe {segment_path.name}")
        print(f"[dry-run]   match_id: {match_id}, segment: {seg_num}")
        print(f"[dry-run]   output: {transcript_dir}")
        print(f"[dry-run]   model: {model_size}")
        return None

    from pipeline.whisper_transcriber import transcribe as whisper_transcribe

    audio_path = transcript_dir / f"{segment_path.stem}_audio.m4a"
    extract_audio(segment_path, audio_path)

    full_text, segs = whisper_transcribe(audio_path, model_size=model_size)

    audio_path.unlink(missing_ok=True)

    metadata = {
        "input": str(segment_path),
        "match_id": match_id,
        "segment_number": seg_num,
        "duration_seconds": status.get("duration_seconds"),
        "method": "live_segment",
        "model": model_size,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    return write_transcript_outputs(transcript_dir, full_text, segs, metadata)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Watch LIVE_READY for new segments and transcribe them.",
    )
    parser.add_argument(
        "--match-id",
        default=None,
        help="Optional filter: only process segments for this match slug",
    )
    parser.add_argument(
        "--watch-dir",
        help=f"Directory to watch (default: FOOTBALL_ARCHIVE/LIVE_READY)",
    )
    parser.add_argument(
        "--league",
        default="LIVE",
        help="League label for transcript output path (default: LIVE)",
    )
    parser.add_argument(
        "--model",
        default="base",
        help="Whisper model size (default: base)",
    )
    parser.add_argument(
        "--poll-interval",
        type=int,
        default=30,
        help="Seconds between polls (default: 30)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without executing",
    )

    args = parser.parse_args()

    watch_dir = Path(args.watch_dir or archive_path("LIVE_READY"))
    if not watch_dir.exists():
        print(f"Watch directory does not exist: {watch_dir}")
        sys.exit(1)

    registry_path = watch_dir / REGISTRY_FILENAME
    registry = load_registry(registry_path)

    orphaned = [
        name for name, info in registry.get("segments", {}).items()
        if info.get("state") in ("claimed", "transcribing")
    ]
    if orphaned:
        print(f"Found {len(orphaned)} previously orphaned segment(s) — will retry")
        for name in orphaned:
            print(f"  {name} ({registry['segments'][name].get('state')} at {registry['segments'][name].get('claimed_at', '?')})")
        registry["segments"] = {
            k: v for k, v in registry["segments"].items()
            if v.get("state") not in ("claimed", "transcribing")
        }
        save_registry(registry_path, registry)

    if args.dry_run:
        segments = find_ready_segments(watch_dir, args.match_id)
        if not segments:
            print("[dry-run] No ready segments found")
            return
        for ts_path in segments:
            status_path = ts_path.with_suffix(".status.json")
            status = json.loads(status_path.read_text(encoding="utf-8"))
            match_id = parse_segment_name(ts_path.name)[0]
            seg_num = parse_segment_name(ts_path.name)[1]
            transcript_dir = ROOT / "TRANSCRIPTS" / args.league / match_id / f"{match_id}_S{seg_num:04d}"
            print(f"[dry-run] Segment: {ts_path.name}")
            print(f"[dry-run]   match_id: {match_id}, segment: {seg_num}")
            print(f"[dry-run]   status: {status_path.name}")
            print(f"[dry-run]   transcript dir: {transcript_dir}")
        return

    print(f"Watching {watch_dir} (poll every {args.poll_interval}s)")
    print(f"League: {args.league}, Model: {args.model}")
    if args.match_id:
        print(f"Filter: match_id = {args.match_id}")

    while True:
        try:
            registry = load_registry(registry_path)
            processed = set(registry.get("segments", {}).keys())

            ready = find_ready_segments(watch_dir, args.match_id)
            pending = [p for p in ready if p.name not in processed]

            if pending:
                print(f"Found {len(pending)} new segment(s) to transcribe")

            for ts_path in pending:
                status_path = ts_path.with_suffix(".status.json")
                status = json.loads(status_path.read_text(encoding="utf-8"))
                match_id = parse_segment_name(ts_path.name)[0]
                seg_num = parse_segment_name(ts_path.name)[1]
                transcript_dir = ROOT / "TRANSCRIPTS" / args.league / match_id / f"{match_id}_S{seg_num:04d}"

                registry["segments"][ts_path.name] = {
                    "state": "claimed",
                    "match_id": match_id,
                    "segment_number": seg_num,
                    "claimed_at": datetime.now(timezone.utc).isoformat(),
                    "completed_at": None,
                }
                save_registry(registry_path, registry)

                registry["segments"][ts_path.name]["state"] = "transcribing"
                save_registry(registry_path, registry)

                print(f"Transcribing {ts_path.name} (segment {seg_num})...")
                try:
                    transcribe_segment(ts_path, status, transcript_dir, args.model)
                except Exception as exc:
                    print(f"ERROR transcribing {ts_path.name}: {exc}")
                    registry["segments"][ts_path.name]["state"] = "failed"
                    registry["segments"][ts_path.name]["error"] = str(exc)
                    save_registry(registry_path, registry)
                    continue

                registry["segments"][ts_path.name]["state"] = "transcribed"
                registry["segments"][ts_path.name]["completed_at"] = datetime.now(timezone.utc).isoformat()
                save_registry(registry_path, registry)
                print(f"Done: {ts_path.name} -> {transcript_dir}")

            time.sleep(args.poll_interval)
        except KeyboardInterrupt:
            print("\nShutting down.")
            break


if __name__ == "__main__":
    main()
