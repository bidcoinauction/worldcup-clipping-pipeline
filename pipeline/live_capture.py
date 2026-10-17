"""Phase 7.2 — reliable Mac-native live capture.

An *additive* capture layer that records authorized live video (HLS/HTTP, a
local file, a synthetic FFmpeg source, or — optionally — an Ace Stream) into
durable, integrity-checked segments, recovers safely from interruption, and
hands a completed recording to the Phase 7 local-ingestion service.

Design goals (see ``docs/phase-7/LIVE_CAPTURE.md``):

* **Transport is separate from the engine.** ``resolve_source`` builds the
  FFmpeg input for a *source kind* (``ace`` / ``hls`` / ``http`` / ``file`` /
  ``synthetic``); ``build_segment_argv`` / ``build_full_argv`` build the
  recording command. Ace Stream is one optional input kind, never a
  requirement. No Docker, no database, no mandatory network.
* **Durable manifests.** A session manifest (atomic JSON) records every
  segment's index, state, size, checksum, duration, and timestamps. The
  manifest — not a transient list file — is the source of truth.
* **Integrity before completion.** A segment is only finalized once it is
  non-empty and its duration is readable. A completion marker is written
  alongside it. Partially written segments are never treated as complete.
* **Interruption recovery + idempotence.** ``recover_session`` re-scans a
  staging directory and finalizes any valid leftovers without ever moving a
  segment twice (duplicate suppression by manifest index).
* **Opt-in delivery.** ``assemble_recording`` concatenates finalized segments
  into one file and ``register_recording`` hands it to
  :mod:`pipeline.local_ingest`. Uploading to OORT remains optional, private,
  and verified; full-length media is never uploaded automatically.

Public surface:

* :func:`resolve_source` — build a transport-agnostic source descriptor.
* :func:`build_ace_url` — the (optional) Ace Stream input URL.
* :func:`build_segment_argv` / :func:`build_full_argv` — the recording engine.
* :func:`build_synthetic_segment_argv` / :func:`build_synthetic_full_argv`.
* :func:`new_manifest` / :func:`load_manifest` / :func:`save_manifest`.
* :func:`validate_segment` — non-empty + readable-duration integrity check.
* :func:`finalize_segment` — idempotent, marker-writing finalize.
* :func:`recover_session` — safe interruption recovery.
* :func:`run_capture` — the supervised capture loop with bounded retries.
* :func:`assemble_recording` — concatenate finalized segments.
* :func:`register_recording` — register through Phase 7 ``local_ingest``.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import shutil
import subprocess
import time
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .configurator import resolve_archive_path, resolve_archive_root
from .utils import slugify


# ── Identity ─────────────────────────────────────────────────────────────────

CAPTURE_KIND_ACE = "ace"
CAPTURE_KIND_HLS = "hls"
CAPTURE_KIND_HTTP = "http"
CAPTURE_KIND_FILE = "file"
CAPTURE_KIND_SYNTHETIC = "synthetic"
CAPTURE_KINDS = (
    CAPTURE_KIND_ACE,
    CAPTURE_KIND_HLS,
    CAPTURE_KIND_HTTP,
    CAPTURE_KIND_FILE,
    CAPTURE_KIND_SYNTHETIC,
)
_NETWORK_KINDS = (CAPTURE_KIND_ACE, CAPTURE_KIND_HLS, CAPTURE_KIND_HTTP)

MANIFEST_VERSION = 1
MANIFEST_SUFFIX = ".manifest.json"
COMPLETION_SUFFIX = ".complete.json"
STATUS_SUFFIX = ".status.json"

DEFAULT_ACE_HOST = "http://localhost:6878"
DEFAULT_SEGMENT_SECONDS = 900
DEFAULT_MAX_ATTEMPTS = 3

# Bounded reconnect behaviour for network inputs (mirrors the legacy full-mode
# command). These are the only retries the engine issues by itself.
RECONNECT_FLAGS = [
    "-reconnect", "1",
    "-reconnect_at_eof", "1",
    "-reconnect_streamed", "1",
    "-reconnect_delay_max", "4294",
    "-err_detect", "ignore_err",
]

# Segment lifecycle states.
SEGMENT_WRITING = "writing"
SEGMENT_COMPLETE = "complete"
SEGMENT_FINALIZED = "finalized"
SEGMENT_PARTIAL = "partial"

# Session lifecycle states.
SESSION_RECORDING = "recording"
SESSION_STOPPED = "stopped"
SESSION_INTERRUPTED = "interrupted"
SESSION_RECOVERED = "recovered"
SESSION_FAILED = "failed"
SESSION_ASSEMBLED = "assembled"
SESSION_REGISTERED = "registered"

# Default archive subdirectories (resolved under the documented archive root).
STAGING_SUBDIR = "LIVE_SEGMENTS"
READY_SUBDIR = "LIVE_READY"
RECORDINGS_SUBDIR = "LIVE_RECORDINGS"


class LiveCaptureError(Exception):
    """Raised for expected live-capture failures."""


# ── Archive root (single documented normalization point) ─────────────────────


def archive_root() -> str:
    """The documented archive root: ``FOOTBALL_ARCHIVE_ROOT`` or the platform
    default (see :mod:`pipeline.configurator`). All default capture paths hang
    off this so recordings are never scattered."""
    return resolve_archive_root()


def archive_path(*parts: str) -> str:
    return resolve_archive_path(*parts)


def default_staging_dir() -> Path:
    return Path(archive_path(STAGING_SUBDIR))


def default_ready_dir() -> Path:
    return Path(archive_path(READY_SUBDIR))


def default_recordings_dir() -> Path:
    return Path(archive_path(RECORDINGS_SUBDIR))


def normalize_path(path: str | Path) -> str:
    """Absolute, normalized string form of *path* (no symlink resolution)."""
    return str(Path(path).expanduser().absolute())


# ── Secrets hygiene ──────────────────────────────────────────────────────────


def redact_url(url: str) -> str:
    """Strip credentials and query strings from a URL for safe logging."""
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<redacted-url>"
    if not parts.scheme:
        return "<redacted-url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    return urlunsplit((parts.scheme, netloc, parts.path, "", ""))


# ── Transport resolution (separated from the engine) ─────────────────────────


def build_ace_url(ace_id: str, ace_host: str = DEFAULT_ACE_HOST) -> str:
    return f"{ace_host}/ace/getstream?id={ace_id}"


def is_network_kind(kind: str) -> bool:
    return kind in _NETWORK_KINDS


def is_network_input(input_ref: str | None) -> bool:
    """True only for real network protocols. Reconnect flags are HTTP/RTSP
    options and must never be passed to a local file/playlist input."""
    if not input_ref:
        return False
    return str(input_ref).lower().startswith(("http://", "https://", "rtmp://", "rtmps://", "rtsp://"))


def resolve_source(
    kind: str,
    *,
    ace_id: str | None = None,
    ace_host: str = DEFAULT_ACE_HOST,
    url: str | None = None,
    path: str | Path | None = None,
    verify_exists: bool = True,
) -> dict:
    """Build a transport-agnostic source descriptor.

    Returns ``{"kind", "input", "reference"}`` where ``input`` is the FFmpeg
    input (or ``None`` for the synthetic kind) and ``reference`` is a
    human-readable, secret-free label safe to store in a manifest or log.
    """
    if kind not in CAPTURE_KINDS:
        raise LiveCaptureError(f"unknown capture kind {kind!r}; expected one of {CAPTURE_KINDS}")

    if kind == CAPTURE_KIND_ACE:
        if not ace_id:
            raise LiveCaptureError("ace source requires an ace_id (content id)")
        return {"kind": kind, "input": build_ace_url(ace_id, ace_host), "reference": f"ace:{ace_id}"}

    if kind in (CAPTURE_KIND_HLS, CAPTURE_KIND_HTTP):
        if not url:
            raise LiveCaptureError(f"{kind} source requires a url")
        return {"kind": kind, "input": url, "reference": redact_url(url)}

    if kind == CAPTURE_KIND_FILE:
        if not path:
            raise LiveCaptureError("file source requires a path")
        target = Path(path).expanduser()
        if verify_exists and not target.exists():
            raise LiveCaptureError(f"file source not found: {target}")
        return {"kind": kind, "input": str(target), "reference": normalize_path(target)}

    return {"kind": CAPTURE_KIND_SYNTHETIC, "input": None, "reference": "synthetic:testsrc2"}


# ── Recording engine (transport-agnostic) ────────────────────────────────────


def build_segment_argv(
    input_ref: str,
    output_pattern: str,
    *,
    segment_seconds: int,
    list_path: str | None = None,
    reconnect: bool = False,
    reset_timestamps: bool = True,
    start_number: int | None = None,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build the general segmented-recording command for any input."""
    argv = [ffmpeg_bin, "-y"]
    if reconnect:
        argv += list(RECONNECT_FLAGS)
    argv += ["-i", input_ref, "-c", "copy", "-f", "segment", "-segment_time", str(segment_seconds)]
    if reset_timestamps:
        argv += ["-reset_timestamps", "1"]
    if start_number is not None and start_number > 0:
        argv += ["-start_number", str(start_number)]
    if list_path:
        argv += ["-segment_list", list_path]
    argv.append(output_pattern)
    return argv


