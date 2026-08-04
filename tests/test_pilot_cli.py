"""CLI behavior: exit codes, error handling, no media/network execution."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from scripts.pilot_job import main
from tests.test_pilot_intake import build_intake

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pilot_job.py"


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"cli media bytes" * 100)
    return path


@pytest.fixture
def jobs_root(tmp_path: Path) -> Path:
    return tmp_path / "jobs"


def _write_intake(tmp_path: Path, intake: dict, name: str = "intake.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return path


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=env,
    )


# ── validate ─────────────────────────────────────────────────────────────────


def test_validate_valid_intake_exits_zero(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    result = _run_cli(["validate", str(intake_path)], jobs_root)
    assert result.returncode == 0, result.stderr
    assert "execution-ready" in result.stdout
    assert "execution_ready=yes" in result.stdout


def test_validate_structurally_invalid_exits_nonzero(tmp_path: Path, jobs_root: Path):
    intake = build_intake(str(tmp_path / "source.mp4"))
    intake["pilot"]["pilot_id"] = "bad id"
    intake_path = _write_intake(tmp_path, intake)
    result = _run_cli(["validate", str(intake_path)], jobs_root)
    assert result.returncode != 0
    assert "pilot.pilot_id" in result.stdout


def test_validate_nonready_but_structurally_valid_reported(tmp_path: Path, jobs_root: Path):
    intake = build_intake(str(tmp_path / "missing.mp4"), overrides={"rights": {"status": "UNCONFIRMED"}})
    intake_path = _write_intake(tmp_path, intake)
    result = _run_cli(["validate", str(intake_path)], jobs_root)
    assert result.returncode == 0
    assert "not execution-ready" in result.stdout
    assert "RIGHTS_NOT_CONFIRMED" in result.stdout


def test_validate_makes_no_file_changes(tmp_path: Path, jobs_root: Path, media_file: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    before = sorted(p.name for p in tmp_path.iterdir())
    _run_cli(["validate", str(intake_path)], jobs_root)
    after = sorted(p.name for p in tmp_path.iterdir())
    assert before == after
    assert not jobs_root.exists()


# ── create ───────────────────────────────────────────────────────────────────


def test_create_produces_correct_state(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    result = _run_cli(["create", str(intake_path), "--operator", "op1"], jobs_root)
    assert result.returncode == 0, result.stderr
    assert "JOB CREATED" in result.stdout
    assert "READY" in result.stdout
    job_file = jobs_root / "pilot_alpha_source_alpha.json"
    events_file = jobs_root / "pilot_alpha_source_alpha.events.json"
    assert job_file.exists() and events_file.exists()


def test_create_unconfirmed_creates_awaiting_rights(tmp_path: Path, jobs_root: Path, media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}})
    intake_path = _write_intake(tmp_path, intake)
    result = _run_cli(["create", str(intake_path)], jobs_root)
    assert result.returncode == 2
    assert "AWAITING_RIGHTS" in result.stdout


def test_create_duplicate_exits_nonzero_no_traceback(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    first = _run_cli(["create", str(intake_path)], jobs_root)
    assert first.returncode == 0
    second = _run_cli(["create", str(intake_path)], jobs_root)
    assert second.returncode != 0
    assert "already exists" in second.stderr
    assert "Traceback" not in second.stderr


def test_create_missing_file_exits_nonzero(tmp_path: Path, jobs_root: Path):
    result = _run_cli(["create", str(tmp_path / "nope.json")], jobs_root)
    assert result.returncode != 0
    assert "intake file not found" in result.stderr


# ── show ─────────────────────────────────────────────────────────────────────


def test_show_returns_correct_summary(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    _run_cli(["create", str(intake_path)], jobs_root)
    result = _run_cli(["show", "pilot_alpha_source_alpha"], jobs_root)
    assert result.returncode == 0, result.stderr
    assert "READY" in result.stdout
    assert "pilot_alpha" in result.stdout
    assert "source_alpha" in result.stdout
    assert "events: 1" in result.stdout


def test_show_missing_job_exits_nonzero(jobs_root: Path):
    result = _run_cli(["show", "nope"], jobs_root)
    assert result.returncode != 0
    assert "not found" in result.stderr


# ── list ─────────────────────────────────────────────────────────────────────


def test_list_records(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    _run_cli(["create", str(intake_path)], jobs_root)
    result = _run_cli(["list"], jobs_root)
    assert result.returncode == 0
    assert "pilot_alpha_source_alpha" in result.stdout


# ── error behavior ───────────────────────────────────────────────────────────


def test_unexpected_errors_not_silently_swallowed(tmp_path: Path, media_file: Path, monkeypatch, jobs_root: Path):
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_root))

    def boom(*args, **kwargs):
        raise RuntimeError("unexpected programming error")

    with patch("scripts.pilot_job.create_job", side_effect=boom):
        with pytest.raises(RuntimeError):
            main(["create", str(_write_intake(tmp_path, build_intake(str(media_file))))])


def test_no_ffmpeg_or_api_execution_during_validate_and_create(
    media_file: Path, tmp_path: Path, jobs_root: Path, monkeypatch
):
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_root))
    intake_path = _write_intake(tmp_path, build_intake(str(media_file)))
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess during pilot ops")):
        code_validate = main(["validate", str(intake_path)])
        code_create = main(["create", str(intake_path)])
    assert code_validate == 0
    assert code_create == 0
