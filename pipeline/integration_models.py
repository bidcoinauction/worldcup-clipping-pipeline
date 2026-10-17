"""Integration adapter boundary models."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INTEGRATION_CAPABILITIES = frozenset({
    "ARTIFACT_STORAGE",
    "RECORD_SYNC",
    "NOTIFICATION",
    "REVIEW_CARD",
    "DELIVERY",
})

HEALTH_STATUSES = frozenset({"READY", "NOT_CONFIGURED", "UNAVAILABLE", "ERROR"})


@dataclass(frozen=True)
class AdapterHealth:
    adapter_id: str
    configured: bool
    available: bool
    status: str
    message: str = ""
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.status not in HEALTH_STATUSES:
            raise ValueError(f"invalid health status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "configured": self.configured,
            "available": self.available,
            "status": self.status,
            "message": self.message,
            "capabilities": list(self.capabilities),
        }


@dataclass(frozen=True)
class IntegrationResult:
    adapter_id: str
    operation: str
    ok: bool
    status: str
    message: str = ""
    external_reference: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "adapter_id": self.adapter_id,
            "operation": self.operation,
            "ok": self.ok,
            "status": self.status,
            "message": self.message,
            "external_reference": self.external_reference,
            "metadata": dict(self.metadata),
        }