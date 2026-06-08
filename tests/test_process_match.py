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


def test_mode_not_passed_when_omitted(tmp_path):
    """process_match should not pass --mode when omitted (generate_claude_prompt uses config default)."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    assert "--mode" not in prompt_call


def test_mode_passed_when_specified(tmp_path):
    """process_match should pass --mode when specified."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "micro",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    mode_idx = prompt_call.index("--mode") + 1
    assert prompt_call[mode_idx] == "micro"


def test_package_mode_accepted(tmp_path):
    """process_match should accept --mode package."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "package",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    mode_idx = prompt_call.index("--mode") + 1
    assert prompt_call[mode_idx] == "package"


def test_package_mode_adds_condensed_windows(tmp_path):
    """--mode package should auto-append --condensed-windows."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "package",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    assert "--condensed-windows" in prompt_call


def test_story_mode_does_not_add_condensed(tmp_path):
    """--mode story should not auto-append --condensed-windows."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "story",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    assert "--condensed-windows" not in prompt_call


def test_micro_mode_does_not_add_condensed(tmp_path):
    """--mode micro should not auto-append --condensed-windows."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "micro",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    assert "--condensed-windows" not in prompt_call


def test_no_condense_flag_suppresses_condensed(tmp_path):
    """--no-condense with package mode should suppress --condensed-windows."""
    input_video = tmp_path / "match.mp4"
    input_video.touch()

    with patch("sys.argv", [
        "process_match", "--input", str(input_video),
        "--league", "WORLD_CUP", "--match-name", "Test Match",
        "--mode", "package",
        "--no-condense",
    ]), patch("scripts.process_match.run") as mock_run:
        main()

    prompt_call = mock_run.call_args_list[1][0][0]
    assert "--condensed-windows" not in prompt_call
