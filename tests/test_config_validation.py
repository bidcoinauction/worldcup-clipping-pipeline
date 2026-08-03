import json

import pytest

from pipeline.config import (
    CONFIG_PATH,
    KNOWN_LEGACY_KEYS,
    load_config,
    load_validated_config,
    validate_config_dict,
)
from pipeline.config_errors import ConfigurationError


def _valid_legacy():
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def test_reference_config_validates():
    validate_config_dict(_valid_legacy())
    assert load_validated_config() == _valid_legacy()


def test_unknown_top_level_key_rejected_with_path():
    data = _valid_legacy()
    data["modelz"] = "oops"
    with pytest.raises(ConfigurationError, match=r"modelz"):
        validate_config_dict(data)


def test_unknown_nested_map_inside_unknown_key_still_reports_top_level():
    data = _valid_legacy()
    data["providers"] = {"timeout": 5}
    data["providors"] = {"timeout": 5}
    with pytest.raises(ConfigurationError, match=r"unknown configuration key 'providors'"):
        validate_config_dict(data, source="pipeline_config.json")


def test_wrong_list_type_rejected():
    data = _valid_legacy()
    data["leagues"] = "WORLD_CUP"
    with pytest.raises(ConfigurationError, match=r"leagues"):
        validate_config_dict(data)


def test_non_string_element_rejected():
    data = _valid_legacy()
    data["leagues"] = ["PREMIER_LEAGUE", 42]
    with pytest.raises(ConfigurationError, match=r"leagues"):
        validate_config_dict(data)


def test_root_must_be_object():
    with pytest.raises(ConfigurationError, match="root"):
        validate_config_dict([1, 2, 3])


def test_legacy_functions_preserved():
    assert isinstance(load_config(), dict)
    assert KNOWN_LEGACY_KEYS[0] == "account_positioning"