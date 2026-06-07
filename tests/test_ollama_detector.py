from unittest.mock import patch, MagicMock
from pathlib import Path

from pipeline.config import load_config
from pipeline.ollama_detector import run_ollama_detection


@patch("pipeline.ollama_detector.requests.post")
def test_dry_run_skips_api_call(mock_post, tmp_path, capsys):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze this match", encoding="utf-8")
    out_file = tmp_path / "out.json"

    run_ollama_detection(str(prompt_file), str(out_file), model="llama3.1", dry_run=True)

    mock_post.assert_not_called()
    assert not out_file.exists()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out


@patch("pipeline.ollama_detector.requests.post")
def test_normal_mode_writes_json_output(mock_post, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze this match", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": '[{"clip_id": "001", "category": "EMOTION"}]'
    }
    mock_post.return_value = mock_resp

    run_ollama_detection(str(prompt_file), str(out_file), model="llama3.1")

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "clip_id" in content
    assert "001" in content

    raw_file = out_file.with_suffix(".raw.txt")
    assert raw_file.exists()


@patch("pipeline.ollama_detector.requests.post")
def test_handles_text_before_json_array(mock_post, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "response": "Here are the clips:\n[{\"clip_id\": \"001\"}]\n"
    }
    mock_post.return_value = mock_resp

    run_ollama_detection(str(prompt_file), str(out_file), model="llama3.1")

    content = out_file.read_text(encoding="utf-8")
    assert "001" in content


@patch("pipeline.ollama_detector.requests.post")
def test_non_json_raises_system_exit(mock_post, tmp_path):
    import pytest
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.json.return_value = {"response": "This is not JSON at all"}
    mock_post.return_value = mock_resp

    with pytest.raises(SystemExit):
        run_ollama_detection(str(prompt_file), str(out_file), model="llama3.1")


@patch("pipeline.ollama_detector.requests.post")
@patch("pipeline.ollama_detector.load_config")
def test_timeout_reads_from_config(mock_load_config, mock_post, tmp_path):
    mock_load_config.return_value = {"providers": {"timeout": 999}}
    mock_post.return_value.json.return_value = {"response": "[]"}

    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("test", encoding="utf-8")
    out_file = tmp_path / "out.json"

    run_ollama_detection(str(prompt_file), str(out_file), model="llama3.1")

    (_, kwargs) = mock_post.call_args
    assert kwargs["timeout"] == 999
