"""Regression tests: FULL operator-media smoke must reuse production artifacts.

A real source that already has a transcript (production lookup path + runtime
Artifact record) must report the transcription stage as REUSED, must not call
the transcription implementation, must not create a second transcript Artifact,
and must not inherit a nested smoke-derived source identity. FAST fixture smoke
stays isolated and deterministic.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from pipeline import operator_console
from pipeline import smoke_service
from pipeline.runtime_service import list_project_artifacts


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))
    for key in ("OPENAI_API_KEY", "SLACK_WEBHOOK_URL", "AIRTABLE_API_TOKEN", "OORT_API_KEY"):
        monkeypatch.delenv(key, raising=False)


def _real_style_source(tmp_path: Path) -> Path:
    source = tmp_path / "germany_italy_2012.mp4"
    source.write_bytes(b"real media fixture")
    return source


def _patch_reuse_and_detection(monkeypatch, existing_transcript: Path):
    """Resolve the existing transcript via the production lookup and stub
    detection so the smoke does not require a live model provider."""
    import pipeline.transcription as transcription_mod

    monkeypatch.setattr(
        transcription_mod,
        "find_existing_transcript",
        lambda *_a, **_k: existing_transcript,
    )

    def _must_not_transcribe(*_a, **_k):
        raise AssertionError("transcribe_source must not run when a transcript is reused")

    monkeypatch.setattr(transcription_mod, "transcribe_source", _must_not_transcribe)
    monkeypatch.setattr(operator_console, "require_detection_provider", lambda: None)
    monkeypatch.setattr(operator_console, "run_detection_call", lambda *_a, **_k: [
        {"clip_id": "clip_001", "category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:03"},
    ])
    monkeypatch.setattr(operator_console, "build_clip_manifest", lambda *_a, **_k: {
        "fieldnames": ["clip_id", "category", "start_time", "end_time"],
        "rows": [{"clip_id": "clip_001", "category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:03"}],
    })


def _transcript_stage(report):
    return next(stage for stage in report["timings"]["stages"] if stage["stage"] == "transcription")


def test_full_smoke_reuses_existing_transcript(tmp_path, monkeypatch):
    source = _real_style_source(tmp_path)
    existing = tmp_path / "existing_transcript.txt"
    existing.write_text('{"segments": []}', encoding="utf-8")
    _patch_reuse_and_detection(monkeypatch, existing)

    report = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="full",
        smoke_mode="FULL",
        source_type="OPERATOR_MEDIA",
        real_media=True,
    )

    assert report["project_id"] == "pilot_germany_italy_2012_source_germany_italy_2012"
    assert report["smoke_run_id"].startswith("smoke_")
    assert report["smoke_run_id"] != report["project_id"]

    stage = _transcript_stage(report)
    assert stage["status"] == "PASS"
    assert stage["reused"] is True

    artifacts = list_project_artifacts(report["project_id"], artifact_type="transcript")
    assert len(artifacts) == 1
    assert Path(artifacts[0].path) == existing


def test_full_smoke_repeated_runs_keep_single_transcript_artifact(tmp_path, monkeypatch):
    source = _real_style_source(tmp_path)
    existing = tmp_path / "existing_transcript.txt"
    existing.write_text('{"segments": []}', encoding="utf-8")
    _patch_reuse_and_detection(monkeypatch, existing)

    first = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="full",
        smoke_mode="FULL",
        source_type="OPERATOR_MEDIA",
        real_media=True,
    )
    second = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="full",
        smoke_mode="FULL",
        source_type="OPERATOR_MEDIA",
        real_media=True,
    )

    assert first["project_id"] == second["project_id"]
    assert _transcript_stage(first)["reused"] is True
    assert _transcript_stage(second)["reused"] is True

    artifacts = list_project_artifacts(first["project_id"], artifact_type="transcript")
    assert len(artifacts) == 1
    assert Path(artifacts[0].path) == existing


def test_full_smoke_does_not_create_alternate_transcript_path(tmp_path, monkeypatch):
    source = _real_style_source(tmp_path)
    existing = tmp_path / "existing_transcript.txt"
    existing.write_text('{"segments": []}', encoding="utf-8")
    _patch_reuse_and_detection(monkeypatch, existing)

    report = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="full",
        smoke_mode="FULL",
        source_type="OPERATOR_MEDIA",
        real_media=True,
    )

    artifacts = list_project_artifacts(report["project_id"], artifact_type="transcript")
    assert [Path(a.path) for a in artifacts] == [existing]


def test_fast_fixture_smoke_stays_isolated_and_deterministic(tmp_path, monkeypatch):
    source = tmp_path / "fixture_source.mp4"
    source.write_bytes(b"fixture media")

    first = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="fast",
    )
    second = smoke_service.run_smoke(
        source_file=source,
        jobs_dir=tmp_path / "jobs",
        db_path=tmp_path / "runtime.sqlite3",
        mode="fast",
    )

    assert first["project_id"] == second["project_id"]
    assert first["project_id"].startswith("smoke_")
    assert first["classification"] == {"smoke_mode": "FAST", "source_type": "FIXTURE", "real_media": False}