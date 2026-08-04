"""Pilot output manifest validation, registration, review, and readiness."""

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
    JobRevisionError,
    JobTransitionError,
    OutputManifestError,
    confirm_delivery,
    create_job,
    generate_delivery_package,
    output_summary,
    register_output_manifest,
    review_output,
    show_output_manifest,
    transition_job,
    validate_output_manifest,
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
    path.write_bytes(b"source media" * 100)
    return path


def _write_intake(tmp_path: Path, media_file: Path, overrides: dict | None = None) -> Path:
    data = build_intake(str(media_file), overrides=overrides)
    path = tmp_path / f"intake_{data['pilot']['pilot_id']}.json"
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    return path


def _create_running_job(tmp_path: Path, media_file: Path, jobs_root: Path, *, pilot_id: str = "pilot_alpha") -> dict:
    intake_path = _write_intake(tmp_path, media_file, overrides={"pilot": {"pilot_id": pilot_id}})
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)
    return transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)


def _fixture_files(tmp_path: Path) -> dict[str, Path]:
    out = tmp_path / "exports"
    out.mkdir(exist_ok=True)
    clip1 = out / "clip_001.mp4"
    clip2 = out / "clip_002.mp4"
    caption = out / "clip_001_caption.txt"
    thumb = out / "clip_001_thumb.jpg"
    meta = out / "clip_001.json"
    manifest = out / "source_manifest.csv"
    dashboard = out / "review.html"
    for path, content in (
        (clip1, b"clip one"), (clip2, b"clip two"), (caption, b"caption"),
        (thumb, b"thumb"), (meta, b"{}"), (manifest, b"clip_id\nclip_001\n"),
        (dashboard, b"<html>review</html>"),
    ):
        path.write_bytes(content)
    return {"clip1": clip1, "clip2": clip2, "caption": caption, "thumb": thumb,
            "meta": meta, "manifest": manifest, "dashboard": dashboard}


def _output_manifest(job: dict, files: dict[str, Path], *, manifest_id: str = "outputs_alpha") -> dict:
    return {
        "schema_version": 1,
        "manifest_id": manifest_id,
        "job_id": job["job_id"],
        "pilot_id": job["pilot_id"],
        "project_id": job["project_id"],
        "source_id": job["source_id"],
        "created_at": "2026-08-03T00:00:00+00:00",
        "created_by": "operator",
        "source_clip_manifest_path": str(files["manifest"]),
        "revision": 0,
        "outputs": [
            {
                "output_id": "clip_001_tiktok",
                "output_type": "VIDEO_CLIP",
                "local_path": str(files["clip1"]),
                "filename": files["clip1"].name,
                "export_profile": "vertical_clean",
                "platform": "TikTok",
                "operational_category": "EMOTION",
                "editorial_labels": ["aura"],
                "clip_id": "clip_001",
                "start_time": "00:01:00",
                "end_time": "00:01:20",
                "duration": 20,
                "caption_path": str(files["caption"]),
                "thumbnail_path": str(files["thumb"]),
                "metadata_path": str(files["meta"]),
                "review_status": "PENDING",
                "include_in_delivery": False,
            },
            {
                "output_id": "clip_002_shorts",
                "output_type": "VIDEO_CLIP",
                "local_path": str(files["clip2"]),
                "filename": files["clip2"].name,
                "export_profile": "vertical_clean",
                "platform": "Shorts",
                "operational_category": "AURA",
                "clip_id": "clip_002",
                "review_status": "PENDING",
                "include_in_delivery": False,
            },
            {
                "output_id": "review_dashboard",
                "output_type": "REVIEW_DASHBOARD",
                "local_path": str(files["dashboard"]),
                "filename": files["dashboard"].name,
                "export_profile": "vertical_clean",
                "platform": "Shorts",
                "operational_category": "EMOTION",
                "review_status": "EXCLUDED",
                "include_in_delivery": False,
            },
        ],
    }


def _codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


def _run_cli(args: list[str], jobs_root: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "STADIUM_PILOT_JOBS_DIR": str(jobs_root)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], capture_output=True, text=True, env=env)


