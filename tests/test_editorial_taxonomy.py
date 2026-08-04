import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    get_taxonomy,
    resolve_editorial_taxonomy,
    resolve_operational_categories,
    resolve_story_targets,
    validate_editorial_taxonomy,
)


def test_editorial_taxonomy_resolves_football():
    editorial = resolve_editorial_taxonomy()
    assert "EMOTION" in editorial["emotional_kinds"]
    assert {"AURA", "CHAOS", "AMERICA"} <= set(editorial["emotional_kinds"])
    assert "narrative_functions" in editorial


def test_story_targets_resolves_arc_roles():
    targets = resolve_story_targets()
    assert "setup" in targets["arc_roles"]
    assert "aftermath" in targets["arc_roles"]
    assert "climax" in targets["narrative_roles"]


def test_operational_categories_match_legacy_surface():
    categories = resolve_operational_categories()
    assert "EMOTION" in categories
    legacy = get_taxonomy()["match_kinds"]
    assert set(categories) == set(legacy)


def test_get_taxonomy_keeps_backward_compat_emotional_kinds():
    kinds = get_taxonomy()
    assert "EMOTION" in kinds["emotional_kinds"]
    assert isinstance(kinds["emotional_kinds"], list)


def test_editorial_distinct_from_operational_categories():
    editorial = set(resolve_editorial_taxonomy()["emotional_kinds"])
    operational = set(resolve_operational_categories())
    assert editorial == operational  # World Cup keeps same names, distinct surface


def test_editorial_taxonomy_file_is_only_source_of_truth():
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / "config" / "editorial" / "world_cup.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert resolve_editorial_taxonomy()["emotional_kinds"] == data["emotional_kinds"]
    assert resolve_story_targets() == data["story_targets"]


def test_validate_editorial_taxonomy_accepts_valid():
    validate_editorial_taxonomy(
        {"emotional_kinds": ["A"], "narrative_functions": ["B"], "story_targets": {"arc_roles": ["setup"]}}
    )


def test_validate_editorial_taxonomy_non_list_rejected():
    with pytest.raises(ConfigurationError, match=r"emotional_kinds"):
        validate_editorial_taxonomy({"emotional_kinds": "EMOTION"})


def test_validate_editorial_taxonomy_unknown_key_rejected():
    with pytest.raises(ConfigurationError, match=r"\.bogus"):
        validate_editorial_taxonomy({"bogus": ["x"]})


def test_validate_editorial_taxonomy_bad_story_target():
    with pytest.raises(ConfigurationError, match=r"story_targets\.arc_roles"):
        validate_editorial_taxonomy({"story_targets": {"arc_roles": "setup"}})