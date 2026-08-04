"""Read-only pilot readiness reporting across jobs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.pilot import (
    create_job,
    create_pipeline_run,
    finish_pipeline_run,
    generate_delivery_package,
    pilot_readiness_report,
    register_output_manifest,
    review_output,
    start_pipeline_run,
    transition_job,
    update_pipeline_stage,
)
from tests.test_pilot_intake import build_intake
from tests.test_pilot_outputs import _fixture_files, _output_manifest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pilot_job.py"


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"readiness source" * 100)
    return path


def _ready_job(tmp_path: Path, media_file: Path, jobs_root: Path, *, pilot_id: str = "pilot_alpha") -> dict:
    intake = build_intake(str(media_file), overrides={"pilot": {"pilot_id": pilot_id}})
    intake_path = tmp_path / f"intake_{pilot_id}.json"
    intake_path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")
    return create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)


def _completed_run(job: dict, media_file: Path, jobs_root: Path) -> dict:
    created = create_pipeline_run(job["job_id"], run_id="run_001", operator="op", entry_point="process-match",
                                  command_args=["scripts/process_match.py", str(media_file)], manual_confirmed=True,
                                  jobs_dir=jobs_root)
    started = start_pipeline_run(job["job_id"], "run_001", operator="op", expected_job_revision=created["job"]["revision"],
                                 expected_run_revision=0, jobs_dir=jobs_root)
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="RUNNING", operator="op",
                                  expected_job_revision=started["job"]["revision"], expected_run_revision=1,
                                  jobs_dir=jobs_root)
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="SUCCEEDED", operator="op",
                                  expected_job_revision=stage["job"]["revision"], expected_run_revision=2,
                                  jobs_dir=jobs_root)
    return finish_pipeline_run(job["job_id"], "run_001", status="SUCCEEDED", operator="op", summary="done",
                               expected_job_revision=stage["job"]["revision"], expected_run_revision=3,
                               jobs_dir=jobs_root)


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_readiness_report_combines_run_output_and_delivery(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    finished = _completed_run(job, media_file, jobs_root)
    running = transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, expected_revision=finished["job"]["revision"], jobs_dir=jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(running, files)
    manifest["run_id"] = "run_001"
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=running["revision"])
    review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="reviewer",
                  reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="REJECTED", operator="reviewer",
                  reason="Rejected", jobs_dir=jobs_root)
    transition_job(job["job_id"], "REVIEW_REQUIRED", metadata={"operator": "op", "reason": "review"},
                   artifact_references=["outputs/review"], jobs_dir=jobs_root)
    transition_job(job["job_id"], "APPROVED", metadata={"operator": "reviewer", "approval_statement": "Approved", "deliverable_count": 1},
                   jobs_dir=jobs_root)
    generate_delivery_package(job["job_id"], package_id="pkg_ready", operator="op", delivery_method="manual",
                              delivery_destination="operator handoff", jobs_dir=jobs_root)

    before_job = (jobs_root / f"{job['job_id']}.json").read_text(encoding="utf-8")
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess")), \
         patch("socket.socket", side_effect=AssertionError("no network")):
        report = pilot_readiness_report(job["job_id"], jobs_dir=jobs_root)
    row = report["jobs"][0]
    assert row["latest_run"] == {"run_id": "run_001", "status": "SUCCEEDED", "revision": 4,
                                  "entry_point": "process-match", "started_at": row["latest_run"]["started_at"],
                                  "completed_at": row["latest_run"]["completed_at"]}
    assert row["outputs"]["review_complete"] is True
    assert row["delivery"]["active_package_id"] == "pkg_ready"
    assert row["delivery"]["represented_run_ids"] == ["run_001"]
    assert row["blockers"] == []
    assert (jobs_root / f"{job['job_id']}.json").read_text(encoding="utf-8") == before_job


def test_readiness_report_flags_blockers_and_lists_all_jobs(media_file: Path, tmp_path: Path, jobs_root: Path):
    ready = _ready_job(tmp_path, media_file, jobs_root, pilot_id="ready_job")
    running = transition_job(ready["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(running, files, manifest_id="missing_outputs")
    register_output_manifest(ready["job_id"], manifest, jobs_dir=jobs_root, expected_revision=running["revision"])
    review_output(ready["job_id"], "missing_outputs", "clip_001_tiktok", status="APPROVED", operator="reviewer",
                  reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    files["clip1"].unlink()

    other = _ready_job(tmp_path, media_file, jobs_root, pilot_id="other_ready")
    report = pilot_readiness_report(jobs_dir=jobs_root)
    rows = {row["job_id"]: row for row in report["jobs"]}
    assert report["job_count"] == 2
    assert "output_files_missing" in rows[ready["job_id"]]["blockers"]
    assert rows[other["job_id"]]["blockers"] == []


def test_readiness_cli(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    result = _run_cli(["readiness", job["job_id"], "--verbose"], jobs_root)
    assert result.returncode == 0, result.stderr
    assert "PILOT READINESS: jobs=1" in result.stdout
    assert f"{job['job_id']}\tstate=READY" in result.stdout
    assert "Traceback" not in result.stderr

    missing = _run_cli(["readiness", "missing_job"], jobs_root)
    assert missing.returncode != 0
    assert "Traceback" not in missing.stderr
