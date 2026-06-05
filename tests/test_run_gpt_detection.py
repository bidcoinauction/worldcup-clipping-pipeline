from unittest.mock import patch

from scripts.run_gpt_detection import main


@patch("scripts.run_gpt_detection.run_gpt_detection")
def test_openai_provider_dispatches_to_openai(mock_openai, tmp_path):
    prompt_file = tmp_path / "p.txt"
    prompt_file.touch()
    out_file = tmp_path / "o.json"

    with patch("sys.argv", [
        "run_gpt_detection", "--prompt", str(prompt_file),
        "--output", str(out_file), "--provider", "openai",
    ]):
        main()

    mock_openai.assert_called_once()


@patch("pipeline.ollama_detector.run_ollama_detection")
def test_ollama_provider_dispatches_to_ollama(mock_ollama, tmp_path):
    prompt_file = tmp_path / "p.txt"
    prompt_file.touch()
    out_file = tmp_path / "o.json"

    with patch("sys.argv", [
        "run_gpt_detection", "--prompt", str(prompt_file),
        "--output", str(out_file), "--provider", "ollama",
    ]):
        main()

    mock_ollama.assert_called_once()
