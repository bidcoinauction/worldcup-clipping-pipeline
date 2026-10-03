import json
from pathlib import Path

import pytest

from pipeline.config_errors import ConfigurationError
from pipeline.prompt_generation import build_prompt, write_prompt_file


def _write_transcript(tmp_path: Path) -> Path:
    directory = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "service_match"
    directory.mkdir(parents=True, exist_ok=True)
    transcript = directory / "transcript.txt"
    transcript.write_text("The crowd rises before the final play.", encoding="utf-8")
    (directory / "timestamps.json").write_text(
        json.dumps([{"start": 0, "end": 12, "text": "The crowd rises before the final play."}]),
        encoding="utf-8",
    )
    return transcript


def test_build_prompt_defaults_to_football_profile(tmp_path: Path):
    result = build_prompt(transcript=_write_transcript(tmp_path), match_name="service_match")
    assert result["profile"] == "football"
    assert result["sport"] == "football"
    assert "World Cup" in result["prompt"]
    assert "The crowd rises" in result["prompt"]


def test_build_prompt_explicit_basketball_profile(tmp_path: Path):
    result = build_prompt(
        transcript=_write_transcript(tmp_path),
        match_name="Lakers vs Celtics",
        profile="basketball",
    )
    assert result["profile"] == "basketball"
    assert result["sport"] == "basketball"
    assert "short-form basketball" in result["prompt"]
    assert "FINAL_QUARTER" in result["prompt"]
    assert "Global hoops for new American fans" in result["prompt"]
    assert "World Cup" not in result["prompt"]


def test_build_prompt_unknown_profile_fails_closed(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="unknown profile"):
        build_prompt(transcript=_write_transcript(tmp_path), match_name="x", profile="lacrosse")


def test_build_prompt_non_production_profile_rejected(tmp_path: Path):
    with pytest.raises(ConfigurationError, match="not production-capable"):
        build_prompt(transcript=_write_transcript(tmp_path), match_name="x", profile="basketball_sandbox")


def test_write_prompt_file_uses_service_output_path(tmp_path: Path):
    result = write_prompt_file(
        transcript=_write_transcript(tmp_path),
        match_name="service_match",
        output_root=tmp_path,
    )
    out = tmp_path / "PROMPTS" / "service_match_claude_prompt.txt"
    assert result["output_path"] == out
    assert out.exists()
    assert "service_match" in out.read_text(encoding="utf-8")
