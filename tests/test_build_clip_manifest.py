import csv
import json
from unittest.mock import patch

import pytest

from pipeline.clip_manifest import FIELDNAMES, build_clip_manifest, write_clip_manifest
from pipeline.config_errors import ConfigurationError


def _analysis(tmp_path, data):
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _clip(category="EMOTION", start="00:01:00", end="00:01:15"):
    return {
        "clip_id": "001",
        "category": category,
        "start_time": start,
        "end_time": end,
        "virality_score": 8,
        "retention_reason": "crowd",
        "share_reason": "epic",
        "hook_text": "Watch this",
        "caption": "Amazing",
        "thumbnail_idea": "zoom",
        "manual_scrub_note": "check faces",
        "platform_notes": {"tiktok": "fast cuts", "reels": "slow mo"},
    }


def test_service_builds_structured_result_default_football(tmp_path):
    analysis = _analysis(tmp_path, [_clip()])
    result = build_clip_manifest(
        analysis,
        league="PREMIER_LEAGUE",
        match_name="Test Match",
        source_video="source.mp4",
    )
    assert result["profile"] == "football"
    assert result["sport"] == "football"
    assert result["analysis_input"] == str(analysis)
    assert result["output_path"] is None
    assert result["clip_window_count"] == 1
    assert result["fieldnames"] == FIELDNAMES
    assert result["coverage"] == {
        "parsed_windows": 1,
        "total_clip_seconds": 15.0,
        "first_start_seconds": 60.0,
        "last_end_seconds": 75.0,
    }
    assert result["warnings"] == []
    assert result["rows"][0]["clip_id"] == "PREMIER_LEAGUE_test_match_001"
    assert result["rows"][0]["source_video"] == "source.mp4"


def test_service_writes_existing_csv_schema(tmp_path):
    result = write_clip_manifest(
        [_clip("chaos", "5", "20")],
        league="UCL",
        match_name="Big Match",
        output_root=tmp_path,
    )
    out = tmp_path / "CLIP_MANIFESTS" / "big_match_manifest.csv"
    assert result["output_path"] == out
    with out.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
    assert reader.fieldnames == FIELDNAMES
    assert rows[0]["clip_id"] == "UCL_big_match_001"
    assert rows[0]["category"] == "CHAOS"
    assert rows[0]["status"] == "needs_visual_scrub"


def test_service_explicit_basketball_profile_for_sport_neutral_manifest(tmp_path):
    result = build_clip_manifest(
        [_clip("momentum", "10", "18")],
        league="BASKETBALL",
        match_name="Lakers vs Celtics",
        profile="basketball",
    )
    assert result["profile"] == "basketball"
    assert result["sport"] == "basketball"
    assert result["rows"][0]["clip_id"] == "BASKETBALL_lakers_vs_celtics_001"
    assert result["rows"][0]["category"] == "MOMENTUM"


def test_service_unknown_profile_fails_closed(tmp_path):
    with pytest.raises(ConfigurationError, match="unknown profile"):
        build_clip_manifest([_clip()], league="X", match_name="x", profile="lacrosse")


def test_service_non_production_profile_rejected(tmp_path):
    with pytest.raises(ConfigurationError, match="not production-capable"):
        build_clip_manifest([_clip()], league="X", match_name="x", profile="basketball_sandbox")


def test_cli_uses_shared_clip_manifest_service(tmp_path):
    analysis = _analysis(tmp_path, [_clip()])
    output = tmp_path / "out.csv"
    with patch("scripts.build_clip_manifest.write_clip_manifest", return_value={"output_path": output}) as shared:
        from scripts.build_clip_manifest import main

        with patch("sys.argv", [
            "prog", "--analysis", str(analysis), "--league", "BASKETBALL",
            "--match-name", "Lakers vs Celtics", "--profile", "basketball",
        ]):
            main()
    shared.assert_called_once()
    assert shared.call_args.args == (str(analysis),)
    assert shared.call_args.kwargs["profile"] == "basketball"
    assert shared.call_args.kwargs["league"] == "BASKETBALL"


@patch("scripts.build_clip_manifest.ROOT")
def test_converts_json_list_to_csv(mock_root, tmp_path):
    mock_root.__truediv__ = lambda self, other: tmp_path / other
    analysis = _analysis(tmp_path, [
        {"clip_id": "001", "category": "EMOTION", "start_time": "00:01:00",
         "end_time": "00:01:15", "virality_score": 8,
         "retention_reason": "crowd", "share_reason": "epic",
         "hook_text": "Watch this", "caption": "Amazing",
         "thumbnail_idea": "zoom", "manual_scrub_note": "check faces",
         "platform_notes": {"tiktok": "fast cuts", "reels": "slow mo"}},
    ])

    from scripts.build_clip_manifest import main

    with patch("sys.argv", [
        "prog", "--analysis", str(analysis),
        "--league", "PREMIER_LEAGUE", "--match-name", "Test Match",
    ]):
        main()

    out = tmp_path / "CLIP_MANIFESTS" / "test_match_manifest.csv"
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "clip_id" in content
    assert "EMOTION" in content
    assert "PREMIER_LEAGUE" in content
    assert "fast cuts" in content


@patch("scripts.build_clip_manifest.ROOT")
def test_handles_wrapped_clips_key(mock_root, tmp_path):
    mock_root.__truediv__ = lambda self, other: tmp_path / other
    analysis = _analysis(tmp_path, {
        "clips": [{"clip_id": "001", "category": "CHAOS", "start_time": "00:00:05",
                   "end_time": "00:00:20", "platform_notes": {}}],
    })

    from scripts.build_clip_manifest import main

    with patch("sys.argv", [
        "prog", "--analysis", str(analysis),
        "--league", "UCL", "--match-name", "Big Match",
    ]):
        main()

    out = tmp_path / "CLIP_MANIFESTS" / "big_match_manifest.csv"
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "CHAOS" in content
    assert "UCL" in content


@patch("scripts.build_clip_manifest.ROOT")
def test_applies_status_default(mock_root, tmp_path):
    mock_root.__truediv__ = lambda self, other: tmp_path / other
    analysis = _analysis(tmp_path, [
        {"clip_id": "X01", "category": "AMERICA", "start_time": "00:00:00",
         "end_time": "00:00:10", "platform_notes": None},
    ])

    from scripts.build_clip_manifest import main

    with patch("sys.argv", [
        "prog", "--analysis", str(analysis),
        "--league", "MLS", "--match-name", "Another Match",
    ]):
        main()

    out = tmp_path / "CLIP_MANIFESTS" / "another_match_manifest.csv"
    content = out.read_text(encoding="utf-8")
    assert "needs_visual_scrub" in content
