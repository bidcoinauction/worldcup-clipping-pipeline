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
import re
from pathlib import Path, PureWindowsPath

from .config import load_config
from .config_errors import ConfigurationError

_REPO_ROOT = Path(__file__).resolve().parent.parent
_CONFIG_DIR = _REPO_ROOT / "config"
_EDITORIAL_DIR = _CONFIG_DIR / "editorial"
_DEFAULT_POSITIONING = "America Discovers Football"

_PLACEHOLDER_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Top-level keys recognized in a structured profile file.
_PROFILE_KEYS = ("name", "project", "taxonomies", "templates", "platforms", "outputs")
_PROJECT_KEYS = ("name", "positioning")
_TAXONOMY_KEYS = ("match_kinds", "emotional_kinds", "operational", "editorial")
_OPERATIONAL_KEYS = ("categories",)
_EDITORIAL_KEYS = ("emotional_kinds", "narrative_functions", "story_targets")
_TEMPLATE_KEYS = ("prompt", "assets")
_OUTPUT_KEYS = ("default_format", "directory")


def _validate_output_root(directory, source: str) -> str:
    """Validate a structured output root. Absolute or repository-relative
    roots are allowed; invalid types and ``..`` traversal are rejected with
    :class:`ConfigurationError`. Does not create the directory. Read-only."""
    if not isinstance(directory, str) or not directory.strip():
        raise ConfigurationError(f"{source}: output directory must be a non-empty string, got {type(directory).__name__}")
    if ".." in Path(directory).parts:
        raise ConfigurationError(f"{source}: output directory path traversal is not allowed: '{directory}'")
    return directory.strip()


def _profile_outputs(profile: str) -> dict:
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"output resolution for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    outputs = dict(_PROFILES[profile]()["outputs"])
    if not isinstance(outputs, dict):
        raise ConfigurationError(f"{profile}.outputs: expected an object, got {type(outputs).__name__}")
    return outputs


def _resolve_output_from(outputs: dict, source: str, override: str | None = None) -> str:
    """Resolve an output root from a validated outputs map.

    Precedence: explicit override -> structured ``outputs.directory`` ->
    ``FOOTBALL_ARCHIVE_ROOT`` -> platform default. Read-only; no mkdir, no network.
    """
    if override:
        return _validate_output_root(override, f"{source}#override")
    directory = outputs.get("directory")
    if directory:
        return _validate_output_root(directory, f"{source}.directory")
    env = os.environ.get("FOOTBALL_ARCHIVE_ROOT")
    if env:
        return env
    return "C:\\FootballArchive" if os.name == "nt" else "FootballArchive"


def resolve_output_root(profile: str = "football", override: str | None = None) -> str:
    """Resolve the archive output root for a profile. See
    :func:`_resolve_output_from` for precedence. Unknown profiles raise
    :class:`ConfigurationError`. Read-only."""
    return _resolve_output_from(_profile_outputs(profile), f"{profile}.outputs", override)


def resolve_structured_output_root(
    outputs: dict, source: str, override: str | None = None
) -> str:
    """Resolve an output root from an already-validated structured ``outputs``
    map (for example one loaded by :func:`load_structured_profile` or the
    non-production basketball example). Unknown/invalid fields raise
    :class:`ConfigurationError`. Read-only."""
    if not isinstance(outputs, dict):
        raise ConfigurationError(f"{source}: outputs must be an object, got {type(outputs).__name__}")
    return _resolve_output_from(outputs, source, override)


def resolve_archive_root(profile: str = "football") -> str:
    """Canonical resolution of the football archive root.

    Falls back through structured project output (when configured), then
    ``FOOTBALL_ARCHIVE_ROOT``, then the platform default. Backward compatible:
    ``resolve_archive_root()`` with no arguments keeps the historical behaviour.
    """
    return resolve_output_root(profile)


def resolve_archive_path(*parts: str, profile: str = "football", root_override: str | None = None) -> str:
    """Join *parts* beneath the canonical archive root using the correct
    Windows or POSIX path flavour."""
    root = resolve_output_root(profile, override=root_override)
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


