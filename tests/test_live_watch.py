import json
import os
import sys
from pathlib import Path
from unittest.mock import ANY, MagicMock, patch, call

from scripts.live_watch import (
    parse_segment_name,
    load_registry,
    save_registry,
    find_ready_segments,
    extract_audio,
    write_transcript_outputs,
    transcribe_segment,
    REGISTRY_FILENAME,
)


def test_parse_segment_name_valid():
    assert parse_segment_name("argentina_france_S0001.ts") == ("argentina_france", 1)
    assert parse_segment_name("test_S0000.ts") == ("test", 0)
    assert parse_segment_name("abc_S9999.ts") == ("abc", 9999)
    assert parse_segment_name("a_b_c_S0042.ts") == ("a_b_c", 42)


def test_parse_segment_name_invalid():
    assert parse_segment_name("no_match.ts") is None
    assert parse_segment_name("bad_Sfoo.ts") is None
    assert parse_segment_name("nosuffix") is None
    assert parse_segment_name("_S.ts") is None


def test_parse_segment_name_handles_double_extension():
    result = parse_segment_name("match_S0001.ts.tmp")
    assert result is None


def test_load_registry_not_found(tmp_path):
    data = load_registry(tmp_path / "nonexistent.json")
    assert data == {"version": 1, "segments": {}}


def test_load_registry_empty_file(tmp_path):
    p = tmp_path / "reg.json"
    p.write_text("{}")
    data = load_registry(p)
    assert data.get("version") == 1
    assert data["segments"] == {}


def test_load_registry_corrupt(tmp_path):
    p = tmp_path / "reg.json"
    p.write_text("not json")
    data = load_registry(p)
    assert data == {"version": 1, "segments": {}}


def test_load_registry_valid(tmp_path):
    p = tmp_path / "reg.json"
    p.write_text(json.dumps({"version": 1, "segments": {"a.ts": {"state": "transcribed"}}}))
    data = load_registry(p)
    assert data["segments"]["a.ts"]["state"] == "transcribed"


def test_save_registry_atomic_write(tmp_path):
    p = tmp_path / "registry.json"
    data = {"version": 1, "segments": {"s.ts": {"state": "transcribed"}}}
    save_registry(p, data)
    assert p.exists()
    assert json.loads(p.read_text(encoding="utf-8")) == data
    tmp_files = list(tmp_path.glob("*.tmp.json"))
    assert len(tmp_files) == 0


def test_save_registry_updates_version(tmp_path):
    p = tmp_path / "reg.json"
    save_registry(p, {"segments": {}})
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["version"] == 1


def test_find_ready_segments_no_status_skipped(tmp_path):
    (tmp_path / "match_S0001.ts").write_text("x")
    result = find_ready_segments(tmp_path)
    assert result == []


def test_find_ready_segments_with_status(tmp_path):
    (tmp_path / "match_S0001.ts").write_text("x")
    (tmp_path / "match_S0001.status.json").write_text("{}")
    result = find_ready_segments(tmp_path)
    assert len(result) == 1
    assert result[0].name == "match_S0001.ts"


def test_find_ready_segments_skips_bad_names(tmp_path):
    (tmp_path / "bad.ts").write_text("x")
    (tmp_path / "bad.status.json").write_text("{}")
    result = find_ready_segments(tmp_path)
    assert result == []


def test_find_ready_segments_filter_by_match_id(tmp_path):
    for m in ["a_S0001.ts", "a_S0002.ts", "b_S0001.ts"]:
        (tmp_path / m).write_text("x")
        (tmp_path / m.replace(".ts", ".status.json")).write_text("{}")
    result = find_ready_segments(tmp_path, match_id_filter="a")
    assert len(result) == 2
    assert all("a_S" in p.name for p in result)


def test_find_ready_segments_filter_no_match(tmp_path):
    (tmp_path / "a_S0001.ts").write_text("x")
    (tmp_path / "a_S0001.status.json").write_text("{}")
    result = find_ready_segments(tmp_path, match_id_filter="b")
    assert result == []


def test_find_ready_segments_sorted(tmp_path):
    (tmp_path / "m_S0002.ts").write_text("x")
    (tmp_path / "m_S0002.status.json").write_text("{}")
    (tmp_path / "m_S0001.ts").write_text("x")
    (tmp_path / "m_S0001.status.json").write_text("{}")
    result = find_ready_segments(tmp_path)
    assert result[0].name == "m_S0001.ts"
    assert result[1].name == "m_S0002.ts"


@patch("scripts.live_watch.subprocess.run")
def test_extract_audio_builds_correct_command(mock_run, tmp_path):
    seg = tmp_path / "seg.ts"
    seg.write_text("x")
    out = tmp_path / "audio.m4a"
    extract_audio(seg, out)
    mock_run.assert_called_once()
    cmd = mock_run.call_args[0][0]
    assert cmd[0] == "ffmpeg"
    assert "-i" in cmd
    assert str(seg) in cmd
    assert str(out) in cmd


