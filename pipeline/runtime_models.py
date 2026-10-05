"""Canonical runtime records for the local application index."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


RUN_STATES = frozenset({"QUEUED", "RUNNING", "SUCCEEDED", "FAILED", "CANCELLED", "BLOCKED"})
ANALYSIS_STRATEGIES = frozenset({"TRANSCRIPT_FIRST", "RESEARCH_FIRST", "HYBRID"})
DEFAULT_ANALYSIS_STRATEGY = "TRANSCRIPT_FIRST"


def normalize_analysis_strategy(value: str | None) -> str:
    strategy = (value or DEFAULT_ANALYSIS_STRATEGY).strip().upper()
    if strategy not in ANALYSIS_STRATEGIES:
        raise ValueError(f"invalid analysis strategy: {value}")
    return strategy


def _metadata(value: dict[str, Any] | None) -> dict[str, Any]:
    return dict(value or {})


@dataclass(frozen=True)
class Project:
    project_id: str
    job_id: str
    profile: str
    sport: str
    display_name: str
    status: str
    source_artifact_id: str | None
    created_at: str
    updated_at: str
    parent_project_id: str | None = None
    source_project_id: str | None = None
    reuse_mode: str = ""
    analysis_strategy: str = DEFAULT_ANALYSIS_STRATEGY

    def __post_init__(self) -> None:
        normalize_analysis_strategy(self.analysis_strategy)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class Artifact:
    artifact_id: str
    project_id: str
    artifact_type: str
    path: str
    mime_type: str
    status: str
    parent_artifact_id: str | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = _metadata(self.metadata)
        return data


@dataclass(frozen=True)
class PipelineRun:
    run_id: str
    project_id: str
    stage: str
    status: str
    started_at: str | None
    finished_at: str | None
    progress_current: int | None
    progress_total: int | None
    error_code: str | None
    error_message: str | None
    retry_count: int
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in RUN_STATES:
            raise ValueError(f"invalid pipeline run status: {self.status}")

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = _metadata(self.metadata)
        return data


@dataclass(frozen=True)
class PipelineEvent:
    event_id: str
    run_id: str | None
    project_id: str
    event_type: str
    stage: str
    message: str
    progress_current: int | None
    progress_total: int | None
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["metadata"] = _metadata(self.metadata)
        return data