def test_valid_output_manifest(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    report = validate_output_manifest(manifest, job=job)
    assert report["valid"]


@pytest.mark.parametrize("mutator,code", [
    (lambda m: m.update({"manifest_id": "bad id"}), "BAD_ID"),
    (lambda m: m.update({"job_id": "wrong"}), "JOB_MISMATCH"),
    (lambda m: m["outputs"].append(dict(m["outputs"][0])), "DUPLICATE_OUTPUT_ID"),
    (lambda m: m["outputs"][0].update({"output_type": "BAD"}), "UNKNOWN_OUTPUT_TYPE"),
    (lambda m: m["outputs"][0].update({"review_status": "BAD"}), "UNKNOWN_REVIEW_STATUS"),
    (lambda m: m["outputs"][0].update({"export_profile": "bogus"}), "CONFIG_UNKNOWN_EXPORT_PROFILE"),
    (lambda m: m["outputs"][0].update({"platform": "Vine"}), "UNKNOWN_PLATFORM"),
    (lambda m: m["outputs"][0].update({"operational_category": "GOALS"}), "UNKNOWN_OPERATIONAL_CATEGORY"),
    (lambda m: m["outputs"][0].update({"local_path": "https://example.com/clip.mp4"}), "URL_NOT_ALLOWED"),
    (lambda m: m["outputs"][0].update({"local_path": "../escape.mp4"}), "PATH_TRAVERSAL"),
    (lambda m: m["outputs"][0].update({"token": "secret"}), "UNKNOWN_KEY"),
])
def test_output_manifest_validation_failures(media_file: Path, tmp_path: Path, jobs_root: Path, mutator, code):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    mutator(manifest)
    report = validate_output_manifest(manifest, job=job)
    assert not report["valid"]
    assert code in _codes(report)


def test_output_missing_empty_directory_and_extension_failures(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    manifest["outputs"][0]["local_path"] = str(tmp_path / "missing.mp4")
    assert "OUTPUT_MISSING" in _codes(validate_output_manifest(manifest, job=job))
    manifest = _output_manifest(job, files)
    manifest["outputs"][0]["local_path"] = str(tmp_path)
    assert "OUTPUT_IS_DIRECTORY" in _codes(validate_output_manifest(manifest, job=job))
    manifest = _output_manifest(job, files)
    bad = tmp_path / "clip.exe"
    bad.write_bytes(b"bad")
    manifest["outputs"][0]["local_path"] = str(bad)
    manifest["outputs"][0]["filename"] = bad.name
    assert "UNSUPPORTED_EXTENSION" in _codes(validate_output_manifest(manifest, job=job))


def test_checksum_match_and_mismatch(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    manifest["outputs"][0]["checksum"] = hashlib.sha256(files["clip1"].read_bytes()).hexdigest()
    assert validate_output_manifest(manifest, job=job)["valid"]
    manifest["outputs"][0]["checksum"] = "0" * 64
    assert "CHECKSUM_MISMATCH" in _codes(validate_output_manifest(manifest, job=job))


def test_validation_no_mutation_or_network(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    before = {p: p.stat().st_mtime_ns for p in files.values()}
    with patch("socket.socket", side_effect=AssertionError("no network")), \
         patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no ffmpeg")):
        assert validate_output_manifest(manifest, job=job)["valid"]
    assert before == {p: p.stat().st_mtime_ns for p in files.values()}


def test_register_manifest_links_job_and_appends_event(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    result = register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=1, operator="op")
    assert result["job"]["revision"] == 2
    assert result["job"]["output_manifests"] == ["outputs_alpha"]
    assert (jobs_root / f"{job['job_id']}.outputs" / "outputs_alpha.json").exists()
    events = json.loads((jobs_root / f"{job['job_id']}.events.json").read_text(encoding="utf-8"))
    assert events[-1]["event_type"] == "OUTPUT_REGISTERED"


def test_register_duplicate_stale_invalid_state_and_terminal_rejected(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    with pytest.raises(JobRevisionError):
        register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=0)
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=1)
    with pytest.raises(OutputManifestError):
        register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    failed_job = create_job(build_intake(str(media_file), overrides={"pilot": {"pilot_id": "bad_output_state"}}), jobs_dir=jobs_root)
    bad_manifest = _output_manifest(failed_job, _fixture_files(tmp_path), manifest_id="bad_state_outputs")
    with pytest.raises(OutputManifestError):
        register_output_manifest(failed_job["job_id"], bad_manifest, jobs_dir=jobs_root)
    cancelled = _create_running_job(tmp_path, media_file, jobs_root, pilot_id="cancelled_outputs")
    transition_job(cancelled["job_id"], "CANCELLED", metadata={"operator": "op", "reason": "stop", "client_requested": False}, jobs_dir=jobs_root)
    cancel_manifest = _output_manifest(cancelled, _fixture_files(tmp_path), manifest_id="cancel_outputs")
    with pytest.raises(OutputManifestError):
        register_output_manifest(cancelled["job_id"], cancel_manifest, jobs_dir=jobs_root)


def test_review_actions_and_revisions(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root, expected_revision=1)
    result = review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED",
                           operator="reviewer", reason="Approved", include_in_delivery=True,
                           expected_job_revision=2, expected_manifest_revision=0, jobs_dir=jobs_root)
    assert result["job"]["revision"] == 3
    assert result["manifest"]["revision"] == 1
    reviewed = show_output_manifest(job["job_id"], "outputs_alpha", jobs_dir=jobs_root)
    assert reviewed["outputs"][0]["review_status"] == "APPROVED"
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="REJECTED", operator="reviewer",
                  reason="Not strong enough", expected_job_revision=3, expected_manifest_revision=1, jobs_dir=jobs_root)
    reviewed = show_output_manifest(job["job_id"], "outputs_alpha", jobs_dir=jobs_root)
    assert reviewed["outputs"][1]["include_in_delivery"] is False
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="CHANGES_REQUESTED", operator="reviewer",
                  reason="Needs caption", jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="EXCLUDED", operator="reviewer",
                  reason="Exclude", jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="PENDING", operator="reviewer",
                  reason="Reset", jobs_dir=jobs_root)