@patch("scripts.live_watch.subprocess.run")
def test_extract_audio_creates_parent_dir(mock_run, tmp_path):
    seg = tmp_path / "seg.ts"
    seg.write_text("x")
    out = tmp_path / "sub" / "deep" / "audio.m4a"
    extract_audio(seg, out)
    assert out.parent.exists()


def test_write_transcript_outputs_creates_three_files(tmp_path):
    txt, ts, md = write_transcript_outputs(
        transcript_dir=tmp_path,
        full_text="hello world",
        segments=[{"start": 0.0, "end": 1.0, "text": "hello"}],
        metadata={"model": "base"},
    )
    assert txt == tmp_path / "transcript.txt"
    assert ts == tmp_path / "timestamps.json"
    assert md == tmp_path / "metadata.json"
    assert txt.read_text(encoding="utf-8") == "hello world"
    assert json.loads(ts.read_text(encoding="utf-8")) == [{"start": 0.0, "end": 1.0, "text": "hello"}]
    assert json.loads(md.read_text(encoding="utf-8"))["model"] == "base"


def test_write_transcript_outputs_creates_dir(tmp_path):
    nested = tmp_path / "a" / "b"
    write_transcript_outputs(nested, "", [], {})
    assert nested.exists()


def test_write_transcript_outputs_empty_text(tmp_path):
    txt, ts, md = write_transcript_outputs(tmp_path, "", [], {"k": "v"})
    assert txt.read_text(encoding="utf-8") == ""
    assert json.loads(ts.read_text(encoding="utf-8")) == []


def test_transcribe_segment_extracts_and_transcribes(tmp_path):
    mock_whisper_module = MagicMock()
    mock_whisper_module.transcribe = MagicMock(return_value=(
        "hello world", [{"start": 0.0, "end": 1.0, "text": "hello"}]
    ))
    import sys as _sys
    _sys.modules["pipeline.whisper_transcriber"] = mock_whisper_module

    with patch("scripts.live_watch.subprocess.run") as mock_ffmpeg:
        seg = tmp_path / "segments" / "match_S0001.ts"
        seg.parent.mkdir()
        seg.write_text("video data")
        status = {"match_id": "match", "segment_number": 1, "duration_seconds": 900.0}
        transcript_dir = tmp_path / "transcripts" / "match_S0001"

        txt, ts, md = transcribe_segment(seg, status, transcript_dir, "base")

        assert txt.exists()
        assert ts.exists()
        assert md.exists()
        mock_ffmpeg.assert_called_once()
        mock_whisper_module.transcribe.assert_called_once_with(ANY, model_size="base")


def test_transcribe_segment_cleans_up_audio(tmp_path):
    mock_whisper_module = MagicMock()
    mock_whisper_module.transcribe = MagicMock(return_value=("x", []))
    import sys as _sys
    _sys.modules["pipeline.whisper_transcriber"] = mock_whisper_module

    with patch("scripts.live_watch.subprocess.run") as mock_ffmpeg:
        seg = tmp_path / "match_S0001.ts"
        seg.write_text("x")
        status = {"match_id": "m", "segment_number": 1}
        transcript_dir = tmp_path / "out"
        transcribe_segment(seg, status, transcript_dir, "base")
        audio_files = list(transcript_dir.glob("*_audio.*"))
        assert len(audio_files) == 0


def test_transcribe_segment_dry_run(tmp_path, capsys):
    seg = tmp_path / "match_S0001.ts"
    seg.write_text("x")
    status = {"match_id": "match", "segment_number": 1, "duration_seconds": 900.0}
    transcript_dir = tmp_path / "out"
    result = transcribe_segment(seg, status, transcript_dir, "base", dry_run=True)
    assert result is None
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "match" in captured.out


def test_main_dry_run_with_no_segments(tmp_path, capsys):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir), "--dry-run"]):
        main()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "No ready segments" in captured.out


def test_main_dry_run_with_segments(tmp_path, capsys):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    (watch_dir / "match_S0001.ts").write_text("x")
    (watch_dir / "match_S0001.status.json").write_text(
        json.dumps({"match_id": "match", "segment_number": 1, "duration_seconds": 900.0})
    )
    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir), "--dry-run"]):
        main()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "match_S0001.ts" in captured.out


