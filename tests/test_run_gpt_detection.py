"""Tests for detection CLI adapter."""

import json
from unittest.mock import patch

from scripts.run_gpt_detection import main


@patch("pipeline.detection.run_detection_call")
def test_openai_provider_dispatches_through_service(mock_detect, tmp_path):
    mock_detect.return_value = [{"clip_id": "001"}]
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    out_file = tmp_path / "o.json"

    with patch("sys.argv", [
        "run_gpt_detection", "--prompt", str(prompt_file),
        "--output", str(out_file), "--provider", "openai",
    ]):
        main()

    mock_detect.assert_called_once()
    assert mock_detect.call_args.kwargs["provider"] == "openai"
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert data == [{"clip_id": "001"}]


@patch("pipeline.detection.run_detection_call")
def test_ollama_provider_dispatches_through_service(mock_detect, tmp_path):
    mock_detect.return_value = [{"clip_id": "002"}]
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    out_file = tmp_path / "o.json"

    with patch("sys.argv", [
        "run_gpt_detection", "--prompt", str(prompt_file),
        "--output", str(out_file), "--provider", "ollama",
    ]):
        main()

    mock_detect.assert_called_once()
    assert mock_detect.call_args.kwargs["provider"] == "ollama"
    assert out_file.exists()


def test_dry_run_skips_detection(tmp_path):
    prompt_file = tmp_path / "p.txt"
    prompt_file.write_text("test prompt", encoding="utf-8")
    out_file = tmp_path / "o.json"

    with patch("sys.argv", [
        "run_gpt_detection", "--prompt", str(prompt_file),
        "--output", str(out_file), "--dry-run",
    ]):
        main()

    assert not out_file.exists()
