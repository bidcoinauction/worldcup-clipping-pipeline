from unittest.mock import patch

from pipeline.stadium_signal import execute_ffmpeg_commands


@patch("pipeline.stadium_signal.subprocess.run")
@patch("pipeline.stadium_signal.ensure_clip_output_dir")
def test_uses_list_form_not_shell(mock_ensure, mock_run):
    command = 'ffmpeg -y -ss 00:00 -i "/path/with spaces/input.mp4" -c copy "/output/path/clip.mp4"'

    execute_ffmpeg_commands([command])

    (args,), kwargs = mock_run.call_args
    assert isinstance(args, list), f"subprocess.run called with {type(args)}, expected list"
    assert args[0] == "ffmpeg"
    i_idx = args.index("-i")
    assert args[i_idx + 1] == "/path/with spaces/input.mp4"
    assert args[-1] == "/output/path/clip.mp4"
    assert kwargs.get("check") is True
    assert kwargs.get("shell") is not True


@patch("pipeline.stadium_signal.subprocess.run")
@patch("pipeline.stadium_signal.ensure_clip_output_dir")
def test_preserves_simple_command_without_spaces(mock_ensure, mock_run):
    command = "ffmpeg -y -ss 00:00 -i /input.mp4 -c copy /output.mp4"

    execute_ffmpeg_commands([command])

    (args,), _ = mock_run.call_args
    assert args == ["ffmpeg", "-y", "-ss", "00:00", "-i", "/input.mp4", "-c", "copy", "/output.mp4"]
