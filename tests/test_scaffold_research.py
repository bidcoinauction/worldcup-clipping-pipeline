import json
from unittest.mock import patch


def _run_scaffold(tmp_path, extra_args=None):
    argv = [
        "prog",
        "--league", "WORLD_CUP",
        "--match-name", "psg_arsenal_test",
        "--home-team", "PSG",
        "--away-team", "Arsenal",
        "--competition", "Champions League",
        "--date", "2025-04-15",
    ]
    if extra_args:
        argv += extra_args
    with patch("sys.argv", argv):
        from scripts.scaffold_research import main
        main()


def test_scaffold_creates_file(tmp_path):
    with patch("scripts.scaffold_research.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        _run_scaffold(tmp_path)

    out = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_test" / "match_research.json"
    assert out.exists()
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["match"]["home_team"] == "PSG"
    assert data["match"]["away_team"] == "Arsenal"
    assert data["match"]["competition"] == "Champions League"
    assert data["match"]["date"] == "2025-04-15"


def test_scaffold_events_empty(tmp_path):
    with patch("scripts.scaffold_research.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        _run_scaffold(tmp_path)

    out = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_test" / "match_research.json"
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["events"] == []


def test_scaffold_dry_run(tmp_path):
    with patch("scripts.scaffold_research.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        _run_scaffold(tmp_path, extra_args=["--dry-run"])

    out = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_test" / "match_research.json"
    assert not out.exists()


def test_scaffold_no_overwrite(tmp_path):
    out = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_test" / "match_research.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("EXISTING", encoding="utf-8")

    with patch("scripts.scaffold_research.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        _run_scaffold(tmp_path)

    assert out.read_text(encoding="utf-8") == "EXISTING"


def test_scaffold_force_overwrite(tmp_path):
    out = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_test" / "match_research.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("EXISTING", encoding="utf-8")

    with patch("scripts.scaffold_research.ROOT") as mock_root:
        mock_root.__truediv__ = lambda self, other: tmp_path / other
        _run_scaffold(tmp_path, extra_args=["--force"])

    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["match"]["home_team"] == "PSG"
