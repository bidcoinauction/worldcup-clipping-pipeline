import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    resolve_export_destination,
    resolve_export_profile,
    resolve_platform_export_profile,
    validate_export_profiles,
)


# ── Platform profiles ───────────────────────────────────────────────────────

def test_platform_profiles_resolve_case_insensitive():
    for label in ("TIKTOK", "tiktok", "TikTok"):
        profile = resolve_platform_export_profile(label)
        assert profile["id"] == "tiktok"
    assert resolve_platform_export_profile("REELS")["id"] == "reels"
    assert resolve_platform_export_profile("SHORTS")["id"] == "shorts"


def test_platform_width_height_unchanged():
    for label in ("TIKTOK", "REELS", "SHORTS"):
        profile = resolve_platform_export_profile(label)
        assert (profile["width"], profile["height"]) == (1080, 1920)


def test_platform_codecs_unchanged():
    profile = resolve_platform_export_profile("TIKTOK")
    assert profile["video_codec"] == "libx264"
    assert profile["audio_codec"] == "aac"
    assert profile["extension"] == "mp4"


def test_platform_destination_subdir_unchanged():
    assert resolve_platform_export_profile("TIKTOK")["destination"] == "EXPORTS"


# ── Research profiles ───────────────────────────────────────────────────────

def test_research_profiles_resolve():
    for profile_id in (
        "vertical_clean", "vertical_blur", "vertical_review", "vertical_safe",
        "vertical_zoom", "vertical_social", "vertical_social_dynamic",
        "goal_context", "source",
    ):
        assert resolve_export_profile(profile_id)["id"] == profile_id


def test_research_profile_encoding_values_unchanged():
    profile = resolve_export_profile("vertical_clean")
    assert (profile["width"], profile["height"]) == (1080, 1920)
    assert profile["video_codec"] == "libx264"
    assert profile["audio_codec"] == "aac"
    assert profile["audio_bitrate"] == "160k"


def test_source_profile_uses_copy():
    assert resolve_export_profile("source")["video_codec"] == "copy"


# ── Destination resolution ──────────────────────────────────────────────────

def test_destination_matches_historical_pattern():
    dest = resolve_export_destination(
        "tiktok", platform="TIKTOK", clip_id="clip_001", category="EMOTION", root="/root"
    )
    assert dest == "/root/EXPORTS/TIKTOK/EMOTION/clip_001_tiktok.mp4"


def test_destination_without_template_falls_back_to_clip_id():
    dest = resolve_export_destination(
        profile={"id": "vertical_clean", "destination": "CLIPS", "extension": "mp4"},
        clip_id="c1",
    )
    assert dest == "CLIPS/c1.mp4"


# ── Errors ──────────────────────────────────────────────────────────────────

def test_unknown_export_profile_raises():
    with pytest.raises(ConfigurationError, match="export profile 'nope'"):
        resolve_export_profile("nope")


def test_unknown_platform_raises():
    with pytest.raises(ConfigurationError, match="platform 'TWITCH'"):
        resolve_platform_export_profile("TWITCH")


def test_invalid_dimensions_fail():
    with pytest.raises(ConfigurationError, match=r"export\.platforms\.bad\.width"):
        validate_export_profiles(
            {"platforms": {"bad": {"id": "bad", "width": 0, "height": 1920,
                                   "video_codec": "libx264", "audio_codec": "aac",
                                   "extension": "mp4", "destination": "EXPORTS"}}},
            source="export",
        )


def test_invalid_codec_type_fails():
    with pytest.raises(ConfigurationError, match=r"\.video_codec"):
        validate_export_profiles(
            {"platforms": {"bad": {"id": "bad", "width": 1080, "height": 1920,
                                   "video_codec": 42, "audio_codec": "aac",
                                   "extension": "mp4", "destination": "EXPORTS"}}},
            source="export",
        )


def test_unsafe_destination_path_fails():
    with pytest.raises(ConfigurationError, match="traversal"):
        validate_export_profiles(
            {"platforms": {"bad": {"id": "bad", "width": 1080, "height": 1920,
                                   "video_codec": "libx264", "audio_codec": "aac",
                                   "extension": "mp4", "destination": "EXPORTS/../../tmp"}}},
            source="export",
        )


def test_missing_required_field_fails():
    with pytest.raises(ConfigurationError, match=r"\.audio_codec"):
        validate_export_profiles(
            {"platforms": {"bad": {"id": "bad", "width": 1080, "height": 1920,
                                   "video_codec": "libx264",
                                   "extension": "mp4", "destination": "EXPORTS"}}},
            source="export",
        )


def test_unknown_export_key_fails_with_full_path():
    with pytest.raises(ConfigurationError, match=r"export\.platforms\.t\.bogus"):
        validate_export_profiles(
            {"platforms": {"t": {"id": "t", "width": 1, "height": 1, "video_codec": "x",
                                 "audio_codec": "a", "extension": "mp4",
                                 "destination": "D", "bogus": 1}}},
            source="export",
        )


# ── No mutation / no network ────────────────────────────────────────────────

def test_resolution_creates_no_files(monkeypatch):
    marker = {"called": False}
    monkeypatch.setattr("os.makedirs", lambda *a, **k: marker.__setitem__("called", True))
    resolve_export_profile("vertical_clean")
    resolve_export_destination("tiktok", clip_id="c1", platform="TIKTOK", category="A")
    assert not marker["called"]