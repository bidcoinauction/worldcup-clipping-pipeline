from unittest.mock import MagicMock, patch, call
from pathlib import Path

import pytest

from pipeline.utils import get_video_duration, timestamp_to_seconds
from scripts.export_clips_ffmpeg import _micro_slice, _validate_and_clamp, export_clip


@patch("scripts.export_clips_ffmpeg.subprocess.run")
def test_export_clip_builds_ffmpeg_command(mock_run, tmp_path):
    source = "/videos/match.mp4"
    start = "00:01:00"
    end = "00:01:15"
    output = tmp_path / "clips" / "clip_001.mp4"

    export_clip(source, start, end, output)

    (cmd,) = mock_run.call_args[0]
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    ss_idx = cmd.index("-ss")
    assert cmd[ss_idx + 1] == start
    assert "-i" in cmd
    i_idx = cmd.index("-i")
    assert cmd[i_idx + 1] == source
    assert "-t" in cmd
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "15.0"
    assert cmd[-1] == str(output)
    mock_run.assert_called_once()


@patch("scripts.export_clips_ffmpeg.subprocess.run")
def test_export_clip_enforces_minimum_duration(mock_run, tmp_path):
    output = tmp_path / "short.mp4"
    export_clip("/v/m.mp4", "00:00:00", "00:00:00", output)
    cmd = mock_run.call_args[0][0]
    t_idx = cmd.index("-t")
    assert float(cmd[t_idx + 1]) == 0.1


@patch("scripts.export_clips_ffmpeg.subprocess.run")
def test_export_clip_creates_parent_dir(mock_run, tmp_path):
    output = tmp_path / "nested" / "deep" / "clip.mp4"
    export_clip("/v/m.mp4", "00:00:10", "00:00:20", output)
    assert output.parent.exists()
    assert output.parent.is_dir()


@patch("scripts.export_clips_ffmpeg.subprocess.run")
@patch("scripts.export_clips_ffmpeg.Path.open")
def test_main_dry_run_does_not_call_export(mock_open, mock_run, tmp_path, capsys):
    from scripts.export_clips_ffmpeg import main
    import io, csv

    manifest_csv = "clip_id,category,start_time,end_time\nclip_001,EMOTION,00:00:05,00:00:15\n"
    mock_open.return_value.__enter__.return_value = io.StringIO(manifest_csv)

    with patch(
        "scripts.export_clips_ffmpeg.Path.open",
        return_value=io.StringIO(manifest_csv),
    ):
        with patch(
            "sys.argv",
            ["export_clips_ffmpeg", "--manifest", "dummy.csv", "--source-video", "/v/m.mp4", "--dry-run"],
        ):
            main()

    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "clip_001" in captured.out


def test_micro_slice_leaves_short_window_unchanged():
    """Window <= max_seconds should pass through unchanged."""
    s, e = _micro_slice("00:00:05", "00:00:07", 3.8)
    assert s == "00:00:05"
    assert e == "00:00:07"


def test_micro_slice_centers_long_window():
    """Window > max_seconds should return centered slice of exactly max_seconds."""
    s, e = _micro_slice("00:00:10", "00:00:20", 4.0)
    expected_start = 13.0
    expected_end = 17.0
    assert abs(timestamp_to_seconds(s) - expected_start) < 0.01
    assert abs(timestamp_to_seconds(e) - expected_end) < 0.01


def test_micro_slice_exact_boundary():
    """Window exactly equal to max_seconds should pass through unchanged."""
    s, e = _micro_slice("00:00:00", "00:00:03.8", 3.8)
    assert s == "00:00:00"
    assert e == "00:00:03.8"


def test_micro_slice_zero_duration():
    """Zero-duration window should return start and end as-is."""
    s, e = _micro_slice("00:01:00", "00:01:00", 3.8)
    assert s == "00:01:00"
    assert e == "00:01:00"


@patch("scripts.export_clips_ffmpeg._micro_slice")
@patch("scripts.export_clips_ffmpeg.Path.open")
def test_micro_mode_calls_slice(mock_open, mock_slice, tmp_path, capsys):
    """Micro mode should call _micro_slice on each row."""
    from scripts.export_clips_ffmpeg import main
    import io

    manifest_csv = "clip_id,category,start_time,end_time\nclip_001,EMOTION,00:00:10,00:00:30\n"
    mock_open.return_value.__enter__.return_value = io.StringIO(manifest_csv)
    mock_slice.return_value = ("00:00:15", "00:00:18.8")

    ffprobe_mock = MagicMock()
    ffprobe_mock.stdout = '{"format": {"duration": "120.0"}}'
    ffprobe_mock.returncode = 0

    with patch(
        "scripts.export_clips_ffmpeg.Path.open",
        return_value=io.StringIO(manifest_csv),
    ), patch("pipeline.utils.subprocess.run", return_value=ffprobe_mock):
        with patch(
            "sys.argv",
            ["export_clips_ffmpeg", "--manifest", "dummy.csv",
             "--source-video", "/v/m.mp4", "--mode", "micro", "--dry-run"],
        ):
            main()

    mock_slice.assert_called_once()
    (called_start, called_end, called_max) = mock_slice.call_args[0]
    assert abs(timestamp_to_seconds(called_start) - 10.0) < 0.01
    assert abs(timestamp_to_seconds(called_end) - 30.0) < 0.01
    assert called_max == 3.8