def test_review_stale_revisions_and_no_media_mutation(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    before = files["clip1"].read_bytes()
    with pytest.raises(JobRevisionError):
        review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="r",
                      reason="Approved", include_in_delivery=True, expected_job_revision=0, jobs_dir=jobs_root)
    with pytest.raises(JobRevisionError):
        review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="r",
                      reason="Approved", include_in_delivery=True, expected_manifest_revision=99, jobs_dir=jobs_root)
    assert files["clip1"].read_bytes() == before


def test_summary_and_transition_integration(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path))
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    assert not output_summary(job["job_id"], jobs_dir=jobs_root)["review_complete"]
    review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="r",
                  reason="Approved one", include_in_delivery=True, jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_002_shorts", status="REJECTED", operator="r",
                  reason="Rejected", jobs_dir=jobs_root)
    summary = output_summary(job["job_id"], jobs_dir=jobs_root)
    assert summary["total_outputs"] == 3
    assert summary["video_count"] == 2
    assert summary["delivery_included_count"] == 1
    assert summary["counts_by_review_status"]["APPROVED"] == 1
    assert summary["counts_by_review_status"]["REJECTED"] == 1
    assert summary["review_complete"]
    transition_job(job["job_id"], "REVIEW_REQUIRED", metadata={"operator": "op", "reason": "ready"},
                   artifact_references=["outputs/review"], jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "APPROVED", metadata={"operator": "r", "approval_statement": "Approved", "deliverable_count": 2}, jobs_dir=jobs_root)
    transition_job(job["job_id"], "APPROVED", metadata={"operator": "r", "approval_statement": "Approved", "deliverable_count": 1}, jobs_dir=jobs_root)
    generate_delivery_package(job["job_id"], package_id="pkg_outputs", operator="op", delivery_method="shared_folder",
                              delivery_destination="exports", jobs_dir=jobs_root)
    transition_job(job["job_id"], "DELIVERY_READY", metadata={"delivery_package_id": "pkg_outputs", "deliverable_count": 1}, jobs_dir=jobs_root)
    with pytest.raises(JobTransitionError):
        transition_job(job["job_id"], "DELIVERED", metadata={"operator": "op", "confirmation": "done", "delivery_destination": "exports", "delivered_item_count": 2}, jobs_dir=jobs_root)
    confirm_delivery(job["job_id"], "pkg_outputs", operator="op", confirmation="done", delivered_count=1, jobs_dir=jobs_root)
    transition_job(job["job_id"], "DELIVERED", metadata={"operator": "op", "confirmation": "done", "delivery_package_id": "pkg_outputs", "delivered_item_count": 1}, jobs_dir=jobs_root)


