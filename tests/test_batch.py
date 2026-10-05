from __future__ import annotations

import json

import pytest

from pipeline import batch_service, operator_console
from pipeline.batch_models import BatchItem, BatchOperation
from pipeline.detection import _ACTIVE_ANALYSES
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.pilot import create_job
from pipeline.runtime_service import (
    duplicate_project,
    index_existing_project,
    list_project_moments,
    register_artifact,
    upsert_moment,
)
from tests.test_pilot_intake import build_intake


@pytest.fixture(autouse=True)
def _isolated_runtime_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))


def _make_job(tmp_path, monkeypatch, suffix, *, write_moments=False):
    source = tmp_path / f"source_{suffix}.mp4"
    source.write_bytes(b"media")
    intake = build_intake(str(source), overrides={
        "pilot": {"pilot_id": f"pilot_{suffix}"},
        "media": {"source_id": f"source_{suffix}"},
    })
    intake_path = tmp_path / f"intakes_{suffix}.json"
    intake_path.parent.mkdir(exist_ok=True)
    intake_path.write_text(json.dumps(intake), encoding="utf-8")
    job = create_job(intake, intake_path=intake_path, jobs_dir=tmp_path / "jobs")
    project = index_existing_project(job["job_id"], jobs_dir=tmp_path / "jobs")
    if write_moments:
        artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / f"m_{suffix}.json")
        moment = adapt_detection_to_moments(
            [{"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5}],
            project_id=project.project_id,
            source_artifact_id=artifact.artifact_id,
        )[0]
        upsert_moment(moment)
    return job, project


def _patch_analyze(monkeypatch, results: dict[str, bool]):
    from pipeline.runtime_service import create_pipeline_run
    calls: list[str] = []

    def fake_analyze(job_id, *args, **kwargs):
        calls.append(job_id)
        ok = results.get(job_id, True)
        run = create_pipeline_run(project_id=job_id, stage="analysis", status="SUCCEEDED" if ok else "FAILED")
        return {"ok": ok, "status": "COMPLETE" if ok else "FAILED",
                "runtime_run_id": run.run_id, "error": None if ok else "mocked failure"}

    monkeypatch.setattr(operator_console, "analyze_project", fake_analyze)
    return calls


# ── Models ───────────────────────────────────────────────────────────────────


def test_batch_models_valid_and_invalid():
    batch = BatchOperation(batch_id="b1", operation_type="ANALYZE", status="QUEUED", total_items=2,
                           queued_count=2, running_count=0, succeeded_count=0, failed_count=0,
                           blocked_count=0, cancelled_count=0, created_at="now")
    assert batch.to_dict()["total_items"] == 2
    with pytest.raises(ValueError, match="operation type"):
        BatchOperation("b2", "RENDER", "QUEUED", 0, 0, 0, 0, 0, 0, 0, "now")
    with pytest.raises(ValueError, match="batch status"):
        BatchOperation("b3", "ANALYZE", "NOPE", 0, 0, 0, 0, 0, 0, 0, "now")

    item = BatchItem(batch_item_id="bi1", batch_id="b1", project_id="p1", operation_type="ANALYZE", status="QUEUED")
    assert item.to_dict()["status"] == "QUEUED"
    with pytest.raises(ValueError, match="batch item status"):
        BatchItem("bi2", "b1", "p1", "ANALYZE", "NOPE")


# ── Persistence ──────────────────────────────────────────────────────────────


def test_batch_persistence_and_aggregate_counts(tmp_path, monkeypatch):
    from pipeline.runtime_service import create_pipeline_run, upsert_project
    upsert_project(project_id="p1", job_id="p1", profile="football", sport="football", display_name="A", status="READY")
    upsert_project(project_id="p2", job_id="p2", profile="football", sport="football", display_name="B", status="READY")
    run_a = create_pipeline_run(project_id="p1", stage="analysis", status="SUCCEEDED")
    batch = batch_service.create_batch_operation(operation_type="ANALYZE", total_items=2)
    item_a = batch_service.create_batch_item(batch_id=batch.batch_id, project_id="p1")
    item_b = batch_service.create_batch_item(batch_id=batch.batch_id, project_id="p2")

    batch_service.update_batch_item_status(item_a.batch_item_id, "SUCCEEDED", pipeline_run_id=run_a.run_id)
    batch_service.update_batch_item_status(item_b.batch_item_id, "FAILED", error_code="ANALYSIS_FAILED", error_message="boom")

    fetched = batch_service.get_batch_operation(batch.batch_id)
    assert fetched.status == "PARTIAL"
    assert fetched.succeeded_count == 1
    assert fetched.failed_count == 1
    assert len(batch_service.list_batch_items(batch.batch_id)) == 2
    assert batch_service.list_batch_operations(limit=5)[0].batch_id == batch.batch_id


# ── Successful batch ─────────────────────────────────────────────────────────


def test_batch_success_three_projects(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _job_c, project_c = _make_job(tmp_path, monkeypatch, "c")
    calls = _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: True, project_c.project_id: True})

    result = batch_service.run_analysis_batch([project_a.project_id, project_b.project_id, project_c.project_id])

    batch = batch_service.get_batch_operation(result["batch_id"])
    items = batch_service.list_batch_items(batch.batch_id)
    assert batch.status == "SUCCEEDED"
    assert batch.succeeded_count == 3
    run_ids = {item.pipeline_run_id for item in items}
    assert len(run_ids) == 3
    assert {item.project_id for item in items} == {project_a.project_id, project_b.project_id, project_c.project_id}
    assert len(calls) == 3


# ── Partial batch ────────────────────────────────────────────────────────────


def test_batch_partial_failure_isolated(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _job_c, project_c = _make_job(tmp_path, monkeypatch, "c")
    _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: False, project_c.project_id: True})

    result = batch_service.run_analysis_batch([project_a.project_id, project_b.project_id, project_c.project_id])

    batch = batch_service.get_batch_operation(result["batch_id"])
    items = {item.project_id: item for item in batch_service.list_batch_items(batch.batch_id)}
    assert batch.status == "PARTIAL"
    assert batch.succeeded_count == 2
    assert batch.failed_count == 1
    assert items[project_a.project_id].status == "SUCCEEDED"
    assert items[project_c.project_id].status == "SUCCEEDED"
    assert items[project_b.project_id].status == "FAILED"
    assert items[project_b.project_id].error_message == "mocked failure"


