import json
from pathlib import Path
from unittest.mock import patch

import pytest

import scripts.live_watch as lw
import scripts.record_live as rl
from pipeline.configurator import resolve_archive_path, resolve_archive_root, resolve_positioning


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    monkeypatch.delenv("FOOTBALL_ARCHIVE_ROOT", raising=False)
    monkeypatch.delenv("ACCOUNT_POSITIONING", raising=False)


def test_record_live_archive_root_canonical(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert rl.archive_root() == "/data/archive"
    assert rl.archive_root() == resolve_archive_root()


def test_record_live_archive_path_canonical(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert rl.archive_path("RAW", "x.ts") == "/data/archive/RAW/x.ts"
    assert rl.archive_path("RAW", "x.ts") == resolve_archive_path("RAW", "x.ts")


def test_live_watch_archive_root_canonical(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert lw.archive_root() == "/data/archive"
    assert lw.archive_root() == resolve_archive_root()


def test_live_watch_archive_path_canonical(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/data/archive")
    assert lw.archive_path("LIVE_READY") == "/data/archive/LIVE_READY"
    assert lw.archive_path("LIVE_READY") == resolve_archive_path("LIVE_READY")


def test_archive_root_environment_fallback(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/env/root")
    assert resolve_archive_root() == "/env/root"
    monkeypatch.delenv("FOOTBALL_ARCHIVE_ROOT")
    assert resolve_archive_root() in ("FootballArchive", "C:\\FootballArchive")


def test_archive_path_posix_behavior(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/a/b")
    assert resolve_archive_path("c", "d.ts") == "/a/b/c/d.ts"


def test_archive_path_windows_behavior(monkeypatch):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "C:\\FootballArchive")
    assert resolve_archive_path("c", "d.ts") == "C:\\FootballArchive\\c\\d.ts"


@patch("scripts.record_live.subprocess.Popen")
def test_record_live_explicit_output_wins_over_env(mock_popen, monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("FOOTBALL_ARCHIVE_ROOT", "/env/root")
    custom = tmp_path / "custom.ts"
    with patch(
        "sys.argv",
        [
            "record_live",
            "abc",
            "--match-id", "m",
            "--mode", "full",
            "--output", str(custom),
            "--dry-run",
        ],
    ):
        rl.main()
    captured = capsys.readouterr()
    assert "custom.ts" in captured.out
    assert "/env/root" not in captured.out


def _write_transcript(tmp_path):
    d = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "psg_arsenal_2min"
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.txt").write_text("Referee explains the penalty rules.", encoding="utf-8")
    (d / "timestamps.json").write_text(
        json.dumps([{"start": 0, "end": 10, "text": "Referee explains the penalty rules."}]), encoding="utf-8"
    )
    return d / "transcript.txt"


def _run_prompt_main(tmp_path, monkeypatch, mode=None):
    transcript = _write_transcript(tmp_path)
    argv = ["prog", "--transcript", str(transcript), "--match-name", "psg_arsenal_2min"]
    if mode:
        argv += ["--mode", mode]
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        with patch("sys.argv", argv):
            from scripts.generate_claude_prompt import main
            main()
    return (tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt").read_text(encoding="utf-8")


def test_positioning_config_wins_over_env(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCOUNT_POSITIONING", "ENV SHOULD NOT WIN")
    content = _run_prompt_main(tmp_path, monkeypatch)
    assert '"America Discovers Football"' in content
    assert "ENV SHOULD NOT WIN" not in content


def test_prompt_generation_routes_through_canonical_resolver(monkeypatch, tmp_path):
    monkeypatch.setenv("ACCOUNT_POSITIONING", "ENV FALLBACK")
    from scripts.generate_claude_prompt import main as prompt_main

    sentinel = "RESOLVED POSITIONING"
    with patch(
        "scripts.generate_claude_prompt.resolve_project_identity",
        return_value={"name": "Football Archive", "positioning": sentinel},
    ):
        transcript = _write_transcript(tmp_path)
        with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
            mock_root.__truediv__ = lambda self, other: tmp_path / other
            with patch(
                "sys.argv",
                ["prog", "--transcript", str(transcript), "--match-name", "psg_arsenal_2min"],
            ):
                prompt_main()
    content = (tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt").read_text(encoding="utf-8")
    assert sentinel in content
    assert "ENV FALLBACK" not in content


def test_generate_claude_prompt_has_no_independent_fallback():
    source = Path(__file__).resolve().parents[1] / "scripts" / "generate_claude_prompt.py"
    text = source.read_text(encoding="utf-8")
    assert 'load_config().get("account_positioning"' not in text
    assert "resolve_project_identity" in text