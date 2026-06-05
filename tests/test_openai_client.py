from unittest.mock import patch, MagicMock
from pathlib import Path

from pipeline.openai_client import run_gpt_detection


@patch("pipeline.api.make_openai_client")
def test_dry_run_skips_api_call(mock_make_client, tmp_path, capsys):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze this match", encoding="utf-8")
    out_file = tmp_path / "out.json"

    run_gpt_detection(str(prompt_file), str(out_file), dry_run=True)

    mock_make_client.assert_not_called()
    assert not out_file.exists()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out


@patch("pipeline.api.make_openai_client")
@patch("pipeline.openai_client._get_model", return_value="gpt-4.1")
def test_normal_mode_writes_json_output(mock_model, mock_make_client, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze this match", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.output_text = '[{"clip_id": "001", "category": "EMOTION"}]'
    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_resp
    mock_make_client.return_value = mock_client

    run_gpt_detection(str(prompt_file), str(out_file))

    assert out_file.exists()
    content = out_file.read_text(encoding="utf-8")
    assert "clip_id" in content
    assert "001" in content


@patch("pipeline.api.make_openai_client")
@patch("pipeline.openai_client._get_model", return_value="gpt-4.1")
def test_handles_text_before_json_array(mock_model, mock_make_client, tmp_path):
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.output_text = "Here are the clips:\n[{\"clip_id\": \"001\"}]\n"
    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_resp
    mock_make_client.return_value = mock_client

    run_gpt_detection(str(prompt_file), str(out_file))

    content = out_file.read_text(encoding="utf-8")
    assert "clip_id" in content
    assert "001" in content


@patch("pipeline.api.make_openai_client")
@patch("pipeline.openai_client._get_model", return_value="gpt-4.1")
def test_non_json_raises_system_exit(mock_model, mock_make_client, tmp_path):
    import pytest
    prompt_file = tmp_path / "prompt.txt"
    prompt_file.write_text("Analyze", encoding="utf-8")
    out_file = tmp_path / "out.json"

    mock_resp = MagicMock()
    mock_resp.output_text = "This is not JSON at all"
    mock_client = MagicMock()
    mock_client.responses.create.return_value = mock_resp
    mock_make_client.return_value = mock_client

    with pytest.raises(SystemExit):
        run_gpt_detection(str(prompt_file), str(out_file))
