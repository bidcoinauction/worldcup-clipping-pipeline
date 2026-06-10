#!/usr/bin/env python3
"""Export research window CSVs into actual MP4 clips via ffmpeg.

Reads STADIUM-style clip_windows CSV, resolves source files from
local raw directories, runs ffmpeg to cut clips, and writes an
export manifest CSV with editorial metadata preserved.
"""

from __future__ import annotations

import argparse
import csv
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Iterable

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.utils import ROOT, slugify, timestamp_to_seconds


DEFAULT_RAW_DIRS = [
    ROOT / "FootballArchive" / "RAW",
    Path("/Volumes/STADIUM/FootballArchive/RAW"),
]
DEFAULT_CLIPS_DIR = ROOT / "FootballArchive" / "CLIPS"
DEFAULT_MANIFEST_PATH = ROOT / "CLIP_MANIFESTS" / "researched_clip_exports.csv"


@dataclass
class ClipRow:
    clip_id: str
    match_title: str
    source_file: str
    start_time: str
    end_time: str
    moment_label: str
    emotional_angle: str
    platform: str
    export_profile: str


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export researched clip windows to MP4."
    )
    parser.add_argument(
        "--csv",
        required=True,
        help="Path to clip_windows CSV (STADIUM format).",
    )
    parser.add_argument(
        "--clips-dir",
        default=str(DEFAULT_CLIPS_DIR),
        help="Output directory for MP4 clips (default: FootballArchive/CLIPS).",
    )
    parser.add_argument(
        "--raw-dir",
        action="append",
        dest="raw_dirs",
        default=[],
        help="Directory to search for RAW source videos. May be repeated. "
        "Defaults to local FootballArchive/RAW then STADIUM volume RAW.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Path to write the export manifest CSV.",
    )
    parser.add_argument(
        "--profile",
        default="vertical_clean",
        choices=["vertical_clean", "vertical_blur", "vertical_safe", "source"],
        help="Default export profile when CSV row does not set export_profile.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print planned actions without running ffmpeg.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually run ffmpeg to export clips.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-export clips even when output already exists.",
    )
    return parser.parse_args(argv)


def log(step: str, message: str) -> None:
    print(f"{step.upper()}: {message}")


def parse_timestamp(value: str) -> float:
    return timestamp_to_seconds(value)


