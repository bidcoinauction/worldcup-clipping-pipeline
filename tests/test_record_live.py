import json
from pathlib import Path
from unittest.mock import MagicMock, patch, call

from scripts.record_live import (
    build_ace_url,
    build_ffmpeg_cmd,
    build_list_path,
    build_output_pattern,
    build_test_ffmpeg_cmd,
    finalize_segment,
    read_segment_list,
    segment_number_from_path,
    write_status_json,
)


def test_build_ace_url_default_host():
    url = build_ace_url("abc123")
    assert url == "http://localhost:6878/ace/getstream?id=abc123"


def test_build_ace_url_custom_host():
    url = build_ace_url("xyz789", ace_host="http://192.168.1.5:7000")
    assert url == "http://192.168.1.5:7000/ace/getstream?id=xyz789"


def test_build_ace_url_with_special_chars():
    url = build_ace_url("a1b2c3_d4e5f6")
    assert url == "http://localhost:6878/ace/getstream?id=a1b2c3_d4e5f6"


def test_segment_number_from_path():
    assert segment_number_from_path(Path("match_S0000.ts")) == 0
    assert segment_number_from_path(Path("match_S0001.ts")) == 1
    assert segment_number_from_path(Path("match_S0042.ts")) == 42
    assert segment_number_from_path(Path("match_S9999.ts")) == 9999


def test_build_output_pattern():
    p = build_output_pattern(Path("C:/STAGING"), "test_match")
    assert "C:/STAGING" in p or "C:\\STAGING" in p
    assert "test_match_S%04d.ts" in p


def test_build_list_path():
    p = build_list_path(Path("C:/STAGING"), "test_match")
    assert "test_match_list.txt" in p


