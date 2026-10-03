"""Tests for transcription CLI adapter."""

from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.transcribe_match import main


@patch("pipeline.transcription.transcribe_source")
def test_main_delegates_to_transcription_service(mock_transcribe, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_transcribe.return_value = {
        "ok": True,
        "transcript_path": str(tmp_path / "transcript.txt"),
        "reused": False,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 5,
        "dry_run": False,
    }

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE", "--provider", "openai",
    ]):
        main()

    mock_transcribe.assert_called_once()
    assert mock_transcribe.call_args.args[0] == str(input_video)


@patch("pipeline.transcription.transcribe_source")
def test_main_reused_transcript(mock_transcribe, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_transcribe.return_value = {
        "ok": True,
        "transcript_path": str(tmp_path / "transcript.txt"),
        "reused": True,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 5,
        "dry_run": False,
    }

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE",
    ]):
        main()

    mock_transcribe.assert_called_once()


@patch("pipeline.transcription.transcribe_source")
def test_main_dry_run(mock_transcribe, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_transcribe.return_value = {
        "ok": True,
        "transcript_path": str(tmp_path / "TRANSCRIPTS" / "PREMIER_LEAGUE" / "match" / "transcript.txt"),
        "reused": False,
        "provider": "openai",
        "model": "gpt-4o-transcribe",
        "segments": 0,
        "dry_run": True,
    }

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE", "--dry-run",
    ]):
        main()

    mock_transcribe.assert_called_once()
    assert mock_transcribe.call_args.kwargs["dry_run"] is True


@patch("pipeline.transcription.transcribe_source")
def test_main_force_flag(mock_transcribe, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_transcribe.return_value = {
        "ok": True,
        "transcript_path": str(tmp_path / "transcript.txt"),
        "reused": False,
        "provider": "faster-whisper",
        "model": "base",
        "segments": 10,
        "dry_run": False,
    }

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE", "--provider", "faster-whisper",
        "--force",
    ]):
        main()

    assert mock_transcribe.call_args.kwargs["force"] is True
