import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from pipeline.condense_transcript import _merge_windows
from pipeline.utils import slugify
from scripts.transcribe_research_windows import (
    build_event_context,
    compute_windows,
    offset_segments,
)

# ---------------------------------------------------------------------------
# compute_windows
# ---------------------------------------------------------------------------

EVENTS_FIXTURE = [
    {"minute_raw": "7", "type": "goal", "video_time_seconds": 420,
     "description": "First goal", "player": "Player A"},
    {"minute_raw": "23", "type": "yellow_card", "video_time_seconds": 1380,
     "description": "Rough tackle", "player": "Player B"},
    {"minute_raw": "55", "type": "goal", "video_time_seconds": 3300,
     "description": "Equalizer", "player": "Player C"},
]


def test_compute_windows_basic():
    windows = compute_windows(EVENTS_FIXTURE, padding=30.0, duration=5400.0)
    assert len(windows) == 3
    assert windows[0] == (390.0, 450.0)
    assert windows[1] == (1350.0, 1410.0)
    assert windows[2] == (3270.0, 3330.0)


def test_compute_windows_merge_adjacent():
    events = [
        {"video_time_seconds": 100},
        {"video_time_seconds": 155},
    ]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert len(windows) == 1
    assert windows[0] == (70.0, 185.0)


def test_compute_windows_merge_chain():
    events = [
        {"video_time_seconds": 100},
        {"video_time_seconds": 150},
        {"video_time_seconds": 200},
    ]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert len(windows) == 1
    start, end = windows[0]
    assert start == 70.0
    assert end == 230.0


def test_compute_windows_no_merge_distant():
    events = [
        {"video_time_seconds": 100},
        {"video_time_seconds": 400},
    ]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert len(windows) == 2


def test_compute_windows_clamp_zero():
    events = [{"video_time_seconds": 10}]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert windows[0][0] == 0.0


def test_compute_windows_clamp_duration():
    events = [{"video_time_seconds": 580}]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert windows[0][1] == 600.0


def test_compute_windows_skips_no_timestamp():
    events = [{"minute_raw": "7", "type": "goal"}]
    windows = compute_windows(events, padding=30.0, duration=600.0)
    assert windows == []


def test_compute_windows_empty_events():
    windows = compute_windows([], padding=30.0, duration=600.0)
    assert windows == []


def test_compute_windows_no_duration_passthrough():
    events = [{"video_time_seconds": 500}]
    windows = compute_windows(events, padding=30.0, duration=0.0)
    assert windows[0] == (470.0, 530.0)


# ---------------------------------------------------------------------------
# _merge_windows (imported from condense_transcript)
# ---------------------------------------------------------------------------

def test_merge_windows_empty():
    assert _merge_windows([]) == []


def test_merge_windows_no_overlap():
    assert _merge_windows([(0, 10), (20, 30)]) == [(0, 10), (20, 30)]


def test_merge_windows_overlapping():
    assert _merge_windows([(0, 15), (10, 25)]) == [(0, 25)]


# ---------------------------------------------------------------------------
# build_event_context
# ---------------------------------------------------------------------------

def test_build_event_context_in_window():
    events = [
        {"video_time_seconds": 100, "type": "goal", "description": "Opener", "player": "A"},
        {"video_time_seconds": 200, "type": "yellow_card", "description": "Foul", "player": "B"},
    ]
    ctx = build_event_context(events, 90.0, 110.0)
    assert "[GOAL] Opener (A)" in ctx
    assert "[YELLOW_CARD]" not in ctx


def test_build_event_context_outside_window():
    events = [{"video_time_seconds": 500, "type": "goal", "description": "Late goal"}]
    ctx = build_event_context(events, 0.0, 100.0)
    assert ctx == ""


def test_build_event_context_no_player():
    events = [{"video_time_seconds": 50, "type": "half_time", "description": "Half time"}]
    ctx = build_event_context(events, 0.0, 100.0)
    assert "[HALF_TIME] Half time" in ctx


# ---------------------------------------------------------------------------
# offset_segments
# ---------------------------------------------------------------------------

def test_offset_segments():
    segments = [{"start": 1.0, "end": 3.5, "text": "hello"}]
    result = offset_segments(segments, 100.0)
    assert result[0]["start"] == 101.0
    assert result[0]["end"] == 103.5
    assert result[0]["text"] == "hello"


def test_offset_segments_empty():
    assert offset_segments([], 50.0) == []