@patch("scripts.export_clips_ffmpeg._micro_slice")
@patch("scripts.export_clips_ffmpeg.Path.open")
def test_story_mode_does_not_slice(mock_open, mock_slice, tmp_path, capsys):
    """Story mode should not call _micro_slice."""
    from scripts.export_clips_ffmpeg import main
    import io

    manifest_csv = "clip_id,category,start_time,end_time\nclip_001,EMOTION,00:00:10,00:00:30\n"
    mock_open.return_value.__enter__.return_value = io.StringIO(manifest_csv)

    with patch(
        "scripts.export_clips_ffmpeg.Path.open",
        return_value=io.StringIO(manifest_csv),
    ):
        with patch(
            "sys.argv",
            ["export_clips_ffmpeg", "--manifest", "dummy.csv",
             "--source-video", "/v/m.mp4", "--mode", "story", "--dry-run"],
        ):
            main()

    mock_slice.assert_not_called()


def test_validate_clamp_skips_start_exceeds_duration():
    result = _validate_and_clamp("125", "130", 120.0)
    assert result is None


def test_validate_clamp_clamps_end():
    result = _validate_and_clamp("115", "130", 120.0)
    assert result is not None
    s, e = result
    assert abs(timestamp_to_seconds(s) - 115.0) < 0.01
    assert abs(timestamp_to_seconds(e) - 120.0) < 0.01


def test_validate_clamp_within_bounds():
    result = _validate_and_clamp("00:00:10", "00:00:20", 120.0)
    assert result is not None
    s, e = result
    assert abs(timestamp_to_seconds(s) - 10.0) < 0.01
    assert abs(timestamp_to_seconds(e) - 20.0) < 0.01


def test_get_video_duration_returns_float():
    sample = Path("FootballArchive/SAMPLES/psg_arsenal_2min.mp4")
    if not sample.exists():
        pytest.skip(f"local media sample missing: {sample}")

    dur = get_video_duration(sample)
    assert isinstance(dur, float)
    assert dur > 0
    assert abs(dur - 120.0) < 1.0


@patch("scripts.export_clips_ffmpeg._micro_slice")
@patch("scripts.export_clips_ffmpeg.Path.open")
def test_micro_mode_skips_out_of_bounds_row(mock_open, mock_slice, tmp_path, capsys):
    from scripts.export_clips_ffmpeg import main
    import io

    manifest_csv = (
        "clip_id,category,start_time,end_time\n"
        "clip_001,EMOTION,00:00:10,00:00:20\n"
        "clip_002,EMOTION,125,130\n"
        "clip_003,EMOTION,115,130\n"
    )
    mock_open.return_value.__enter__.return_value = io.StringIO(manifest_csv)
    mock_slice.return_value = ("00:00:14", "00:00:17.8")

    ffprobe_mock = MagicMock()
    ffprobe_mock.stdout = '{"format": {"duration": "120.0"}}'
    ffprobe_mock.returncode = 0

    with patch(
        "scripts.export_clips_ffmpeg.Path.open",
        return_value=io.StringIO(manifest_csv),
    ), patch("pipeline.utils.subprocess.run", return_value=ffprobe_mock):
        with patch(
            "sys.argv",
            ["export_clips_ffmpeg", "--manifest", "dummy.csv",
             "--source-video", "/v/m.mp4", "--mode", "micro", "--dry-run"],
        ):
            main()

    assert mock_slice.call_count == 2
    captured = capsys.readouterr()
    assert "[skip] clip_002" in captured.out
    assert "[clamp] clip_003" in captured.out


@patch("scripts.export_clips_ffmpeg._micro_slice")
@patch("scripts.export_clips_ffmpeg.Path.open")
def test_micro_mode_no_false_positive_clamp(mock_open, mock_slice, tmp_path, capsys):
    """Raw-number timestamps within bounds should NOT trigger [clamp]."""
    from scripts.export_clips_ffmpeg import main
    import io

    manifest_csv = (
        "clip_id,category,start_time,end_time\n"
        "clip_001,EMOTION,67,72\n"
    )
    mock_open.return_value.__enter__.return_value = io.StringIO(manifest_csv)
    mock_slice.return_value = ("00:01:07", "00:01:12")

    ffprobe_mock = MagicMock()
    ffprobe_mock.stdout = '{"format": {"duration": "120.0"}}'
    ffprobe_mock.returncode = 0

    with patch(
        "scripts.export_clips_ffmpeg.Path.open",
        return_value=io.StringIO(manifest_csv),
    ), patch("pipeline.utils.subprocess.run", return_value=ffprobe_mock):
        with patch(
            "sys.argv",
            ["export_clips_ffmpeg", "--manifest", "dummy.csv",
             "--source-video", "/v/m.mp4", "--mode", "micro", "--dry-run"],
        ):
            main()

    mock_slice.assert_called_once()
    captured = capsys.readouterr()
    assert "[clamp]" not in captured.out
