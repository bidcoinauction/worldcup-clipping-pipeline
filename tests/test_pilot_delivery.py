"""Pilot delivery package manifests, handoff checklists, and confirmation."""

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
    DeliveryPackageError,
    JobRevisionError,
    JobTransitionError,
    confirm_delivery,
    create_job,
    generate_delivery_package,
    list_delivery_packages,
    read_delivery_checklist,
    register_output_manifest,
    review_output,
    show_delivery_package,
    transition_job,
    validate_delivery_package,
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
    path.write_bytes(b"delivery source" * 100)
    return path


def _write_intake(tmp_path: Path, media_file: Path, *, pilot_id: str = "pilot_alpha") -> Path:
    data = build_intake(str(media_file), overrides={"pilot": {"pilot_id": pilot_id}})
    path = tmp_path / f"intake_{pilot_id}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _approved_job(tmp_path: Path, media_file: Path, jobs_root: Path, *, include_checksum: bool = False,
                  pilot_id: str = "pilot_alpha") -> tuple[str, dict[str, Path]]:
    intake_path = _write_intake(tmp_path, media_file, pilot_id=pilot_id)
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)
    job = transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    if include_checksum:
        manifest["outputs"][0]["checksum"] = hashlib.sha256(files["clip1"].read_bytes()).hexdigest()
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    transition_job(job["job_id"], "REVIEW_REQUIRED", metadata={"operator": "op", "reason": "ready"},
                   artifact_references=["outputs/review"], jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="reviewer",
                  reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="REJECTED", operator="reviewer",
                  reason="Rejected", jobs_dir=jobs_root)
    transition_job(job["job_id"], "APPROVED", metadata={"operator": "reviewer", "approval_statement": "Approved", "deliverable_count": 1},
                   jobs_dir=jobs_root)
    return job["job_id"], files


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_generate_package_omits_rejected_outputs_and_writes_checklist(media_file: Path, tmp_path: Path, jobs_root: Path):
    job_id, files = _approved_job(tmp_path, media_file, jobs_root, include_checksum=True)
    before = {name: path.read_bytes() for name, path in files.items()}
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no processing")), \
         patch("socket.socket", side_effect=AssertionError("no network")):
        result = generate_delivery_package(job_id, package_id="pkg_alpha", operator="op", delivery_method="shared_folder",
                                           delivery_destination="deliveries/job-alpha", jobs_dir=jobs_root)
    package = result["package"]
    assert package["summary"]["total_deliverable_count"] == 1
    assert package["summary"]["checksum_verified_count"] == 1
    assert [item["output_id"] for item in package["deliverables"]] == ["clip_001_tiktok"]
    assert "clip_002_shorts" not in json.dumps(package)
    assert Path(result["package_path"]).exists()
    assert Path(result["checklist_path"]).exists()
    assert validate_delivery_package(package, jobs_dir=jobs_root)["valid"]
    checklist = read_delivery_checklist(job_id, "pkg_alpha", jobs_dir=jobs_root)
    assert "No files have been copied" in checklist
    assert "No publishing has occurred" in checklist
    assert list_delivery_packages(job_id, jobs_dir=jobs_root)[0]["package_id"] == "pkg_alpha"
    assert show_delivery_package(job_id, "pkg_alpha", jobs_dir=jobs_root)["deliverable_count"] == 1
    assert before == {name: path.read_bytes() for name, path in files.items()}


def test_missing_file_stale_revision_and_duplicate_package_rejected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job_id, files = _approved_job(tmp_path, media_file, jobs_root)
    with pytest.raises(JobRevisionError):
        generate_delivery_package(job_id, package_id="pkg_stale", operator="op", delivery_method="manual",
                                  delivery_destination="desk handoff", expected_revision=0, jobs_dir=jobs_root)
    generate_delivery_package(job_id, package_id="pkg_alpha", operator="op", delivery_method="manual",
                              delivery_destination="desk handoff", jobs_dir=jobs_root)
    with pytest.raises(DeliveryPackageError):
        generate_delivery_package(job_id, package_id="pkg_alpha", operator="op", delivery_method="manual",
                                  delivery_destination="desk handoff", jobs_dir=jobs_root)

    other_job_id, other_files = _approved_job(tmp_path, media_file, jobs_root, include_checksum=False, pilot_id="pilot_missing")
    other_files["clip1"].unlink()
    with pytest.raises(DeliveryPackageError) as exc:
        generate_delivery_package(other_job_id, package_id="pkg_missing", operator="op", delivery_method="manual",
                                  delivery_destination="desk handoff", jobs_dir=jobs_root)
    assert "not ready" in str(exc.value) or "path failed" in str(exc.value)