# ---------------------------------------------------------------------------
# extract_audio_window — ffmpeg command shape
# ---------------------------------------------------------------------------

@patch("scripts.transcribe_research_windows.subprocess.run")
def test_extract_audio_window_ffmpeg_command(mock_run, tmp_path):
    from scripts.transcribe_research_windows import extract_audio_window

    video = tmp_path / "match.mp4"
    out = tmp_path / "audio.m4a"
    extract_audio_window(video, out, 100.0, 130.0)

    (cmd,) = mock_run.call_args[0]
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    assert str(100.0) in cmd
    assert "-i" in cmd
    assert str(video) in cmd
    assert "-t" in cmd
    assert str(30.0) in cmd
    assert "-vn" in cmd
    assert "-acodec" in cmd


# ---------------------------------------------------------------------------
# main — dry-run
# ---------------------------------------------------------------------------

@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="faster-whisper")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
@patch("scripts.transcribe_research_windows.get_video_duration", return_value=5400.0)
def test_dry_run_prints_windows_and_events(mock_duration, mock_leagues, mock_provider, mock_model, capsys):
    from scripts.transcribe_research_windows import main

    research_data = {
        "events": [
            {"minute_raw": "7", "type": "goal", "video_time_seconds": 420,
             "description": "First goal", "player": "A"},
            {"minute_raw": "55", "type": "goal", "video_time_seconds": 3300,
             "description": "Equalizer"},
        ]
    }
    with patch("scripts.transcribe_research_windows.Path.exists", return_value=True):
        with patch("scripts.transcribe_research_windows.Path.read_text",
                   return_value=json.dumps(research_data)):
            with patch(
                "sys.argv",
                ["transcribe_research_windows",
                 "--research", "/fake/research.json",
                 "--source-video", "/fake/match.mp4",
                 "--league", "WORLD_CUP",
                 "--dry-run"],
            ):
                main()

    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "Window 1" in captured.out
    assert "Window 2" in captured.out
    assert "[GOAL] First goal (A)" in captured.out
    assert "[GOAL] Equalizer" in captured.out
    assert "5400.0s" in captured.out or "90.0" in captured.out


@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="faster-whisper")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
@patch("scripts.transcribe_research_windows.get_video_duration", return_value=5400.0)
def test_dry_run_does_not_write_files(mock_duration, mock_leagues, mock_provider, mock_model, tmp_path):
    from scripts.transcribe_research_windows import main

    research_data = {
        "events": [
            {"minute_raw": "7", "type": "goal", "video_time_seconds": 420},
        ]
    }
    with patch("scripts.transcribe_research_windows.Path.exists", return_value=True):
        with patch("scripts.transcribe_research_windows.Path.read_text",
                   return_value=json.dumps(research_data)):
            with patch(
                "sys.argv",
                ["transcribe_research_windows",
                 "--research", "/fake/research.json",
                 "--source-video", "/fake/match.mp4",
                 "--league", "WORLD_CUP",
                 "--dry-run"],
            ):
                main()

    assert not (tmp_path / "TRANSCRIPTS").exists()


# ---------------------------------------------------------------------------
# main — error exits
# ---------------------------------------------------------------------------

@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="faster-whisper")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
def test_missing_research_file(mock_leagues, mock_provider, mock_model):
    from scripts.transcribe_research_windows import main

    with patch("scripts.transcribe_research_windows.Path.exists", return_value=False):
        with patch(
            "sys.argv",
            ["transcribe_research_windows",
             "--research", "/nonexistent/research.json",
             "--source-video", "/fake/match.mp4",
             "--league", "WORLD_CUP"],
        ):
            try:
                main()
            except SystemExit as e:
                assert e.code is not None
                return
    assert False, "Expected SystemExit"


@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="faster-whisper")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
@patch("scripts.transcribe_research_windows.Path.exists", return_value=True)
def test_no_events_with_timestamps_exits(mock_exists, mock_leagues, mock_provider, mock_model):
    from scripts.transcribe_research_windows import main

    research_data = {
        "events": [
            {"minute_raw": "7", "type": "goal"},  # no video_time_seconds
        ]
    }
    with patch("scripts.transcribe_research_windows.Path.read_text",
               return_value=json.dumps(research_data)):
        with patch("scripts.transcribe_research_windows.get_video_duration", return_value=5400.0):
            with patch(
                "sys.argv",
                ["transcribe_research_windows",
                 "--research", "/fake/research.json",
                 "--source-video", "/fake/match.mp4",
                 "--league", "WORLD_CUP"],
            ):
                try:
                    main()
                except SystemExit as e:
                    assert e.code is not None
                    return
    assert False, "Expected SystemExit"


