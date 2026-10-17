from __future__ import annotations

import json

import pytest

from pipeline import media_catalog as mc


def _contract(**overrides):
    base = {
        "contract_schema": "stadium_signal.local_ingest/v1",
        "contract_version": 1,
        "generated_at": "2026-10-17T00:00:00+00:00",
        "producer": {"application": "worldcup-clipping-pipeline", "component": "pipeline.local_ingest"},
        "project": {
            "job_id": "job1", "project_id": "basketball_sandbox",
            "reference_deployment": "stadium_signal_lite_local", "current_state": "READY",
            "intake_manifest_path": "/x/intake.json",
        },
        "source": {
            "source_id": "src1", "local_file_path": "/secret/source.mp4",
            "original_filename": "source.mp4", "sha256": "a" * 64,
            "duration_seconds": 8.0, "width": 320, "height": 240,
            "video_codec": "h264", "audio_codec": "aac", "has_audio": True,
        },
        "processing": {
            "operation": "reference_render", "render_mode": "REFERENCE",
            "status": "COMPLETE", "story_id": "s", "format": "SHORT", "dry_run": False,
        },
        "artifact": {
            "path": "/out/reference.mp4", "filename": "reference.mp4",
            "sha256": "b" * 64, "size_bytes": 100, "duration_seconds": 2.0,
        },
        "provenance": {
            "links_to_source": True, "source_sha256": "a" * 64,
            "artifact_sha256": "b" * 64, "job_id": "job1",
        },
        "rights": {"status": "CONFIRMED", "cleared": True},
        "verification": {"verified": True, "checks": {"ok": True}},
        "stages": [{"stage": "source_registration", "status": "real", "detail": "x"}],
        "storage": {"enabled": False, "provider": None, "status": "skipped",
                    "object_uri": None, "sha256": None, "verified": False},
    }
    base.update(overrides)
    return base


# ── Derivations ──────────────────────────────────────────────────────────────


def test_stable_asset_id_is_deterministic():
    a = mc.stable_asset_id(mc.ASSET_TYPE_ARTIFACT, "job1", "b" * 64)
    b = mc.stable_asset_id(mc.ASSET_TYPE_ARTIFACT, "job1", "b" * 64)
    assert a == b
    assert a.startswith("clipper:processed_artifact:")
    assert mc.stable_asset_id(mc.ASSET_TYPE_ARTIFACT, "job2", "b" * 64) != a


def test_container_and_media_kind():
    assert mc.infer_container("/x/ref.MP4") == "mp4"
    assert mc.media_kind_for("mp4") == "video"
    assert mc.media_kind_for("m4a") == "audio"
    assert mc.media_kind_for(None) == "unknown"
    assert mc.infer_container(None) is None


def test_infer_sport():
    assert mc.infer_sport("basketball_sandbox") == "basketball"
    assert mc.infer_sport("football", None) == "football"
    assert mc.infer_sport(None, "nfl_demo") == "american_football"
    assert mc.infer_sport(None) is None


# ── Asset builders ───────────────────────────────────────────────────────────


def test_asset_from_contract_shape_and_privacy():
    asset = mc.asset_from_contract(_contract())
    assert asset is not None
    assert asset["asset_type"] == mc.ASSET_TYPE_ARTIFACT
    assert asset["processing"]["state"] == mc.PROCESSING_COMPLETE
    assert asset["playback"]["visibility"] == mc.VISIBILITY_LOCAL_ONLY
    assert asset["playback"]["previewable"] is True
    assert asset["playback"]["media_kind"] == "video"
    assert asset["artifact"]["path"].endswith("reference.mp4")
    assert asset["sport"] == "basketball"
    # private source path must never be emitted
    assert "local_file_path" not in asset["source"]
    assert "/secret/source.mp4" not in json.dumps(asset)


def test_asset_from_contract_invalid_returns_none():
    bad = _contract()
    bad["contract_schema"] = "wrong"
    assert mc.asset_from_contract(bad) is None


def test_asset_from_contract_failed_render():
    contract = _contract()
    contract["processing"]["status"] = "FAILED"
    contract["verification"]["verified"] = False
    asset = mc.asset_from_contract(contract)
    assert asset["processing"]["state"] == mc.PROCESSING_FAILED


def test_asset_from_contract_storage_uploaded_is_private_remote():
    contract = _contract()
    contract["storage"] = {
        "enabled": True, "provider": "oort", "status": "uploaded",
        "bucket": "b", "region": "us-east-1", "object_uri": "s3://b/key.mp4",
        "sha256": "b" * 64, "verified": True,
    }
    asset = mc.asset_from_contract(contract)
    assert asset["playback"]["visibility"] == mc.VISIBILITY_PRIVATE_REMOTE
    assert asset["storage"]["object_uri"] == "s3://b/key.mp4"