def test_main_exits_if_watch_dir_missing(tmp_path, capsys):
    import pytest
    from scripts.live_watch import main
    with patch("sys.argv", ["live_watch", "--watch-dir", str(tmp_path / "NONEXISTENT")]):
        with pytest.raises(SystemExit) as exc_info:
            main()
    assert exc_info.value.code == 1


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.transcribe_segment")
@patch("scripts.live_watch.find_ready_segments")
@patch("scripts.live_watch.load_registry")
def test_main_claims_and_transcribes_segment(
    mock_load, mock_find, mock_transcribe, mock_save, mock_sleep, tmp_path, capsys
):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    seg_path = watch_dir / "match_S0001.ts"
    seg_path.write_text("x")
    (watch_dir / "match_S0001.status.json").write_text(
        json.dumps({"match_id": "match", "segment_number": 1, "duration_seconds": 900.0})
    )

    mock_load.return_value = {"version": 1, "segments": {}}
    mock_find.return_value = [seg_path]
    mock_transcribe.return_value = (
        watch_dir / "transcript.txt",
        watch_dir / "timestamps.json",
        watch_dir / "metadata.json",
    )

    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir)]):
        main()

    mock_transcribe.assert_called_once()
    assert mock_save.call_count >= 3
    assert mock_save.call_args_list[-1][0][1]["segments"]["match_S0001.ts"]["state"] == "transcribed"


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.transcribe_segment")
@patch("scripts.live_watch.find_ready_segments")
@patch("scripts.live_watch.load_registry")
def test_main_skips_already_transcribed(
    mock_load, mock_find, mock_transcribe, mock_save, mock_sleep, tmp_path, capsys
):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    seg_path = watch_dir / "match_S0001.ts"
    seg_path.write_text("x")
    (watch_dir / "match_S0001.status.json").write_text(
        json.dumps({"match_id": "match", "segment_number": 1})
    )

    mock_load.return_value = {
        "version": 1,
        "segments": {"match_S0001.ts": {"state": "transcribed"}},
    }
    mock_find.return_value = [seg_path]

    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir)]):
        main()

    mock_transcribe.assert_not_called()


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.transcribe_segment")
@patch("scripts.live_watch.find_ready_segments")
@patch("scripts.live_watch.load_registry")
def test_main_retries_orphaned_claims(
    mock_load, mock_find, mock_transcribe, mock_save, mock_sleep, tmp_path, capsys
):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    seg_path = watch_dir / "match_S0001.ts"
    seg_path.write_text("x")
    (watch_dir / "match_S0001.status.json").write_text(
        json.dumps({"match_id": "match", "segment_number": 1})
    )

    mock_load.return_value = {
        "version": 1,
        "segments": {
            "match_S0001.ts": {
                "state": "claimed",
                "claimed_at": "2026-06-11T10:00:00",
            },
        },
    }
    mock_find.return_value = [seg_path]
    mock_transcribe.return_value = (
        watch_dir / "transcript.txt",
        watch_dir / "timestamps.json",
        watch_dir / "metadata.json",
    )

    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir)]):
        main()

    captured = capsys.readouterr()
    assert "previously orphaned" in captured.out
    assert "retry" in captured.out
    mock_transcribe.assert_called_once()


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.transcribe_segment")
@patch("scripts.live_watch.find_ready_segments")
@patch("scripts.live_watch.load_registry")
def test_main_transcribe_error_sets_failed_state(
    mock_load, mock_find, mock_transcribe, mock_save, mock_sleep, tmp_path
):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    seg_path = watch_dir / "match_S0001.ts"
    seg_path.write_text("x")
    (watch_dir / "match_S0001.status.json").write_text(
        json.dumps({"match_id": "match", "segment_number": 1})
    )

    mock_load.return_value = {"version": 1, "segments": {}}
    mock_find.return_value = [seg_path]
    mock_transcribe.side_effect = RuntimeError("whisper crashed")

    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir)]):
        main()

    failed_calls = [
        c for c in mock_save.call_args_list
        if c[0][1]["segments"].get("match_S0001.ts", {}).get("state") == "failed"
    ]
    assert len(failed_calls) >= 1


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.transcribe_segment")
@patch("scripts.live_watch.find_ready_segments")
@patch("scripts.live_watch.load_registry")
def test_main_match_id_filter(
    mock_load, mock_find, mock_transcribe, mock_save, mock_sleep, tmp_path
):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    seg_a = watch_dir / "a_S0001.ts"
    seg_a.write_text("x")
    (watch_dir / "a_S0001.status.json").write_text("{}")
    seg_b = watch_dir / "b_S0001.ts"
    seg_b.write_text("x")
    (watch_dir / "b_S0001.status.json").write_text("{}")

    mock_load.return_value = {"version": 1, "segments": {}}
    mock_find.side_effect = lambda w, m: (
        [seg_a] if m == "a" else [seg_b] if m == "b" else []
    )
    mock_transcribe.return_value = (tmp_path / "x", tmp_path / "y", tmp_path / "z")

    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir), "--match-id", "a"]):
        main()

    mock_find.assert_called_once_with(watch_dir, "a")


@patch("scripts.live_watch.time.sleep", side_effect=KeyboardInterrupt)
@patch("scripts.live_watch.save_registry")
@patch("scripts.live_watch.load_registry")
def test_main_initial_log_orphaned_claims(mock_load, mock_save, mock_sleep, tmp_path, capsys):
    from scripts.live_watch import main
    watch_dir = tmp_path / "LIVE_READY"
    watch_dir.mkdir()
    mock_load.return_value = {
        "version": 1,
        "segments": {
            "m_S0001.ts": {"state": "claimed", "claimed_at": "2026-06-11T10:00:00"},
            "m_S0002.ts": {"state": "transcribed", "completed_at": "2026-06-11T11:00:00"},
        },
    }
    with patch("sys.argv", ["live_watch", "--watch-dir", str(watch_dir)]):
        main()
    captured = capsys.readouterr()
    assert "previously orphaned" in captured.out
    assert "m_S0001.ts" in captured.out
    assert "m_S0002.ts" not in captured.out
