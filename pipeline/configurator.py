"""Structured project configuration resolution.

Additive layer built on top of the legacy ``pipeline/config.py`` accessors.
It does not replace any existing public function. It provides:

* project identity resolution (with explicit ``ACCOUNT_POSITIONING`` legacy fallback)
* an explicit taxonomy registry (no plugin framework) with strict lookup
* a safe, repository-relative template resolver
* platform / output selection
* the canonical archive-root resolver shared by call sites

Validation is strict: unknown structured keys raise :class:`ConfigurationError`
with the full field path. Loading and validation perform no network calls and
no filesystem mutations.
"""

from __future__ import annotations

import json
import os
from pathlib import Path, PureWindowsPath

from .config import load_config
from .config_errors import ConfigurationError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_POSITIONING = "America Discovers Football"

# Top-level keys recognized in a structured profile file.
_PROFILE_KEYS = ("name", "project", "taxonomies", "templates", "platforms", "outputs")
_PROJECT_KEYS = ("name", "positioning")
_TAXONOMY_KEYS = ("match_kinds", "emotional_kinds")
_TEMPLATE_KEYS = ("prompt", "assets")
_OUTPUT_KEYS = ("default_format", "directory")


def resolve_archive_root() -> str:
    """Canonical resolution of the football archive root.

    ``FOOTBALL_ARCHIVE_ROOT`` wins, otherwise the platform default
    (Windows ``C:\\FootballArchive``, elsewhere ``FootballArchive``).
    """
    env = os.environ.get("FOOTBALL_ARCHIVE_ROOT")
    if env:
        return env
    return "C:\\FootballArchive" if os.name == "nt" else "FootballArchive"


def resolve_archive_path(*parts: str) -> str:
    """Join *parts* beneath the canonical archive root using the correct
    Windows or POSIX path flavour."""
    root = resolve_archive_root()
    if "\\" in root or ":" in root:
        return str(PureWindowsPath(root, *parts))
    return str(Path(root, *parts))


def _reject_unknown(obj: dict, allowed: tuple[str, ...], path: str) -> None:
    for key in obj:
        if key.startswith("_"):
            continue  # underscore-prefixed keys are documentation-only comments
        if key not in allowed:
            raise ConfigurationError(f"{path}.{key}: not a recognized configuration key")


def _require_map(data: dict, key: str, path: str) -> dict:
    val = data.get(key)
    if not isinstance(val, dict):
        raise ConfigurationError(f"{path}.{key}: expected an object, got {type(val).__name__}")
    return val


def validate_structured_profile(data: dict, source: str = "profile") -> None:
    """Strictly validate an in-memory structured profile against the known
    key surface. Raises :class:`ConfigurationError` with the full field path
    for unknown keys or wrong types. Read-only."""
    if not isinstance(data, dict):
        raise ConfigurationError(f"{source}: profile root must be an object, got {type(data).__name__}")
    _reject_unknown(data, _PROFILE_KEYS, source)

    name = data.get("name")
    if not name or not isinstance(name, str):
        raise ConfigurationError(f"{source}.name: expected a non-empty string profile identifier")

    if "project" in data:
        project = _require_map(data, "project", source)
        _reject_unknown(project, _PROJECT_KEYS, f"{source}.project")
        for key in _PROJECT_KEYS:
            if key in project and not isinstance(project[key], str):
                raise ConfigurationError(f"{source}.project.{key}: expected string, got {type(project[key]).__name__}")

    if "taxonomies" in data:
        taxonomy = _require_map(data, "taxonomies", source)
        _reject_unknown(taxonomy, _TAXONOMY_KEYS, f"{source}.taxonomies")
        for key in _TAXONOMY_KEYS:
            val = taxonomy.get(key)
            if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
                raise ConfigurationError(f"{source}.taxonomies.{key}: expected a list of strings")

    if "templates" in data:
        templates = _require_map(data, "templates", source)
        _reject_unknown(templates, _TEMPLATE_KEYS, f"{source}.templates")
        for key in _TEMPLATE_KEYS:
            if key in templates and not isinstance(templates[key], str):
                raise ConfigurationError(f"{source}.templates.{key}: expected a string, got {type(templates[key]).__name__}")

    if "platforms" in data:
        platforms = data["platforms"]
        if not isinstance(platforms, list) or not all(isinstance(v, str) for v in platforms):
            raise ConfigurationError(f"{source}.platforms: expected a list of strings")

    if "outputs" in data:
        outputs = _require_map(data, "outputs", source)
        _reject_unknown(outputs, _OUTPUT_KEYS, f"{source}.outputs")
        for key in _OUTPUT_KEYS:
            if key in outputs and not isinstance(outputs[key], str):
                raise ConfigurationError(f"{source}.outputs.{key}: expected a string, got {type(outputs[key]).__name__}")

    return None