def test_build_ffmpeg_cmd_structure():
    cmd = build_ffmpeg_cmd(
        ace_url="http://host/ace/getstream?id=abc",
        output_pattern="C:/STAGING/test_S%04d.ts",
        segment_time=900,
        list_path="C:/STAGING/test_list.txt",
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-i" in cmd
    i_idx = cmd.index("-i")
    assert cmd[i_idx + 1] == "http://host/ace/getstream?id=abc"
    assert "-c" in cmd
    c_idx = cmd.index("-c")
    assert cmd[c_idx + 1] == "copy"
    assert "-f" in cmd
    f_idx = cmd.index("-f")
    assert cmd[f_idx + 1] == "segment"
    assert "-segment_time" in cmd
    t_idx = cmd.index("-segment_time")
    assert cmd[t_idx + 1] == "900"
    assert "-segment_list" in cmd
    l_idx = cmd.index("-segment_list")
    assert cmd[l_idx + 1] == "C:/STAGING/test_list.txt"
    assert cmd[-1] == "C:/STAGING/test_S%04d.ts"


def test_build_ffmpeg_cmd_segment_time():
    cmd = build_ffmpeg_cmd("u", "p", 300, "l")
    t_idx = cmd.index("-segment_time")
    assert cmd[t_idx + 1] == "300"


def test_build_ffmpeg_cmd_reset_timestamps():
    cmd = build_ffmpeg_cmd("u", "p", 900, "l")
    assert "-reset_timestamps" in cmd
    rt_idx = cmd.index("-reset_timestamps")
    assert cmd[rt_idx + 1] == "1"


def test_build_test_ffmpeg_cmd_structure():
    cmd = build_test_ffmpeg_cmd(
        output_pattern="C:/STAGING/test_S%04d.ts",
        segment_time=10,
        list_path="C:/STAGING/test_list.txt",
        test_duration=120,
    )
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-f" in cmd
    f_idx = cmd.index("-f")
    assert cmd[f_idx + 1] == "lavfi"
    assert "-i" in cmd
    i_idx = cmd.index("-i")
    assert "testsrc2" in cmd[i_idx + 1]
    assert "duration=120" in cmd[i_idx + 1]
    assert "size=1280x720" in cmd[i_idx + 1]
    assert "-c:v" in cmd
    c_idx = cmd.index("-c:v")
    assert cmd[c_idx + 1] == "libx264"
    assert "-preset" in cmd
    p_idx = cmd.index("-preset")
    assert cmd[p_idx + 1] == "ultrafast"
    seg_idx = cmd.index("-f", f_idx + 1)
    assert cmd[seg_idx + 1] == "segment"
    assert "-segment_time" in cmd
    t_idx = cmd.index("-segment_time")
    assert cmd[t_idx + 1] == "10"
    assert "-segment_list" in cmd
    assert cmd[-1] == "C:/STAGING/test_S%04d.ts"


def test_build_test_ffmpeg_cmd_duration_and_time():
    cmd = build_test_ffmpeg_cmd("p", 5, "l", 60)
    i_idx = cmd.index("-i")
    assert "duration=60" in cmd[i_idx + 1]
    t_idx = cmd.index("-segment_time")
    assert cmd[t_idx + 1] == "5"


def test_build_test_ffmpeg_cmd_reset_timestamps():
    cmd = build_test_ffmpeg_cmd("p", 10, "l", 120)
    assert "-reset_timestamps" in cmd
    rt_idx = cmd.index("-reset_timestamps")
    assert cmd[rt_idx + 1] == "1"


def test_write_status_json(tmp_path):
    seg = tmp_path / "test_match_S0001.ts"
    seg.write_text("dummy content")
    result = write_status_json(
        segment_path=seg,
        match_id="test_match",
        acestream_id="abc123",
        segment_number=1,
        duration_seconds=900.0,
        exit_code=None,
    )
    assert result == seg.with_suffix(".status.json")
    assert result.exists()
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["match_id"] == "test_match"
    assert data["acestream_id"] == "abc123"
    assert data["segment_number"] == 1
    assert data["filename"] == "test_match_S0001.ts"
    assert data["size_bytes"] > 0
    assert data["duration_seconds"] == 900.0
    assert "recorded_at" in data
    assert data["ffmpeg_exit_code"] is None


def test_write_status_json_zero_size(tmp_path):
    seg = tmp_path / "nonexistent_S0002.ts"
    # file does not exist
    result = write_status_json(
        segment_path=seg,
        match_id="m",
        acestream_id="id",
        segment_number=2,
        duration_seconds=None,
        exit_code=0,
    )
    data = json.loads(result.read_text(encoding="utf-8"))
    assert data["size_bytes"] == 0
    assert data["duration_seconds"] is None
    assert data["ffmpeg_exit_code"] == 0


def test_write_status_json_output_path(tmp_path):
    seg = tmp_path / "sub" / "match_S0005.ts"
    seg.parent.mkdir(parents=True, exist_ok=True)
    seg.write_text("x")
    result = write_status_json(seg, "m", "id", 5, 300.0, None)
    assert result == seg.with_suffix(".status.json")


def test_finalize_segment(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    src = staging / "test_S0000.ts"
    src.write_text("segment data")
    dest = finalize_segment(
        src=src,
        ready_dir=ready,
        match_id="test",
        acestream_id="abc",
        segment_number=0,
        duration_seconds=900.0,
    )
    assert dest == ready / "test_S0000.ts"
    assert dest.exists()
    assert not src.exists()
    status = ready / "test_S0000.status.json"
    assert status.exists()
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["segment_number"] == 0


def test_finalize_segment_default_duration(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    src = staging / "test_S0003.ts"
    src.write_text("data")
    finalize_segment(
        src=src, ready_dir=ready, match_id="t", acestream_id="a", segment_number=3,
    )
    status = ready / "test_S0003.status.json"
    assert status.exists()
    data = json.loads(status.read_text(encoding="utf-8"))
    assert data["duration_seconds"] is None


def test_read_segment_list_not_found(tmp_path):
    result = read_segment_list(tmp_path / "nonexistent.txt")
    assert result == set()


def test_read_segment_list_present(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text(
        "match_S0000.ts\n"
        "match_S0001.ts\n"
        "match_S0002.ts\n"
    )
    result = read_segment_list(lst)
    assert result == {"match_S0000.ts", "match_S0001.ts", "match_S0002.ts"}


def test_read_segment_list_ignores_empty_lines(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text("match_S0000.ts\n\nmatch_S0001.ts\n")
    result = read_segment_list(lst)
    assert result == {"match_S0000.ts", "match_S0001.ts"}


def test_read_segment_list_full_paths(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text(
        "C:\\FOOTBALL\\LIVE_SEGMENTS\\match_S0000.ts\n"
        "/football/live_segments/match_S0001.ts\n"
    )
    result = read_segment_list(lst)
    assert result == {"match_S0000.ts", "match_S0001.ts"}


def test_read_segment_list_mixed_separators(tmp_path):
    lst = tmp_path / "list.txt"
    lst.write_text(
        "C:/FOOTBALL/LIVE_SEGMENTS/match_S0002.ts\n"
        "D:\\ARCHIVE\\RAW\\match_S0003.ts\n"
        "//network/share/live/match_S0004.ts\n"
    )
    result = read_segment_list(lst)
    assert result == {"match_S0002.ts", "match_S0003.ts", "match_S0004.ts"}


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_does_not_start_ffmpeg(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc123",
            "--match-id", "test_match",
            "--dry-run",
        ],
    ):
        main()

    mock_popen.assert_not_called()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "abc123" in captured.out
    assert "test_match" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_no_file_writes(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "xyz789",
            "--match-id", "some_match",
            "--dry-run",
        ],
    ):
        main()

    mock_popen.assert_not_called()


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_accepts_segment_minutes(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc",
            "--match-id", "match",
            "--segment-minutes", "30",
            "--dry-run",
        ],
    ):
        main()

    captured = capsys.readouterr()
    assert "1800s" in captured.out
    assert "30 min" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_accepts_custom_host(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc",
            "--match-id", "m",
            "--ace-host", "http://192.168.1.100:7000",
            "--dry-run",
        ],
    ):
        main()

    captured = capsys.readouterr()
    assert "192.168.1.100:7000" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_slugifies_match_id(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc",
            "--match-id", "Argentina vs France 2022!!!",
            "--dry-run",
        ],
    ):
        main()

    captured = capsys.readouterr()
    assert "argentina_vs_france_2022" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_with_test(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "test",
            "--match-id", "test_match",
            "--test",
            "--dry-run",
        ],
    ):
        main()

    mock_popen.assert_not_called()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "Test mode enabled" in captured.out
    assert "synthetic video" in captured.out
    assert "test_match" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_with_verbose(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc123",
            "--match-id", "test_match",
            "--verbose",
            "--dry-run",
        ],
    ):
        main()

    mock_popen.assert_not_called()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "Verbose mode" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_with_test_and_verbose(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "test",
            "--match-id", "test_match",
            "--test",
            "--verbose",
            "--dry-run",
        ],
    ):
        main()

    mock_popen.assert_not_called()
    captured = capsys.readouterr()
    assert "Test mode enabled" in captured.out
    assert "Verbose mode" in captured.out


