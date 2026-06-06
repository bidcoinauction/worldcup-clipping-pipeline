from unittest.mock import patch

from scripts.process_match import main


def test_model_defaults_to_config_value(tmp_path):
    """process_match --model default should read from config."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    call_args = mock_run.call_args_list[0][0][0]
    model_idx = call_args.index("--model") + 1
    assert call_args[model_idx] == "base"