def build_full_argv(
    input_ref: str,
    output_path: str,
    *,
    reconnect: bool = True,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Build the general single-file recording command for any input."""
    argv = [ffmpeg_bin, "-y"]
    if reconnect:
        argv += list(RECONNECT_FLAGS)
    argv += ["-i", input_ref, "-c", "copy", output_path]
    return argv


def build_synthetic_segment_argv(
    output_pattern: str,
    *,
    segment_seconds: int,
    list_path: str | None = None,
    duration: int = 120,
    size: str = "1280x720",
    rate: int = 30,
    preset: str = "ultrafast",
    crf: int = 28,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Synthetic (``testsrc2``) segmented recorder — no network, no Ace."""
    argv = [
        ffmpeg_bin, "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=duration={duration}:size={size}:rate={rate}",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        "-f", "segment",
        "-segment_time", str(segment_seconds),
        "-reset_timestamps", "1",
    ]
    if list_path:
        argv += ["-segment_list", list_path]
    argv.append(output_pattern)
    return argv


def build_synthetic_full_argv(
    output_path: str,
    *,
    duration: int = 120,
    size: str = "1280x720",
    rate: int = 30,
    preset: str = "ultrafast",
    crf: int = 28,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Synthetic single-file recorder."""
    return [
        ffmpeg_bin, "-y",
        "-f", "lavfi",
        "-i", f"testsrc2=duration={duration}:size={size}:rate={rate}",
        "-c:v", "libx264",
        "-preset", preset,
        "-crf", str(crf),
        output_path,
    ]


def build_record_argv(
    source: dict,
    *,
    output: str,
    mode: str = "segment",
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    list_path: str | None = None,
    start_number: int | None = None,
    test_duration: int = 120,
    ffmpeg_bin: str = "ffmpeg",
) -> list[str]:
    """Dispatch to the right engine command for a resolved source + mode."""
    kind = source["kind"]
    if mode == "full":
        if kind == CAPTURE_KIND_SYNTHETIC:
            return build_synthetic_full_argv(output, duration=test_duration, ffmpeg_bin=ffmpeg_bin)
        return build_full_argv(source["input"], output, reconnect=is_network_input(source.get("input")), ffmpeg_bin=ffmpeg_bin)

    if kind == CAPTURE_KIND_SYNTHETIC:
        return build_synthetic_segment_argv(
            output, segment_seconds=segment_seconds, list_path=list_path, duration=test_duration,
            ffmpeg_bin=ffmpeg_bin,
        )
    return build_segment_argv(
        source["input"], output,
        segment_seconds=segment_seconds, list_path=list_path,
        reconnect=is_network_input(source.get("input")), start_number=start_number, ffmpeg_bin=ffmpeg_bin,
    )


# ── Naming / list helpers ────────────────────────────────────────────────────


def segment_filename(match_id: str, index: int) -> str:
    return f"{match_id}_S{index:04d}.ts"


def build_output_pattern(staging_dir: Path, match_id: str) -> str:
    return str(Path(staging_dir) / f"{match_id}_S%04d.ts")


def build_list_path(staging_dir: Path, match_id: str, attempt: int = 0) -> str:
    suffix = "" if attempt <= 0 else f"_attempt{attempt}"
    return str(Path(staging_dir) / f"{match_id}_list{suffix}.txt")


def segment_number_from_path(path: Path) -> int:
    return int(Path(path).stem.split("_S")[-1])


def segment_number_from_name(name: str) -> int | None:
    stem = Path(name).stem
    if "_S" not in stem:
        return None
    num = stem.rsplit("_S", 1)[-1]
    return int(num) if num.isdigit() else None


def read_segment_list(list_path: Path) -> set[str]:
    path = Path(list_path)
    if not path.exists():
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {Path(line.strip().replace("\\", "/")).name for line in lines if line.strip()}


# ── Checksums / status / markers ─────────────────────────────────────────────


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def completion_marker_path(segment_path: Path) -> Path:
    return Path(segment_path).with_suffix(COMPLETION_SUFFIX)


def has_completion_marker(segment_path: Path) -> bool:
    marker = completion_marker_path(segment_path)
    if not marker.exists():
        return False
    try:
        return bool(json.loads(marker.read_text(encoding="utf-8")).get("complete"))
    except (json.JSONDecodeError, OSError):
        return False


def build_status(
    segment_path: Path,
    *,
    match_id: str,
    source_reference: str,
    segment_number: int,
    duration_seconds: float | None,
    exit_code: int | None = None,
) -> dict:
    path = Path(segment_path)
    size = path.stat().st_size if path.exists() else 0
    return {
        "match_id": match_id,
        "acestream_id": source_reference,
        "source_reference": source_reference,
        "segment_number": segment_number,
        "filename": path.name,
        "size_bytes": size,
        "duration_seconds": duration_seconds,
        "recorded_at": now_iso(),
        "ffmpeg_exit_code": exit_code,
    }


def write_status_json(
    segment_path: Path,
    match_id: str,
    acestream_id: str,
    segment_number: int,
    duration_seconds: float | None,
    exit_code: int | None = None,
) -> Path:
    status = build_status(
        segment_path, match_id=match_id, source_reference=acestream_id,
        segment_number=segment_number, duration_seconds=duration_seconds, exit_code=exit_code,
    )
    status_path = Path(segment_path).with_suffix(STATUS_SUFFIX)
    status_path.write_text(json.dumps(status, indent=2), encoding="utf-8")
    return status_path


def write_completion_marker(segment_path: Path, *, sha256: str | None, size_bytes: int) -> Path:
    marker = completion_marker_path(segment_path)
    marker.write_text(
        json.dumps(
            {
                "complete": True,
                "filename": Path(segment_path).name,
                "sha256": sha256,
                "size_bytes": size_bytes,
                "completed_at": now_iso(),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return marker


# ── Integrity ────────────────────────────────────────────────────────────────


def probe_duration(path: str | Path) -> float | None:
    """Read-only duration via ffprobe. Returns ``None`` when unreadable or
    when ffprobe is unavailable (never raises)."""
    target = Path(path)
    if not shutil.which("ffprobe") or not target.exists():
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(target)],
            capture_output=True, text=True, shell=False,
        )
    except OSError:
        return None
    if result.returncode != 0:
        return None
    try:
        value = json.loads(result.stdout).get("format", {}).get("duration")
    except (json.JSONDecodeError, AttributeError):
        return None
    return float(value) if value not in (None, "") else None


def validate_segment(path: str | Path, *, min_bytes: int = 1, probe: bool = True) -> dict:
    """Integrity check that a segment is genuinely complete.

    A segment is valid only when it exists, is at least *min_bytes* large, and
    its duration is readable. Duration probing is skipped (size-only) when
    ffprobe is unavailable, so the check still works offline without it.
    """
    target = Path(path)
    if not target.exists() or not target.is_file():
        return {"valid": False, "reason": "missing", "size_bytes": 0, "duration_seconds": None}
    size = target.stat().st_size
    if size < min_bytes:
        return {"valid": False, "reason": "empty", "size_bytes": size, "duration_seconds": None}
    duration = None
    if probe and shutil.which("ffprobe"):
        duration = probe_duration(target)
        if duration is None:
            return {"valid": False, "reason": "unreadable_duration", "size_bytes": size, "duration_seconds": None}
    return {"valid": True, "reason": "ok", "size_bytes": size, "duration_seconds": duration}


# ── Manifest ─────────────────────────────────────────────────────────────────


def default_session_id(match_id: str, when: _dt.datetime | None = None) -> str:
    stamp = (when or _dt.datetime.now(_dt.timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    return f"{slugify(match_id)}_{stamp}"


def manifest_path(staging_dir: Path, session_id: str) -> Path:
    return Path(staging_dir) / f"{session_id}{MANIFEST_SUFFIX}"


def _atomic_write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, sort_keys=False), encoding="utf-8")
    os.replace(str(tmp), str(path))


def new_manifest(
    *,
    session_id: str,
    match_id: str,
    source: dict,
    segment_seconds: int,
    staging_dir: Path,
    ready_dir: Path,
    mode: str = "segment",
    started_at: str | None = None,
) -> dict:
    return {
        "manifest_version": MANIFEST_VERSION,
        "session_id": session_id,
        "match_id": slugify(match_id),
        "mode": mode,
        "state": SESSION_RECORDING,
        "source": {
            "kind": source.get("kind"),
            "reference": source.get("reference"),
        },
        "segment_seconds": segment_seconds,
        "staging_dir": normalize_path(staging_dir),
        "ready_dir": normalize_path(ready_dir),
        "started_at": started_at or now_iso(),
        "updated_at": now_iso(),
        "stopped_at": None,
        "recovered_at": None,
        "attempts": 0,
        "ffmpeg_exit_code": None,
        "error": None,
        "segments": [],
    }


def load_manifest(path: str | Path) -> dict | None:
    """Tolerant manifest load. Returns ``None`` when missing or corrupt."""
    target = Path(path)
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or "segments" not in data:
        return None
    data.setdefault("manifest_version", MANIFEST_VERSION)
    return data


def save_manifest(path: str | Path, manifest: dict) -> Path:
    manifest["manifest_version"] = MANIFEST_VERSION
    manifest["updated_at"] = now_iso()
    target = Path(path)
    _atomic_write_json(target, manifest)
    return target


def find_segment_entry(manifest: dict, index: int) -> dict | None:
    for entry in manifest.get("segments", []):
        if entry.get("index") == index:
            return entry
    return None


def upsert_segment_entry(manifest: dict, entry: dict) -> dict:
    existing = find_segment_entry(manifest, entry["index"])
    if existing is None:
        manifest.setdefault("segments", []).append(entry)
    else:
        existing.update(entry)
    manifest.setdefault("segments", []).sort(key=lambda e: e.get("index", 0))
    return entry


# ── Finalize / recover ───────────────────────────────────────────────────────


def finalize_segment(
    manifest: dict,
    *,
    src: Path,
    ready_dir: Path,
    index: int | None = None,
    duration_seconds: float | None = None,
    record: bool = True,
    probe: bool = True,
) -> dict:
    """Finalize one segment: validate, move to *ready_dir*, write status +
    completion marker, and record it in the manifest.

    Idempotent: a segment already finalized in the manifest is returned
    unchanged (``skipped=True``) and is never moved twice. Invalid (partial)
    segments are marked ``partial`` and left in staging.
    """
    src = Path(src)
    ready_dir = Path(ready_dir)
    if index is None:
        index = segment_number_from_path(src)

    existing = find_segment_entry(manifest, index)
    if existing and existing.get("state") == SEGMENT_FINALIZED and existing.get("ready_path") == normalize_path(ready_dir / src.name):
        return {**existing, "skipped": True}

    check = validate_segment(src, probe=probe)
    if not check["valid"]:
        entry = {
            "index": index,
            "filename": src.name,
            "state": SEGMENT_PARTIAL,
            "reason": check["reason"],
            "size_bytes": check["size_bytes"],
            "sha256": None,
            "duration_seconds": None,
            "staging_path": normalize_path(src),
            "ready_path": None,
            "status_path": None,
            "marker_path": None,
            "finalized_at": None,
            "updated_at": now_iso(),
        }
        if record:
            upsert_segment_entry(manifest, entry)
        return {**entry, "skipped": False}

    ready_dir.mkdir(parents=True, exist_ok=True)
    dest = ready_dir / src.name
    if dest.exists() and dest.stat().st_size == check["size_bytes"] and has_completion_marker(dest):
        src.unlink(missing_ok=True)
    elif dest.exists() and dest.stat().st_size == check["size_bytes"]:
        src.unlink(missing_ok=True)
    else:
        os.replace(str(src), str(dest))

    duration = duration_seconds if duration_seconds is not None else check["duration_seconds"]
    checksum = sha256_file(dest)
    status_path = write_status_json(
        dest, manifest.get("match_id", dest.stem), manifest.get("source", {}).get("reference", ""),
        index, duration, None,
    )
    marker_path = write_completion_marker(dest, sha256=checksum, size_bytes=check["size_bytes"])

    entry = {
        "index": index,
        "filename": dest.name,
        "state": SEGMENT_FINALIZED,
        "reason": "ok",
        "size_bytes": check["size_bytes"],
        "sha256": checksum,
        "duration_seconds": duration,
        "staging_path": normalize_path(src),
        "ready_path": normalize_path(dest),
        "status_path": normalize_path(status_path),
        "marker_path": normalize_path(marker_path),
        "finalized_at": now_iso(),
        "updated_at": now_iso(),
    }
    if record:
        upsert_segment_entry(manifest, entry)
    return {**entry, "skipped": False}


def discover_staging_segments(staging_dir: Path, match_id: str | None = None) -> list[Path]:
    """Sorted ``.ts`` segments in a staging directory (optionally filtered)."""
    staging_dir = Path(staging_dir)
    if not staging_dir.exists():
        return []
    results = []
    for candidate in staging_dir.glob("*.ts"):
        index = segment_number_from_name(candidate.name)
        if index is None:
            continue
        if match_id and not candidate.name.startswith(f"{match_id}_S"):
            continue
        results.append((index, candidate))
    return [path for _, path in sorted(results, key=lambda pair: pair[0])]


def recover_session(
    manifest: dict,
    *,
    staging_dir: Path,
    ready_dir: Path,
    probe: bool = True,
) -> dict:
    """Recover a session after interruption.

    Re-scans *staging_dir* and finalizes every valid leftover segment exactly
    once; invalid (partial) segments are marked partial and never treated as
    complete. Safe to run repeatedly (idempotent). Sets the session state to
    ``recovered`` when it had been recording/interrupted.
    """
    match_id = manifest.get("match_id")
    finalized: list[dict] = []
    partial: list[dict] = []
    skipped: list[dict] = []

    for src in discover_staging_segments(Path(staging_dir), match_id):
        index = segment_number_from_path(src)
        entry = finalize_segment(manifest, src=src, ready_dir=ready_dir, index=index, probe=probe)
        if entry.get("skipped"):
            skipped.append(entry)
        elif entry.get("state") == SEGMENT_FINALIZED:
            finalized.append(entry)
        else:
            partial.append(entry)

    if manifest.get("state") in (SESSION_RECORDING, SESSION_INTERRUPTED, SESSION_STOPPED):
        manifest["state"] = SESSION_RECOVERED
        manifest["recovered_at"] = now_iso()

    return {
        "session_id": manifest.get("session_id"),
        "state": manifest.get("state"),
        "finalized": finalized,
        "partial": partial,
        "skipped": skipped,
    }


# ── Supervised capture loop ──────────────────────────────────────────────────


def _terminate(process) -> None:
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()


def _next_index(manifest: dict) -> int:
    finalized = [e["index"] for e in manifest.get("segments", []) if e.get("state") == SEGMENT_FINALIZED]
    return (max(finalized) + 1) if finalized else 0


def run_capture(
    source: dict,
    *,
    match_id: str,
    staging_dir: Path,
    ready_dir: Path,
    segment_seconds: int = DEFAULT_SEGMENT_SECONDS,
    mode: str = "segment",
    session_id: str | None = None,
    output: str | Path | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    poll_interval: float = 5.0,
    dry_run: bool = False,
    verbose: bool = False,
    test_duration: int = 120,
    ffmpeg_bin: str = "ffmpeg",
    popen=subprocess.Popen,
    sleep=time.sleep,
    stop_requested=None,
) -> dict:
    """Supervised capture with bounded retries and a durable manifest.

    In ``segment`` mode, segments listed by FFmpeg are validated and finalized
    into *ready_dir* (idempotently) while recording. If FFmpeg exits non-zero
    before the operator stops it, the engine restarts up to *max_attempts*
    times, continuing segment numbering so nothing is overwritten. On stop it
    recovers any valid leftovers. In ``full`` mode a single file is written.
    """
    match_id = slugify(match_id)
    staging_dir = Path(staging_dir)
    ready_dir = Path(ready_dir)
    session_id = session_id or default_session_id(match_id)

    if mode == "full":
        out_path = Path(output or (staging_dir / f"{match_id}_live.ts"))
        argv = build_record_argv(
            source, output=str(out_path), mode="full", test_duration=test_duration, ffmpeg_bin=ffmpeg_bin,
        )
        if dry_run:
            return {"session_id": session_id, "mode": "full", "argv": argv, "output": str(out_path), "dry_run": True}
        out_path.parent.mkdir(parents=True, exist_ok=True)
        process = popen(argv, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
        process.wait()
        return {"session_id": session_id, "mode": "full", "argv": argv, "output": str(out_path),
                "ffmpeg_exit_code": process.poll()}

    manifest = new_manifest(
        session_id=session_id, match_id=match_id, source=source, segment_seconds=segment_seconds,
        staging_dir=staging_dir, ready_dir=ready_dir, mode=mode,
    )
    mpath = manifest_path(staging_dir, session_id)

    if dry_run:
        argv = build_record_argv(
            source, output=build_output_pattern(staging_dir, match_id), mode="segment",
            segment_seconds=segment_seconds, list_path=build_list_path(staging_dir, match_id),
            start_number=0, test_duration=test_duration, ffmpeg_bin=ffmpeg_bin,
        )
        return {"session_id": session_id, "mode": "segment", "argv": argv,
                "manifest_path": str(mpath), "dry_run": True}

    staging_dir.mkdir(parents=True, exist_ok=True)
    ready_dir.mkdir(parents=True, exist_ok=True)
    save_manifest(mpath, manifest)

    attempts = 0
    stopped = False
    stderr_fh = None
    process = None
    try:
        while attempts < max_attempts:
            attempts += 1
            manifest["attempts"] = attempts
            save_manifest(mpath, manifest)

            start_index = _next_index(manifest)
            list_path = Path(build_list_path(staging_dir, match_id, attempt=attempts - 1))
            output_pattern = build_output_pattern(staging_dir, match_id)
            argv = build_record_argv(
                source, output=output_pattern, mode="segment", segment_seconds=segment_seconds,
                list_path=str(list_path), start_number=start_index,
                test_duration=test_duration, ffmpeg_bin=ffmpeg_bin,
            )
            if verbose:
                print(f"[capture] attempt {attempts}: {' '.join(argv)}")

            log_path = staging_dir / f"{match_id}_ffmpeg_attempt{attempts}.log"
            stderr_fh = open(log_path, "w", encoding="utf-8")
            process = popen(argv, stdout=subprocess.DEVNULL, stderr=stderr_fh)

            while process.poll() is None:
                if stop_requested is not None and stop_requested():
                    stopped = True
                    _terminate(process)
                    break
                for name in sorted(read_segment_list(list_path) - _finalized_names(manifest)):
                    src = staging_dir / name
                    if not src.exists():
                        continue
                    finalize_segment(manifest, src=src, ready_dir=ready_dir)
                    save_manifest(mpath, manifest)
                sleep(poll_interval)

            if stopped:
                break

            ret = process.poll()
            manifest["ffmpeg_exit_code"] = ret
            if ret == 0:
                break
            if attempts >= max_attempts:
                manifest["state"] = SESSION_FAILED
                manifest["error"] = f"ffmpeg exited with code {ret} after {attempts} attempt(s)"
                break
            # bounded retry: continue numbering, new list file
    finally:
        if stderr_fh is not None:
            stderr_fh.close()
        recover_session(manifest, staging_dir=staging_dir, ready_dir=ready_dir)
        manifest["stopped_at"] = manifest.get("stopped_at") or now_iso()
        if stopped and manifest["state"] not in (SESSION_FAILED,):
            manifest["state"] = SESSION_STOPPED
        save_manifest(mpath, manifest)

    return {
        "session_id": session_id,
        "mode": "segment",
        "manifest_path": str(mpath),
        "state": manifest["state"],
        "attempts": manifest["attempts"],
        "ffmpeg_exit_code": manifest.get("ffmpeg_exit_code"),
        "error": manifest.get("error"),
        "segments": manifest.get("segments", []),
        "manifest": manifest,
    }


def _finalized_names(manifest: dict) -> set[str]:
    return {e["filename"] for e in manifest.get("segments", []) if e.get("state") == SEGMENT_FINALIZED and e.get("filename")}


# ── Assembly ─────────────────────────────────────────────────────────────────


def finalized_segments(manifest: dict) -> list[dict]:
    return sorted(
        (e for e in manifest.get("segments", []) if e.get("state") == SEGMENT_FINALIZED),
        key=lambda e: e.get("index", 0),
    )


def assemble_recording(
    manifest: dict,
    *,
    out_path: str | Path,
    container: str | None = None,
    dry_run: bool = False,
    ffmpeg_bin: str = "ffmpeg",
) -> dict:
    """Concatenate finalized segments into a single recording file.

    Uses FFmpeg's concat demuxer with stream copy. Verifies every source
    segment still exists, then records the recording's path, size, SHA-256,
    and readable duration in the manifest (state → ``assembled``).
    """
    entries = finalized_segments(manifest)
    if not entries:
        raise LiveCaptureError("no finalized segments to assemble")

    missing = [e["ready_path"] for e in entries if not Path(e["ready_path"]).exists()]
    if missing:
        raise LiveCaptureError(f"finalized segment(s) missing from disk: {missing}")

    out_path = Path(out_path)
    if out_path.suffix == "":
        out_path = out_path.with_suffix(f".{container or 'mp4'}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    list_file = out_path.parent / f".{out_path.stem}_concat.txt"
    list_file.write_text(
        "".join(f"file '{_concat_escape(e['ready_path'])}'\n" for e in entries), encoding="utf-8",
    )
    argv = [ffmpeg_bin, "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", str(out_path)]

    if dry_run:
        list_file.unlink(missing_ok=True)
        return {"argv": argv, "path": str(out_path), "segment_count": len(entries), "dry_run": True}

    try:
        result = subprocess.run(argv, capture_output=True, text=True, shell=False)
        if result.returncode != 0:
            raise LiveCaptureError(f"ffmpeg concat failed: {result.stderr.strip()[:300]}")
        duration = probe_duration(out_path)
        recording = {
            "path": normalize_path(out_path),
            "filename": out_path.name,
            "size_bytes": out_path.stat().st_size,
            "sha256": sha256_file(out_path),
            "duration_seconds": duration,
            "segment_count": len(entries),
        }
    finally:
        list_file.unlink(missing_ok=True)

    manifest["state"] = SESSION_ASSEMBLED
    manifest["recording"] = recording
    return recording


def _concat_escape(path: str) -> str:
    return str(path).replace("'", "'\\''")


# ── Registration through Phase 7 local_ingest ────────────────────────────────


def register_recording(
    recording: dict,
    *,
    pilot_id: str,
    jobs_dir: str | Path | None = None,
    source_id: str | None = None,
    intake_path: str | Path | None = None,
    upload: bool = False,
    story_id: str | None = None,
    fmt: str | None = None,
) -> dict:
    """Register a completed recording with the Phase 7 ``local_ingest`` service.

    This is a thin delegation: the recording file is fed to
    :func:`pipeline.local_ingest.run_local_ingest`, which emits the versioned
    ``stadium_signal.local_ingest/v1`` contract. OORT upload stays opt-in and
    off by default — a completed capture is never uploaded automatically.
    """
    from . import local_ingest as _local_ingest

    path = recording.get("path")
    if not path or not Path(path).exists():
        raise LiveCaptureError(f"recording file not found: {path}")

    kwargs: dict = {"pilot_id": pilot_id, "jobs_dir": jobs_dir, "intake_path": intake_path, "upload": upload}
    if source_id:
        kwargs["source_id"] = source_id
    if story_id:
        kwargs["story_id"] = story_id
    if fmt:
        kwargs["fmt"] = fmt
    return _local_ingest.run_local_ingest(path, **kwargs)
