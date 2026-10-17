"""Phase 7.4 — end-to-end media workflow regression tests.

Exercises the operator orchestrator (``pipeline.media_workflow``) against the
real Phase 7 services using a small synthetic FFmpeg video. The optional
cross-repository check additionally drives Stadium Signal Lite's
``tools/verify_library.py`` when that checkout is available (it is skipped
otherwise, so the suite stays runnable without the sibling repository).

The World Cup reference deployment is never selected: runs use the built-in
non-production ``basketball_sandbox`` profile and an explicit temporary job root.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from pipeline import integration_service, local_ingest, media_catalog, media_workflow, pilot

def _write_video(path: Path, duration: float = 2.0, size: str = "320x240") -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"testsrc=duration={duration}:size={size}:rate=15",
            "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest",
            str(path),
        ],
        check=True, capture_output=True, shell=False,
    )
    return path


@pytest.fixture(scope="module")
def synthetic_video(tmp_path_factory) -> Path:
    if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
        pytest.skip("ffmpeg/ffprobe not available")
    media_dir = tmp_path_factory.mktemp("wf_media")
    return _write_video(media_dir / "capture_test_small.mp4")


def _lite_root() -> Path | None:
    root = os.environ.get(media_workflow.LITE_ROOT_ENV)
    if not root:
        return None
    candidate = Path(root).expanduser()
    if (candidate / media_workflow.LITE_VERIFY_SCRIPT).is_file():
        return candidate
    return None


class _FailingAdapter:
    """Storage adapter whose upload always fails (connectivity/credentials)."""

    class _Config:
        endpoint = "https://s3-standard.oortech.com"
        region = "us-east-1"
        bucket = "test-bucket"

    config = _Config()

    def upload_file(self, *args, **kwargs):
        raise integration_service.StorageIntegrationError("simulated upload failure")


def _run(source: Path, jobs_dir: Path, **kwargs) -> dict:
    kwargs.setdefault("pilot_id", "ss_lite_wf_test")
    kwargs.setdefault("verify_library", False)
    return media_workflow.run_media_workflow(source, jobs_dir=jobs_dir, **kwargs)


def _status_path(pilot_id: str, jobs_dir: Path, source_id: str = "capture_test_small") -> Path:
    return media_workflow.workflow_status_path(pilot_id, jobs_dir, source_id=source_id)


# ── Full run + idempotency ───────────────────────────────────────────────────


def test_full_run_builds_catalog_and_contract(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    report = _run(synthetic_video, jobs)

    assert report["verified"] is True
    assert report["outcome"] == "succeeded"
    assert report["reused"] is False
    assert report["artifact"]["path"] and Path(report["artifact"]["path"]).exists()
    assert Path(report["contract"]["path"]).is_file()
    assert report["catalog"]["asset_count"] == 2  # processed artifact + registered source
    assert report["storage"]["status"] == "skipped"

    # The catalog on disk is valid and carries the new artifact asset once.
    catalog = json.loads(Path(report["catalog"]["path"]).read_text(encoding="utf-8"))
    assert media_catalog.validate_catalog(catalog) == []
    matching = [a for a in catalog["assets"] if a["asset_id"] == report["catalog"]["asset_id"]]
    assert len(matching) == 1
    assert report["catalog"]["duplicate_suppressed"] is True

    # No private source path in the catalog, and durable status written.
    assert "local_file_path" not in json.dumps(catalog)
    status = _status_path("ss_lite_wf_test", jobs)
    assert status.is_file()


def test_rerun_reuses_verified_contract(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    first = _run(synthetic_video, jobs)
    second = _run(synthetic_video, jobs)

    assert second["reused"] is True
    assert second["verified"] is True
    assert second["catalog"]["asset_id"] == first["catalog"]["asset_id"], "stable asset id across reruns"
    assert second["catalog"]["asset_count"] == first["catalog"]["asset_count"] == 2


def test_resume_after_missing_contract(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    first = _run(synthetic_video, jobs)

    # Simulate an interruption: the job record exists but the contract is gone.
    Path(first["contract"]["path"]).unlink()
    second = _run(synthetic_video, jobs)

    assert second["reused"] is False
    assert second["resumed"] is True, "existing job reused instead of raising"
    assert second["verified"] is True
    assert Path(second["contract"]["path"]).is_file()


def test_no_reuse_forces_resume_not_failure(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    _run(synthetic_video, jobs)
    again = _run(synthetic_video, jobs, reuse=False)

    assert again["reused"] is False
    assert again["resumed"] is True
    assert again["verified"] is True


def test_changed_trim_is_rerendered_not_reused(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    _run(synthetic_video, jobs, source_start=0.0, source_end=1.0)
    again = _run(synthetic_video, jobs, source_start=1.0, source_end=2.0)

    assert again["reused"] is False
    assert again["resumed"] is True
    contract = json.loads(Path(again["contract"]["path"]).read_text(encoding="utf-8"))
    edl = json.loads(Path(contract["processing"]["edl_path"]).read_text(encoding="utf-8"))
    assert edl["segments"][0]["source_start"] == 1.0


def test_changed_source_cannot_reuse_or_overwrite_intake(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    _run(synthetic_video, jobs, source_id="shared_source")
    intake_path = jobs / "intakes" / "ss_lite_wf_test_shared_source.json"
    original_intake = intake_path.read_text(encoding="utf-8")
    other = tmp_path / "other.mp4"
    shutil.copyfile(synthetic_video, other)

    with pytest.raises(local_ingest.LocalIngestError, match="cannot safely resume"):
        _run(other, jobs, source_id="shared_source")

    assert intake_path.read_text(encoding="utf-8") == original_intake


def test_multiple_sources_under_one_pilot_keep_distinct_runtime_records(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    first_source = tmp_path / "first.mp4"
    second_source = tmp_path / "second.mp4"
    shutil.copyfile(synthetic_video, first_source)
    shutil.copyfile(synthetic_video, second_source)

    first = _run(first_source, jobs, pilot_id="multi_source", source_id="first")
    second = _run(second_source, jobs, pilot_id="multi_source", source_id="second")

    assert first["job_id"] != second["job_id"]
    for source_id in ("first", "second"):
        assert (jobs / "intakes" / f"multi_source_{source_id}.json").is_file()
        assert (jobs / "contracts" / f"multi_source_{source_id}.contract.json").is_file()
        assert _status_path("multi_source", jobs, source_id=source_id).is_file()


def test_same_path_source_mutation_cannot_resume(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    source = tmp_path / "source.mp4"
    shutil.copyfile(synthetic_video, source)
    first = _run(source, jobs)
    Path(first["contract"]["path"]).unlink()
    source.write_bytes(source.read_bytes() + b"changed")

    with pytest.raises(local_ingest.LocalIngestError, match="source_checksum"):
        _run(source, jobs)


def test_terminal_job_cannot_resume(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    first = _run(synthetic_video, jobs)
    Path(first["contract"]["path"]).unlink()
    job_path = jobs / f"{first['job_id']}.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job["current_state"] = "CANCELLED"
    job_path.write_text(json.dumps(job), encoding="utf-8")

    with pytest.raises(local_ingest.LocalIngestError, match="state is 'CANCELLED'"):
        _run(synthetic_video, jobs)


def test_dry_run_writes_status_without_artifacts(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    report = _run(synthetic_video, jobs, dry_run=True)

    assert report["dry_run"] is True
    assert report["outcome"] == "planned"
    assert report["verified"] is False
    assert report["contract"]["written"] is False
    assert report["catalog"] is None
    assert _status_path("ss_lite_wf_test", jobs).is_file()
    assert not list(media_workflow.default_contracts_dir(jobs).glob("*.contract.json"))


def test_dry_run_rejects_invalid_id_and_range(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    with pytest.raises(media_workflow.MediaWorkflowError, match="pilot_id"):
        _run(synthetic_video, jobs, pilot_id="../../escape", dry_run=True)
    with pytest.raises(media_workflow.MediaWorkflowError, match="source range"):
        _run(synthetic_video, jobs, pilot_id="safe_dry", source_start=2.0, source_end=1.0, dry_run=True)
    with pytest.raises(media_workflow.MediaWorkflowError, match="finite"):
        _run(synthetic_video, jobs, pilot_id="safe_nan", source_start=0.0, source_end=float("nan"), dry_run=True)
    with pytest.raises(media_workflow.MediaWorkflowError, match="media duration"):
        _run(synthetic_video, jobs, pilot_id="safe_long", source_start=0.0, source_end=99.0, dry_run=True)
    assert not (tmp_path / "escape.workflow.json").exists()


def test_dry_run_rejects_corrupt_media(tmp_path):
    source = tmp_path / "corrupt.mp4"
    source.write_bytes(b"not media")
    with pytest.raises(media_workflow.MediaWorkflowError, match="probe failed"):
        _run(source, tmp_path / "jobs", pilot_id="corrupt_dry", dry_run=True)


def test_require_storage_requires_upload_and_writes_failure_status(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    with pytest.raises(media_workflow.MediaWorkflowError, match="requires upload"):
        _run(synthetic_video, jobs, pilot_id="storage_required", require_storage=True)

    status = json.loads(
        _status_path("storage_required", jobs).read_text(encoding="utf-8")
    )
    assert status["outcome"] == "failed"
    assert status["stages"][-1]["stage"] == "request_validation"


def test_storage_failure_preserves_local_artifact(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    report = _run(
        synthetic_video, jobs,
        upload=True, storage_adapter=_FailingAdapter(),
        storage_bucket=None,
    )

    assert report["storage"]["status"] == "failed"
    assert report["verified"] is True, "verification still succeeds"
    assert Path(report["artifact"]["path"]).exists(), "local artifact preserved on upload failure"
    assert any("OORT upload failed" in r for r in report["recommendations"])


def test_upload_request_retries_a_reused_local_contract(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    first = _run(synthetic_video, jobs)
    assert first["storage"]["status"] == "skipped"

    retried = _run(
        synthetic_video, jobs,
        upload=True, storage_adapter=_FailingAdapter(), storage_bucket=None,
    )
    assert retried["reused"] is False
    assert retried["resumed"] is True
    assert retried["storage"]["status"] == "failed"


def test_failed_lite_verification_sets_failed_outcome(synthetic_video, tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    monkeypatch.setattr(media_workflow, "verify_with_lite", lambda **kwargs: {
        "ran": True, "ok": False, "available": True, "asset_found": True,
        "leak_free": True, "arbitrary_blocked": True, "unknown_asset_blocked": True,
        "preview_enabled": True, "preview_url": "/preview?asset=test",
        "range_status": 500, "range_bytes": 0, "range_valid": False,
        "reason": "simulated playback failure",
    })
    report = _run(
        synthetic_video, jobs, pilot_id="lite_failure",
        verify_library=True, lite_root=tmp_path,
    )

    assert report["outcome"] == "failed"
    assert report["error"]["type"] == "LibraryVerificationError"
    status = json.loads(
        _status_path("lite_failure", jobs).read_text(encoding="utf-8")
    )
    assert status["outcome"] == "failed"


# ── Contract re-verification ─────────────────────────────────────────────────


def test_verify_contract_artifact_detects_missing_artifact(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    report = _run(synthetic_video, jobs)
    contract = json.loads(Path(report["contract"]["path"]).read_text(encoding="utf-8"))

    assert media_workflow.verify_contract_artifact(contract)["verified"] is True

    Path(contract["artifact"]["path"]).unlink()
    check = media_workflow.verify_contract_artifact(contract)
    assert check["verified"] is False
    assert check["artifact_exists"] is False


# ── local_ingest resume primitive ────────────────────────────────────────────


def test_local_ingest_needs_resume_for_existing_job(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    local_ingest.run_local_ingest(synthetic_video, pilot_id="ss_resume", jobs_dir=jobs)

    with pytest.raises(pilot.JobExistsError):
        local_ingest.run_local_ingest(synthetic_video, pilot_id="ss_resume", jobs_dir=jobs)

    resumed = local_ingest.run_local_ingest(
        synthetic_video, pilot_id="ss_resume", jobs_dir=jobs, resume=True,
    )
    assert resumed["resumed"] is True
    assert resumed["verification"]["verified"] is True


# ── Cross-repository integration (Stadium Signal Lite) ───────────────────────


@pytest.mark.skipif(_lite_root() is None, reason="Stadium Signal Lite checkout not available")
def test_cross_repo_lite_import_and_playback(synthetic_video, tmp_path):
    lite_root = _lite_root()
    assert lite_root is not None
    jobs = tmp_path / "jobs"

    report = _run(
        synthetic_video, jobs,
        pilot_id="ss_lite_wf_e2e",
        verify_library=True,
        lite_root=lite_root,
        preview_roots=[str(jobs)],
        preview_enabled=True,
    )

    library = report["library"]
    assert library is not None and library["ran"], library
    assert library["ok"] is True, library
    assert library["available"] is True
    assert library["asset_found"] is True
    assert library["leak_free"] is True
    assert library["arbitrary_blocked"] is True
    assert library["range_status"] == 206
