import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.create_match_manifest import main as create_main
from scripts.process_from_manifest import main as process_main


MATCH_ID = "test_match"
MANIFEST_CONTENT = {
    "manifest_version": 1,
    "match_id": MATCH_ID,
    "match_no": 99,
    "home_team": "Test",
    "away_team": "Team",
    "date": "2026-06-12",
    "sources": [
        {"label": "first_half", "filename": "first.ts", "status": "recorded"},
    ],
    "pipeline": {
        "recorded": True, "verified": False, "transcribed": False,
        "researched": False, "clipped": False, "exported": False,
    },
}


def _write_manifest(tmp_path: Path, overrides: dict | None = None) -> Path:
    data = {**MANIFEST_CONTENT}
    if overrides:
        data.update(overrides)
    p = tmp_path / "manifest.json"
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return p


# ── create_match_manifest.py ──


def test_create_dry_run_prints_manifest(tmp_path, capsys):
    with patch("sys.argv", [
        "create_match_manifest",
        "--match-id", "test_new",
        "--match-no", "50",
        "--home", "Home",
        "--away", "Away",
        "--date", "2026-06-15",
        "--source", "file.ts:first_half",
        "--dry-run",
    ]):
        create_main()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "test_new" in captured.out
    assert "first_half" in captured.out


def test_create_new_manifest_writes_file(tmp_path, capsys):
    with patch("sys.argv", [
        "create_match_manifest",
        "--match-id", "test_new",
        "--match-no", "50",
        "--home", "Home",
        "--away", "Away",
        "--date", "2026-06-15",
        "--source", "file.ts:first_half",
    ]):
        with patch("scripts.create_match_manifest.MANIFESTS_DIR", tmp_path):
            create_main()
    p = tmp_path / "test_new.json"
    assert p.exists()
    data = json.loads(p.read_text(encoding="utf-8"))
    assert data["match_id"] == "test_new"
    assert data["match_no"] == 50
    assert len(data["sources"]) == 1
    assert data["sources"][0]["filename"] == "file.ts"


def test_create_adds_source_to_existing(tmp_path, capsys):
    manifest = tmp_path / "existing.json"
    manifest.write_text(json.dumps({
        "manifest_version": 1,
        "match_id": "existing",
        "match_no": 10,
        "home_team": "A", "away_team": "B", "date": "2026-06-10",
        "sources": [{"label": "first", "filename": "first.ts", "status": "recorded"}],
        "pipeline": {"recorded": True, "verified": False, "transcribed": False,
                      "researched": False, "clipped": False, "exported": False},
    }), encoding="utf-8")
    with patch("sys.argv", [
        "create_match_manifest",
        "--match-id", "existing",
        "--source", "second.ts:second_half",
    ]):
        with patch("scripts.create_match_manifest.MANIFESTS_DIR", tmp_path):
            create_main()
    data = json.loads(manifest.read_text(encoding="utf-8"))
    assert len(data["sources"]) == 2
    assert data["sources"][1]["filename"] == "second.ts"


def test_create_missing_match_no_exits(tmp_path, capsys):
    with patch("sys.argv", [
        "create_match_manifest",
        "--match-id", "new_no_no",
    ]):
        try:
            create_main()
            assert False, "expected SystemExit"
        except SystemExit:
            pass


# ── process_from_manifest.py ──


def test_process_dry_run_prints_commands(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--dry-run",
    ]):
        with patch("scripts.process_from_manifest.archive_path", return_value="/tmp/first.ts"):
            process_main()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out
    assert "ffmpeg" in captured.out
    assert "update_match.py" in captured.out
    assert "process_scheduled_match.py" in captured.out


def test_process_dry_run_shows_warning_for_missing_files(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--dry-run",
    ]):
        process_main()
    captured = capsys.readouterr()
    assert "WARNING" in captured.out
    assert "first.ts" in captured.out


def test_process_manifest_not_found_exits(tmp_path, capsys):
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(tmp_path / "nonexistent.json"),
    ]):
        try:
            process_main()
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_process_no_recorded_sources_exits(tmp_path, capsys):
    manifest = _write_manifest(tmp_path, {
        "sources": [{"label": "first", "filename": "first.ts", "status": "failed"}],
    })
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
    ]):
        try:
            process_main()
            assert False, "expected SystemExit"
        except SystemExit:
            pass


def test_process_run_detection_flag_shown_in_dry_run(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--run-detection",
        "--dry-run",
    ]):
        with patch("scripts.process_from_manifest.archive_path", return_value="/tmp/first.ts"):
            process_main()
    captured = capsys.readouterr()
    assert "--run-detection" in captured.out


def test_process_overwrite_flag_shown_in_dry_run(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--overwrite",
        "--dry-run",
    ]):
        with patch("scripts.process_from_manifest.archive_path", return_value="/tmp/first.ts"):
            process_main()
    captured = capsys.readouterr()
    assert "[dry-run]" in captured.out


def test_process_mode_flag_pass_through(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--mode", "micro",
        "--dry-run",
    ]):
        with patch("scripts.process_from_manifest.archive_path", return_value="/tmp/first.ts"):
            process_main()
    captured = capsys.readouterr()
    assert "--mode" in captured.out
    assert "micro" in captured.out


def test_process_no_condense_flag_pass_through(tmp_path, capsys):
    manifest = _write_manifest(tmp_path)
    with patch("sys.argv", [
        "process_from_manifest",
        "--manifest", str(manifest),
        "--no-condense",
        "--dry-run",
    ]):
        with patch("scripts.process_from_manifest.archive_path", return_value="/tmp/first.ts"):
            process_main()
    captured = capsys.readouterr()
    assert "--no-condense" in captured.out
