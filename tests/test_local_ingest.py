"""Phase 7 — Mac-native media ingestion regression tests.

These tests exercise the real pipeline: a small synthetic video is generated
with FFmpeg's ``lavfi`` sources, registered as an authorized local source,
rendered through the genuine reference renderer, and verified for duration and
provenance. They skip cleanly when FFmpeg/ffprobe are unavailable.

The World Cup reference deployment is never selected: the built-in test project
is the registered non-production ``basketball_sandbox`` profile, and every run
points at an explicit temporary job root.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import urllib.parse
from pathlib import Path

import pytest

from pipeline import integration_service, local_ingest, pilot


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
    media_dir = tmp_path_factory.mktemp("media")
    return _write_video(media_dir / "capture_test_small.mp4")


def _run(source: Path, jobs_dir: Path, **kwargs) -> dict:
    return local_ingest.run_local_ingest(
        source, pilot_id=kwargs.pop("pilot_id", "ss_lite_test"), jobs_dir=jobs_dir, **kwargs,
    )


# ── Read-only media facts ────────────────────────────────────────────────────


def test_probe_media_reads_duration_and_codecs(synthetic_video):
    facts = local_ingest.probe_media(synthetic_video)
    assert facts["duration_seconds"] == pytest.approx(2.0, abs=0.5)
    assert facts["width"] == 320 and facts["height"] == 240
    assert facts["video_codec"] == "h264"
    assert facts["has_audio"] is True
    assert facts["size_bytes"] > 0


def test_probe_media_rejects_missing_file(tmp_path):
    with pytest.raises(local_ingest.LocalIngestError):
        local_ingest.probe_media(tmp_path / "nope.mp4")


def test_sha256_file_is_stable(synthetic_video):
    first = local_ingest.sha256_file(synthetic_video)
    assert len(first) == 64
    assert first == local_ingest.sha256_file(synthetic_video)


# ── Intake + registration ────────────────────────────────────────────────────


def test_build_test_intake_uses_non_production_project(synthetic_video):
    intake = local_ingest.build_test_intake(
        synthetic_video, pilot_id="ss_lite_test", source_id="cap", duration_seconds=2.0,
    )
    assert intake["configuration"]["project"] == "basketball_sandbox"
    assert intake["configuration"]["project"] != "football"
    assert intake["pilot"]["reference_deployment"] == local_ingest.DEFAULT_TEST_REFERENCE_DEPLOYMENT
    assert intake["rights"]["status"] == "CONFIRMED"


def test_register_creates_ready_project(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    intake = local_ingest.build_test_intake(
        synthetic_video, pilot_id="ss_lite_reg", source_id="cap", duration_seconds=2.0,
    )
    intake_path = tmp_path / "intake.json"
    intake_path.write_text(json.dumps(intake), encoding="utf-8")

    reg = local_ingest.register_local_source(intake, intake_path=intake_path, jobs_dir=jobs)
    assert reg["current_state"] == "READY"
    assert reg["project_id"] == "basketball_sandbox"
    assert reg["report"]["rights_cleared"] is True
    assert reg["source"]["sha256"] == local_ingest.sha256_file(synthetic_video)
    assert (jobs / f"{reg['job_id']}.json").exists()


def test_register_rejects_unconfirmed_rights(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    intake = local_ingest.build_test_intake(
        synthetic_video, pilot_id="ss_lite_blocked", source_id="cap", duration_seconds=2.0,
    )
    intake["rights"]["status"] = "UNCONFIRMED"
    intake["rights"].pop("confirmation_statement", None)

    with pytest.raises(local_ingest.LocalIngestNotReadyError) as exc:
        local_ingest.register_local_source(
            intake, intake_path=tmp_path / "intake.json", jobs_dir=jobs,
        )
    assert exc.value.report["rights_cleared"] is False
    assert not list(jobs.glob("*.json"))


# ── Full run: one source -> one project -> one verified artifact ─────────────


def test_full_run_produces_verified_artifact_and_contract(synthetic_video, tmp_path):
    result = _run(synthetic_video, tmp_path / "jobs")
    contract = result["contract"]

    assert local_ingest.validate_integration_contract(contract) == []
    assert contract["contract_schema"] == local_ingest.LOCAL_INGEST_SCHEMA
    assert contract["contract_version"] == 1
    assert contract["verification"]["verified"] is True
    assert contract["provenance"]["links_to_source"] is True

    artifact = Path(contract["artifact"]["path"])
    assert artifact.exists() and artifact.stat().st_size > 0
    assert contract["artifact"]["sha256"] == local_ingest.sha256_file(artifact)
    assert contract["artifact"]["duration_seconds"] == pytest.approx(2.0, abs=1.0)

    assert contract["source"]["sha256"] == local_ingest.sha256_file(synthetic_video)
    assert contract["processing"]["ffmpeg_argv"][0] == "ffmpeg"
    assert contract["processing"]["operation"] == "reference_render"


def test_contract_stage_report_is_honest(synthetic_video, tmp_path):
    contract = _run(synthetic_video, tmp_path / "jobs")["contract"]
    stages = {s["stage"]: s["status"] for s in contract["stages"]}
    assert stages["ffmpeg_reference_render"] == "real"
    assert stages["artifact_provenance"] == "real"
    assert stages["transcription"] == "unavailable"
    assert stages["network_worker_oort"] == "unavailable"
    assert stages["delivery"] == "mocked"


def test_verify_detects_tampered_source(synthetic_video, tmp_path):
    jobs = tmp_path / "jobs"
    source = tmp_path / "tamper_source.mp4"
    shutil.copy2(synthetic_video, source)
    media = local_ingest.probe_media(source)

    intake = local_ingest.build_test_intake(
        source, pilot_id="ss_lite_tamper", source_id="cap", duration_seconds=media["duration_seconds"],
    )
    intake_path = tmp_path / "intake.json"
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    reg = local_ingest.register_local_source(intake, intake_path=intake_path, jobs_dir=jobs)
    original_sha = reg["source"]["sha256"]

    local_ingest.write_reference_edl(reg["job_id"], source_start=0.0, source_end=1.0, jobs_dir=jobs)
    render = local_ingest.render_reference(reg["job_id"], jobs_dir=jobs)
    assert render["status"] == "COMPLETE"

    with source.open("ab") as handle:
        handle.write(b"\x00tampered")

    verification = local_ingest.verify_artifact(
        reg["job_id"], jobs_dir=jobs, expected_source_sha256=original_sha,
    )
    assert verification["checks"]["source_provenance_linked"] is False
    assert verification["verified"] is False
    assert verification["checks"]["artifact_duration_readable"] is True


def test_dry_run_plans_without_producing_artifact(synthetic_video, tmp_path):
    result = _run(synthetic_video, tmp_path / "jobs", dry_run=True)
    contract = result["contract"]
    assert contract["processing"]["dry_run"] is True
    assert contract["artifact"]["path"] is None
    assert local_ingest.validate_integration_contract(contract) == []


def test_run_does_not_touch_default_world_cup_jobs_dir(synthetic_video, tmp_path):
    result = _run(synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_isolated")
    default_record = Path(pilot.PILOT_JOBS_DIR) / f"{result['job_id']}.json"
    assert not default_record.exists()
    assert Path(result["contract"]["artifact"]["path"]).is_relative_to(tmp_path)


# ── Contract validation ──────────────────────────────────────────────────────


def test_contract_validation_rejects_wrong_version(synthetic_video, tmp_path):
    contract = _run(synthetic_video, tmp_path / "jobs")["contract"]
    bad = dict(contract, contract_version=999)
    errors = local_ingest.validate_integration_contract(bad)
    assert any("contract_version" in e for e in errors)


def test_contract_validation_rejects_bad_stage_status(synthetic_video, tmp_path):
    contract = _run(synthetic_video, tmp_path / "jobs")["contract"]
    contract["stages"][0]["status"] = "super-real"
    errors = local_ingest.validate_integration_contract(contract)
    assert any("stages[0].status" in e for e in errors)


# ── Phase 7.1: optional OORT storage upload ──────────────────────────────────


class _FakeOORT:
    """In-memory S3 transport so storage tests never touch the network."""

    def __init__(self, *, fail_status: int | None = None) -> None:
        self.objects: dict[str, bytes] = {}
        self.fail_status = fail_status

    def __call__(self, method, url, headers, body, timeout):
        if self.fail_status:
            return self.fail_status, {}, b"<Error><Code>AccessDenied</Code></Error>"
        parts = urllib.parse.urlsplit(url)
        segments = [s for s in parts.path.split("/") if s]
        if method == "GET" and not segments:
            return 200, {}, b"<ListAllMyBucketsResult/>"
        if method == "PUT":
            self.objects["/".join(segments[1:])] = body
            return 200, {"etag": '"e"'}, b""
        if method == "HEAD":
            data = self.objects["/".join(segments[1:])]
            import hashlib

            return 200, {
                "content-length": str(len(data)),
                "etag": '"e"',
                "x-amz-meta-sha256": hashlib.sha256(data).hexdigest(),
            }, b""
        if method == "GET":
            return 200, {}, self.objects["/".join(segments[1:])]
        return 200, {}, b""


def _storage_adapter(fake) -> integration_service.OORTAdapter:
    config = integration_service.OORTConfig(
        access_key_id="AKIA", secret_access_key="secret",
        bucket="sandbox-bucket", region="us-east-1",
    )
    return integration_service.OORTAdapter(config, transport=fake)


def test_run_with_upload_records_storage_block(synthetic_video, tmp_path):
    fake = _FakeOORT()
    result = _run(
        synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_upload",
        upload=True, storage_adapter=_storage_adapter(fake),
    )
    contract = result["contract"]
    storage = contract["storage"]

    assert local_ingest.validate_integration_contract(contract) == []
    assert storage["status"] == "uploaded"
    assert storage["enabled"] is True
    assert storage["provider"] == "oort"
    assert storage["object_uri"].startswith("s3://sandbox-bucket/")
    assert storage["verified"] is True
    assert storage["read_back_verified"] is True
    assert storage["sha256"] == contract["artifact"]["sha256"]
    assert contract["provenance"]["storage_object_uri"] == storage["object_uri"]
    assert contract["provenance"]["storage_object_sha256"] == storage["sha256"]

    stages = {s["stage"]: s["status"] for s in contract["stages"]}
    assert stages["oort_storage_upload"] == "real"
    assert stages["network_worker_oort"] == "unavailable"

    assert Path(contract["artifact"]["path"]).exists()


def test_run_upload_without_credentials_degrades_gracefully(synthetic_video, tmp_path, monkeypatch):
    for var in (
        integration_service.ENV_ACCESS_KEY_ID, integration_service.ENV_SECRET_ACCESS_KEY,
        integration_service.ENV_BUCKET,
    ):
        monkeypatch.delenv(var, raising=False)

    result = _run(synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_nocreds", upload=True)
    contract = result["contract"]
    storage = contract["storage"]

    assert local_ingest.validate_integration_contract(contract) == []
    assert storage["status"] == "unavailable"
    assert storage["enabled"] is False
    assert storage["error"]
    assert contract["verification"]["verified"] is True
    assert Path(contract["artifact"]["path"]).exists()

    stages = {s["stage"]: s["status"] for s in contract["stages"]}
    assert stages["oort_storage_upload"] == "unavailable"


def test_run_require_storage_without_credentials_raises(synthetic_video, tmp_path, monkeypatch):
    for var in (
        integration_service.ENV_ACCESS_KEY_ID, integration_service.ENV_SECRET_ACCESS_KEY,
        integration_service.ENV_BUCKET,
    ):
        monkeypatch.delenv(var, raising=False)

    with pytest.raises(local_ingest.LocalIngestError, match="storage upload required"):
        _run(synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_required", upload=True, require_storage=True)


def test_run_upload_failure_preserves_local_artifact(synthetic_video, tmp_path):
    fake = _FakeOORT(fail_status=403)
    result = _run(
        synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_failupload",
        upload=True, storage_adapter=_storage_adapter(fake),
    )
    contract = result["contract"]

    assert local_ingest.validate_integration_contract(contract) == []
    assert contract["storage"]["status"] == "failed"
    assert "AccessDenied" in contract["storage"]["error"]
    assert contract["verification"]["verified"] is True
    assert Path(contract["artifact"]["path"]).exists()

    stages = {s["stage"]: s["status"] for s in contract["stages"]}
    assert stages["oort_storage_upload"] == "unavailable"


def test_run_require_storage_failure_raises_but_keeps_local_artifact(synthetic_video, tmp_path):
    fake = _FakeOORT(fail_status=403)
    jobs = tmp_path / "jobs"
    with pytest.raises(local_ingest.LocalIngestError, match="storage upload required"):
        _run(
            synthetic_video, jobs, pilot_id="ss_lite_reqfail",
            upload=True, storage_adapter=_storage_adapter(fake), require_storage=True,
        )
    # The local render survives a required-storage failure.
    assert list(jobs.rglob("reference.mp4"))


def test_dry_run_skips_upload(synthetic_video, tmp_path):
    fake = _FakeOORT()
    result = _run(
        synthetic_video, tmp_path / "jobs", pilot_id="ss_lite_dryupload",
        upload=True, storage_adapter=_storage_adapter(fake), dry_run=True,
    )
    assert result["contract"]["storage"]["status"] == "skipped"
    assert result["contract"]["storage"]["enabled"] is False
