import json
import os
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "validate_config.py"
CONFIG = REPO_ROOT / "config" / "pipeline_config.json"
BASKETBALL = REPO_ROOT / "config" / "examples" / "basketball.json"


def _run(path: Path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        capture_output=True,
        text=True,
    )


def test_cli_valid_legacy_config():
    result = _run(CONFIG)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_valid_structured_config():
    result = _run(BASKETBALL)
    assert result.returncode == 0, result.stderr
    assert "OK" in result.stdout


def test_cli_invalid_config_exits_nonzero_with_field_path(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"name": "demo", "taxonomies": {"emitional_kinds": []}}), encoding="utf-8")
    before = bad.stat().st_mtime_ns
    result = _run(bad)
    assert result.returncode != 0
    assert "taxonomies.emitional_kinds" in result.stderr
    assert bad.stat().st_mtime_ns == before


def test_cli_missing_file_nonzero(tmp_path):
    result = _run(tmp_path / "nope.json")
    assert result.returncode != 0


def test_cli_malformed_json_nonzero(tmp_path):
    bad = tmp_path / "broken.json"
    bad.write_text("{not json", encoding="utf-8")
    result = _run(bad)
    assert result.returncode != 0
    assert "INVALID" in result.stderr