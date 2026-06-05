from unittest.mock import patch, call
from pathlib import Path

from scripts.export_clips_ffmpeg import export_clip


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
