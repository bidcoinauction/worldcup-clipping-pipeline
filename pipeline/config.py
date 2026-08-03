from __future__ import annotations

import json
from pathlib import Path

from .config_errors import ConfigurationError

_config: dict | None = None

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.json"

# Explicit allowlist of recognized legacy top-level keys. This is the
# reference World Cup deployment surface. Any other top-level key is
# rejected as unknown (with its full path) rather than silently ignored.
KNOWN_LEGACY_KEYS = (
    "account_positioning",
    "leagues",
    "categories",
    "platforms",
    "daily_targets",
    "default_clip_mode",
    "clip_modes",
    "scoring_weights",
    "models",
    "paths",
    "providers",
)

_SCALAR_KEYS = ("account_positioning", "default_clip_mode")
_LIST_KEYS = ("leagues", "categories", "platforms")
_MAP_KEYS = (
    "daily_targets",
    "clip_modes",
    "scoring_weights",
    "models",
    "paths",
    "providers",
)


def load_config() -> dict:
    global _config
    if _config is None:
        _config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _config


def validate_config_dict(data: dict, source: str = "pipeline_config.json") -> None:
    """Strictly validate a legacy configuration dictionary.

    Raises :class:`ConfigurationError` for unknown top-level keys (with the
    full field path) or wrong value types. Read-only: performs no network
    access and mutates nothing.
    """
    if not isinstance(data, dict):
        raise ConfigurationError(f"{source}: configuration root must be an object, got {type(data).__name__}")

    for key in data:
        if key not in KNOWN_LEGACY_KEYS:
            raise ConfigurationError(
                f"{source}: unknown configuration key '{key}' at top level; "
                f"allowed legacy keys: {', '.join(KNOWN_LEGACY_KEYS)}"
            )

    for key in _SCALAR_KEYS:
        if key in data and not isinstance(data[key], str):
            raise ConfigurationError(f"{source}.{key}: expected string, got {type(data[key]).__name__}")

    for key in _LIST_KEYS:
        if key in data and not isinstance(data[key], list):
            raise ConfigurationError(f"{source}.{key}: expected a list, got {type(data[key]).__name__}")
        if isinstance(data.get(key), list) and not all(isinstance(v, str) for v in data[key]):
            raise ConfigurationError(f"{source}.{key}: every element must be a string")

    for key in _MAP_KEYS:
        if key in data and not isinstance(data[key], dict):
            raise ConfigurationError(f"{source}.{key}: expected an object, got {type(data[key]).__name__}")


def load_validated_config(path: str | Path | None = None) -> dict:
    """Read and strictly validate a legacy configuration file.

    Unlike :func:`load_config` this always re-reads from disk and validates
    the result. Read-only: no network access, no file mutation.
    """
    config_path = Path(path) if path is not None else CONFIG_PATH
    data = json.loads(config_path.read_text(encoding="utf-8"))
    validate_config_dict(data, source=config_path.name)
    return data


def get_leagues() -> list[str]:
    return load_config()["leagues"]


def get_model(name: str) -> str:
    return load_config()["models"][name]


def get_path(name: str) -> str:
    return load_config()["paths"][name]


def get_provider(name: str) -> str:
    return load_config()["providers"][name]


def get_clip_mode(name: str) -> dict:
    return load_config()["clip_modes"][name]


def get_default_clip_mode() -> str:
    return load_config()["default_clip_mode"]


def reload_config() -> dict:
    global _config
    _config = None
    return load_config()