def read_rows(csv_path: Path, default_profile: str) -> list[ClipRow]:
    if not csv_path.exists():
        raise FileNotFoundError(f"Research CSV not found: {csv_path}")

    rows: list[ClipRow] = []
    with csv_path.open("r", newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for index, row in enumerate(reader, start=2):
            clip_id = (row.get("clip_id") or "").strip()
            match_title = (row.get("match_title") or row.get("match") or "").strip()
            source_file = (row.get("source_file") or row.get("source_video") or row.get("source") or "").strip()
            start_time = (row.get("start_time") or row.get("start") or "").strip()
            end_time = (row.get("end_time") or row.get("end") or "").strip()
            moment_label = (row.get("moment_label") or row.get("event") or row.get("title") or "").strip()
            emotional_angle = (row.get("emotional_angle") or row.get("hook") or "").strip()
            platform = (row.get("platform") or "shorts").strip()
            export_profile = (row.get("export_profile") or row.get("profile") or default_profile).strip()

            missing = [
                name
                for name, value in {
                    "clip_id": clip_id,
                    "match_title": match_title,
                    "source_file": source_file,
                    "start_time": start_time,
                    "end_time": end_time,
                }.items()
                if not value
            ]
            if missing:
                log("skip", f"row {index} missing required fields: {', '.join(missing)}")
                continue

            if parse_timestamp(end_time) <= parse_timestamp(start_time):
                log("skip", f"row {index}: end_time '{end_time}' not after start_time '{start_time}'")
                continue

            rows.append(ClipRow(
                clip_id=clip_id,
                match_title=match_title,
                source_file=source_file,
                start_time=start_time,
                end_time=end_time,
                moment_label=moment_label,
                emotional_angle=emotional_angle,
                platform=platform,
                export_profile=export_profile,
            ))
    return rows


def resolve_source(source_file: str, raw_dirs: list[Path]) -> Path:
    source = Path(source_file)
    if source.is_absolute() and source.exists():
        return source.resolve()

    for raw_dir in raw_dirs:
        candidate = raw_dir / source_file
        if candidate.exists():
            return candidate.resolve()

    for raw_dir in raw_dirs:
        matches = list(raw_dir.glob(source_file))
        if matches:
            return matches[0].resolve()

    raise FileNotFoundError(
        f"Source file not found in any RAW directory: {source_file}"
    )


def export_path(row: ClipRow, clips_root: Path) -> Path:
    match_slug = slugify(row.match_title)
    return clips_root / match_slug / f"{row.clip_id}.mp4"


def ffmpeg_filter(profile: str) -> list[str]:
    if profile == "source":
        return ["-c", "copy"]

    if profile == "vertical_clean":
        filtergraph = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=28:2[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
    elif profile == "vertical_safe":
        top = 0.14
        bottom = 0.02
        left = 0.0
        right = 0.08
        keep_h = max(0.50, 1.0 - top - bottom)
        keep_w = max(0.50, 1.0 - left - right)
        filtergraph = (
            f"[0:v]crop=trunc(iw*{keep_w:.4f}/2)*2:trunc(ih*{keep_h:.4f}/2)*2:"
            f"trunc(iw*{left:.4f}/2)*2:trunc(ih*{top:.4f}/2)*2,"
            "split=2[clean_a][clean_b];"
            "[clean_a]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=28:2[bg];"
            "[clean_b]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
    else:
        filtergraph = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=28:2[bg];"
            "[0:v]scale=1080:-2:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )

    return [
        "-filter_complex", filtergraph,
        "-map", "[v]",
        "-map", "0:a?",
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "20",
        "-c:a", "aac",
        "-b:a", "160k",
        "-movflags", "+faststart",
    ]


def export_clip(row: ClipRow, source: Path, destination: Path, force: bool) -> tuple[str, str]:
    if destination.exists() and not force:
        return "skipped", "already_exported"

    destination.parent.mkdir(parents=True, exist_ok=True)
    clip_duration = parse_timestamp(row.end_time) - parse_timestamp(row.start_time)

    command = [
        "ffmpeg",
        "-y" if force else "-n",
        "-hide_banner",
        "-loglevel", "error",
        "-ss", row.start_time,
        "-i", str(source),
        "-t", f"{clip_duration:.3f}",
        *ffmpeg_filter(row.export_profile),
        str(destination),
    ]

    completed = subprocess.run(command, check=False, capture_output=True, text=True)
    if completed.returncode != 0:
        stderr = (completed.stderr or "").strip()
        return "failed", stderr or "ffmpeg_failed"
    return "exported", ""


def append_manifest(rows: list[dict[str, str]], manifest_path: Path) -> None:
    manifest_path.parent.mkdir(parents=True, exist_ok=True)

    base_fieldnames = [
        "clip_id",
        "match_title",
        "source_file",
        "start_time",
        "end_time",
        "moment_label",
        "emotional_angle",
        "platform",
        "export_profile",
        "local_export_path",
        "status",
        "reason",
        "updated_at",
    ]

    existing: dict[str, dict[str, str]] = {}
    fieldnames = list(base_fieldnames)
    if manifest_path.exists():
        with manifest_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for fn in reader.fieldnames or []:
                if fn not in fieldnames:
                    fieldnames.append(fn)
            existing = {row["clip_id"]: row for row in reader}

    for row in rows:
        for fn in row:
            if fn not in fieldnames:
                fieldnames.append(fn)
        merged = dict(existing.get(row["clip_id"], {}))
        merged.update(row)
        existing[row["clip_id"]] = merged

    with manifest_path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(existing.values())
    log("manifest", f"updated {manifest_path}")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    csv_path = Path(args.csv)
    raw_dirs = [Path(d) for d in args.raw_dirs] if args.raw_dirs else DEFAULT_RAW_DIRS
    clips_root = Path(args.clips_dir)
    manifest_path = Path(args.manifest)

    rows = read_rows(csv_path, args.profile)
    log("found", f"{len(rows)} researched rows from {csv_path}")

    manifest_rows: list[dict[str, str]] = []
    summary = {"exported": 0, "skipped": 0, "failed": 0, "planned": 0}

    for row in rows:
        destination = export_path(row, clips_root)
        status = "planned"
        reason = ""

        try:
            source = resolve_source(row.source_file, raw_dirs)
        except FileNotFoundError as exc:
            status = "failed"
            reason = str(exc)
            log("failed", f"{row.clip_id}: {reason}")
            manifest_rows.append(build_manifest_row(row, destination, source=None, status=status, reason=reason))
            summary["failed"] += 1
            continue

        if args.dry_run:
            log("planned", f"{row.clip_id} -> {destination}")
            status = "planned"
            summary["planned"] += 1
        elif args.execute:
            status, reason = export_clip(row, source, destination, args.force)
            log(status, f"{row.clip_id} -> {destination}")
            if status == "exported":
                summary["exported"] += 1
            elif status == "skipped":
                summary["skipped"] += 1
            else:
                summary["failed"] += 1
        else:
            log("planned", f"{row.clip_id} -> {destination} (use --execute to export)")
            status = "planned"
            summary["planned"] += 1

        manifest_rows.append(build_manifest_row(row, destination, source, status, reason))

    append_manifest(manifest_rows, manifest_path)

    parts = [f"{k}={v}" for k, v in summary.items() if v > 0]
    log("done", f"{', '.join(parts)} — manifest: {manifest_path}")
    return 0 if summary.get("failed", 0) == 0 else 1


def build_manifest_row(
    row: ClipRow,
    destination: Path,
    source: Path | None,
    status: str,
    reason: str,
) -> dict[str, str]:
    return {
        "clip_id": row.clip_id,
        "match_title": row.match_title,
        "source_file": str(source) if source else row.source_file,
        "start_time": row.start_time,
        "end_time": row.end_time,
        "moment_label": row.moment_label,
        "emotional_angle": row.emotional_angle,
        "platform": row.platform,
        "export_profile": row.export_profile,
        "local_export_path": str(destination),
        "status": status,
        "reason": reason,
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
