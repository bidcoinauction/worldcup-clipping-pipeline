"""Lightweight stage performance instrumentation.

Provides structured stage timing records and an operator-readable report. Not a
full observability platform. Durations are wall-clock measurements; reused stages
report near-zero orchestration time with ``reused = true``.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable

logger = logging.getLogger("pipeline.performance")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class StageTiming:
    stage: str
    started_at: str
    finished_at: str
    duration_seconds: float
    status: str = "PASS"
    reused: bool = False
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.stage,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "duration_seconds": round(self.duration_seconds, 3),
            "status": self.status,
            "reused": self.reused,
            "details": self.details,
        }


class PerformanceReport:
    """Collects stage timings and renders a concise report."""

    def __init__(self) -> None:
        self.stages: list[StageTiming] = []
        self.counts: dict[str, int] = {}

    def record(self, stage: str, *, status: str = "PASS", reused: bool = False,
               duration_seconds: float = 0.0, started_at: str = "",
               finished_at: str = "", details: dict[str, Any] | None = None) -> StageTiming:
        timing = StageTiming(
            stage=stage,
            started_at=started_at or _now_iso(),
            finished_at=finished_at or _now_iso(),
            duration_seconds=max(0.0, duration_seconds),
            status=status,
            reused=reused,
            details=details or {},
        )
        self.stages.append(timing)
        return timing

    @property
    def total_wall_time(self) -> float:
        if not self.stages:
            return 0.0
        start = min(t.started_at for t in self.stages)
        finish = max(t.finished_at for t in self.stages)
        try:
            s = datetime.fromisoformat(start)
            f = datetime.fromisoformat(finish)
            return max(0.0, (f - s).total_seconds())
        except ValueError:
            return sum(t.duration_seconds for t in self.stages)

    def ok(self) -> bool:
        return all(t.status == "PASS" for t in self.stages)

    def to_dict(self) -> dict[str, Any]:
        return {
            "stages": [t.to_dict() for t in self.stages],
            "counts": dict(self.counts),
            "total_wall_time_seconds": round(self.total_wall_time, 3),
            "result": "PASS" if self.ok() else "FAIL",
        }

    def render(self) -> str:
        lines = ["Clipper Real-Match Smoke", ""]
        for t in self.stages:
            label = t.status
            if t.reused:
                label = "REUSED"
            stage_label = t.stage.replace("_", " ").title()
            lines.append(f"{stage_label:<22} {label:<8} {t.duration_seconds:7.1f}s")
        lines.append("")
        lines.append(f"Total wall time: {self.total_wall_time:.1f}s")
        for key, value in self.counts.items():
            lines.append(f"{key}: {value}")
        lines.append("")
        lines.append(f"Result: {'PASS' if self.ok() else 'FAIL'}")
        return "\n".join(lines)


@contextmanager
def measure_stage(report: PerformanceReport, stage: str, *, reused: bool = False,
                  status: str = "PASS", details: dict[str, Any] | None = None,
                  on_finish: Callable[[str, float, bool], None] | None = None):
    """Time a stage and record it. ``on_finish`` may log the result."""
    import time

    started = time.perf_counter()
    started_at = _now_iso()
    try:
        yield
        report.record(stage, status=status, reused=reused, duration_seconds=time.perf_counter() - started,
                      started_at=started_at, finished_at=_now_iso(), details=details)
        if on_finish:
            on_finish(stage, time.perf_counter() - started, reused)
    except Exception as exc:  # noqa: BLE001
        report.record(stage, status="FAIL", reused=reused, duration_seconds=time.perf_counter() - started,
                      started_at=started_at, finished_at=_now_iso(),
                      details={**(details or {}), "error": str(exc)[:300]})
        raise


def log_stage(component: str, stage: str, duration_seconds: float, reused: bool = False) -> None:
    logger.info("component=%s stage=%s duration=%.2fs reused=%s", component, stage, duration_seconds, reused)