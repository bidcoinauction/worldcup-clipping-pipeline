import json
from pathlib import Path

_config: dict | None = None

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "pipeline_config.json"


def load_config() -> dict:
    global _config
    if _config is None:
        _config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    return _config


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
