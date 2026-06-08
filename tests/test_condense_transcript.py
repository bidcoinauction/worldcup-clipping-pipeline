import json
from pathlib import Path
from unittest.mock import patch

from pipeline.condense_transcript import condense_timestamps, _merge_windows, _text_matches_keywords


# --- helpers ---

def _make_segments(count: int, duration: int = 600) -> list[dict]:
    seg_dur = duration / count
    segments = []
    for i in range(count):
        start = round(i * seg_dur, 1)
        end = round(start + seg_dur, 1)
        text = f"segment {i}"
        if i == 20:
            text = "great goal by ronaldo"
        if i == 25:
            text = "the crowd is celebrating"
        if i == 30:
            text = "yellow card for the foul"
        segments.append({"start": start, "end": end, "text": text})
    return segments


# --- unit tests for helpers ---

def test_text_matches_keywords_goal():
    assert _text_matches_keywords("great goal")


def test_text_matches_keywords_penalty():
    assert _text_matches_keywords("penalty save")


def test_text_matches_keywords_no_match():
    assert not _text_matches_keywords("the weather is nice today")


def test_text_matches_keywords_case_insensitive():
    assert _text_matches_keywords("GREAT GOAL")


def test_merge_windows_empty():
    assert _merge_windows([]) == []


def test_merge_windows_no_overlap():
    result = _merge_windows([(0, 10), (20, 30)])
    assert result == [(0, 10), (20, 30)]


def test_merge_windows_adjacent():
    result = _merge_windows([(0, 10), (10, 20)])
    assert result == [(0, 20)]


def test_merge_windows_overlapping():
    result = _merge_windows([(0, 15), (10, 25)])
    assert result == [(0, 25)]


def test_merge_windows_contained():
    result = _merge_windows([(0, 30), (10, 20)])
    assert result == [(0, 30)]


# --- condense_timestamps tests ---

def test_empty_segments():
    assert condense_timestamps([]) == []


def test_first_60s_included():
    segs = _make_segments(100, 600)
    result = condense_timestamps(segs, coverage_interval=9999)
    assert any(s["start"] < 60 for s in result)


def test_last_60s_included():
    segs = _make_segments(100, 600)
    result = condense_timestamps(segs, coverage_interval=9999)
    assert any(s["end"] > 540 for s in result)


def test_research_anchor_creates_window():
    segs = _make_segments(100, 600)
    events = [{"video_time_seconds": 300}]
    result = condense_timestamps(segs, research_events=events, coverage_interval=9999)
    midpoints = [(s["start"] + s["end"]) / 2 for s in result]
    assert any(275 <= m <= 325 for m in midpoints), "Anchor window at 300s not found"


def test_research_anchor_no_timestamp():
    segs = _make_segments(100, 600)
    events = [{"minute_raw": "50"}]
    result = condense_timestamps(segs, research_events=events, coverage_interval=9999)
    assert len(result) > 0


def test_multiple_research_anchors():
    segs = _make_segments(100, 600)
    events = [{"video_time_seconds": 100}, {"video_time_seconds": 500}]
    result = condense_timestamps(segs, research_events=events, coverage_interval=9999)
    assert len(result) > 0


def test_keyword_not_enabled_by_default():
    segs = _make_segments(100, 600)
    result = condense_timestamps(segs, coverage_interval=9999)
    # Segment 5 says "great goal by ronaldo" but keywords are off
    # Only bookends kept (first 60s and last 60s)
    goal_segs = [s for s in result if "goal" in s["text"]]
    assert len(goal_segs) == 0, "Keywords should not match by default"


def test_keyword_enabled():
    segs = _make_segments(100, 600)
    result = condense_timestamps(segs, coverage_interval=9999, enable_keywords=True)
    goal_segs = [s for s in result if "goal" in s["text"]]
    assert len(goal_segs) > 0, "Keywords should match when enabled"


def test_reaction_keyword_crowd():
    assert _text_matches_keywords("the crowd is celebrating")


def test_event_keyword_card():
    assert _text_matches_keywords("yellow card for the foul")


def test_time_sampling_creates_windows():
    segs = _make_segments(100, 600)
    result = condense_timestamps(segs, coverage_interval=200)
    # Should have bookends + 2 sampling windows (200±15, 400±15) + merged
    assert len(result) >= 10


def test_duration_auto_calculated():
    segs = [{"start": 0, "end": 10, "text": "a"}, {"start": 10, "end": 300, "text": "b"}]
    result = condense_timestamps(segs)
    assert len(result) >= 1


def test_germany_portugal_reduction():
    ts_path = Path("TRANSCRIPTS/WORLD_CUP/2024_25_germany_portugal_1/timestamps.json")
    if not ts_path.exists():
        return
    segs = json.loads(ts_path.read_text(encoding="utf-8"))
    result = condense_timestamps(segs)
    formatted = "\n".join(
        f'[{s["start"]:.0f}s - {s["end"]:.0f}s] {s["text"]}' for s in result
    )
    assert 30 <= len(result) <= 100, f"Expected 30-100 segs, got {len(result)}"
    assert len(formatted) / 1024 <= 15, f"Prompt block exceeds 15KB ({len(formatted)/1024:.1f}KB)"


def test_germany_portugal_with_anchors():
    ts_path = Path("TRANSCRIPTS/WORLD_CUP/2024_25_germany_portugal_1/timestamps.json")
    if not ts_path.exists():
        return
    segs = json.loads(ts_path.read_text(encoding="utf-8"))
    events = [
        {"video_time_seconds": 300},
        {"video_time_seconds": 900},
        {"video_time_seconds": 1500},
        {"video_time_seconds": 2500},
    ]
    result = condense_timestamps(segs, research_events=events)
    assert len(result) <= 150


# --- generation integration test ---

def test_prompt_with_condensed_flag_is_smaller(tmp_path):
    transcript_dir = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "test_match"
    transcript_dir.mkdir(parents=True, exist_ok=True)
    segs = _make_segments(200, 1200)
    json.dump(segs, (transcript_dir / "timestamps.json").open("w", encoding="utf-8"))
    transcript_path = transcript_dir / "transcript.txt"
    transcript_path.write_text("dummy", encoding="utf-8")

    argv = [
        "prog",
        "--transcript", str(transcript_path),
        "--match-name", "test_match",
        "--mode", "package",
    ]
    with patch("sys.argv", argv):
        with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
            mock_root.__truediv__ = lambda self, other: tmp_path / other
            from scripts.generate_claude_prompt import main
            main()

    default_size = (tmp_path / "PROMPTS" / "test_match_claude_prompt.txt").stat().st_size

    argv_condensed = [
        "prog",
        "--transcript", str(transcript_path),
        "--match-name", "test_match",
        "--mode", "package",
        "--condensed-windows",
    ]
    with patch("sys.argv", argv_condensed):
        with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
            mock_root.__truediv__ = lambda self, other: tmp_path / other
            from scripts.generate_claude_prompt import main
            main()

    condensed_size = (tmp_path / "PROMPTS" / "test_match_claude_prompt.txt").stat().st_size
    assert condensed_size < default_size, "Condensed prompt should be smaller"
    assert condensed_size / 1024 <= 15, f"Condensed prompt exceeds 15KB ({condensed_size/1024:.1f}KB)"