# ---------------------------------------------------------------------------
# full flow mocked
# ---------------------------------------------------------------------------

@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="openai")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
@patch("scripts.transcribe_research_windows.subprocess.run")
@patch("scripts.transcribe_research_windows.transcribe_window_openai")
@patch("scripts.transcribe_research_windows.get_video_duration", return_value=5400.0)
def test_full_flow_produces_standard_output(
    mock_duration, mock_transcribe, mock_ffmpeg, mock_leagues, mock_provider, mock_model, tmp_path
):
    from scripts.transcribe_research_windows import main

    mock_transcribe.return_value = (
        "some transcript",
        [{"start": 0.0, "end": 2.0, "text": "hello world"}],
    )

    research_data = {
        "events": [
            {"minute_raw": "7", "type": "goal", "video_time_seconds": 420,
             "description": "First goal"},
        ]
    }

    with patch("scripts.transcribe_research_windows.Path.exists", return_value=True):
        with patch("scripts.transcribe_research_windows.Path.read_text",
                   return_value=json.dumps(research_data)):
            with patch("scripts.transcribe_research_windows.ROOT", tmp_path):
                with patch(
                    "sys.argv",
                    ["transcribe_research_windows",
                     "--research", "/fake/research.json",
                     "--source-video", "/fake/match.mp4",
                     "--league", "WORLD_CUP"],
                ):
                    main()

    slug = slugify("match")
    out_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / slug

    assert (out_dir / "transcript.txt").exists()
    assert (out_dir / "timestamps.json").exists()
    assert (out_dir / "metadata.json").exists()

    segments = json.loads((out_dir / "timestamps.json").read_text())
    assert len(segments) == 1
    assert segments[0]["start"] == 390.0
    assert segments[0]["end"] == 392.0
    assert segments[0]["text"] == "hello world"

    meta = json.loads((out_dir / "metadata.json").read_text())
    assert meta["method"] == "research_windows"
    assert meta["windows"] == 1
    assert meta["match_slug"] == slug


@patch("scripts.transcribe_research_windows._get_model", return_value="base")
@patch("scripts.transcribe_research_windows.get_provider", return_value="openai")
@patch("scripts.transcribe_research_windows.get_leagues", return_value=["WORLD_CUP"])
@patch("scripts.transcribe_research_windows.subprocess.run")
@patch("scripts.transcribe_research_windows.transcribe_window_openai")
@patch("scripts.transcribe_research_windows.get_video_duration", return_value=5400.0)
def test_full_flow_temp_audio_cleaned(mock_duration, mock_transcribe, mock_ffmpeg, mock_leagues, mock_provider, mock_model, tmp_path):
    from scripts.transcribe_research_windows import main

    mock_transcribe.return_value = ("tx", [{"start": 0.0, "end": 1.0, "text": "x"}])

    research_data = {
        "events": [
            {"video_time_seconds": 420},
        ]
    }

    with patch("scripts.transcribe_research_windows.Path.exists", return_value=True):
        with patch("scripts.transcribe_research_windows.Path.read_text",
                   return_value=json.dumps(research_data)):
            with patch("scripts.transcribe_research_windows.ROOT", tmp_path):
                with patch(
                    "sys.argv",
                    ["transcribe_research_windows",
                     "--research", "/fake/research.json",
                     "--source-video", "/fake/match.mp4",
                     "--league", "WORLD_CUP"],
                ):
                    main()

    slug = slugify("match")
    out_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / slug
    temp_audios = list(out_dir.glob("window_*.m4a"))
    assert temp_audios == [], f"Temp audio files not cleaned: {temp_audios}"


# ---------------------------------------------------------------------------
# output schema matches transcribe_match.py
# ---------------------------------------------------------------------------

def test_timestamps_schema_flat():
    segments = [{"start": 0.0, "end": 2.5, "text": "hello"}]
    raw = json.dumps(segments)
    loaded = json.loads(raw)
    assert all(k in loaded[0] for k in ("start", "end", "text"))
    assert isinstance(loaded[0]["start"], float)


def test_metadata_schema():
    meta = {
        "input": "/v.mp4",
        "method": "research_windows",
        "windows": 2,
        "window_padding": 30,
        "league": "WORLD_CUP",
        "match_slug": "test",
        "model": "base",
        "created_at": "2026-01-01T00:00:00Z",
    }
    assert "method" in meta
    assert "input" in meta
