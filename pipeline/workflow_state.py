"""Canonical creative workflow state for the server-rendered console."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


TERMINAL_STATES = {"ROUGH_CUT_READY", "FINISH_READY", "BLOCKED", "FAILED", "IDENTITY_CONFIRMATION_REQUIRED"}
ACTIVE_STATES = {"IDENTIFYING", "RESEARCHING", "ALIGNING", "STORY_BUILDING", "EDIT_BUILDING", "RENDERING"}


@dataclass(frozen=True)
class WorkflowState:
    state: str
    current_stage: str
    completed_stages: list[str] = field(default_factory=list)
    rail: list[dict[str, str]] = field(default_factory=list)
    blocked_reason: str = ""
    failure_reason: str = ""
    confirmation_required: bool = False
    updated_at: str = ""
    actions: dict[str, dict[str, Any]] = field(default_factory=dict)
    identity: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_stage": self.current_stage,
            "completed_stages": list(self.completed_stages),
            "blocked_reason": self.blocked_reason,
            "failure_reason": self.failure_reason,
            "confirmation_required": self.confirmation_required,
            "updated_at": self.updated_at,
            "rail": list(self.rail),
            "actions": dict(self.actions),
            "identity": dict(self.identity),
            "active": self.state in ACTIVE_STATES,
            "terminal": self.state in TERMINAL_STATES,
        }


def _latest_run(runtime: dict[str, Any]) -> dict[str, Any]:
    runs = runtime.get("pipeline_runs") if isinstance(runtime.get("pipeline_runs"), list) else []
    return runs[0] if runs else {}


def _availability_count(moments: list[dict[str, Any]]) -> int:
    return len([m for m in moments if (m.get("metadata") or {}).get("availability_status") == "AVAILABLE" and (m.get("metadata") or {}).get("alignment_status") in {"ALIGNED", "VERIFIED"}])


def story_ready_from_usable_moments(count: int) -> bool:
    return int(count or 0) >= 2


def resolve_workflow_state(detail: dict[str, Any], *, analysis: dict[str, Any] | None = None,
                           runtime: dict[str, Any] | None = None) -> WorkflowState:
    runtime = runtime if runtime is not None else (detail.get("runtime") if isinstance(detail.get("runtime"), dict) else {})
    readiness = detail.get("readiness_summary") if isinstance(detail.get("readiness_summary"), dict) else {}
    analysis = analysis or {}
    project = runtime.get("project") if isinstance(runtime.get("project"), dict) else {}
    moments = runtime.get("moments") if isinstance(runtime.get("moments"), list) else []
    research = runtime.get("research") if isinstance(runtime.get("research"), list) else []
    events = runtime.get("research_events") if isinstance(runtime.get("research_events"), list) else []
    stories = runtime.get("stories") if isinstance(runtime.get("stories"), list) else []
    edit_plans = runtime.get("edit_plans") if isinstance(runtime.get("edit_plans"), list) else []
    renders = runtime.get("renders") if isinstance(runtime.get("renders"), list) else []
    edit_plan_summary = runtime.get("edit_plan_summary") if isinstance(runtime.get("edit_plan_summary"), dict) else {}
    render_summary = runtime.get("render_summary") if isinstance(runtime.get("render_summary"), dict) else {}
    artifacts = runtime.get("artifacts") if isinstance(runtime.get("artifacts"), list) else []
    latest_run = _latest_run(runtime)
    run_meta = latest_run.get("metadata") if isinstance(latest_run.get("metadata"), dict) else {}
    source_artifacts = [a for a in artifacts if a.get("artifact_type") == "source_media"]
    source_clock = {}
    for source_artifact in source_artifacts:
        candidate_clock = (source_artifact.get("metadata") or {}).get("source_clock") if isinstance(source_artifact.get("metadata"), dict) else {}
        if isinstance(candidate_clock, dict) and candidate_clock.get("status") == "NEEDS_OPERATOR":
            source_clock = candidate_clock
            break
    if not source_clock and source_artifacts:
        source_clock = (source_artifacts[0].get("metadata") or {}).get("source_clock") if isinstance(source_artifacts[0].get("metadata"), dict) else {}
    clock_needs_operator = isinstance(source_clock, dict) and source_clock.get("status") == "NEEDS_OPERATOR"

    source_ready = bool(readiness.get("source_ready") or project.get("source_artifact_id") or any(a.get("artifact_type") == "source_media" for a in artifacts))
    rights_cleared = bool(readiness.get("rights_cleared"))
    execution_ready = bool(readiness.get("execution_ready"))
    usable_moment_count = _availability_count(moments)
    has_story = bool(stories)
    has_edit_plan = bool(edit_plans) or int(edit_plan_summary.get("edit_plan_count") or 0) > 0
    has_rough_cut = any((r.get("metadata") or {}).get("preview") for r in renders) or int(render_summary.get("ready_render_count") or 0) > 0
    has_handoff = any(a.get("artifact_type") == "chatcut_handoff" for a in artifacts)

    blocked_reason = ""
    failure_reason = ""
    confirmation_required = False
    identity = run_meta.get("identity_candidate") if isinstance(run_meta.get("identity_candidate"), dict) else {}
    if not identity:
        identity = run_meta.get("candidate") if isinstance(run_meta.get("candidate"), dict) else {}

    run_status = str(latest_run.get("status") or "")
    run_stage = str(latest_run.get("stage") or "")
    if run_status == "FAILED":
        state = "FAILED"
        failure_reason = str(latest_run.get("error_message") or analysis.get("analysis_error") or "Workflow failed.")
    elif run_status == "BLOCKED" or str(run_meta.get("status") or "") in {"NEEDS_CONFIRMATION", "NEEDS_HINT"}:
        state = "IDENTITY_CONFIRMATION_REQUIRED" if str(run_meta.get("status") or "") == "NEEDS_CONFIRMATION" else "BLOCKED"
        confirmation_required = state == "IDENTITY_CONFIRMATION_REQUIRED"
        blocked_reason = str(latest_run.get("error_message") or run_meta.get("blocked_reason") or "Action required before continuing.")
    elif not source_ready:
        state = "BLOCKED"
        blocked_reason = "Source media is not registered."
    elif not rights_cleared and not execution_ready:
        state = "BLOCKED"
        blocked_reason = "Rights must be confirmed before analysis."
    elif run_status == "RUNNING":
        stage_map = {"identify": "IDENTIFYING", "research": "RESEARCHING", "align": "ALIGNING", "story": "STORY_BUILDING", "edit": "EDIT_BUILDING", "render": "RENDERING"}
        analysis_stage = str(analysis.get("analysis_stage") or "").upper()
        state = "ALIGNING" if analysis_stage == "ALIGNING" else stage_map.get(run_stage, "IDENTIFYING")
    elif clock_needs_operator:
        state = "ALIGNING"
        blocked_reason = "Review Kickoff"
    elif has_handoff:
        state = "FINISH_READY"
    elif has_rough_cut:
        state = "ROUGH_CUT_READY"
    elif has_edit_plan:
        state = "EDIT_READY"
    elif has_story:
        state = "STORY_READY"
    elif usable_moment_count:
        state = "MOMENTS_READY"
    elif research or events:
        state = "RESEARCH_READY"
    else:
        state = "SOURCE_READY"

    completed: list[str] = []
    if source_ready:
        completed.append("Source ready")
    if identity:
        completed.append("Match identified")
    if research or events:
        completed.append("Research ready")
    if usable_moment_count:
        completed.append("Moments ready")
    if has_story:
        completed.append("Story ready")
    if has_edit_plan:
        completed.append("Edit ready")
    if has_rough_cut:
        completed.append("Rough cut ready")
    if has_handoff:
        completed.append("Finish ready")

    rail_specs = [("Match", source_ready and (bool(identity) or bool(research) or state not in {"SOURCE_READY", "BLOCKED"})), ("Moments", usable_moment_count > 0), ("Story", has_story), ("Cut", has_edit_plan), ("Watch", has_rough_cut), ("Finish", has_handoff)]
    current_by_state = {
        "SOURCE_READY": "Match", "IDENTIFYING": "Match", "IDENTITY_CONFIRMATION_REQUIRED": "Match", "RESEARCHING": "Match", "RESEARCH_READY": "Moments", "ALIGNING": "Moments", "MOMENTS_READY": "Story", "STORY_BUILDING": "Story", "STORY_READY": "Cut", "EDIT_BUILDING": "Cut", "EDIT_READY": "Watch", "RENDERING": "Watch", "ROUGH_CUT_READY": "Finish", "FINISH_READY": "Finish", "BLOCKED": "Match", "FAILED": "Match",
    }
    current_label = current_by_state.get(state, "Match")
    rail = []
    for label, complete in rail_specs:
        if complete:
            rail_state = "COMPLETE"
        elif label == current_label:
            rail_state = "CURRENT"
        else:
            rail_state = "LOCKED"
        rail.append({"label": label, "state": rail_state})

    actions = {
        "find_story": {"enabled": story_ready_from_usable_moments(usable_moment_count), "reason": "requires at least 2 usable Moments" if usable_moment_count == 1 else ("requires usable Moments" if usable_moment_count == 0 else "")},
        "build_cut": {"enabled": has_story, "reason": "requires Story" if not has_story else ""},
        "generate_rough_cut": {"enabled": has_edit_plan, "reason": "requires EditPlan" if not has_edit_plan else ""},
        "finish": {"enabled": has_rough_cut or has_handoff, "reason": "requires rough cut/package" if not (has_rough_cut or has_handoff) else ""},
        "retry_research": {"enabled": state in {"FAILED", "BLOCKED"}, "reason": ""},
        "review_kickoff": {"enabled": clock_needs_operator, "reason": "Confirm kickoff once to improve moment locations" if clock_needs_operator else ""},
    }
    return WorkflowState(
        state=state,
        current_stage=current_label,
        completed_stages=completed,
        rail=rail,
        blocked_reason=blocked_reason,
        failure_reason=failure_reason,
        confirmation_required=confirmation_required,
        updated_at=str(project.get("updated_at") or latest_run.get("finished_at") or latest_run.get("started_at") or ""),
        actions=actions,
        identity=identity,
    )