def test_delivery_ready_confirmation_delivered_and_second_confirmation_rejected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job_id, _ = _approved_job(tmp_path, media_file, jobs_root)
    result = generate_delivery_package(job_id, package_id="pkg_alpha", operator="op", delivery_method="local_directory",
                                       delivery_destination="deliveries/job-alpha", jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job_id, "DELIVERY_READY", metadata={"delivery_package_id": "pkg_alpha", "deliverable_count": 2},
                       jobs_dir=jobs_root)
    job = transition_job(job_id, "DELIVERY_READY", metadata={"delivery_package_id": "pkg_alpha", "deliverable_count": 1},
                         expected_revision=result["job"]["revision"], jobs_dir=jobs_root)
    assert job["current_state"] == "DELIVERY_READY"
    with pytest.raises(JobTransitionError):
        transition_job(job_id, "DELIVERED", metadata={"operator": "op", "confirmation": "done", "delivery_package_id": "pkg_alpha", "delivered_item_count": 1},
                       jobs_dir=jobs_root)
    confirmed = confirm_delivery(job_id, "pkg_alpha", operator="op", confirmation="Manual delivery completed",
                                 delivered_count=1, expected_revision=job["revision"], jobs_dir=jobs_root)
    assert Path(confirmed["confirmation_path"]).exists()
    with pytest.raises(DeliveryPackageError):
        confirm_delivery(job_id, "pkg_alpha", operator="op", confirmation="again", delivered_count=1, jobs_dir=jobs_root)
    delivered = transition_job(job_id, "DELIVERED", metadata={"operator": "op", "confirmation": "Client received", "delivery_package_id": "pkg_alpha", "delivered_item_count": 1},
                               expected_revision=confirmed["job"]["revision"], jobs_dir=jobs_root)
    assert delivered["current_state"] == "DELIVERED"


def test_delivery_cli(media_file: Path, tmp_path: Path, jobs_root: Path):
    job_id, _ = _approved_job(tmp_path, media_file, jobs_root)
    generated = _run_cli(["delivery", "generate", job_id, "pkg_cli", "--operator", "op", "--delivery-method", "manual",
                          "--delivery-destination", "desk handoff"], jobs_root)
    assert generated.returncode == 0, generated.stderr
    package_path = jobs_root / f"{job_id}.delivery" / "pkg_cli.json"
    assert _run_cli(["delivery", "validate", str(package_path), "--job-id", job_id], jobs_root).returncode == 0
    assert _run_cli(["delivery", "list", job_id], jobs_root).returncode == 0
    assert _run_cli(["delivery", "show", job_id, "pkg_cli"], jobs_root).returncode == 0
    checklist = _run_cli(["delivery", "checklist", job_id, "pkg_cli"], jobs_root)
    assert checklist.returncode == 0
    assert "Manual actions still required" in checklist.stdout
    ready = _run_cli(["transition", job_id, "DELIVERY_READY", "--delivery-package-id", "pkg_cli", "--deliverable-count", "1"], jobs_root)
    assert ready.returncode == 0, ready.stderr
    confirmed = _run_cli(["delivery", "confirm", job_id, "pkg_cli", "--operator", "op", "--confirmation", "delivered", "--delivered-count", "1"], jobs_root)
    assert confirmed.returncode == 0, confirmed.stderr
    delivered = _run_cli(["transition", job_id, "DELIVERED", "--operator", "op", "--confirmation", "client received",
                          "--delivery-package-id", "pkg_cli", "--delivered-item-count", "1"], jobs_root)
    assert delivered.returncode == 0, delivered.stderr
    second_confirm = _run_cli(["delivery", "confirm", job_id, "pkg_cli", "--operator", "op", "--confirmation", "again", "--delivered-count", "1"], jobs_root)
    assert second_confirm.returncode != 0
    assert "Traceback" not in second_confirm.stderr
