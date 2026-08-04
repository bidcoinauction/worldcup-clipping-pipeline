import os

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    resolve_archive_root,
    resolve_output_root,
    resolve_structured_output_root,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("FOOTBALL_ARCHIVE_ROOT", raising=False)


def test_precedence_override_wins_over_everything(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/fallback")
    outputs = {"directory": "/data/structured"}
    assert resolve_structured_output_root(outputs, "profile", override="/data/override") == "/data/override"


def test_structured_directory_beats_env_fallback(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/env")
    outputs = {"directory": "/data/structured"}
    assert resolve_structured_output_root(outputs, "profile") == "/data/structured"


def test_env_fallback_used_when_no_structured_directory(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/env")
    assert resolve_structured_output_root({"default_format": "story"}, "profile") == "/data/env"


def test_platform_default_when_nothing_set(monkeypatch):
    monkeypatch.delenv("FOOTBALL_ARCHIVE_ROOT", raising=False)
    root = resolve_structured_output_root({}, "profile")
    assert root == ("C:\\FootballArchive" if os.name == "nt" else "FootballArchive")


def test_repository_relative_root_allowed():
    assert resolve_structured_output_root({"directory": "FootballArchive"}, "profile") == "FootballArchive"


def test_invalid_directory_type_rejected():
    with pytest.raises(ConfigurationError, match=r"profile\.directory"):
        resolve_structured_output_root({"directory": 42}, "profile")


def test_empty_directory_rejected():
    with pytest.raises(ConfigurationError, match="directory"):
        resolve_structured_output_root({"directory": "  "}, "profile")


def test_path_traversal_rejected():
    with pytest.raises(ConfigurationError, match="traversal"):
        resolve_structured_output_root({"directory": "Safe/../../etc"}, "profile")


def test_non_dict_outputs_rejected():
    with pytest.raises(ConfigurationError, match="outputs must be an object"):
        resolve_structured_output_root(["nope"], "profile")


def test_archive_root_backward_compatible(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert resolve_archive_root() == "/data/archive"


def test_output_root_for_unknown_profile_rejected():
    with pytest.raises(ConfigurationError, match="unknown profile"):
        resolve_output_root("basketball")


def test_resolution_does_not_create_directories(monkeypatch, tmp_path):
    marker = {"called": False}
    original = __import__("os").makedirs

    def fake_makedirs(*args, **kwargs):
        marker["called"] = True
        return original(*args, **kwargs)

    monkeypatch.setattr("os.makedirs", fake_makedirs)
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", str(tmp_path / "archive"))
    root = resolve_structured_output_root({}, "profile")
    assert not marker["called"]
    assert root == str(tmp_path / "archive")