def test_asset_from_session_manifest_recording():
    manifest = {
        "manifest_version": 1, "session_id": "m_2026", "match_id": "m",
        "state": "assembled", "started_at": "2026-10-17T00:00:00+00:00",
        "source": {"kind": "hls", "reference": "https://h/x"},
        "recording": {"path": "/rec/out.mp4", "filename": "out.mp4",
                      "size_bytes": 10, "sha256": "c" * 64, "duration_seconds": 8.0,
                      "segment_count": 4},
    }
    asset = mc.asset_from_session_manifest(manifest)
    assert asset["asset_type"] == mc.ASSET_TYPE_RECORDING
    assert asset["processing"]["state"] == mc.PROCESSING_COMPLETE
    assert asset["artifact"]["sha256"] == "c" * 64


def test_asset_from_session_manifest_failed():
    manifest = {"manifest_version": 1, "session_id": "m_2026", "match_id": "m",
                "state": "failed", "source": {"kind": "hls", "reference": "x"}}
    asset = mc.asset_from_session_manifest(manifest)
    assert asset["processing"]["state"] == mc.PROCESSING_FAILED
    assert asset["artifact"]["path"] is None


def test_asset_from_session_manifest_incomplete_skipped():
    manifest = {"manifest_version": 1, "session_id": "m_2026", "match_id": "m",
                "state": "recording", "source": {"kind": "hls", "reference": "x"}}
    assert mc.asset_from_session_manifest(manifest) is None


def test_asset_from_job():
    asset = mc.asset_from_job({"job_id": "j1", "project_id": "basketball_sandbox", "current_state": "READY"})
    assert asset["asset_type"] == mc.ASSET_TYPE_SOURCE
    assert asset["processing"]["state"] == mc.PROCESSING_IN_PROGRESS
    assert asset["playback"]["previewable"] is False


# ── Collect + build ──────────────────────────────────────────────────────────


def test_collect_assets_dedupes_by_id():
    contract = _contract()
    assets = mc.collect_assets(contracts=[contract, dict(contract)], sessions=[
        {"manifest_version": 1, "session_id": "s", "match_id": "m", "state": "failed",
         "source": {"kind": "hls", "reference": "x"}},
    ])
    ids = [a["asset_id"] for a in assets]
    assert len(ids) == len(set(ids)) == 2


def test_collect_assets_from_dirs(tmp_path):
    cdir = tmp_path / "contracts"
    cdir.mkdir()
    (cdir / "one.contract.json").write_text(json.dumps(_contract()), encoding="utf-8")
    assets = mc.collect_assets(contracts_dir=cdir)
    assert len(assets) == 1
    assert assets[0]["asset_type"] == mc.ASSET_TYPE_ARTIFACT


def test_build_catalog_valid_and_counted():
    catalog = mc.build_catalog([mc.asset_from_contract(_contract())])
    assert catalog["catalog_schema"] == mc.MEDIA_CATALOG_SCHEMA
    assert catalog["asset_count"] == 1
    assert mc.validate_catalog(catalog) == []


# ── Validation ───────────────────────────────────────────────────────────────


def test_validate_catalog_rejects_duplicate_ids():
    asset = mc.asset_from_contract(_contract())
    catalog = mc.build_catalog([asset])
    catalog["assets"].append(dict(asset))
    catalog["asset_count"] = 2
    errors = mc.validate_catalog(catalog)
    assert any("duplicate" in e for e in errors)


def test_validate_catalog_rejects_bad_version_and_schema():
    catalog = mc.build_catalog([mc.asset_from_contract(_contract())])
    catalog["catalog_version"] = 99
    catalog["catalog_schema"] = "nope"
    errors = mc.validate_catalog(catalog)
    assert any("catalog_version" in e for e in errors)
    assert any("catalog_schema" in e for e in errors)


def test_validate_catalog_rejects_source_path_leak():
    asset = mc.asset_from_contract(_contract())
    catalog = mc.build_catalog([asset])
    catalog["assets"][0]["source"]["local_file_path"] = "/secret/source.mp4"
    errors = mc.validate_catalog(catalog)
    assert any("local_file_path" in e for e in errors)


def test_validate_catalog_rejects_bad_visibility():
    catalog = mc.build_catalog([mc.asset_from_contract(_contract())])
    catalog["assets"][0]["playback"]["visibility"] = "everyone"
    errors = mc.validate_catalog(catalog)
    assert any("visibility" in e for e in errors)


def test_write_catalog_atomic_and_refuses_invalid(tmp_path):
    catalog = mc.build_catalog([mc.asset_from_contract(_contract())])
    out = tmp_path / "cat.json"
    mc.write_catalog(out, catalog)
    assert json.loads(out.read_text(encoding="utf-8"))["asset_count"] == 1
    assert list(tmp_path.glob("*.tmp")) == []

    catalog["catalog_version"] = 5
    with pytest.raises(mc.MediaCatalogError):
        mc.write_catalog(tmp_path / "bad.json", catalog)