# ── All failed ───────────────────────────────────────────────────────────────


def test_batch_all_failed(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _patch_analyze(monkeypatch, {project_a.project_id: False, project_b.project_id: False})

    result = batch_service.run_analysis_batch([project_a.project_id, project_b.project_id])

    batch = batch_service.get_batch_operation(result["batch_id"])
    assert batch.status == "FAILED"
    assert batch.failed_count == 2


# ── Duplicate input dedupe ───────────────────────────────────────────────────


def test_batch_dedupes_duplicate_input(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _job_c, project_c = _make_job(tmp_path, monkeypatch, "c")
    calls = _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: True, project_c.project_id: True})

    result = batch_service.run_analysis_batch([
        project_a.project_id, project_b.project_id, project_a.project_id,
        project_c.project_id, project_b.project_id,
    ])

    batch = batch_service.get_batch_operation(result["batch_id"])
    items = batch_service.list_batch_items(batch.batch_id)
    assert batch.total_items == 3
    assert {item.project_id for item in items} == {project_a.project_id, project_b.project_id, project_c.project_id}
    assert len(calls) == 3


# ── Active analysis blocked ──────────────────────────────────────────────────


def test_batch_active_analysis_blocked(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    calls = _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: True})
    _ACTIVE_ANALYSES.add(project_a.project_id)
    try:
        result = batch_service.run_analysis_batch([project_a.project_id, project_b.project_id])
    finally:
        _ACTIVE_ANALYSES.discard(project_a.project_id)

    batch = batch_service.get_batch_operation(result["batch_id"])
    items = {item.project_id: item for item in batch_service.list_batch_items(batch.batch_id)}
    assert items[project_a.project_id].status == "BLOCKED"
    assert items[project_a.project_id].error_code == "ANALYSIS_ALREADY_RUNNING"
    assert items[project_b.project_id].status == "SUCCEEDED"
    assert len(calls) == 1


