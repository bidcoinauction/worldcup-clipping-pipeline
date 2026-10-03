"""Transcription service boundary.

Extracts reusable transcription logic from scripts/transcribe_match.py into a
service module. Provides transcript resolution (reuse existing or transcribe),
audio extraction, and provider-agnostic transcription. CLI scripts and the
Operator Console call these functions rather than reimplementing transcription.

No CLI invocation. No secrets exposure. Sport-neutral.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .config import get_model as _get_model, get_provider
from .config_errors import ConfigurationError

# ── Supported media extensions ──────────────────────────────────────────────

SUPPORTED_EXTENSIONS = {".mp4", ".ts", ".mkv", ".avi", ".mov", ".m4a", ".mp3", ".wav", ".aac"}


# ── Source validation ───────────────────────────────────────────────────────


def validate_source(source_path: str | Path) -> Path:
    """Validate that a source media file exists, is readable, and is a
    supported local media type. Returns the resolved Path.

    Raises descriptive errors for:
    - missing file
    - directory instead of file
    - unsupported extension
    - unreadable file
    """
    path = Path(source_path)
    if not path.exists():
        raise FileNotFoundError(f"Source file not found: {path}")
    if path.is_dir():
        raise ValueError(f"Source path is a directory, not a file: {path}")
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValueError(
            f"Unsupported file type '{path.suffix}' for source: {path}. "
            f"Supported types: {', '.join(sorted(SUPPORTED_EXTENSIONS))}"
        )
    if not os.access(path, os.R_OK):
        raise PermissionError(f"Source file is not readable: {path}")
    return path


# ── Transcript resolution ───────────────────────────────────────────────────


def resolve_transcript_path(source_path: str | Path, league: str = "WORLD_CUP") -> Path:
    """Resolve the expected transcript path for a source file.

    Uses the source file stem (not source_id) as the slug, matching
    existing transcribe_match.py behavior.
    """
    from .utils import slugify
    match_slug = slugify(Path(source_path).stem)
    return Path(os.environ.get("FOOTBALL_ARCHIVE_ROOT", "FootballArchive")).parent / "TRANSCRIPTS" / league / match_slug / "transcript.txt"


def resolve_transcript_path_from_root(source_path: str | Path, root: Path, league: str = "WORLD_CUP") -> Path:
    """Resolve the expected transcript path using a specific root directory."""
    from .utils import slugify
    match_slug = slugify(Path(source_path).stem)
    return root / "TRANSCRIPTS" / league / match_slug / "transcript.txt"


def is_transcript_valid(transcript_path: Path) -> bool:
    """Check if a transcript file exists and is non-empty.

    A transcript is considered valid if:
    - The file exists
    - The file is non-empty
    - The file contains non-whitespace content
    """
    if not transcript_path.exists():
        return False
    if not transcript_path.is_file():
        return False
    try:
        content = transcript_path.read_text(encoding="utf-8")
        return bool(content.strip())
    except (OSError, UnicodeDecodeError):
        return False


def find_existing_transcript(source_path: str | Path, root: Path, league: str = "WORLD_CUP") -> Path | None:
    """Find an existing valid transcript for the source file.

    Returns the transcript path if found and valid, None otherwise.
    Does NOT create any files.
    """
    transcript_path = resolve_transcript_path_from_root(source_path, root, league)
    if is_transcript_valid(transcript_path):
        return transcript_path
    return None


# ── Audio extraction ────────────────────────────────────────────────────────


def extract_audio(video_path: Path, out_audio: Path) -> Path:
    """Extract audio from a video file using ffmpeg.

    Returns the output audio path. Raises on ffmpeg failure.
    """
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "aac",
        "-b:a", "192k",
        str(out_audio),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_audio


# ── Provider transcription ──────────────────────────────────────────────────


def transcribe_with_openai(audio_path: Path, model: str) -> tuple[str, list[dict]]:
    """Transcribe audio using the OpenAI API. Returns (text, segments)."""
    from .api import make_openai_client
    client = make_openai_client()

    with audio_path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="verbose_json",
        )

    transcript = str(result.text)
    segments = []
    if hasattr(result, "segments") and result.segments:
        for seg in result.segments:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            })
    return transcript, segments


def transcribe_with_whisper(audio_path: Path, model_size: str) -> tuple[str, list[dict]]:
    """Transcribe audio using local faster-whisper. Returns (text, segments)."""
    from .whisper_transcriber import transcribe
    return transcribe(audio_path, model_size)


# ── Full transcription workflow ─────────────────────────────────────────────


def transcribe_source(
    source_path: str | Path,
    *,
    league: str = "WORLD_CUP",
    root: Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> dict:
    """Transcribe a source file end-to-end.

    This is the main entry point for the transcription service. It:
    1. Validates the source file
    2. Checks for an existing valid transcript (reuse if found)
    3. Extracts audio via ffmpeg
    4. Runs provider transcription
    5. Writes transcript.txt, timestamps.json, metadata.json
    6. Returns a structured result

    If a valid transcript already exists and force=False, returns the
    existing transcript path without retranscribing.

    Raises on failure. Never shells into CLI scripts.
    """
    from .utils import ROOT, slugify

    source = validate_source(source_path)
    root_dir = root if root is not None else ROOT
    selected_provider = provider or get_provider("transcription")
    selected_model = model or os.getenv("DEFAULT_TRANSCRIBE_MODEL") or _get_model("transcription")

    # 1. Check for existing transcript
    if not force:
        existing = find_existing_transcript(source, root_dir, league)
        if existing is not None:
            return {
                "ok": True,
                "transcript_path": str(existing),
                "reused": True,
                "provider": selected_provider,
                "model": selected_model,
                "segments": _count_segments(existing),
                "dry_run": False,
            }

    # 2. Prepare output directory
    match_slug = slugify(source.stem)
    out_dir = root_dir / "TRANSCRIPTS" / league / match_slug
    audio_path = out_dir / f"{match_slug}_audio.m4a"

    if dry_run:
        return {
            "ok": True,
            "transcript_path": str(out_dir / "transcript.txt"),
            "reused": False,
            "provider": selected_provider,
            "model": selected_model,
            "segments": 0,
            "dry_run": True,
        }

    # 3. Extract audio
    extract_audio(source, audio_path)

    # 4. Transcribe
    if selected_provider == "openai":
        transcript, segments = transcribe_with_openai(audio_path, selected_model)
    elif selected_provider == "faster-whisper":
        transcript, segments = transcribe_with_whisper(audio_path, selected_model)
    else:
        raise ConfigurationError(f"unknown transcription provider '{selected_provider}'")

    # 5. Write outputs
    out_dir.mkdir(parents=True, exist_ok=True)
    transcript_txt = out_dir / "transcript.txt"
    timestamps_json = out_dir / "timestamps.json"
    meta_json = out_dir / "metadata.json"

    transcript_txt.write_text(transcript.strip(), encoding="utf-8")
    timestamps_json.write_text(json.dumps(segments, indent=2), encoding="utf-8")
    meta_json.write_text(json.dumps({
        "input": str(source),
        "audio": str(audio_path),
        "league": league,
        "match_slug": match_slug,
        "model": selected_model,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }, indent=2), encoding="utf-8")

    return {
        "ok": True,
        "transcript_path": str(transcript_txt),
        "reused": False,
        "provider": selected_provider,
        "model": selected_model,
        "segments": len(segments),
        "dry_run": False,
    }


def _count_segments(transcript_path: Path) -> int:
    """Count segments in the timestamps.json companion file."""
    ts_path = transcript_path.with_name("timestamps.json")
    if not ts_path.exists():
        return 0
    try:
        data = json.loads(ts_path.read_text(encoding="utf-8"))
        return len(data) if isinstance(data, list) else 0
    except (json.JSONDecodeError, OSError):
        return 0