def _validate_str_list(val, path: str) -> None:
    if not isinstance(val, list) or not all(isinstance(v, str) for v in val):
        raise ConfigurationError(f"{path} must be a list of strings")


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

        # Legacy direct lists (kept for backward compatibility).
        for key in ("match_kinds", "emotional_kinds"):
            if key in taxonomy:
                _validate_str_list(taxonomy[key], f"{source}.taxonomies.{key}")

        if "operational" in taxonomy:
            operational = _require_map(taxonomy, "operational", f"{source}.taxonomies")
            _reject_unknown(operational, _OPERATIONAL_KEYS, f"{source}.taxonomies.operational")
            if "categories" in operational:
                _validate_str_list(operational["categories"], f"{source}.taxonomies.operational.categories")

        if "editorial" in taxonomy:
            editorial = _require_map(taxonomy, "editorial", f"{source}.taxonomies")
            _reject_unknown(editorial, _EDITORIAL_KEYS, f"{source}.taxonomies.editorial")
            for key in ("emotional_kinds", "narrative_functions"):
                if key in editorial:
                    _validate_str_list(editorial[key], f"{source}.taxonomies.editorial.{key}")
            if "story_targets" in editorial:
                story = editorial["story_targets"]
                if isinstance(story, dict):
                    for skey, sval in story.items():
                        _validate_str_list(sval, f"{source}.taxonomies.editorial.story_targets.{skey}")
                else:
                    _validate_str_list(story, f"{source}.taxonomies.editorial.story_targets")

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
        if "directory" in outputs:
            _validate_output_root(outputs["directory"], f"{source}.outputs")

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
def _load_editorial_taxonomy(name: str) -> dict:
    """Load a dedicated editorial taxonomy data file for a built-in profile.

    ``config/editorial/<name>.json`` holds the football editorial language
    (emotional kinds, narrative functions, story targets) independently of the
    operational ``categories`` list. Read-only; no network access, no mutation.
    """
    path = _EDITORIAL_DIR / f"{name}.json"
    if not path.exists():
        raise ConfigurationError(f"editorial taxonomy file not found at '{path}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    validate_editorial_taxonomy(data, source=path.name)
    return data


def _football_profile() -> dict:
    cfg = load_config()
    editorial = _load_editorial_taxonomy("world_cup")
    return {
        "name": "football",
        "project": {
            "name": "Football Archive",
            "positioning": cfg.get("account_positioning", _DEFAULT_POSITIONING),
        },
        "taxonomies": {
            # Backward-compatible operational categories (legacy surface).
            "match_kinds": list(cfg.get("categories", [])),
            "emotional_kinds": list(editorial["emotional_kinds"]),
            "operational": {"categories": list(cfg.get("categories", []))},
            # Editorial taxonomy is a distinct resolved concept (data-backed).
            "editorial": editorial,
        },
        "templates": {
            "prompt": "prompts/world_cup_detection_prompt.txt",
            "assets": cfg.get("paths", {}).get("thumbnail_template", "prompts/thumbnail_prompt_template.txt"),
        },
        "platforms": list(cfg.get("platforms", ["TikTok", "Reels", "Shorts"])),
        # No structured `outputs.directory` for the built-in profile: this
        # omits the tier so World Cup resolution stays environment/default.
        "outputs": {"default_format": cfg.get("default_clip_mode", "story")},
    }


_PROFILES = {"football": _football_profile}


def get_taxonomy(profile: str = "football") -> dict:
    """Return the full taxonomy map for the named profile. Unknown profiles
    raise :class:`ConfigurationError` rather than returning an error object."""
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"taxonomy lookup for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    return dict(_PROFILES[profile]()["taxonomies"])


_EDITORIAL_FILE_KEYS = ("emotional_kinds", "narrative_functions", "story_targets")


def validate_editorial_taxonomy(data: dict, source: str = "editorial") -> None:
    """Strictly validate a dedicated editorial-taxonomy data file. Raises
    :class:`ConfigurationError` with the full field path for unknown keys or
    wrong types. Read-only."""
    if not isinstance(data, dict):
        raise ConfigurationError(f"{source}: editorial taxonomy root must be an object, got {type(data).__name__}")
    _reject_unknown(data, _EDITORIAL_FILE_KEYS, source)
    if "emotional_kinds" in data:
        _validate_str_list(data["emotional_kinds"], f"{source}.emotional_kinds")
    if "narrative_functions" in data:
        _validate_str_list(data["narrative_functions"], f"{source}.narrative_functions")
    if "story_targets" in data:
        story = data["story_targets"]
        if isinstance(story, dict):
            for skey, sval in story.items():
                _validate_str_list(sval, f"{source}.story_targets.{skey}")
        else:
            _validate_str_list(story, f"{source}.story_targets")


def _profile_taxonomy(profile: str) -> dict:
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"taxonomy lookup for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    return _PROFILES[profile]()["taxonomies"]


def resolve_operational_categories(profile: str = "football") -> list[str]:
    """Resolve the operational clip/event categories for a profile.

    Priority: structured ``operational.categories``, else the legacy
    ``match_kinds`` list. Unknown profile or missing categories raise
    :class:`ConfigurationError`.
    """
    taxonomy = _profile_taxonomy(profile)
    operational = taxonomy.get("operational", {})
    categories = operational.get("categories") if isinstance(operational, dict) else None
    if not categories:
        categories = taxonomy.get("match_kinds")
    if not isinstance(categories, list) or not all(isinstance(c, str) for c in categories):
        raise ConfigurationError(f"{profile}.taxonomies: operational categories are not configured")
    return list(categories)


def resolve_editorial_taxonomy(profile: str = "football") -> dict:
    """Resolve the dedicated editorial taxonomy for a profile: emotional kinds,
    narrative functions, and story targets. Unknown profile or missing/invalid
    editorial fields raise :class:`ConfigurationError`."""
    taxonomy = _profile_taxonomy(profile)
    editorial = taxonomy.get("editorial")
    if not isinstance(editorial, dict):
        raise ConfigurationError(f"{profile}.taxonomies.editorial: editorial taxonomy is not configured")
    emotional = editorial.get("emotional_kinds")
    narrative = editorial.get("narrative_functions")
    if not isinstance(emotional, list) or not emotional:
        raise ConfigurationError(f"{profile}.taxonomies.editorial.emotional_kinds: must be a non-empty list")
    if not isinstance(narrative, list) or not narrative:
        raise ConfigurationError(f"{profile}.taxonomies.editorial.narrative_functions: must be a non-empty list")
    return {
        "emotional_kinds": list(emotional),
        "narrative_functions": list(narrative),
        "story_targets": dict(editorial.get("story_targets", {})),
    }


def resolve_story_targets(profile: str = "football") -> dict:
    """Resolve the editorial story-target kinds for a profile (arc roles and
    narrative roles). Missing/unknown targets raise :class:`ConfigurationError`."""
    taxonomy = _profile_taxonomy(profile)
    editorial = taxonomy.get("editorial")
    if not isinstance(editorial, dict) or "story_targets" not in editorial:
        raise ConfigurationError(f"{profile}.taxonomies.editorial.story_targets: not configured")
    story = editorial["story_targets"]
    if not isinstance(story, dict):
        raise ConfigurationError(f"{profile}.taxonomies.editorial.story_targets: expected an object")
    return dict(story)


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
    return _resolve_template_path(templates[name], name, source=f"{profile}.templates.{name}", root=root_path)


def _resolve_template_path(raw: str, name: str, source: str, root: Path) -> Path:
    """Resolve a repository-relative template path with traversal protection.

    Absolute paths and ``..`` escape attempts are rejected with
    :class:`ConfigurationError`. The resolved file must exist.
    """
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ConfigurationError(
            f"{source}: template '{name}' must be a repository-relative path, got absolute '{raw}'"
        )
    if ".." in candidate.parts:
        raise ConfigurationError(
            f"{source}: template '{name}' path traversal is not allowed: '{raw}'"
        )
    resolved = (root / candidate).resolve()
    if not resolved.exists():
        raise ConfigurationError(
            f"{source}: template file not found at '{resolved}' for template '{name}'"
        )
    return resolved


def resolve_profile_template_path(
    templates: dict, name: str, source: str, root: str | Path | None = None
) -> Path:
    """Resolve a template path from an already-validated structured profile's
    ``templates`` map. Unknown template name, unsafe path, or missing file
    raise :class:`ConfigurationError`."""
    if not isinstance(templates, dict) or name not in templates:
        raise ConfigurationError(f"{source}: template '{name}' is not configured")
    raw = templates[name]
    if not isinstance(raw, str) or not raw:
        raise ConfigurationError(f"{source}.{name}: expected a non-empty template path string")
    root_path = Path(root) if root is not None else _REPO_ROOT
    return _resolve_template_path(raw, name, source=f"{source}.{name}", root=root_path)


# Registered renderable templates and their exact required variables. Only
# these template IDs can be rendered; anything else is rejected as unregistered.
_TEMPLATE_VARIABLES: dict[str, dict[str, frozenset[str]]] = {
    "football": {
        "prompt": frozenset({
            "account_positioning",
            "goal",
            "category_rule",
            "clip_schema",
            "rules_block",
            "story_targets_block",
            "research_block",
            "brief_block",
            "match_name",
            "duration_seconds",
            "timestamped_transcript",
        }),
    },
}


def render_template(
    name: str,
    profile: str = "football",
    variables: dict | None = None,
    root: str | Path | None = None,
) -> str:
    """Render a registered template with the provided variables.

    The template is read from a repository-relative tracked file and only its
    registered placeholders are substituted. Unknown template IDs, unknown or
    missing variables, missing files, and unsafe paths raise
    :class:`ConfigurationError`. Read-only: no network access, no file mutation.
    """
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"render for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    profile_templates = _TEMPLATE_VARIABLES.get(profile, {})
    if name not in profile_templates:
        registered = ", ".join(sorted(profile_templates)) if profile_templates else "(none registered)"
        raise ConfigurationError(
            f"render for unregistered template '{name}' on profile '{profile}'; registered templates: {registered}"
        )
    required = profile_templates[name]

    template_path = resolve_template(name, profile, root)
    text = template_path.read_text(encoding="utf-8")

    provided = dict(variables or {})
    missing = sorted(required - set(provided))
    if missing:
        raise ConfigurationError(
            f"render template '{name}' on profile '{profile}': missing required variable(s): {', '.join(missing)}"
        )

    found = set(_PLACEHOLDER_RE.findall(text))
    unknown = sorted(found - required)
    if unknown:
        raise ConfigurationError(
            f"render template '{name}' on profile '{profile}': unknown variable(s) in template: {', '.join(unknown)}"
        )

    result = text
    for var in found:
        result = result.replace("{" + var + "}", str(provided[var]))
    return result


def select_platforms(profile: str = "football") -> list[str]:
    """Return the platforms selected for a profile. Unknown profiles raise
    ConfigurationError."""
    if profile not in _PROFILES:
        raise ConfigurationError(
            f"platform selection for unknown profile '{profile}'; known profiles: {', '.join(sorted(_PROFILES))}"
        )
    return list(_PROFILES[profile]()["platforms"])