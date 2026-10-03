"""Tests for the transcription service boundary (Slice 6)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.transcription import (
    validate_source,
    resolve_transcript_path,
    resolve_transcript_path_from_root,
    is_transcript_valid,
    find_existing_transcript,
    extract_audio,
    transcribe_source,
    SUPPORTED_EXTENSIONS,
)
from pipeline.detection import (
    analyze_project,
    read_analysis_state,
    update_analysis_state,
    _ensure_transcription,
    STAGE_TRANSCRIBING,
)
from tests.test_pilot_intake import build_intake


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"transcription media bytes" * 100)
    return path


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "tr_test", "project": sport},
        "media": {"source_id": "tr_source", "match_or_event_name": "Transcribe Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


# ── Source validation ───────────────────────────────────────────────────────


def test_validate_source_existing_file(media_file):
    result = validate_source(media_file)
    assert result == media_file


def test_validate_source_missing_file(tmp_path):
    with pytest.raises(FileNotFoundError, match="not found"):
        validate_source(tmp_path / "nonexistent.mp4")


def test_validate_source_directory(tmp_path):
    d = tmp_path / "dir.mp4"
    d.mkdir()
    with pytest.raises(ValueError, match="directory"):
        validate_source(d)


def test_validate_source_unsupported_extension(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("data")
    with pytest.raises(ValueError, match="Unsupported"):
        validate_source(f)


def test_validate_source_supported_extensions():
    for ext in SUPPORTED_EXTENSIONS:
        assert ext.startswith(".")


# ── Transcript resolution ───────────────────────────────────────────────────


def test_resolve_transcript_path_uses_stem(media_file, tmp_path):
    path = resolve_transcript_path_from_root(media_file, tmp_path, "WORLD_CUP")
    assert path.name == "transcript.txt"
    assert "source" in str(path)  # slugified stem of source.mp4


def test_is_transcript_valid_existing(tmp_path):
    t = tmp_path / "transcript.txt"
    t.write_text("[0s - 10s] Test transcript", encoding="utf-8")
    assert is_transcript_valid(t) is True


def test_is_transcript_valid_empty(tmp_path):
    t = tmp_path / "transcript.txt"
    t.write_text("", encoding="utf-8")
    assert is_transcript_valid(t) is False


def test_is_transcript_valid_whitespace_only(tmp_path):
    t = tmp_path / "transcript.txt"
    t.write_text("   \n  \n  ", encoding="utf-8")
    assert is_transcript_valid(t) is False


def test_is_transcript_valid_missing(tmp_path):
    assert is_transcript_valid(tmp_path / "missing.txt") is False


def test_find_existing_transcript_found(media_file, tmp_path):
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    t = transcript_dir / "transcript.txt"
    t.write_text("[0s - 10s] Test", encoding="utf-8")
    result = find_existing_transcript(media_file, tmp_path, "WORLD_CUP")
    assert result == t


def test_find_existing_transcript_missing(media_file, tmp_path):
    result = find_existing_transcript(media_file, tmp_path, "WORLD_CUP")
    assert result is None


# ── extract_audio ───────────────────────────────────────────────────────────


@patch("pipeline.transcription.subprocess.run")
def test_extract_audio_calls_ffmpeg(mock_run, tmp_path):
    video = tmp_path / "match.mp4"
    video.write_text("fake video")
    out = tmp_path / "out.m4a"
    result = extract_audio(video, out)
    assert result == out
    mock_run.assert_called_once()
    cmd = mock_run.call_args.args[0]
    assert cmd[0] == "ffmpeg"
    assert str(video) in cmd
    assert str(out) in cmd


# ── transcribe_source — reuse ──────────────────────────────────────────────


def test_transcribe_source_reuses_existing(media_file, tmp_path):
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    t = transcript_dir / "transcript.txt"
    t.write_text("[0s - 10s] Test transcript", encoding="utf-8")
    ts = transcript_dir / "timestamps.json"
    ts.write_text(json.dumps([{"start": 0, "end": 10, "text": "test"}]), encoding="utf-8")

    result = transcribe_source(media_file, root=tmp_path, league="WORLD_CUP")
    assert result["ok"] is True
    assert result["reused"] is True
    assert result["transcript_path"] == str(t)
    assert result["segments"] == 1


def test_transcribe_source_reuse_skips_transcription(media_file, tmp_path):
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    t = transcript_dir / "transcript.txt"
    t.write_text("existing transcript", encoding="utf-8")

    with patch("pipeline.transcription.extract_audio") as mock_extract:
        result = transcribe_source(media_file, root=tmp_path, league="WORLD_CUP")
        mock_extract.assert_not_called()
    assert result["reused"] is True


# ── transcribe_source — dry_run ────────────────────────────────────────────


def test_transcribe_source_dry_run(media_file, tmp_path):
    result = transcribe_source(media_file, root=tmp_path, league="WORLD_CUP", dry_run=True)
    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["reused"] is False


# ── transcribe_source — missing source ─────────────────────────────────────


def test_transcribe_source_missing_source(tmp_path):
    with pytest.raises(FileNotFoundError):
        transcribe_source(tmp_path / "missing.mp4", root=tmp_path)


# ── _ensure_transcription — auto-transcribe ─────────────────────────────────


def test_ensure_transcription_auto_transcribes(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    fake_result = {
        "ok": True,
        "transcript_path": str(tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source" / "transcript.txt"),
        "reused": False,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 5,
        "dry_run": False,
    }

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.transcription.transcribe_source", return_value=fake_result):
        # Create the transcript file that would be written
        transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
        transcript_dir.mkdir(parents=True)
        (transcript_dir / "transcript.txt").write_text("auto-transcribed", encoding="utf-8")

        result = _ensure_transcription(str(media_file), "Test Match", "football", jobs_root, job_id)

    assert result.exists()
    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_stage"] == "TRANSCRIBING"


def test_ensure_transcription_reuses_existing(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    # Create existing transcript
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    t = transcript_dir / "transcript.txt"
    t.write_text("existing transcript", encoding="utf-8")

    with patch("pipeline.utils.ROOT", tmp_path):
        result = _ensure_transcription(str(media_file), "Test Match", "football", jobs_root, job_id)

    assert result == t
    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state.get("transcription_reused") is True


# ── analyze_project — auto-transcribe ──────────────────────────────────────


def test_analyze_project_auto_transcribes(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    fake_result = {
        "ok": True,
        "transcript_path": str(tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source" / "transcript.txt"),
        "reused": False,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 5,
        "dry_run": False,
    }

    # Create the transcript file
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "transcript.txt").write_text("auto-transcribed", encoding="utf-8")
    (transcript_dir / "timestamps.json").write_text(json.dumps([{"start": 0, "end": 10, "text": "test"}]), encoding="utf-8")

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.transcription.transcribe_source", return_value=fake_result), \
         patch("pipeline.detection.run_detection_call", return_value=[
             {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
              "virality_score": 9, "caption": "Goal", "status": "needs_visual_scrub"}
         ]):
        result = analyze_project(job_id, jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"


def test_analyze_project_transcription_failure_preserves_project(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    # Satisfy preflight so the failure below comes from transcription itself,
    # exercising the mid-run cleanup path rather than the preflight gate.
    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("pipeline.detection.importlib.util.find_spec", return_value=object()), \
         patch("pipeline.transcription.transcribe_source", side_effect=RuntimeError("ffmpeg not found")):
        result = analyze_project(job_id, jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "FAILED"
    assert "ffmpeg not found" in result["error"]

    # Project should still be readable
    from pipeline.pilot import read_job
    read_job(job_id, jobs_dir=jobs_root)

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_status"] == "FAILED"
    assert state["transcription_status"] == "FAILED"
    assert "ffmpeg not found" in state["transcription_error"]


# ── Preflight: dependencies checked before RUNNING ───────────────────────────


def test_analyze_project_preflight_missing_faster_whisper(media_file, jobs_root, tmp_path):
    """A missing dependency must fail before the job ever enters RUNNING."""
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.shutil.which", return_value="/usr/bin/ffmpeg"), \
         patch("pipeline.detection.importlib.util.find_spec", return_value=None):
        result = analyze_project(job_id, jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "NEEDS ATTENTION"
    assert "faster-whisper" in result["error"]

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_status"] == "NEEDS ATTENTION"
    assert state["analysis_status"] != "RUNNING"
    assert state["transcription_status"] == "FAILED"
    assert state["analysis_started_at"] == ""


def test_analyze_project_preflight_missing_ffmpeg(media_file, jobs_root, tmp_path):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.shutil.which", return_value=None):
        result = analyze_project(job_id, jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "NEEDS ATTENTION"
    assert "FFmpeg" in result["error"]

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_status"] != "RUNNING"


def test_analyze_project_preflight_skips_whisper_when_transcript_reusable(media_file, jobs_root, tmp_path):
    """An existing valid transcript must not require the transcription dependency."""
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "transcript.txt").write_text("[0s - 10s] reused", encoding="utf-8")

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.importlib.util.find_spec", return_value=None), \
         patch("pipeline.detection.shutil.which", return_value=None), \
         patch("pipeline.detection.run_detection_call", return_value=[
             {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
              "virality_score": 9, "caption": "Goal", "status": "needs_visual_scrub"}
         ]):
        result = analyze_project(job_id, jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["transcription_reused"] is True
    assert state["transcription_status"] == "READY"


# ── Recovery: orphaned RUNNING state after restart ──────────────────────────


def test_recover_interrupted_analysis_marks_transcribing_run_failed(media_file, jobs_root):
    from pipeline.detection import recover_interrupted_analysis

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(
        job_id, status="RUNNING", stage="TRANSCRIBING",
        jobs_dir=jobs_root, transcription_status="RUNNING",
    )

    state = recover_interrupted_analysis(job_id, jobs_dir=jobs_root)

    assert state["analysis_status"] == "FAILED"
    assert state["analysis_status"] != "RUNNING"
    assert state["analysis_stage"] == ""
    assert state["transcription_status"] == "FAILED"
    assert "interrupted" in state["analysis_error"].lower()


def test_recover_interrupted_analysis_is_idempotent(media_file, jobs_root):
    from pipeline.detection import recover_interrupted_analysis

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)
    first = recover_interrupted_analysis(job_id, jobs_dir=jobs_root)
    second = recover_interrupted_analysis(job_id, jobs_dir=jobs_root)

    assert second == first


def test_recover_interrupted_analysis_leaves_complete_untouched(media_file, jobs_root):
    from pipeline.detection import recover_interrupted_analysis

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="COMPLETE", stage="", jobs_dir=jobs_root,
                         analysis_manifest_count=3)
    state = recover_interrupted_analysis(job_id, jobs_dir=jobs_root)

    assert state["analysis_status"] == "COMPLETE"
    assert state["analysis_manifest_count"] == 3


def test_get_project_recovers_orphaned_running_state(media_file, jobs_root):
    """Operator reads must not show a stale RUNNING state after restart."""
    from pipeline.operator_console import get_analysis_status, get_project

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)

    get_project(job_id, jobs_dir=jobs_root)
    state = get_analysis_status(job_id, jobs_dir=jobs_root)

    assert state["analysis_status"] == "FAILED"
    assert state["analysis_status"] != "RUNNING"


def test_recover_interrupted_analysis_skips_live_run(media_file, jobs_root):
    """A run that is live in this process must never be marked interrupted."""
    from pipeline.detection import _ACTIVE_ANALYSES, recover_interrupted_analysis

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)
    _ACTIVE_ANALYSES.add(job_id)
    try:
        state = recover_interrupted_analysis(job_id, jobs_dir=jobs_root)
    finally:
        _ACTIVE_ANALYSES.discard(job_id)

    assert state["analysis_status"] == "RUNNING"


def test_get_transcription_status_reports_failed_after_recovery(media_file, jobs_root):
    from pipeline.operator_console import get_transcription_status

    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root,
                         transcription_status="RUNNING")

    status = get_transcription_status(job_id, jobs_dir=jobs_root)

    assert status["transcription_status"] == "NEEDS ATTENTION"
    assert "interrupted" in status["error"].lower()


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_execution_in_transcription_service():
    """Verify transcription service does not import or use subprocess for CLI."""
    import pipeline.transcription as mod
    # subprocess is used for ffmpeg only, which is expected
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "os.system" not in source
    assert "shell=True" not in source


# ── Existing transcript path compatibility ──────────────────────────────────


def test_transcript_path_matches_existing_convention(media_file, tmp_path):
    """Verify transcript path matches the convention used by process_match.py."""
    from pipeline.utils import slugify
    expected_slug = slugify(media_file.stem)
    path = resolve_transcript_path_from_root(media_file, tmp_path, "WORLD_CUP")
    assert expected_slug in str(path)
    assert path.name == "transcript.txt"
    assert "WORLD_CUP" in str(path)
