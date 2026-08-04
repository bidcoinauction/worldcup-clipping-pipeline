from pathlib import Path

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import get_taxonomy, load_structured_profile, resolve_project_identity, select_platforms

BASKETBALL_EXAMPLE = Path(__file__).resolve().parents[1] / "config" / "examples" / "basketball.json"


def test_basketball_example_file_exists_and_is_valid():
    profile = load_structured_profile(BASKETBALL_EXAMPLE)
    assert profile["name"] == "basketball"
    assert "basketball" in BASKETBALL_EXAMPLE.read_text(encoding="utf-8").lower()


def test_basketball_example_is_never_selected_by_default():
    assert "basketball" not in select_platforms()
    with pytest.raises(ConfigurationError, match="unknown profile"):
        get_taxonomy("basketball")
    assert resolve_project_identity()["name"] != "Basketball Archive"


def test_basketball_example_has_distinct_taxonomy():
    profile = load_structured_profile(BASKETBALL_EXAMPLE)
    operational = profile["taxonomies"]["operational"]["categories"]
    editorial = profile["taxonomies"]["editorial"]
    assert "BUZZER_BEATER" in editorial["emotional_kinds"]
    assert any(kind in operational for kind in ("FINAL_QUARTER", "OVERTIME", "PLAYOFF"))
    assert "narrative_functions" in editorial
    assert {"arc_roles", "narrative_roles"} <= set(editorial["story_targets"])


def test_basketball_example_taxonomy_stays_separate_from_operational():
    profile = load_structured_profile(BASKETBALL_EXAMPLE)
    operational = set(profile["taxonomies"]["operational"]["categories"])
    emotional = set(profile["taxonomies"]["editorial"]["emotional_kinds"])
    assert operational.isdisjoint(emotional)