# ── Reused analysis blocked ──────────────────────────────────────────────────


def test_batch_reused_analysis_blocked(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a", write_moments=True)
    duplicate_project(source_project_id=project_a.project_id, new_project_id="dup_b", display_name="B",
                      reuse_mode="SOURCE_ANALYSIS_AND_MOMENTS")
    _job_c, project_c = _make_job(tmp_path, monkeypatch, "c")
    calls = _patch_analyze(monkeypatch, {"dup_b": True, project_c.project_id: True})

    result = batch_service.run_analysis_batch(["dup_b", project_c.project_id])

    batch = batch_service.get_batch_operation(result["batch_id"])
    items = {item.project_id: item for item in batch_service.list_batch_items(batch.batch_id)}
    assert items["dup_b"].status == "BLOCKED"
    assert items["dup_b"].error_code == "ANALYSIS_ALREADY_AVAILABLE"
    assert items[project_c.project_id].status == "SUCCEEDED"
    assert "dup_b" not in calls


# ── Batch read model ─────────────────────────────────────────────────────────


def test_batch_read_model_contains_item_summary(tmp_path, monkeypatch):
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: False})

    result = batch_service.run_analysis_batch([project_a.project_id, project_b.project_id])
    summary = batch_service.get_batch_runtime_summary(result["batch_id"])

    assert summary["batch"]["status"] == "PARTIAL"
    assert len(summary["items"]) == 2
    assert all("display_name" in item for item in summary["items"])
    assert any(item["status"] == "FAILED" and item["error_message"] == "mocked failure" for item in summary["items"])


# ── Console ──────────────────────────────────────────────────────────────────


def test_console_batch_start_and_detail(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _html_body, _json_body
    from pipeline.console_server import ConsoleHandler
    _job_a, project_a = _make_job(tmp_path, monkeypatch, "a")
    _job_b, project_b = _make_job(tmp_path, monkeypatch, "b")
    _patch_analyze(monkeypatch, {project_a.project_id: True, project_b.project_id: False})

    body = json.dumps({"project_ids": [project_a.project_id, project_b.project_id]}).encode("utf-8")
    fake = _FakeHandler(body, path="/api/batches/analyze")
    ConsoleHandler.do_POST(fake)
    result = _json_body(fake)
    assert result["ok"] is True
    batch_id = result["batch_id"]

    fake = _FakeHandler()
    ConsoleHandler._render_batch_detail(fake, batch_id)
    html = _html_body(fake)
    assert "Batch Analysis" in html
    assert "PARTIAL" in html
    assert "SUCCEEDED" in html
    assert "FAILED" in html
    assert "mocked failure" in html


# ── DB upgrade ───────────────────────────────────────────────────────────────


def test_runtime_db_upgrade_adds_batch_tables(tmp_path):
    from pipeline import runtime_db
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, parent_project_id TEXT, source_project_id TEXT, reuse_mode TEXT NOT NULL DEFAULT '', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE pipeline_runs (run_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, stage TEXT NOT NULL, status TEXT NOT NULL, started_at TEXT, finished_at TEXT, progress_current INTEGER, progress_total INTEGER, error_code TEXT, error_message TEXT, retry_count INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL);
            """
        )
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1', 'j1', 'football', 'football', 'Match', 'READY', '2026-01-01', '2026-01-01')")

    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        project = conn.execute("SELECT * FROM projects WHERE project_id = 'p1'").fetchone()
    assert {"batch_operations", "batch_items"} <= tables
    assert project["display_name"] == "Match"