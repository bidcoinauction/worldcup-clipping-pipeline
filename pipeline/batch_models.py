"""Canonical Batch runtime records."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

BATCH_STATUSES = frozenset({"QUEUED", "RUNNING", "PARTIAL", "SUCCEEDED", "FAILED", "CANCELLED"})
BATCH_ITEM_STATUSES = frozenset({"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "BLOCKED", "CANCELLED"})
BATCH_OPERATION_TYPES = frozenset({"ANALYZE"})


@dataclass(frozen=True)
class BatchOperation:
    batch_id: str
    operation_type: str
    status: str
    total_items: int
    queued_count: int
    running_count: int
    succeeded_count: int
    failed_count: int
    blocked_count: int
    cancelled_count: int
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation_type not in BATCH_OPERATION_TYPES:
            raise ValueError(f"invalid batch operation type: {self.operation_type}")
        if self.status not in BATCH_STATUSES:
            raise ValueError(f"invalid batch status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_id": self.batch_id,
            "operation_type": self.operation_type,
            "status": self.status,
            "total_items": self.total_items,
            "queued_count": self.queued_count,
            "running_count": self.running_count,
            "succeeded_count": self.succeeded_count,
            "failed_count": self.failed_count,
            "blocked_count": self.blocked_count,
            "cancelled_count": self.cancelled_count,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class BatchItem:
    batch_item_id: str
    batch_id: str
    project_id: str
    operation_type: str
    status: str
    pipeline_run_id: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    created_at: str = ""
    started_at: str | None = None
    finished_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.operation_type not in BATCH_OPERATION_TYPES:
            raise ValueError(f"invalid batch operation type: {self.operation_type}")
        if self.status not in BATCH_ITEM_STATUSES:
            raise ValueError(f"invalid batch item status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "batch_item_id": self.batch_item_id,
            "batch_id": self.batch_id,
            "project_id": self.project_id,
            "operation_type": self.operation_type,
            "status": self.status,
            "pipeline_run_id": self.pipeline_run_id,
            "error_code": self.error_code,
            "error_message": self.error_message,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "metadata": dict(self.metadata),
        }