def load_structured_profile(path: str | Path) -> dict:
    """Read and strictly validate a structured profile file. Read-only:
    no network access, no file mutation."""
    profile_path = Path(path)
    data = json.loads(profile_path.read_text(encoding="utf-8"))
    validate_structured_profile(data, source=profile_path.name)
    return data


# The built-in production profile (World Cup football). Its taxonomy values,
# platforms, and templates are sourced from the existing reference deployment
# config so football values are never duplicated or altered here.
def _football_profile()-> dict:
    cfg = load_config()
    return {
        "name": "football",
        "project": {
            "name": "Football Archive",
            "positioning": cfg.get("account_positioning", _DEFAULT_POSITIONING),
        },
        "taxonomies": {
            "match_kinds": list(cfg.get("categories", [])),
            "emotional_kinds": list(cfg.get("categories", [])),  # complementary; see clip_modes
        },
        "templates": {
            "prompt": "prompts/claude_detection_prompt.stub",
            "assets": cfg.get("paths", {}).get("thumbnail_template", "prompts/thumbnail_prompt_template.txt"),
        },
        "platforms": list(cfg.get("platforms", ["TikTok", "Reels", "Shorts"])),
        "outputs": {"default_format": cfg.get("default_clip_mode", "story"), "directory": resolve_archive_root()},
    }


_PROFILES = {"football": _football_profile}


def get_taxonomy(profile: str = "football") -> dict:
    """Return the taxonomy for the named profile. Unknown profiles raise
    :class:`ConfigurationError` rather than returning an error object."""
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"taxonomy lookup for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    return dict(_PROFILES[profile]()["taxonomies"])


def resolve_project_identity(profile: str = "football") -> dict:
    """Resolve project identity for *profile*, applying the precedence:

    explicit project config -> ``ACCOUNT_POSITIONING`` env (legacy) -> default.
    Unknown profiles raise :class:`ConfigurationError`.
    """
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"project identity lookup for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    identity = dict(_PROFILES[profile]()["project"])
    explicit = identity.get("positioning")
    if not explicit:
        identity["positioning"] = resolve_positioning()
    return identity


def resolve_positioning() -> str:
    """Legacy ``ACCOUNT_POSITIONING`` fallback. Only used when explicit project
    configuration does not declare a positioning value."""
    return os.environ.get("ACCOUNT_POSITIONING") or _DEFAULT_POSITIONING


def resolve_template(name: str, profile: str = "football", root: str | Path | None = None) -> Path:
    """Resolve a template path for a profile relative to the repository root
    or an explicit root. Unknown profile, name, or missing file raise
    :class:`ConfigurationError`."""
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"template resolution for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    templates = dict(_PROFILES[profile]()["templates"])
    if name not in templates:
        raise ConfigurationError(f"template '{name}' is not configured for profile '{profile}'")
    root_path = Path(root) if root is not None else _REPO_ROOT
    raw = templates[name]
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = root_path / candidate
    if not candidate.exists():
        raise ConfigurationError(f"template file not found at '{candidate}' for profile '{profile}' template '{name}'")
    return candidate


def select_platforms(profile: str = "football") -> list[str]:
    """Return the platforms selected for a profile. Unknown profiles raise
    ConfigurationError."""
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"platform selection for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    return list(_PROFILES[profile]()["platforms"])