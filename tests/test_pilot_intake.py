"""Intake validation, rights gate, source validation, and config references."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.pilot import (
    job_id_for_intake,
    rights_cleared,
    validate_intake,
    validate_source,
)


def build_intake(source_path: str | None = None, *, overrides: dict | None = None) -> dict:
    intake = {
        "intake_version": 1,
        "pilot": {
            "pilot_id": "pilot_alpha",
            "project": "football",
            "reference_deployment": "world_cup",
            "requested_clip_count": {"min": 8, "max": 15},
            "operator_notes": "test intake",
        },
        "media": {
            "source_id": "source_alpha",
            "local_file_path": source_path,
            "original_filename": "source.mp4",
            "media_type": "video",
            "supplied_by_client": True,
        },
        "rights": {
            "status": "CONFIRMED",
            "confirmation_statement": "Client confirms clipping, storage, review, and delivery.",
            "confirmed_by": "Test Client",
            "confirmation_date": "2026-01-01",
            "permitted_uses": ["clip", "store", "review", "delivery"],
            "distribution_limitations": ["no_broadcast_without_approval"],
        },
        "configuration": {
            "project": "football",
            "brand": "world_cup",
            "editorial_taxonomy": "world_cup",
            "operational_taxonomy": "world_cup",
            "detection_template": "prompt",
            "export_profiles": ["vertical_clean", "source"],
            "delivery_destination": "EXPORTS",
        },
        "review_and_delivery": {
            "human_review_required": True,
            "approval_method": "email",
            "delivery_method": "shared_folder",
            "delivery_directory": "FootballArchive/EXPORTS",
            "publishing_included": False,
        },
    }
    if overrides:
        _merge(intake, overrides)
    return intake


def _merge(target: dict, overrides: dict) -> None:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = value


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"fake media bytes" * 100)
    return path


def _codes(report: dict) -> set[str]:
    return {issue["code"] for issue in report["issues"]}


# ── Intake validation ────────────────────────────────────────────────────────


def test_valid_world_cup_example(media_file: Path):
    report = validate_intake(build_intake(str(media_file)))
    assert report["structurally_valid"]
    assert report["config_references_valid"]
    assert report["source_ready"]
    assert report["rights_cleared"]
    assert report["execution_ready"]


def test_missing_pilot_id(media_file: Path):
    intake = build_intake(str(media_file), overrides={"pilot": {"pilot_id": ""}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert any(i["path"] == "pilot.pilot_id" for i in report["issues"])


def test_invalid_pilot_id(media_file: Path):
    intake = build_intake(str(media_file), overrides={"pilot": {"pilot_id": "has space"}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert any(i["code"] == "BAD_ID" and i["path"] == "pilot.pilot_id" for i in report["issues"])


def test_unknown_structured_key(media_file: Path):
    intake = build_intake(str(media_file))
    intake["pilot"]["brand_new_key"] = "x"
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert any(i["path"] == "pilot.brand_new_key" and i["code"] == "UNKNOWN_KEY" for i in report["issues"])


def test_missing_source_identifier(media_file: Path):
    intake = build_intake(str(media_file), overrides={"media": {"source_id": ""}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert any(i["path"] == "media.source_id" for i in report["issues"])


def test_source_path_is_directory(tmp_path: Path):
    intake = build_intake(str(tmp_path))
    report = validate_intake(intake)
    assert report["structurally_valid"]
    assert not report["source_ready"]
    assert "SOURCE_IS_DIRECTORY" in _codes(report)


def test_source_file_missing(tmp_path: Path):
    intake = build_intake(str(tmp_path / "nope.mp4"))
    report = validate_intake(intake)
    assert not report["execution_ready"]
    assert "SOURCE_MISSING" in _codes(report)


def test_source_file_empty(tmp_path: Path):
    empty = tmp_path / "empty.mp4"
    empty.write_bytes(b"")
    intake = build_intake(str(empty))
    report = validate_intake(intake)
    assert not report["source_ready"]
    assert "SOURCE_EMPTY" in _codes(report)


def test_unsupported_extension(tmp_path: Path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello", encoding="utf-8")
    intake = build_intake(str(txt))
    report = validate_intake(intake)
    assert not report["source_ready"]
    assert "SOURCE_UNSUPPORTED_EXTENSION" in _codes(report)


def test_optional_checksum_matches(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"checksum me")
    checksum = hashlib.sha256(b"checksum me").hexdigest()
    intake = build_intake(str(media), overrides={"media": {"checksum": checksum}})
    report = validate_intake(intake)
    assert report["source_ready"]
    assert "SOURCE_CHECKSUM_MISMATCH" not in _codes(report)


def test_optional_checksum_mismatch(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"actual bytes")
    intake = build_intake(str(media), overrides={"media": {"checksum": "0" * 64}})
    report = validate_intake(intake)
    assert not report["source_ready"]
    assert "SOURCE_CHECKSUM_MISMATCH" in _codes(report)


def test_network_url_rejected(tmp_path: Path):
    intake = build_intake("https://example.com/match.mp4")
    report = validate_intake(intake)
    assert not report["source_ready"]
    assert "SOURCE_URL_NOT_ALLOWED" in _codes(report)


def test_unsafe_source_path_rejected_within_root(tmp_path: Path):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"data")
    outside = tmp_path.parent / "outside.mp4"
    outside.write_bytes(b"data")
    intake = build_intake(str(outside))
    report = validate_intake(intake, intake_root=str(tmp_path))
    assert not report["source_ready"]
    assert "SOURCE_OUTSIDE_INTAKE_ROOT" in _codes(report)


def test_relative_traversal_source_path_rejected():
    intake = build_intake("../escape.mp4")
    report = validate_intake(intake)
    assert not report["source_ready"]
    assert "SOURCE_UNSAFE_PATH" in _codes(report)


def test_validation_performs_no_mutation(tmp_path: Path, media_file: Path):
    intake = build_intake(str(media_file))
    before = media_file.stat().st_mtime_ns
    files_before = set(tmp_path.iterdir())
    validate_intake(intake)
    assert media_file.stat().st_mtime_ns == before
    assert set(tmp_path.iterdir()) == files_before


def test_validation_performs_no_network_and_no_subprocess(media_file: Path):
    intake = build_intake(str(media_file))
    with patch("pipeline.pilot.subprocess.run", side_effect=AssertionError("no subprocess allowed")):
        report = validate_intake(intake)
    assert report["execution_ready"]


# ── Rights gate ──────────────────────────────────────────────────────────────


def test_confirmed_rights_pass_readiness(media_file: Path):
    report = validate_intake(build_intake(str(media_file)))
    assert report["rights_cleared"]
    assert report["execution_ready"]


def test_unconfirmed_rights_produce_not_ready(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}})
    report = validate_intake(intake)
    assert report["structurally_valid"]
    assert not report["rights_cleared"]
    assert not report["execution_ready"]
    assert "RIGHTS_NOT_CONFIRMED" in _codes(report)


def test_restricted_rights_validate_but_not_ready(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "RESTRICTED"}})
    report = validate_intake(intake)
    assert report["structurally_valid"]
    assert not report["rights_cleared"]
    assert not report["execution_ready"]


def test_rejected_rights_block_readiness(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "REJECTED"}})
    report = validate_intake(intake)
    assert not report["execution_ready"]
    assert "RIGHTS_NOT_CONFIRMED" in _codes(report)


def test_expired_rights_block_readiness(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"expiration_date": "2000-01-01"}})
    report = validate_intake(intake)
    assert not report["rights_cleared"]
    assert not report["execution_ready"]
    assert "RIGHTS_EXPIRED" in _codes(report)


def test_missing_confirmation_statement_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"confirmation_statement": ""}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert "RIGHTS_MISSING_STATEMENT" in _codes(report)


def test_missing_confirmer_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"confirmed_by": ""}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert "RIGHTS_MISSING_CONFIRMER" in _codes(report)


def test_missing_permitted_uses_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"rights": {"permitted_uses": []}})
    report = validate_intake(intake)
    assert not report["structurally_valid"]
    assert any("permitted_uses" in i["path"] for i in report["issues"])


def test_publishing_request_conflicts_with_permissions(media_file: Path):
    intake = build_intake(str(media_file), overrides={
        "review_and_delivery": {"publishing_included": True},
        "rights": {"permitted_uses": ["clip", "store", "review", "delivery"]},
    })
    report = validate_intake(intake)
    assert not report["execution_ready"]
    assert "PUBLISHING_NOT_PERMITTED" in _codes(report)


def test_publishing_allowed_when_permitted(media_file: Path):
    intake = build_intake(str(media_file), overrides={
        "review_and_delivery": {"publishing_included": True},
        "rights": {"permitted_uses": ["clip", "publish"], "distribution_limitations": ["approved_channels_only"]},
    })
    report = validate_intake(intake)
    assert report["execution_ready"]


def test_rights_cleared_helper(media_file: Path):
    cleared, status = rights_cleared(build_intake(str(media_file)))
    assert cleared and status == "CONFIRMED"
    cleared, status = rights_cleared(build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}}))
    assert not cleared and status == "UNCONFIRMED"


def test_full_field_paths_appear_in_errors(media_file: Path):
    intake = build_intake(str(media_file))
    intake["rights"]["permitted_uses"] = "not a list"
    intake["configuration"]["brand"] = "does_not_exist"
    report = validate_intake(intake)
    messages = " | ".join(f"{i['path']} {i['code']}" for i in report["issues"])
    assert "rights.permitted_uses" in messages
    assert "configuration.brand" in messages


# ── Configuration references ─────────────────────────────────────────────────


def test_config_references_resolve(media_file: Path):
    intake = build_intake(str(media_file))
    report = validate_intake(intake)
    assert report["config_references_valid"]


def test_unknown_brand_reference_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"configuration": {"brand": "does_not_exist"}})
    report = validate_intake(intake)
    assert not report["config_references_valid"]
    assert not report["execution_ready"]
    assert any(i["path"] == "configuration.brand" for i in report["issues"])


def test_unknown_export_profile_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"configuration": {"export_profiles": ["bogus_profile"]}})
    report = validate_intake(intake)
    assert not report["config_references_valid"]
    assert any(i["path"] == "configuration.export_profiles[0]" for i in report["issues"])


def test_unknown_detection_template_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"configuration": {"detection_template": "nope"}})
    report = validate_intake(intake)
    assert not report["config_references_valid"]
    assert any(i["path"] == "configuration.detection_template" for i in report["issues"])


def test_unknown_project_fails(media_file: Path):
    intake = build_intake(str(media_file), overrides={"configuration": {"project": "cricket"}})
    report = validate_intake(intake)
    assert not report["config_references_valid"]
    assert any(i["path"] == "configuration.project" for i in report["issues"])


def test_basketball_brand_is_separate_example(tmp_path: Path):
    example = Path(__file__).resolve().parents[1] / "config" / "brands" / "basketball_example.json"
    data = json.loads(example.read_text(encoding="utf-8"))
    assert data["id"] == "basketball_example"
    assert data["id"] != "world_cup"


def test_job_id_derivation(media_file: Path):
    assert job_id_for_intake(build_intake(str(media_file))) == "pilot_alpha_source_alpha"
    assert job_id_for_intake({}) is None
    assert job_id_for_intake(build_intake(str(media_file), overrides={"pilot": {"pilot_id": "bad id"}})) is None


# ── Source validator (public surface) ────────────────────────────────────────


def test_validate_source_public(media_file: Path):
    ok, issues, duration_checked, limitation = validate_source(build_intake(str(media_file)))
    assert ok and not issues
    assert duration_checked is False
    assert limitation is None


def test_duration_limitation_reported_without_ffprobe(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"data")
    intake = build_intake(str(media), overrides={"media": {"duration_seconds": 60.0}})
    monkeypatch.setattr("pipeline.pilot.shutil.which", lambda _: None)
    ok, issues, duration_checked, limitation = validate_source(intake)
    assert ok
    assert not duration_checked
    assert limitation is not None and "ffprobe" in limitation


def test_duration_mismatch_reported(tmp_path: Path, monkeypatch):
    media = tmp_path / "source.mp4"
    media.write_bytes(b"data")
    intake = build_intake(str(media), overrides={"media": {"duration_seconds": 60.0}})
    monkeypatch.setattr("pipeline.pilot._ffprobe_duration", lambda _path: 10.0)
    ok, issues, duration_checked, limitation = validate_source(intake)
    assert ok
    assert duration_checked
    assert "SOURCE_DURATION_MISMATCH" in {i["code"] for i in issues}
