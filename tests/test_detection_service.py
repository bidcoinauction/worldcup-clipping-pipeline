"""Tests for the detection service boundary (Slice 5)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from pipeline.detection import (
    analyze_project,
    analysis_supported,
    profile_capabilities,
    read_analysis_state,
    read_moments,
    run_detection_call,
    update_analysis_state,
    STAGES,
    ANALYSIS_STATES,
    _safe_error_message,
    _parse_clips_json,
)
from pipeline.config_errors import ConfigurationError
from tests.test_pilot_intake import build_intake

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_gpt_detection.py"


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"detection media bytes" * 100)
    return path


def _create_project(media_file: Path, jobs_root: Path, sport: str = "football") -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "det_test", "project": sport},
        "media": {"source_id": "det_source", "match_or_event_name": "Test Match"},
        "configuration": {"project": sport},
    })
    intake_path = media_file.parent / f"{sport}_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


# ── Profile capabilities ────────────────────────────────────────────────────


def test_football_analysis_supported():
    caps = profile_capabilities("football")
    assert caps["analysis_supported"] is True
    assert caps["profile"] == "football"


def test_basketball_analysis_not_supported():
    caps = profile_capabilities("basketball")
    assert caps["analysis_supported"] is False


def test_basketball_sandbox_analysis_not_supported():
    caps = profile_capabilities("basketball_sandbox")
    assert caps["analysis_supported"] is False


def test_analysis_supported_true_for_football():
    assert analysis_supported("football") is True


def test_analysis_supported_false_for_basketball():
    assert analysis_supported("basketball") is False


def test_unknown_profile_fails_closed():
    with pytest.raises(ConfigurationError, match="unknown profile"):
        profile_capabilities("lacrosse")


# ── Analysis state persistence ──────────────────────────────────────────────


def test_update_and_read_analysis_state(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    updated = update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)
    assert updated["analysis_status"] == "RUNNING"
    assert updated["analysis_stage"] == "TRANSCRIBING"
    assert updated.get("analysis_started_at")

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_status"] == "RUNNING"
    assert state["analysis_stage"] == "TRANSCRIBING"


def test_analysis_state_complete(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)
    updated = update_analysis_state(
        job_id, status="COMPLETE", stage="",
        analysis_manifest_count=12,
        jobs_dir=jobs_root,
    )
    assert updated["analysis_status"] == "COMPLETE"
    assert updated["analysis_completed_at"]
    assert updated["analysis_manifest_count"] == 12

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_status"] == "COMPLETE"
    assert state["analysis_manifest_count"] == 12


def test_analysis_state_error_cleared_on_success(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]

    update_analysis_state(job_id, status="FAILED", error="something broke", jobs_dir=jobs_root)
    update_analysis_state(job_id, status="RUNNING", stage="TRANSCRIBING", jobs_dir=jobs_root)

    state = read_analysis_state(job_id, jobs_dir=jobs_root)
    assert state["analysis_error"] == ""


# ── Moments I/O ──────────────────────────────────────────────────────────────


def test_read_moments_empty(media_file, jobs_root):
    job = _create_project(media_file, jobs_root)
    moments = read_moments(job["job_id"], jobs_dir=jobs_root)
    assert moments == []


# ── run_detection_call ──────────────────────────────────────────────────────


def test_run_detection_call_dry_run():
    clips = run_detection_call("test prompt", dry_run=True)
    assert clips == []


@patch("pipeline.detection._run_openai")
def test_run_detection_call_openai(mock_openai):
    mock_openai.return_value = [{"clip_id": "001", "category": "EMOTION"}]
    clips = run_detection_call("test prompt", provider="openai")
    assert len(clips) == 1
    assert clips[0]["clip_id"] == "001"
    mock_openai.assert_called_once()


@patch("pipeline.detection._run_ollama")
def test_run_detection_call_ollama(mock_ollama):
    mock_ollama.return_value = [{"clip_id": "002", "category": "CHAOS"}]
    clips = run_detection_call("test prompt", provider="ollama")
    assert len(clips) == 1
    mock_ollama.assert_called_once()


def test_run_detection_call_unknown_provider():
    with pytest.raises(ConfigurationError, match="unknown detection provider"):
        run_detection_call("test", provider="invalid")


# ── _parse_clips_json ──────────────────────────────────────────────────────


def test_parse_clips_json_array():
    raw = json.dumps([{"clip_id": "001"}])
    assert _parse_clips_json(raw) == [{"clip_id": "001"}]


def test_parse_clips_json_wrapped():
    raw = json.dumps({"clips": [{"clip_id": "001"}]})
    assert _parse_clips_json(raw) == [{"clip_id": "001"}]


def test_parse_clips_json_markdown_wrapped():
    raw = '```json\n[{"clip_id": "001"}]\n```'
    # The markdown wrapper contains [ and ] so the fallback parser should find it
    result = _parse_clips_json(raw)
    assert result == [{"clip_id": "001"}]


def test_parse_clips_json_invalid():
    with pytest.raises(ValueError, match="not valid JSON"):
        _parse_clips_json("not json at all")


# ── _safe_error_message ────────────────────────────────────────────────────


def test_safe_error_message_basic():
    msg = _safe_error_message(FileNotFoundError("source not found"))
    assert "source not found" in msg
    assert "FileNotFoundError" not in msg


def test_safe_error_message_strips_secrets():
    msg = _safe_error_message(Exception("API_KEY=sk-12345 failed"))
    assert "sk-12345" not in msg
    assert "API_KEY" not in msg


def test_safe_error_message_truncates_long():
    long_msg = "x" * 1000
    msg = _safe_error_message(Exception(long_msg))
    assert len(msg) <= 510


def test_safe_error_message_empty_fallback():
    msg = _safe_error_message(Exception(""))
    assert "needs attention" in msg.lower() or "failed" in msg.lower()


# ── analyze_project — unsupported profile ───────────────────────────────────


def test_analyze_project_basketball_shows_unsupported(media_file, jobs_root):
    job = _create_project(media_file, jobs_root, sport="basketball")
    result = analyze_project(job["job_id"], jobs_dir=jobs_root)
    assert result["ok"] is False
    assert "not available yet" in result["error"]
    assert result["status"] == "NEEDS ATTENTION"

    state = read_analysis_state(job["job_id"], jobs_dir=jobs_root)
    assert state["analysis_status"] == "NEEDS ATTENTION"


# ── analyze_project — missing transcript triggers auto-transcribe ────────────


def test_analyze_project_missing_transcript_auto_transcribes(media_file, jobs_root, tmp_path):
    """When no transcript exists, analyze_project should auto-transcribe."""
    job = _create_project(media_file, jobs_root, sport="football")

    fake_result = {
        "ok": True,
        "transcript_path": str(tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source" / "transcript.txt"),
        "reused": False,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 5,
        "dry_run": False,
    }

    # Create the transcript file that auto-transcription would produce
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
        result = analyze_project(job["job_id"], jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"


# ── analyze_project — dry run with mock transcript ─────────────────────────


def test_analyze_project_dry_run(media_file, jobs_root, tmp_path):
    # Create a fake transcript — uses source file stem (source) as slug
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "transcript.txt"
    transcript_file.write_text("[0s - 10s] Test transcript", encoding="utf-8")

    # Create a fake intake with a valid path
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "det_dry", "project": "football"},
        "media": {"source_id": "det_src", "match_or_event_name": "Dry Run Match"},
        "configuration": {"project": "football"},
    })
    intake_path = tmp_path / "intake_dry.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    job = create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)

    with patch("pipeline.utils.ROOT", tmp_path):
        result = analyze_project(job["job_id"], jobs_dir=jobs_root, dry_run=True)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["moments_count"] == 0  # dry run returns empty clips


# ── analyze_project — successful run ────────────────────────────────────────


def test_analyze_project_success(media_file, jobs_root, tmp_path):
    # Create a fake transcript — uses source file stem (source) as slug
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    transcript_file = transcript_dir / "transcript.txt"
    transcript_file.write_text("[0s - 10s] Goal!", encoding="utf-8")

    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "det_ok", "project": "football"},
        "media": {"source_id": "success_source", "match_or_event_name": "Success Match"},
        "configuration": {"project": "football"},
    })
    intake_path = tmp_path / "intake_ok.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    job = create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)

    fake_clips = [
        {"clip_id": "001", "category": "EMOTION", "start_time": "5", "end_time": "20",
         "virality_score": 9, "caption": "Goal celebration", "status": "needs_visual_scrub"},
    ]

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.run_detection_call", return_value=fake_clips):
        result = analyze_project(job["job_id"], jobs_dir=jobs_root)

    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["moments_count"] == 1

    state = read_analysis_state(job["job_id"], jobs_dir=jobs_root)
    assert state["analysis_status"] == "COMPLETE"
    assert state["analysis_manifest_count"] == 1

    moments = read_moments(job["job_id"], jobs_dir=jobs_root)
    assert len(moments) == 1
    assert moments[0]["category"] == "EMOTION"


# ── analyze_project — failure preserves job ─────────────────────────────────


def test_analyze_project_failure_preserves_job(media_file, jobs_root, tmp_path):
    # Create a fake transcript — uses source file stem (source) as slug
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "source"
    transcript_dir.mkdir(parents=True)
    (transcript_dir / "transcript.txt").write_text("data", encoding="utf-8")

    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "det_fail", "project": "football"},
        "media": {"source_id": "fail_source", "match_or_event_name": "Fail Match"},
        "configuration": {"project": "football"},
    })
    intake_path = tmp_path / "intake_fail.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    job = create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)

    with patch("pipeline.utils.ROOT", tmp_path), \
         patch("pipeline.detection.run_detection_call", side_effect=RuntimeError("API timeout")):
        result = analyze_project(job["job_id"], jobs_dir=jobs_root)

    assert result["ok"] is False
    assert result["status"] == "FAILED"
    assert "API timeout" in result["error"]

    # Job record should still be readable
    from pipeline.pilot import read_job
    job_record = read_job(job["job_id"], jobs_dir=jobs_root)
    assert job_record["job_id"] == job["job_id"]
    assert job_record["current_state"] == "READY"

    state = read_analysis_state(job["job_id"], jobs_dir=jobs_root)
    assert state["analysis_status"] == "FAILED"
    assert "API timeout" in state["analysis_error"]


# ── CLI adapter uses shared service ────────────────────────────────────────


def test_detection_cli_uses_shared_service(tmp_path):
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    out_file = tmp_path / "o.json"

    with patch("pipeline.detection.run_detection_call", return_value=[{"clip_id": "001"}]) as mock:
        from scripts.run_gpt_detection import main
        with patch("sys.argv", [
            "run_gpt_detection", "--prompt", str(prompt_file),
            "--output", str(out_file), "--provider", "openai",
        ]):
            main()

    mock.assert_called_once()
    assert mock.call_args.kwargs["provider"] == "openai"
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data == [{"clip_id": "001"}]


# ── No shell execution ──────────────────────────────────────────────────────


def test_no_shell_execution_in_detection_service():
    """Verify detection service does not import or use subprocess."""
    import pipeline.detection as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source
    assert "shell=True" not in source


def test_no_shell_execution_in_console_service():
    """Verify operator console does not import or use subprocess."""
    import pipeline.operator_console as mod
    source = Path(mod.__file__).read_text(encoding="utf-8")
    assert "subprocess" not in source
    assert "os.system" not in source


# ── Stages and states constants ─────────────────────────────────────────────


def test_stages_are_defined():
    assert len(STAGES) == 5
    assert "PREPARING_SOURCE" in STAGES
    assert "TRANSCRIBING" in STAGES
    assert "UNDERSTANDING_GAME" in STAGES
    assert "FINDING_MOMENTS" in STAGES
    assert "PREPARING_RESULTS" in STAGES


def test_analysis_states_are_defined():
    assert "WAITING" in ANALYSIS_STATES
    assert "RUNNING" in ANALYSIS_STATES
    assert "COMPLETE" in ANALYSIS_STATES
    assert "NEEDS ATTENTION" in ANALYSIS_STATES
    assert "FAILED" in ANALYSIS_STATES
