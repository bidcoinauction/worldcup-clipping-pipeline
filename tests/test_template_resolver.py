import json
from pathlib import Path

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.configurator import (
    load_structured_profile,
    render_template,
    resolve_profile_template_path,
    resolve_template,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WORLD_CUP_TEMPLATE = REPO_ROOT / "prompts" / "world_cup_detection_prompt.txt"
BASKETBALL_TEMPLATE = REPO_ROOT / "prompts" / "basketball_detection_prompt.txt"

# Approved fixture: the previous PROMPT_TEMPLATE body, preserved verbatim so the
# new template-driven rendering can be proven byte-for-byte equivalent.
PREVIOUS_PROMPT_TEMPLATE = """You are an elite short-form football clipping strategist for a US-targeted 2026 World Cup account.

Account positioning:
"{account_positioning}"

Goal:
{goal}

Analyze the timestamped transcript below and produce a JSON array of clip candidates.

Prioritize these categories:
1. EMOTION: crowd eruption, anthem moments, crying fans, tunnel walks, national pride, heartbreak, pressure.
2. AURA: Messi, Ronaldo, Mbappe, Bellingham, Vinicius, Neymar, cold reactions, legacy, intimidation.
3. CHAOS: VAR controversy, fights, red cards, meltdowns, tactical collapses.
4. AMERICA: US audience entry point, MLS/Messi, football culture shock, why soccer feels different.

{category_rule}{clip_schema}

{rules_block}

{story_targets_block}{research_block}{brief_block}Match name:
{match_name}

Timestamped transcript (video duration: {duration_seconds}s):
\"\"\"
{timestamped_transcript}
\"\"\"
"""


def _variables():
    return {
        "account_positioning": "America Discovers Football",
        "goal": "Identify emotionally engaging, cinematic, mythological football moments.",
        "category_rule": "",
        "clip_schema": "For each clip candidate return:\n{\n  \"clip_id\": \"001\"\n}",
        "rules_block": "Rules:\n- Favor emotional payoff over play-by-play.\n",
        "story_targets_block": "",
        "research_block": "",
        "brief_block": "",
        "match_name": "psg_arsenal_2min",
        "duration_seconds": 0,
        "timestamped_transcript": "[0s - 1s] hello world",
    }


def test_world_cup_template_file_exists():
    assert WORLD_CUP_TEMPLATE.exists()
    assert WORLD_CUP_TEMPLATE.is_file()
    assert ".stub" not in WORLD_CUP_TEMPLATE.name


def test_configured_world_cup_template_resolves():
    assert resolve_template("prompt") == WORLD_CUP_TEMPLATE


def test_basketball_example_template_resolves():
    profile = load_structured_profile(REPO_ROOT / "config" / "examples" / "basketball.json")
    resolved = resolve_profile_template_path(profile["templates"], "prompt", source="basketball.json")
    assert resolved == BASKETBALL_TEMPLATE
    assert resolved.exists()
    text = resolved.read_text(encoding="utf-8")
    assert "NON-PRODUCTION" in text.upper() or "example" in text.lower()


def test_render_is_byte_for_byte_equivalent_to_previous_template():
    variables = _variables()
    expected = PREVIOUS_PROMPT_TEMPLATE.format(**variables)
    rendered = render_template("prompt", variables=variables)
    assert rendered == expected


def test_render_is_deterministic():
    a = render_template("prompt", variables=_variables())
    b = render_template("prompt", variables=_variables())
    assert a == b


def test_render_requires_no_network_and_no_mutation(tmp_path):
    before = WORLD_CUP_TEMPLATE.read_bytes()
    sentinel = tmp_path / "sentinel.txt"
    output = render_template("prompt", variables=_variables())
    assert "psg_arsenal_2min" in output
    assert WORLD_CUP_TEMPLATE.read_bytes() == before
    assert not sentinel.exists()


def test_render_preserves_system_role_and_task():
    out = render_template("prompt", variables=_variables())
    assert "You are an elite short-form football clipping strategist for a US-targeted 2026 World Cup account." in out
    assert "produce a JSON array of clip candidates" in out


def test_render_inserts_account_positioning():
    out = render_template("prompt", variables=_variables())
    assert '"America Discovers Football"' in out


def test_render_preserves_categories():
    out = render_template("prompt", variables=_variables())
    for category in ("EMOTION", "AURA", "CHAOS", "AMERICA"):
        assert category in out


def test_render_preserves_schema_and_transcript_placement():
    out = render_template("prompt", variables=_variables())
    assert 'For each clip candidate return:\n{\n  "clip_id": "001"\n}' in out
    assert '\n"""\n[0s - 1s] hello world\n"""' in out


def test_render_unknown_template_id_raises():
    with pytest.raises(ConfigurationError, match="unregistered template 'nope'"):
        render_template("nope", variables={})


def test_render_missing_required_variable_raises():
    with pytest.raises(ConfigurationError, match="missing required variable\\(s\\)"):
        render_template("prompt", variables={"account_positioning": "x"})


def test_render_unknown_profile_raises():
    with pytest.raises(ConfigurationError, match="unknown profile"):
        render_template("prompt", profile="basketball")


def test_resolve_profile_template_path_missing_file_raises(tmp_path):
    with pytest.raises(ConfigurationError, match="not found"):
        resolve_profile_template_path({"prompt": "prompts/does_not_exist.txt"}, "prompt", source="t")
    with pytest.raises(ConfigurationError, match="not configured"):
        resolve_profile_template_path({}, "prompt", source="t")


def test_resolve_profile_template_path_rejects_traversal(tmp_path):
    with pytest.raises(ConfigurationError, match="traversal"):
        resolve_profile_template_path({"prompt": "../secrets.txt"}, "prompt", source="t", root=tmp_path)


def test_resolve_profile_template_path_rejects_absolute(tmp_path):
    with pytest.raises(ConfigurationError, match="repository-relative"):
        resolve_profile_template_path({"prompt": str(tmp_path / "abs.txt")}, "prompt", source="t", root=tmp_path)