import json

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    resolve_brand_hashtags,
    resolve_brand_positioning,
    resolve_brand_profile,
    validate_brand_profile,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("ACCOUNT_POSITIONING", raising=False)


def _fixture_brand(monkeypatch, tmp_path, data: dict, name: str = "fixture_brand"):
    (tmp_path / f"{name}.json").write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("pipeline.configurator._BRAND_DIR", tmp_path)
    return name


# ── World Cup brand resolution ──────────────────────────────────────────────

def test_world_cup_brand_profile_resolves():
    profile = resolve_brand_profile("world_cup")
    assert profile["id"] == "world_cup"
    assert profile["display_name"] == "Football Archive"
    assert profile["language"] == "en"


def test_world_cup_positioning_unchanged():
    assert resolve_brand_positioning("world_cup") == "America Discovers Football"


def test_world_cup_hashtags_unchanged():
    assert resolve_brand_hashtags("world_cup") == ["#worldcup", "#football", "#soccer"]


# ── Brand precedence ────────────────────────────────────────────────────────

def test_structured_project_override_wins():
    assert resolve_brand_positioning("world_cup", override="Project Override") == "Project Override"


def test_brand_profile_wins_over_legacy_fallback(monkeypatch, tmp_path):
    _fixture_brand(monkeypatch, tmp_path, {"id": "fixture_brand", "positioning": "Fixture Positioning", "hashtags": ["#fixture"]})
    monkeypatch.setattr("pipeline.configurator.load_config", lambda: {"account_positioning": "Legacy Positioning"})
    assert resolve_brand_positioning("fixture_brand") == "Fixture Positioning"


def test_legacy_fallback_remains_compatible(monkeypatch, tmp_path):
    _fixture_brand(monkeypatch, tmp_path, {"id": "fixture_brand", "hashtags": ["#fixture"]})
    monkeypatch.setattr("pipeline.configurator.load_config", lambda: {"account_positioning": "Legacy Positioning"})
    assert resolve_brand_positioning("fixture_brand") == "Legacy Positioning"


def test_environment_remains_fallback_only(monkeypatch, tmp_path):
    _fixture_brand(monkeypatch, tmp_path, {"id": "fixture_brand", "hashtags": ["#fixture"]})
    monkeypatch.setattr("pipeline.configurator.load_config", lambda: {})
    monkeypatch.setenv("ACCOUNT_POSITIONING", "Env Positioning")
    assert resolve_brand_positioning("fixture_brand") == "Env Positioning"
    monkeypatch.delenv("ACCOUNT_POSITIONING")
    assert resolve_brand_positioning("fixture_brand") == "America Discovers Football"


# ── Validation / errors ─────────────────────────────────────────────────────

def test_unknown_brand_profile_raises():
    with pytest.raises(ConfigurationError, match="brand profile not found"):
        resolve_brand_profile("does_not_exist")


def test_unknown_brand_key_fails_with_full_path():
    with pytest.raises(ConfigurationError, match=r"brand\.bogus"):
        validate_brand_profile({"id": "x", "bogus": "nope"}, source="brand")


def test_invalid_hashtag_list_fails():
    with pytest.raises(ConfigurationError, match=r"brand\.hashtags\[0\]"):
        validate_brand_profile({"id": "x", "hashtags": ["worldcup"]}, source="brand")
    with pytest.raises(ConfigurationError, match=r"brand\.hashtags"):
        validate_brand_profile({"id": "x", "hashtags": "not-a-list"}, source="brand")


def test_invalid_platform_override_fails():
    with pytest.raises(ConfigurationError, match=r"brand\.platforms\.TikTok"):
        validate_brand_profile({"id": "x", "platforms": {"TikTok": "not-a-list"}}, source="brand")
    with pytest.raises(ConfigurationError, match=r"brand\.platforms\.TikTok\[0\]"):
        validate_brand_profile({"id": "x", "platforms": {"TikTok": ["nohash"]}}, source="brand")


def test_unsafe_asset_path_fails():
    with pytest.raises(ConfigurationError, match="traversal"):
        validate_brand_profile({"id": "x", "assets": {"logo": "../../secrets/logo.png"}}, source="brand")
    with pytest.raises(ConfigurationError, match="repository-relative"):
        validate_brand_profile({"id": "x", "assets": {"font": "/etc/passwd"}}, source="brand")


def test_required_brand_identifier():
    with pytest.raises(ConfigurationError, match=r"brand\.id"):
        validate_brand_profile({"positioning": "x"}, source="brand")


# ── No network / no mutation ────────────────────────────────────────────────

def test_resolution_creates_no_files(monkeypatch):
    marker = {"called": False}
    monkeypatch.setattr("os.makedirs", lambda *a, **k: marker.__setitem__("called", True))
    resolve_brand_hashtags("world_cup")
    assert not marker["called"]