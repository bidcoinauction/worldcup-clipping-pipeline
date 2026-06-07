import json
from unittest.mock import patch, MagicMock
from pathlib import Path


RESEARCH_DATA = {
    "match": {"home_team": "Argentina", "away_team": "France"},
    "events": [
        {"minute_raw": "23", "type": "goal", "description": "Messi scores",
         "player": "Messi", "importance": "high"},
        {"minute_raw": "36", "type": "goal", "description": "Di Maria scores",
         "player": "Di Maria", "importance": "high"},
    ],
}


def _research_file(tmp_path, data=None):
    d = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "test_match"
    d.mkdir(parents=True, exist_ok=True)
    path = d / "match_research.json"
    path.write_text(json.dumps(data or RESEARCH_DATA, indent=2), encoding="utf-8")
    return path


def _video_file(tmp_path):
    path = tmp_path / "test_video.mp4"
    path.write_text("fake video", encoding="utf-8")
    return path


def _run_main(tmp_path, research_data=None, extra_args=None, inputs=None):
    research = _research_file(tmp_path, data=research_data)
    video = _video_file(tmp_path)
    argv = ["prog", "--research", str(research), "--source-video", str(video)]
    if extra_args:
        argv += extra_args

    def mock_input(_prompt=""):
        if inputs is None or len(inputs) == 0:
            return ""
        return inputs.pop(0)

    with patch("sys.argv", argv):
        with patch("builtins.input", mock_input):
            with patch("scripts.map_research_timestamps.get_video_duration",
                       return_value=120.0):
                from scripts.map_research_timestamps import main
                main()

    return research


def test_sets_video_time_seconds(tmp_path):
    research = _run_main(tmp_path, inputs=["45", "78"])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert data["events"][0]["video_time_seconds"] == 45
    assert data["events"][1]["video_time_seconds"] == 78


def test_skip_with_empty_input(tmp_path):
    research = _run_main(tmp_path, inputs=["", ""])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert "video_time_seconds" not in data["events"][0]
    assert "video_time_seconds" not in data["events"][1]


def test_rejects_negative(tmp_path):
    research = _run_main(tmp_path, inputs=["-5", "45"])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert "video_time_seconds" not in data["events"][0]
    assert data["events"][1]["video_time_seconds"] == 45


def test_rejects_above_duration(tmp_path):
    research = _run_main(tmp_path, inputs=["200", "45"])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert "video_time_seconds" not in data["events"][0]
    assert data["events"][1]["video_time_seconds"] == 45


def test_rejects_non_integer(tmp_path):
    research = _run_main(tmp_path, inputs=["abc", "45"])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert "video_time_seconds" not in data["events"][0]
    assert data["events"][1]["video_time_seconds"] == 45


def test_dry_run_does_not_write(tmp_path):
    research = _run_main(tmp_path, extra_args=["--dry-run"], inputs=["45"])
    data = json.loads(research.read_text(encoding="utf-8"))
    assert "video_time_seconds" not in data["events"][0]


def test_preserves_existing_without_force(tmp_path):
    data = RESEARCH_DATA.copy()
    data["events"] = [
        {"minute_raw": "23", "type": "goal", "video_time_seconds": 45,
         "description": "Messi scores", "player": "Messi"},
        {"minute_raw": "36", "type": "goal", "description": "Di Maria scores",
         "player": "Di Maria"},
    ]
    research = _run_main(tmp_path, research_data=data, inputs=["60"])
    result = json.loads(research.read_text(encoding="utf-8"))
    assert result["events"][0]["video_time_seconds"] == 45
    assert result["events"][1]["video_time_seconds"] == 60


def test_force_replaces_existing(tmp_path):
    data = RESEARCH_DATA.copy()
    data["events"] = [
        {"minute_raw": "23", "type": "goal", "video_time_seconds": 45,
         "description": "Messi scores", "player": "Messi"},
        {"minute_raw": "36", "type": "goal", "description": "Di Maria scores",
         "player": "Di Maria"},
    ]
    research = _run_main(tmp_path, research_data=data,
                         extra_args=["--force"], inputs=["99", "60"])
    result = json.loads(research.read_text(encoding="utf-8"))
    assert result["events"][0]["video_time_seconds"] == 99
    assert result["events"][1]["video_time_seconds"] == 60
