"""Batch orchestration service — independent project operations."""

from __future__ import annotations

import hashlib
from contextlib import closing
from datetime import datetime, timezone
import uuid
from typing import Any

from . import runtime_db
from .batch_models import BATCH_ITEM_STATUSES, BATCH_STATUSES, BatchItem, BatchOperation


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dump_any(data: Any) -> str:
    import json
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _json_load_any(raw: str | None, default: Any) -> Any:
    import json
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _row_batch(row) -> BatchOperation:
    return BatchOperation(
        batch_id=row["batch_id"],
        operation_type=row["operation_type"],
        status=row["status"],
        total_items=row["total_items"],
        queued_count=row["queued_count"],
        running_count=row["running_count"],
        succeeded_count=row["succeeded_count"],
        failed_count=row["failed_count"],
        blocked_count=row["blocked_count"],
        cancelled_count=row["cancelled_count"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_item(row) -> BatchItem:
    return BatchItem(
        batch_item_id=row["batch_item_id"],
        batch_id=row["batch_id"],
        project_id=row["project_id"],
        operation_type=row["operation_type"],
        status=row["status"],
        pipeline_run_id=row["pipeline_run_id"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        created_at=row["created_at"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def create_batch_operation(*, operation_type: str, total_items: int, metadata: dict[str, Any] | None = None,
                           batch_id: str | None = None, db_path=None) -> BatchOperation:
    if operation_type != "ANALYZE":
        raise ValueError(f"unsupported batch operation type: {operation_type}")
    runtime_db.initialize(db_path)
    now = _now_iso()
    key = batch_id or f"batch_{uuid.uuid4().hex}"
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO batch_operations(
                batch_id, operation_type, status, total_items, queued_count, running_count,
                succeeded_count, failed_count, blocked_count, cancelled_count, created_at, metadata_json
            )
            VALUES(?, ?, 'QUEUED', ?, ?, 0, 0, 0, 0, 0, ?, ?)
            """,
            (key, operation_type, total_items, total_items, now, _json_dump_any(metadata or {})),
        )
        row = conn.execute("SELECT * FROM batch_operations WHERE batch_id = ?", (key,)).fetchone()
    return _row_batch(row)


def get_batch_operation(batch_id: str, *, db_path=None) -> BatchOperation | None:
    runtime_db.initialize(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM batch_operations WHERE batch_id = ?", (batch_id,)).fetchone()
    return _row_batch(row) if row else None


def list_batch_operations(*, limit: int = 25, db_path=None) -> list[BatchOperation]:
    runtime_db.initialize(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM batch_operations ORDER BY created_at DESC, batch_id DESC LIMIT ?", (limit,)
        ).fetchall()
    return [_row_batch(row) for row in rows]


def create_batch_item(*, batch_id: str, project_id: str, operation_type: str = "ANALYZE",
                      metadata: dict[str, Any] | None = None, db_path=None) -> BatchItem:
    if operation_type != "ANALYZE":
        raise ValueError(f"unsupported batch operation type: {operation_type}")
    runtime_db.initialize(db_path)
    now = _now_iso()
    key = f"bi_{uuid.uuid4().hex}"
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO batch_items(
                batch_item_id, batch_id, project_id, operation_type, status, created_at, metadata_json
            )
            VALUES(?, ?, ?, ?, 'QUEUED', ?, ?)
            """,
            (key, batch_id, project_id, operation_type, now, _json_dump_any(metadata or {})),
        )
        row = conn.execute("SELECT * FROM batch_items WHERE batch_item_id = ?", (key,)).fetchone()
    return _row_item(row)


def list_batch_items(batch_id: str, *, status: str | None = None, db_path=None) -> list[BatchItem]:
    runtime_db.initialize(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if status:
            if status not in BATCH_ITEM_STATUSES:
                raise ValueError(f"invalid batch item status: {status}")
            rows = conn.execute(
                "SELECT * FROM batch_items WHERE batch_id = ? AND status = ? ORDER BY created_at, batch_item_id",
                (batch_id, status),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM batch_items WHERE batch_id = ? ORDER BY created_at, batch_item_id", (batch_id,)
            ).fetchall()
    return [_row_item(row) for row in rows]


def get_batch_item(batch_item_id: str, *, db_path=None) -> BatchItem | None:
    runtime_db.initialize(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM batch_items WHERE batch_item_id = ?", (batch_item_id,)).fetchone()
    return _row_item(row) if row else None


def update_batch_item_status(batch_item_id: str, status: str, *, pipeline_run_id: str | None = None,
                             error_code: str | None = None, error_message: str | None = None,
                             started_at: str | None = None, finished_at: str | None = None,
                             db_path=None) -> BatchItem:
    if status not in BATCH_ITEM_STATUSES:
        raise ValueError(f"invalid batch item status: {status}")
    runtime_db.initialize(db_path)
    now = _now_iso()
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM batch_items WHERE batch_item_id = ?", (batch_item_id,)).fetchone()
        if current is None:
            raise ValueError(f"batch item '{batch_item_id}' not found")
        eff_started = started_at if started_at is not None else (current["started_at"] or (now if status == "RUNNING" else None))
        eff_finished = finished_at
        if status in ("SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED") and not eff_finished:
            eff_finished = now
        conn.execute(
            """
            UPDATE batch_items
            SET status = ?, pipeline_run_id = ?, error_code = ?, error_message = ?, started_at = ?, finished_at = ?
            WHERE batch_item_id = ?
            """,
            (
                status,
                pipeline_run_id if pipeline_run_id is not None else current["pipeline_run_id"],
                error_code if error_code is not None else current["error_code"],
                error_message if error_message is not None else current["error_message"],
                eff_started,
                eff_finished,
                batch_item_id,
            ),
        )
        row = conn.execute("SELECT * FROM batch_items WHERE batch_item_id = ?", (batch_item_id,)).fetchone()
    recalculate_batch_summary(current["batch_id"], db_path=db_path)
    return _row_item(row)


def update_batch_status(batch_id: str, status: str, *, started_at: str | None = None,
                        finished_at: str | None = None, db_path=None) -> BatchOperation:
    if status not in BATCH_STATUSES:
        raise ValueError(f"invalid batch status: {status}")
    runtime_db.initialize(db_path)
    now = _now_iso()
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM batch_operations WHERE batch_id = ?", (batch_id,)).fetchone()
        if current is None:
            raise ValueError(f"batch '{batch_id}' not found")
        eff_started = started_at if started_at is not None else (current["started_at"] or (now if status == "RUNNING" else None))
        eff_finished = finished_at
        if status in ("SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED") and not eff_finished:
            eff_finished = now
        conn.execute(
            "UPDATE batch_operations SET status = ?, started_at = ?, finished_at = ? WHERE batch_id = ?",
            (status, eff_started, eff_finished, batch_id),
        )
        row = conn.execute("SELECT * FROM batch_operations WHERE batch_id = ?", (batch_id,)).fetchone()
    return _row_batch(row)


def recalculate_batch_summary(batch_id: str, *, db_path=None) -> BatchOperation:
    """Recompute batch counts from items and derive the aggregate batch status."""
    items = list_batch_items(batch_id, db_path=db_path)
    counts = {status: 0 for status in BATCH_ITEM_STATUSES}
    for item in items:
        counts[item.status] += 1
    total = len(items)
    succeeded = counts["SUCCEEDED"]
    failed = counts["FAILED"]
    blocked = counts["BLOCKED"]
    cancelled = counts["CANCELLED"]
    running = counts["RUNNING"]
    queued = counts["QUEUED"]

    if total == 0:
        status = "SUCCEEDED"
    elif succeeded == total:
        status = "SUCCEEDED"
    elif cancelled == total:
        status = "CANCELLED"
    elif running or queued:
        status = "RUNNING"
    elif succeeded > 0 and (failed + blocked + cancelled) > 0:
        status = "PARTIAL"
    else:
        status = "FAILED"

    finished = status in ("SUCCEEDED", "FAILED", "PARTIAL", "CANCELLED")
    runtime_db.initialize(db_path)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            UPDATE batch_operations
            SET status = ?, total_items = ?, queued_count = ?, running_count = ?, succeeded_count = ?,
                failed_count = ?, blocked_count = ?, cancelled_count = ?, finished_at = ?
            WHERE batch_id = ?
            """,
            (
                status,
                total,
                counts["QUEUED"],
                counts["RUNNING"],
                succeeded,
                failed,
                blocked,
                cancelled,
                _now_iso() if finished else None,
                batch_id,
            ),
        )
        row = conn.execute("SELECT * FROM batch_operations WHERE batch_id = ?", (batch_id,)).fetchone()
    return _row_batch(row)


def get_batch_runtime_summary(batch_id: str, *, db_path=None) -> dict[str, Any]:
    batch = get_batch_operation(batch_id, db_path=db_path)
    if batch is None:
        raise ValueError(f"batch '{batch_id}' not found")
    items = list_batch_items(batch_id, db_path=db_path)
    item_rows = []
    for item in items:
        project_name = item.project_id
        try:
            from .runtime_service import get_project
            project = get_project(item.project_id, db_path=db_path)
            if project is not None:
                project_name = project.display_name
        except Exception:
            pass
        item_rows.append({
            "batch_item_id": item.batch_item_id,
            "project_id": item.project_id,
            "display_name": project_name,
            "status": item.status,
            "pipeline_run_id": item.pipeline_run_id,
            "error_code": item.error_code,
            "error_message": item.error_message,
        })
    return {"batch": batch.to_dict(), "items": item_rows}


def run_analysis_batch(project_ids: list[str], *, operator: str | None = None, jobs_dir=None,
                       db_path=None) -> dict[str, Any]:
    """Run the ANALYZE operation across projects, sequentially and independently.

    Each project uses the existing managed analysis entry point, which creates
    its own PipelineRun. Failures are isolated per item.
    """
    from .detection import _ACTIVE_ANALYSES
    from .operator_console import analyze_project
    from .runtime_service import get_project, get_project_runtime_summary

    seen: set[str] = set()
    ordered_ids: list[str] = []
    for pid in project_ids:
        if pid in seen:
            continue
        seen.add(pid)
        ordered_ids.append(pid)

    batch = create_batch_operation(operation_type="ANALYZE", total_items=len(ordered_ids), db_path=db_path)
    items = [create_batch_item(batch_id=batch.batch_id, project_id=pid, db_path=db_path) for pid in ordered_ids]
    update_batch_status(batch.batch_id, "RUNNING", started_at=_now_iso(), db_path=db_path)

    for item in items:
        project_id = item.project_id
        try:
            project = get_project(project_id, db_path=db_path)
            if project is None:
                update_batch_item_status(item.batch_item_id, "BLOCKED", error_code="PROJECT_NOT_FOUND",
                                         error_message=f"project '{project_id}' not found", db_path=db_path)
                continue
            job_id = project.job_id or project.project_id

            if job_id in _ACTIVE_ANALYSES:
                update_batch_item_status(item.batch_item_id, "BLOCKED", error_code="ANALYSIS_ALREADY_RUNNING",
                                         error_message="analysis is already running for this project", db_path=db_path)
                continue

            summary = get_project_runtime_summary(job_id, jobs_dir=jobs_dir, db_path=db_path)
            lineage = summary.get("lineage", {})
            if lineage.get("analysis_reused"):
                update_batch_item_status(item.batch_item_id, "BLOCKED", error_code="ANALYSIS_ALREADY_AVAILABLE",
                                         error_message="project already reuses analysis; no retranscription required", db_path=db_path)
                continue

            update_batch_item_status(item.batch_item_id, "RUNNING", started_at=_now_iso(), db_path=db_path)
            result = analyze_project(job_id, jobs_dir=jobs_dir)
            run_id = result.get("runtime_run_id")
            if result.get("ok"):
                update_batch_item_status(item.batch_item_id, "SUCCEEDED", pipeline_run_id=run_id, db_path=db_path)
            else:
                update_batch_item_status(item.batch_item_id, "FAILED", pipeline_run_id=run_id,
                                         error_code=result.get("error_code") or "ANALYSIS_FAILED",
                                         error_message=result.get("error") or "analysis failed", db_path=db_path)
        except Exception as exc:
            safe = str(exc)[:500] or "analysis failed"
            update_batch_item_status(item.batch_item_id, "FAILED", error_code="ANALYSIS_FAILED",
                                     error_message=safe, db_path=db_path)

    final = recalculate_batch_summary(batch.batch_id, db_path=db_path)
    return {"batch_id": batch.batch_id, "status": final.status, "ok": final.status in ("SUCCEEDED", "PARTIAL")}