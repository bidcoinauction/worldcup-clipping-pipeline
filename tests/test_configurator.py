import json

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    get_taxonomy,
    resolve_archive_path,
    resolve_archive_root,
    resolve_positioning,
    resolve_project_identity,
    select_platforms,
    validate_structured_profile,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("FOOTBALL_ARCHIVE_ROOT", raising=False)
    monkeypatch.delenv("ACCOUNT_POSITIONING", raising=False)


def _valid_profile():
    return {
        "name": "demo",
        "project": {"name": "Demo Archive", "positioning": "Demo positioning"},
        "taxonomies": {"match_kinds": ["A", "B"], "emotional_kinds": ["C"]},
        "templates": {"prompt": "prompts/demo.txt", "assets": "prompts/demo_assets.txt"},
        "platforms": ["TikTok"],
        "outputs": {"default_format": "story", "directory": "DemoRoot"},
    }


def test_structured_profile_valid():
    validate_structured_profile(_valid_profile())


def test_structured_unknown_top_level_key_rejected():
    profile = _valid_profile()
    profile["taxonemies"] = profile["taxonomies"]
    del profile["taxonomies"]
    with pytest.raises(ConfigurationError, match=r"\.taxonemies"):
        validate_structured_profile(profile, source="demo.json")


def test_structured_unknown_nested_key_full_path():
    profile = _valid_profile()
    profile["taxonomies"]["emitional_kinds"] = []
    with pytest.raises(ConfigurationError, match=r"demo\.json\.taxonomies\.emitional_kinds"):
        validate_structured_profile(profile, source="demo.json")


def test_structured_bad_type_nested():
    profile = _valid_profile()
    profile["project"]["name"] = 12
    with pytest.raises(ConfigurationError, match=r"project\.name"):
        validate_structured_profile(profile)


def test_taxonomy_unknown_profile_raises():
    with pytest.raises(ConfigurationError, match="unknown profile"):
        get_taxonomy("basketball")


def test_taxonomy_football_default():
    kinds = get_taxonomy()
    assert "EMOTION" in kinds["match_kinds"]
    assert isinstance(kinds["emotional_kinds"], list)


def test_project_identity_uses_explicit_config():
    identity = resolve_project_identity()
    assert identity["positioning"] == "America Discovers Football"


def test_positioning_legacy_fallback_used_when_no_explicit(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POSITIONING", "Legacy Angle")
    assert resolve_positioning() == "Legacy Angle"
    monkeypatch.delenv("ACCOUNT_POSITIONING")
    assert resolve_positioning() == "America Discovers Football"


def test_project_identity_prefers_explicit_over_env(monkeypatch):
    monkeypatch.setenv("ACCOUNT_POSITIONING", "Env Angle")
    identity = resolve_project_identity()
    assert identity["positioning"] == "America Discovers Football"


def test_select_platforms_unknown_profile_raises():
    with pytest.raises(ConfigurationError, match="unknown profile"):
        select_platforms("lacrosse")


def test_select_platforms_default():
    assert "TikTok" in select_platforms()


def test_archive_root_env(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert resolve_archive_root() == "/data/archive"


def test_archive_path_windows(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "C:\\FootballArchive")
    assert resolve_archive_path("RAW", "x.ts") == "C:\\FootballArchive\\RAW\\x.ts"


def test_archive_path_posix(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert resolve_archive_path("RAW", "x.ts") == "/data/archive/RAW/x.ts"