def test_missing_approved_file_blocks_readiness(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    files = _fixture_files(tmp_path)
    manifest = _output_manifest(job, files)
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    review_output(job["job_id"], "outputs_alpha", "clip_001_tiktok", status="APPROVED", operator="r", reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    files["clip1"].unlink()
    summary = output_summary(job["job_id"], jobs_dir=jobs_root)
    assert summary["missing_file_count"] >= 1
    assert not summary["review_complete"]


def test_expired_rights_block_output_readiness(media_file: Path, tmp_path: Path, jobs_root: Path):
    intake_path = _write_intake(tmp_path, media_file, overrides={"pilot": {"pilot_id": "expires_outputs"}})
    intake = json.loads(intake_path.read_text(encoding="utf-8"))
    job = create_job(intake, intake_path=intake_path, jobs_dir=jobs_root)
    transition_job(job["job_id"], "RUNNING", metadata={"operator": "op"}, jobs_dir=jobs_root)
    manifest = _output_manifest(job, _fixture_files(tmp_path), manifest_id="expires_manifest")
    register_output_manifest(job["job_id"], manifest, jobs_dir=jobs_root)
    review_output(job["job_id"], "expires_manifest", "clip_001_tiktok", status="APPROVED", operator="r", reason="Approved", include_in_delivery=True, jobs_dir=jobs_root)
    intake["rights"]["expiration_date"] = "2000-01-01"
    intake_path.write_text(json.dumps(intake, indent=2) + "\n", encoding="utf-8")
    assert not output_summary(job["job_id"], jobs_dir=jobs_root)["review_complete"]


def test_outputs_cli(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest_path = tmp_path / "outputs.json"
    manifest_path.write_text(json.dumps(_output_manifest(job, _fixture_files(tmp_path)), indent=2) + "\n", encoding="utf-8")
    assert _run_cli(["outputs", "validate", str(manifest_path)], jobs_root).returncode == 0
    registered = _run_cli(["outputs", "register", job["job_id"], str(manifest_path), "--expected-revision", "1"], jobs_root)
    assert registered.returncode == 0, registered.stderr
    assert _run_cli(["outputs", "list", job["job_id"]], jobs_root).returncode == 0
    assert _run_cli(["outputs", "show", job["job_id"], "outputs_alpha"], jobs_root).returncode == 0
    reviewed = _run_cli(["outputs", "review", job["job_id"], "outputs_alpha", "clip_001_tiktok", "--status", "APPROVED",
                         "--operator", "reviewer", "--reason", "Approved", "--include-in-delivery",
                         "--expected-job-revision", "2", "--expected-manifest-revision", "0"], jobs_root)
    assert reviewed.returncode == 0, reviewed.stderr
    summary = _run_cli(["outputs", "summary", job["job_id"]], jobs_root)
    assert summary.returncode == 0
    assert "delivery_included_count: 1" in summary.stdout


def test_cli_expected_errors_no_traceback_and_no_processing(media_file: Path, tmp_path: Path, jobs_root: Path):
    job = _create_running_job(tmp_path, media_file, jobs_root)
    manifest_path = tmp_path / "outputs.json"
    manifest_path.write_text(json.dumps(_output_manifest(job, _fixture_files(tmp_path)), indent=2) + "\n", encoding="utf-8")
    bad = _run_cli(["outputs", "register", job["job_id"], str(manifest_path), "--expected-revision", "0"], jobs_root)
    assert bad.returncode != 0
    assert "Traceback" not in bad.stderr
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess")), \
         patch("socket.socket", side_effect=AssertionError("no network")):
        assert validate_output_manifest(json.loads(manifest_path.read_text(encoding="utf-8")), job=job)["valid"]
