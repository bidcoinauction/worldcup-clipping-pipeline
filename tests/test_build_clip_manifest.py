import json
from unittest.mock import patch


def _analysis(tmp_path, data):
    p = tmp_path / "analysis.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


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
