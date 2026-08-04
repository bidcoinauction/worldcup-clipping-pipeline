"""Manual pipeline-run records and execution provenance."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.pilot import (
    JobRecordError,
    JobRevisionError,
    OutputManifestError,
    PipelineRunError,
    create_job,
    create_pipeline_run,
    finish_pipeline_run,
    generate_delivery_package,
    list_pipeline_runs,
    pipeline_run_summary,
    read_history,
    register_output_manifest,
    review_output,
    show_pipeline_run,
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
    path.write_bytes(b"manual run source" * 100)
    return path


def _write_intake(tmp_path: Path, media_file: Path, *, pilot_id: str = "pilot_alpha") -> Path:
    data = build_intake(str(media_file), overrides={"pilot": {"pilot_id": pilot_id}})
    path = tmp_path / f"intake_{pilot_id}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _ready_job(tmp_path: Path, media_file: Path, jobs_root: Path, *, pilot_id: str = "pilot_alpha") -> dict:
    intake_path = _write_intake(tmp_path, media_file, pilot_id=pilot_id)
    return create_job(json.loads(intake_path.read_text(encoding="utf-8")), intake_path=intake_path, jobs_dir=jobs_root)


def _command(media_file: Path) -> list[str]:
    return ["scripts/process_match.py", "--input", str(media_file), "--league", "WORLD_CUP", "--match-name", "A vs B"]


def _create_run(job: dict, media_file: Path, jobs_root: Path, *, run_id: str = "run_001") -> dict:
    return create_pipeline_run(job["job_id"], run_id=run_id, operator="op", entry_point="process-match",
                               command_args=_command(media_file), manual_confirmed=True, jobs_dir=jobs_root)


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_create_run_records_provenance_event_and_no_execution(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    before = media_file.read_bytes()
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess")), \
         patch("socket.socket", side_effect=AssertionError("no network")):
        result = create_pipeline_run(job["job_id"], run_id="run_001", operator="op", entry_point="process-match",
                                     command_args=_command(media_file), manual_confirmed=True,
                                     expected_job_revision=0, jobs_dir=jobs_root,
                                     models={"transcription_provider": "faster-whisper", "detection_provider": "openai"})
    run = result["run"]
    assert run["status"] == "PLANNED"
    assert run["revision"] == 0
    assert run["provenance"]["source_media"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert run["provenance"]["project_configuration"]["exists"] is True
    assert run["manual_execution_confirmed"] is True
    assert Path(result["run_path"]).exists()
    assert result["job"]["pipeline_runs"] == ["run_001"]
    assert read_history(job["job_id"], jobs_dir=jobs_root)[-1]["event_type"] == "PIPELINE_RUN_CREATED"
    assert media_file.read_bytes() == before


def test_create_run_rejects_unsafe_unknown_missing_confirmation_stale_duplicate_and_invalid_source(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    with pytest.raises(PipelineRunError):
        create_pipeline_run(job["job_id"], run_id="run_bad", operator="op", entry_point="bogus",
                            command_args=_command(media_file), manual_confirmed=True, jobs_dir=jobs_root)
    with pytest.raises(PipelineRunError):
        create_pipeline_run(job["job_id"], run_id="run_bad", operator="op", entry_point="process-match",
                            command_args=["scripts/process_match.py", "$(cat secrets)"], manual_confirmed=True, jobs_dir=jobs_root)
    with pytest.raises(PipelineRunError):
        create_pipeline_run(job["job_id"], run_id="run_bad", operator="op", entry_point="process-match",
                            command_args=_command(media_file), manual_confirmed=False, jobs_dir=jobs_root)
    with pytest.raises(JobRevisionError):
        create_pipeline_run(job["job_id"], run_id="run_bad", operator="op", entry_point="process-match",
                            command_args=_command(media_file), manual_confirmed=True, expected_job_revision=99, jobs_dir=jobs_root)
    _create_run(job, media_file, jobs_root, run_id="run_001")
    with pytest.raises(PipelineRunError):
        _create_run(job, media_file, jobs_root, run_id="run_001")
    bad_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="source_missing")
    media_file.unlink()
    with pytest.raises(JobRecordError):
        _create_run(bad_job, media_file, jobs_root, run_id="run_missing")


def test_run_lifecycle_stage_updates_and_summary(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    created = _create_run(job, media_file, jobs_root)
    started = start_pipeline_run(job["job_id"], "run_001", operator="op", expected_job_revision=created["job"]["revision"],
                                 expected_run_revision=0, jobs_dir=jobs_root)
    assert started["run"]["status"] == "STARTED"
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="RUNNING", operator="op",
                                  expected_job_revision=started["job"]["revision"], expected_run_revision=1, jobs_dir=jobs_root)
    transcript = tmp_path / "transcript.json"
    transcript.write_text("{}\n", encoding="utf-8")
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="SUCCEEDED", operator="op",
                                  outputs=[str(transcript)], expected_job_revision=stage["job"]["revision"],
                                  expected_run_revision=2, jobs_dir=jobs_root)
    stage = update_pipeline_stage(job["job_id"], "run_001", "DETECTION", status="SKIPPED", operator="op",
                                  notes="Manual detection deferred", expected_job_revision=stage["job"]["revision"],
                                  expected_run_revision=3, jobs_dir=jobs_root)
    with pytest.raises(PipelineRunError):
        update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="RUNNING", operator="op", jobs_dir=jobs_root)
    finished = finish_pipeline_run(job["job_id"], "run_001", status="SUCCEEDED", operator="op",
                                   summary="Manual pipeline run completed", expected_job_revision=stage["job"]["revision"],
                                   expected_run_revision=4, jobs_dir=jobs_root)
    assert finished["run"]["status"] == "SUCCEEDED"
    summary = pipeline_run_summary(job["job_id"], "run_001", jobs_dir=jobs_root)
    assert summary["stage_counts_by_status"]["SUCCEEDED"] == 1
    assert summary["stage_counts_by_status"]["SKIPPED"] == 1
    assert summary["eligible_for_output_registration"] is True
    assert str(transcript) in summary["referenced_outputs"]
    assert show_pipeline_run(job["job_id"], "run_001", jobs_dir=jobs_root)["status"] == "SUCCEEDED"
    assert list_pipeline_runs(job["job_id"], jobs_dir=jobs_root)[0]["run_id"] == "run_001"


def test_run_failure_partial_abort_and_stale_updates(media_file: Path, tmp_path: Path, jobs_root: Path):
    failed_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="failed_run")
    _create_run(failed_job, media_file, jobs_root, run_id="run_failed")
    started = start_pipeline_run(failed_job["job_id"], "run_failed", operator="op", jobs_dir=jobs_root)
    stage = update_pipeline_stage(failed_job["job_id"], "run_failed", "TRANSCRIPTION", status="RUNNING", operator="op",
                                  expected_job_revision=started["job"]["revision"], expected_run_revision=1, jobs_dir=jobs_root)
    with pytest.raises(PipelineRunError):
        update_pipeline_stage(failed_job["job_id"], "run_failed", "TRANSCRIPTION", status="FAILED", operator="op",
                              expected_job_revision=stage["job"]["revision"], expected_run_revision=2, jobs_dir=jobs_root)
    failed_stage = update_pipeline_stage(failed_job["job_id"], "run_failed", "TRANSCRIPTION", status="FAILED", operator="op",
                                         error_category="PROCESSING", error_summary="Transcription failed without secret details",
                                         expected_job_revision=stage["job"]["revision"], expected_run_revision=2, jobs_dir=jobs_root)
    with pytest.raises(JobRevisionError):
        finish_pipeline_run(failed_job["job_id"], "run_failed", status="FAILED", operator="op", summary="failed",
                            failure_category="PROCESSING", failure_summary="failed", expected_run_revision=2, jobs_dir=jobs_root)
    failed = finish_pipeline_run(failed_job["job_id"], "run_failed", status="FAILED", operator="op", summary="failed",
                                 failure_category="PROCESSING", failure_summary="Transcription failed without secret details",
                                 expected_job_revision=failed_stage["job"]["revision"], expected_run_revision=3, jobs_dir=jobs_root)
    assert failed["run"]["failure_summary"] == "Transcription failed without secret details"

    partial_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="partial_run")
    _create_run(partial_job, media_file, jobs_root, run_id="run_partial")
    partial_started = start_pipeline_run(partial_job["job_id"], "run_partial", operator="op", jobs_dir=jobs_root)
    partial_stage = update_pipeline_stage(partial_job["job_id"], "run_partial", "TRANSCRIPTION", status="RUNNING", operator="op",
                                          expected_job_revision=partial_started["job"]["revision"], expected_run_revision=1, jobs_dir=jobs_root)
    partial_stage = update_pipeline_stage(partial_job["job_id"], "run_partial", "TRANSCRIPTION", status="SUCCEEDED", operator="op",
                                          expected_job_revision=partial_stage["job"]["revision"], expected_run_revision=2, jobs_dir=jobs_root)
    assert finish_pipeline_run(partial_job["job_id"], "run_partial", status="PARTIALLY_SUCCEEDED", operator="op", summary="partial",
                               partial_success_explanation="Detection skipped", expected_job_revision=partial_stage["job"]["revision"],
                               expected_run_revision=3, jobs_dir=jobs_root)["run"]["status"] == "PARTIALLY_SUCCEEDED"

    abort_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="abort_run")
    _create_run(abort_job, media_file, jobs_root, run_id="run_abort")
    abort_started = start_pipeline_run(abort_job["job_id"], "run_abort", operator="op", jobs_dir=jobs_root)
    assert finish_pipeline_run(abort_job["job_id"], "run_abort", status="ABORTED", operator="op", summary="operator stopped",
                               expected_job_revision=abort_started["job"]["revision"], expected_run_revision=1,
                               jobs_dir=jobs_root)["run"]["status"] == "ABORTED"


def test_output_manifest_run_linkage_and_delivery_package_run_ids(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    created = _create_run(job, media_file, jobs_root)
    started = start_pipeline_run(job["job_id"], "run_001", operator="op", expected_job_revision=created["job"]["revision"],
                                 expected_run_revision=0, jobs_dir=jobs_root)
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="RUNNING", operator="op",
                                  expected_job_revision=started["job"]["revision"], expected_run_revision=1, jobs_dir=jobs_root)
    stage = update_pipeline_stage(job["job_id"], "run_001", "TRANSCRIPTION", status="SUCCEEDED", operator="op",
                                  expected_job_revision=stage["job"]["revision"], expected_run_revision=2, jobs_dir=jobs_root)
    finished = finish_pipeline_run(job["job_id"], "run_001", status="SUCCEEDED", operator="op", summary="done",
                                   expected_job_revision=stage["job"]["revision"], expected_run_revision=3, jobs_dir=jobs_root)
    running = transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, expected_revision=finished["job"]["revision"], jobs_dir=jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(running, files)
    manifest["run_id"] = "run_001"
    result = register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=running["revision"])
    assert result["manifest"]["run_id"] == "run_001"
    assert pipeline_run_summary(job["job_id"], "run_001", jobs_dir=jobs_root)["registered_output_manifest_ids"] == ["outputs_alpha"]
    review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="reviewer",
                  reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="REJECTED", operator="reviewer",
                  reason="Rejected", jobs_dir=jobs_root)
    transition_job(job["job_id"], "REVIEW_REQUIRED", metadata={"operator": "op", "reason": "review"},
                   artifact_references=["outputs/review"], jobs_dir=jobs_root)
    transition_job(job["job_id"], "APPROVED", metadata={"operator": "reviewer", "approval_statement": "Approved", "deliverable_count": 1},
                   jobs_dir=jobs_root)
    package = generate_delivery_package(job["job_id"], package_id="pkg_run", operator="op", delivery_method="manual",
                                        delivery_destination="operator handoff", jobs_dir=jobs_root)["package"]
    assert package["represented_run_ids"] == ["run_001"]
    assert package["deliverables"][0]["run_id"] == "run_001"

    legacy_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="legacy_output")
    transition_job(legacy_job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    legacy_manifest = _output_manifest(legacy_job, _fixture_files(tmp_path), manifest_id="legacy_outputs")
    assert register_output_manifest(legacy_job["job_id"], legacy_manifest, jobs_dir=jobs_root)["manifest"].get("run_id") is None

    incomplete_job = _ready_job(tmp_path, media_file, jobs_root, pilot_id="incomplete_run")
    _create_run(incomplete_job, media_file, jobs_root, run_id="run_incomplete")
    transition_job(incomplete_job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    bad_manifest = _output_manifest(incomplete_job, _fixture_files(tmp_path), manifest_id="bad_run_outputs")
    bad_manifest["run_id"] = "run_incomplete"
    with pytest.raises(OutputManifestError):
        register_output_manifest(incomplete_job["job_id"], bad_manifest, jobs_dir=jobs_root)


def test_runs_cli_and_expected_errors_no_traceback(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _ready_job(tmp_path, media_file, jobs_root)
    created = _run_cli(["runs", "create", job["job_id"], "--run-id", "run_cli", "--operator", "op",
                        "--entry-point", "process-match", "--command-arg", "scripts/process_match.py",
                        "--command-arg=--input", "--command-arg", str(media_file), "--manual-confirmed",
                        "--expected-job-revision", "0"], jobs_root)
    assert created.returncode == 0, created.stderr
    unsafe = _run_cli(["runs", "create", job["job_id"], "--run-id", "run_unsafe", "--operator", "op",
                       "--entry-point", "process-match", "--command-arg", "scripts/process_match.py;rm", "--manual-confirmed"], jobs_root)
    assert unsafe.returncode != 0
    assert "Traceback" not in unsafe.stderr
    stale = _run_cli(["runs", "start", job["job_id"], "run_cli", "--operator", "op", "--expected-job-revision", "0", "--expected-run-revision", "0"], jobs_root)
    assert stale.returncode != 0
    assert "current revision" in stale.stderr
    started = _run_cli(["runs", "start", job["job_id"], "run_cli", "--operator", "op", "--expected-job-revision", "1", "--expected-run-revision", "0"], jobs_root)
    assert started.returncode == 0, started.stderr
    stage = _run_cli(["runs", "stage", job["job_id"], "run_cli", "TRANSCRIPTION", "--status", "RUNNING", "--operator", "op",
                      "--expected-job-revision", "2", "--expected-run-revision", "1"], jobs_root)
    assert stage.returncode == 0, stage.stderr
    stage_done = _run_cli(["runs", "stage", job["job_id"], "run_cli", "TRANSCRIPTION", "--status", "SUCCEEDED", "--operator", "op",
                           "--output", "outputs/transcript.json", "--expected-job-revision", "3", "--expected-run-revision", "2"], jobs_root)
    assert stage_done.returncode == 0, stage_done.stderr
    finish = _run_cli(["runs", "finish", job["job_id"], "run_cli", "--status", "SUCCEEDED", "--operator", "op",
                       "--summary", "Manual pipeline run completed", "--expected-job-revision", "4", "--expected-run-revision", "3"], jobs_root)
    assert finish.returncode == 0, finish.stderr
    assert _run_cli(["runs", "list", job["job_id"]], jobs_root).returncode == 0
    assert _run_cli(["runs", "show", job["job_id"], "run_cli"], jobs_root).returncode == 0
    summary = _run_cli(["runs", "summary", job["job_id"], "run_cli"], jobs_root)
    assert summary.returncode == 0
    assert "eligible_for_output_registration: yes" in summary.stdout
