"""Readiness-gated pilot execution-plan manifests."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.pilot import (
    EXECUTION_PLAN_SCHEMA_VERSION,
    ExecutionPlanError,
    JobRevisionError,
    PipelineRunError,
    create_job,
    create_pipeline_run,
    generate_execution_plan,
    invalidate_execution_plan,
    list_execution_plans,
    read_execution_plan_checklist,
    read_history,
    show_execution_plan,
    validate_execution_plan,
)
from tests.test_pilot_intake import build_intake

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "pilot_job.py"


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"execution plan source" * 100)
    return path


def _write_intake(tmp_path: Path, intake: dict) -> Path:
    path = tmp_path / f"intake_{intake['pilot']['pilot_id']}.json"
    path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")
    return path


def _ready_job(tmp_path: Path, media_file: Path, jobs_root: Path, *, pilot_id: str = "pilot_alpha", overrides: dict | None = None) -> tuple[dict, Path, dict]:
    merged = {"pilot": {"pilot_id": pilot_id}}
    if overrides:
        for key, value in overrides.items():
            merged.setdefault(key, {}).update(value) if isinstance(value, dict) and isinstance(merged.get(key), dict) else merged.update({key: value})
    intake = build_intake(str(media_file), overrides=merged)
    path = _write_intake(tmp_path, intake)
    return create_job(intake, intake_path=path, jobs_dir=jobs_root), path, intake


def _generate(job: dict, jobs_root: Path, *, plan_id: str = "plan_001", workflow: str = "local-match-file", recording_manifest: Path | None = None) -> dict:
    return generate_execution_plan(job["job_id"], plan_id=plan_id, operator="tyler", expected_job_revision=job["revision"],
                                   workflow=workflow, recording_manifest=str(recording_manifest) if recording_manifest else None,
                                   jobs_dir=jobs_root)


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def _codes(exc: ExecutionPlanError) -> set[str]:
    return {issue["code"] for issue in exc.issues}


def test_valid_local_file_plan_schema_stage_order_provenance_and_no_execution(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    before = media_file.read_bytes()
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess")), \
         patch("socket.socket", side_effect=AssertionError("no network")):
        result = _generate(job, jobs_root)
    plan = result["plan"]
    assert plan["schema_version"] == EXECUTION_PLAN_SCHEMA_VERSION
    assert plan["status"] == "READY"
    assert plan["workflow"] == "local-match-file"
    assert plan["repository"]["commit"]
    assert "dirty" in plan["repository"]
    assert plan["required_environment_variables"] == ["FOOTBALL_ARCHIVE_ROOT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY"]
    assert all("=" not in value for value in plan["required_environment_variables"])
    assert [stage["sequence"] for stage in plan["stages"]] == list(range(1, 12))
    assert len({stage["sequence"] for stage in plan["stages"]}) == 11
    assert [stage["stage_id"] for stage in plan["stages"]] == [
        "SOURCE_INTAKE", "CONCATENATION", "TRANSCRIPTION", "RESEARCH", "PROMPT_GENERATION",
        "DETECTION", "CLIP_MANIFEST", "ASSET_PROMPTS", "CLIP_EXPORT", "REVIEW_DASHBOARD", "OUTPUT_REGISTRATION",
    ]
    assert plan["stages"][1]["enabled"] is False
    assert plan["stages"][1]["skip_reason"]
    assert all(stage["entry_point"] and stage["arguments"][0].startswith("scripts/") for stage in plan["stages"])
    assert plan["manual_run"]["entry_point"] == "process-match"
    assert plan["provenance"]["source_media"]["sha256"]
    assert plan["provenance"]["project_configuration"]["exists"] is True
    assert validate_execution_plan(plan, job=result["job"], jobs_dir=jobs_root)["valid"]
    assert (jobs_root / f"{job['job_id']}.plans" / "plan_001.json").exists()
    assert (jobs_root / f"{job['job_id']}.plans" / "plan_001.txt").exists()
    assert media_file.read_bytes() == before


def test_valid_recording_manifest_plan(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    manifest = tmp_path / "recording_manifest.json"
    manifest.write_text('{"sources": []}\n', encoding="utf-8")
    result = _generate(job, jobs_root, workflow="recording-manifest", recording_manifest=manifest)
    plan = result["plan"]
    concat = plan["stages"][1]
    assert plan["workflow"] == "recording-manifest"
    assert concat["enabled"] is True
    assert concat["entry_point"] == "process-from-manifest"
    assert str(manifest) in concat["arguments"]


def test_generation_blockers_create_no_plan_event_or_revision(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, intake_path, intake = _ready_job(tmp_path, media_file, jobs_root)
    intake["rights"]["status"] = "UNCONFIRMED"
    intake_path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")
    before_job = (jobs_root / f"{job['job_id']}.json").read_text(encoding="utf-8")
    before_events = read_history(job["job_id"], jobs_dir=jobs_root)
    with pytest.raises(ExecutionPlanError) as exc:
        _generate(job, jobs_root)
    assert "rights_not_cleared" in _codes(exc.value)
    assert not (jobs_root / f"{job['job_id']}.plans").exists()
    assert (jobs_root / f"{job['job_id']}.json").read_text(encoding="utf-8") == before_job
    assert read_history(job["job_id"], jobs_dir=jobs_root) == before_events


@pytest.mark.parametrize("mutator,code", [
    (lambda intake, media: intake["rights"].update({"status": "REJECTED"}), "rights_not_cleared"),
    (lambda intake, media: media.unlink(), "source_not_ready"),
    (lambda intake, media: intake["configuration"].update({"brand": "does_not_exist"}), "configuration_invalid"),
])
def test_rights_source_and_configuration_block_generation(media_file: Path, tmp_path: Path, jobs_root: Path, mutator, code):
    job, intake_path, intake = _ready_job(tmp_path, media_file, jobs_root, pilot_id=f"pilot_{code}")
    mutator(intake, media_file)
    intake_path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ExecutionPlanError) as exc:
        _generate(job, jobs_root)
    assert code in _codes(exc.value)


def test_non_ready_unsupported_workflow_basketball_stale_and_duplicate_blocked(media_file: Path, tmp_path: Path, jobs_root: Path):
    bad_job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root, pilot_id="bad_state")
    bad_record = jobs_root / f"{bad_job['job_id']}.json"
    data = json.loads(bad_record.read_text(encoding="utf-8"))
    data["current_state"] = "RUNNING"
    bad_record.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    events_path = jobs_root / f"{bad_job['job_id']}.events.json"
    events = json.loads(events_path.read_text(encoding="utf-8"))
    events[-1]["new_state"] = "RUNNING"
    events_path.write_text(json.dumps(events, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(ExecutionPlanError) as exc:
        _generate(data, jobs_root)
    assert "job_not_ready" in _codes(exc.value)

    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root, pilot_id="bad_workflow")
    with pytest.raises(ExecutionPlanError) as workflow_exc:
        _generate(job, jobs_root, workflow="auto-run")
    assert "unsupported_workflow" in _codes(workflow_exc.value)
    with pytest.raises(JobRevisionError):
        generate_execution_plan(job["job_id"], plan_id="stale", operator="tyler", expected_job_revision=99, jobs_dir=jobs_root)
    _generate(job, jobs_root, plan_id="dupe")
    with pytest.raises(ExecutionPlanError) as duplicate_exc:
        generate_execution_plan(job["job_id"], plan_id="dupe", operator="tyler", expected_job_revision=1, jobs_dir=jobs_root)
    assert "duplicate_plan_id" in _codes(duplicate_exc.value)

    basketball = build_intake(str(media_file), overrides={"pilot": {"pilot_id": "basketball"}, "configuration": {"project": "basketball"}})
    basketball_path = _write_intake(tmp_path, basketball)
    basketball_job = create_job(basketball, intake_path=basketball_path, jobs_dir=jobs_root)
    with pytest.raises(ExecutionPlanError) as basketball_exc:
        generate_execution_plan(basketball_job["job_id"], plan_id="basket", operator="tyler", expected_job_revision=0, jobs_dir=jobs_root)
    assert "unsupported_project" in _codes(basketball_exc.value)


def test_validation_rejects_unsafe_shell_secret_and_path_values(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    result = _generate(job, jobs_root)
    plan = json.loads(json.dumps(result["plan"]))
    plan["stages"][0]["arguments"][1] = "--input;rm"
    assert "UNSAFE_ARGUMENT" in {issue["code"] for issue in validate_execution_plan(plan, job=result["job"], jobs_dir=jobs_root)["issues"]}
    plan = json.loads(json.dumps(result["plan"]))
    plan["manual_run"]["arguments"][1] = "$(cat secrets)"
    assert not validate_execution_plan(plan, job=result["job"], jobs_dir=jobs_root)["valid"]
    plan = json.loads(json.dumps(result["plan"]))
    plan["expected_inputs"].append("sk-1234567890")
    assert "SECRET_VALUE" in {issue["code"] for issue in validate_execution_plan(plan, job=result["job"], jobs_dir=jobs_root)["issues"]}


def test_list_show_checklist_invalidate_historical_preservation_and_cli(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    generated = _run_cli(["plans", "generate", job["job_id"], "--plan-id", "plan_cli", "--operator", "tyler",
                          "--expected-job-revision", "0"], jobs_root)
    assert generated.returncode == 0, generated.stderr
    assert "EXECUTION PLAN GENERATED" in generated.stdout
    assert _run_cli(["plans", "validate", job["job_id"], "plan_cli"], jobs_root).returncode == 0
    assert "plan_cli" in _run_cli(["plans", "list", job["job_id"]], jobs_root).stdout
    shown = _run_cli(["plans", "show", job["job_id"], "plan_cli"], jobs_root)
    assert '"stages"' in shown.stdout
    checklist = _run_cli(["plans", "checklist", job["job_id"], "plan_cli"], jobs_root)
    assert "Execution Plan Checklist" in checklist.stdout
    stale = _run_cli(["plans", "invalidate", job["job_id"], "plan_cli", "--operator", "tyler", "--reason", "Source file was replaced",
                      "--expected-job-revision", "0", "--expected-plan-revision", "0"], jobs_root)
    assert stale.returncode != 0
    invalidated = _run_cli(["plans", "invalidate", job["job_id"], "plan_cli", "--operator", "tyler", "--reason", "Source file was replaced",
                            "--expected-job-revision", "1", "--expected-plan-revision", "0"], jobs_root)
    assert invalidated.returncode == 0, invalidated.stderr
    plan = show_execution_plan(job["job_id"], "plan_cli", jobs_dir=jobs_root)
    assert plan["status"] == "INVALIDATED"
    assert plan["revision"] == 1
    assert read_execution_plan_checklist(job["job_id"], "plan_cli", jobs_dir=jobs_root)
    assert show_execution_plan(job["job_id"], "plan_cli", jobs_dir=jobs_root)["invalidation_reason"] == "Source file was replaced"
    replacement = _run_cli(["plans", "generate", job["job_id"], "--plan-id", "plan_replacement", "--operator", "tyler",
                            "--expected-job-revision", "2"], jobs_root)
    assert replacement.returncode == 0, replacement.stderr
    assert {row["plan_id"] for row in list_execution_plans(job["job_id"], jobs_dir=jobs_root)} == {"plan_cli", "plan_replacement"}


def test_run_linkage_legacy_and_rejections(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    generated = _generate(job, jobs_root)
    plan = generated["plan"]
    run = create_pipeline_run(job["job_id"], run_id="run_plan", operator="tyler", entry_point=plan["manual_run"]["entry_point"],
                              command_args=plan["manual_run"]["arguments"], manual_confirmed=True,
                              expected_job_revision=generated["job"]["revision"], plan_id=plan["plan_id"], jobs_dir=jobs_root)["run"]
    assert run["plan_id"] == "plan_001"
    assert run["plan_revision"] == 0
    assert run["plan_workflow"] == "local-match-file"
    assert run["planned_stage_ids"][0] == "SOURCE_INTAKE"

    legacy_job, _legacy_path, _legacy_intake = _ready_job(tmp_path, media_file, jobs_root, pilot_id="legacy_plan")
    legacy = create_pipeline_run(legacy_job["job_id"], run_id="legacy", operator="tyler", entry_point="process-match",
                                 command_args=["scripts/process_match.py", "--input", str(media_file), "--league", "WORLD_CUP", "--match-name", "A vs B"],
                                 manual_confirmed=True, jobs_dir=jobs_root)["run"]
    assert legacy.get("plan_id") is None

    mismatch_job, _mismatch_path, _mismatch_intake = _ready_job(tmp_path, media_file, jobs_root, pilot_id="mismatch")
    mismatch_plan = _generate(mismatch_job, jobs_root)["plan"]
    with pytest.raises(PipelineRunError):
        create_pipeline_run(mismatch_job["job_id"], run_id="bad", operator="tyler", entry_point=mismatch_plan["manual_run"]["entry_point"],
                            command_args=[*mismatch_plan["manual_run"]["arguments"], "--extra"], manual_confirmed=True,
                            expected_job_revision=1, plan_id=mismatch_plan["plan_id"], jobs_dir=jobs_root)
    invalidate_execution_plan(mismatch_job["job_id"], mismatch_plan["plan_id"], operator="tyler", reason="bad source",
                              expected_job_revision=1, expected_plan_revision=0, jobs_dir=jobs_root)
    with pytest.raises(PipelineRunError):
        create_pipeline_run(mismatch_job["job_id"], run_id="invalid", operator="tyler", entry_point=mismatch_plan["manual_run"]["entry_point"],
                            command_args=mismatch_plan["manual_run"]["arguments"], manual_confirmed=True,
                            expected_job_revision=2, plan_id=mismatch_plan["plan_id"], jobs_dir=jobs_root)


def test_no_subprocess_ffmpeg_api_network_transfer_or_media_processing(media_file: Path, tmp_path: Path, jobs_root: Path):
    job, _path, _intake = _ready_job(tmp_path, media_file, jobs_root)
    before = {path: path.read_bytes() for path in [media_file]}
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess")), \
         patch("socket.socket", side_effect=AssertionError("no network")), \
         patch("shutil.copy", side_effect=AssertionError("no copy")), \
         patch("shutil.move", side_effect=AssertionError("no move")):
        result = _generate(job, jobs_root)
        show_execution_plan(job["job_id"], result["plan"]["plan_id"], jobs_dir=jobs_root)
        read_execution_plan_checklist(job["job_id"], result["plan"]["plan_id"], jobs_dir=jobs_root)
    assert {path: path.read_bytes() for path in [media_file]} == before