@patch("scripts.record_live.subprocess.Popen")
def test_main_dry_run_with_test_and_duration(mock_popen, tmp_path, capsys):
    from scripts.record_live import main

    with patch(
        "sys.argv",
        [
            "record_live",
            "test",
            "--match-id", "test_match",
            "--test",
            "--test-duration", "300",
            "--dry-run",
        ],
    ):
        main()

    captured = capsys.readouterr()
    assert "300s" in captured.out


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_test_mode_uses_test_ffmpeg_cmd(mock_popen, mock_signal, tmp_path):
    from scripts.record_live import main

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc

    staging = tmp_path / "LIVE_SEGMENTS"
    ready = tmp_path / "LIVE_READY"

    import scripts.record_live as rl

    with patch.object(rl, "time") as mock_time:
        with patch(
            "sys.argv",
            [
                "record_live",
                "test",
                "--match-id", "test_match",
                "--test",
                "--staging-dir", str(staging),
                "--ready-dir", str(ready),
            ],
        ):
            main()

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd
    f_idx = cmd.index("-f")
    assert cmd[f_idx + 1] == "lavfi"
    assert any("testsrc2" in part for part in cmd)


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_starts_ffmpeg(mock_popen, mock_signal, tmp_path):
    from scripts.record_live import main

    mock_proc = MagicMock()
    mock_proc.poll.return_value = 0
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_popen.return_value = mock_proc

    with patch(
        "sys.argv",
        [
            "record_live",
            "abc123",
            "--match-id", "test_match",
            "--staging-dir", str(tmp_path / "LIVE_SEGMENTS"),
            "--ready-dir", str(tmp_path / "LIVE_READY"),
        ],
    ):
        main()

    mock_popen.assert_called_once()
    cmd = mock_popen.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert any("abc123" in part for part in cmd)


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_watches_and_finalizes_segments(mock_popen, mock_signal, tmp_path):
    from scripts.record_live import main

    staging = tmp_path / "LIVE_SEGMENTS"
    ready = tmp_path / "LIVE_READY"
    staging.mkdir(parents=True)
    list_file = staging / "test_match_list.txt"

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()

    poll_results = [None, None, None, 0, 0]
    mock_proc.poll.side_effect = poll_results

    mock_popen.return_value = mock_proc

    def write_list_and_segments():
        list_file.write_text("test_match_S0000.ts\ntest_match_S0001.ts\n")
        (staging / "test_match_S0000.ts").write_text("seg0")
        (staging / "test_match_S0001.ts").write_text("seg1")

    import scripts.record_live as rl
    original_finalize = rl.finalize_segment

    finalized = []

    def tracking_finalize(src, ready_dir, match_id, acestream_id, segment_number, duration_seconds=None):
        finalized.append((src.name, segment_number))
        return original_finalize(src, ready_dir, match_id, acestream_id, segment_number, duration_seconds)

    with patch.object(rl, "finalize_segment", side_effect=tracking_finalize):
        with patch.object(rl, "time") as mock_time:
            write_list_and_segments()
            with patch(
                "sys.argv",
                [
                    "record_live",
                    "abc123",
                    "--match-id", "test_match",
                    "--staging-dir", str(staging),
                    "--ready-dir", str(ready),
                ],
            ):
                main()

    assert len(finalized) == 2
    assert finalized[0] == ("test_match_S0000.ts", 0)
    assert finalized[1] == ("test_match_S0001.ts", 1)
    assert (ready / "test_match_S0001.ts").exists()
    assert (ready / "test_match_S0000.status.json").exists()


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_finalizes_remaining_on_exit(mock_popen, mock_signal, tmp_path):
    from scripts.record_live import main

    staging = tmp_path / "LIVE_SEGMENTS"
    ready = tmp_path / "LIVE_READY"
    staging.mkdir(parents=True)

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_proc.poll.return_value = 0
    mock_popen.return_value = mock_proc

    (staging / "test_match_S0002.ts").write_text("orphan")
    (staging / "test_match_S0003.ts").write_text("orphan")

    import scripts.record_live as rl

    finalized = []

    def tracking_finalize(src, ready_dir, match_id, acestream_id, segment_number, duration_seconds=None):
        finalized.append((src.name, segment_number))
        return ready_dir / src.name

    with patch.object(rl, "finalize_segment", side_effect=tracking_finalize):
        with patch.object(rl, "time") as mock_time:
            with patch(
                "sys.argv",
                [
                    "record_live",
                    "abc123",
                    "--match-id", "test_match",
                    "--staging-dir", str(staging),
                    "--ready-dir", str(ready),
                ],
            ):
                main()

    assert len(finalized) == 2
    assert finalized[0] == ("test_match_S0002.ts", 2)
    assert finalized[1] == ("test_match_S0003.ts", 3)


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_graceful_shutdown_on_keyboard_interrupt(mock_popen, mock_signal, tmp_path):
    from scripts.record_live import main

    staging = tmp_path / "LIVE_SEGMENTS"
    ready = tmp_path / "LIVE_READY"
    staging.mkdir(parents=True)

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_proc.poll.side_effect = [None, None]
    mock_popen.return_value = mock_proc

    import scripts.record_live as rl

    original_sleep = rl.time.sleep

    def interrupt_on_second_call(secs):
        raise KeyboardInterrupt()

    with patch.object(rl, "finalize_segment") as mock_finalize:
        mock_finalize.return_value = tmp_path / "dummy"
        with patch.object(rl.time, "sleep", side_effect=interrupt_on_second_call):
            with patch(
                "sys.argv",
                [
                    "record_live",
                    "abc123",
                    "--match-id", "test",
                    "--staging-dir", str(staging),
                    "--ready-dir", str(ready),
                ],
            ):
                main()

    mock_proc.terminate.assert_called_once()
    mock_proc.wait.assert_called()


@patch("scripts.record_live.signal.signal")
@patch("scripts.record_live.subprocess.Popen")
def test_main_ffmpeg_exit_code_displayed(mock_popen, mock_signal, tmp_path, capsys):
    from scripts.record_live import main

    staging = tmp_path / "LIVE_SEGMENTS"
    ready = tmp_path / "LIVE_READY"
    staging.mkdir(parents=True)

    mock_proc = MagicMock()
    mock_proc.stdout = MagicMock()
    mock_proc.stderr = MagicMock()
    mock_proc.poll.return_value = 1
    mock_popen.return_value = mock_proc

    import scripts.record_live as rl

    with patch.object(rl, "finalize_segment") as mock_finalize:
        mock_finalize.return_value = tmp_path / "dummy"
        with patch.object(rl, "time") as mock_time:
            with patch(
                "sys.argv",
                [
                    "record_live",
                    "abc",
                    "--match-id", "m",
                    "--staging-dir", str(staging),
                    "--ready-dir", str(ready),
                ],
            ):
                main()

    captured = capsys.readouterr()
    assert "ffmpeg exited with code 1" in captured.out
