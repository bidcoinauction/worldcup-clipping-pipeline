from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .runtime_service import append_pipeline_event, get_project, list_project_edit_briefs, list_project_moments, list_project_research, list_project_stories, list_story_edit_plans


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _response(project_id: str, action: str, ok: bool, **extra: Any) -> dict[str, Any]:
    state = extra.pop("state", None)
    result = {"ok": ok, "action": action, **extra}
    if state:
        result["state"] = state
    if ok and "redirect" not in result:
        result["redirect"] = f"/projects/{project_id}"
    return result


def _log(project_id: str, action: str, event_type: str, *, error_code: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    try:
        append_pipeline_event(
            project_id=project_id,
            event_type=event_type,
            stage="action",
            message=action,
            metadata={"project_id": project_id, "action": action, "at": _now(), **({"error_code": error_code} if error_code else {}), **(metadata or {})},
        )
    except Exception:
        pass


def _first_story(project_id: str):
    stories = list_project_stories(project_id)
    return stories[0] if stories else None


def _first_edit_plan(project_id: str):
    for story in list_project_stories(project_id):
        plans = list_story_edit_plans(story.story_id)
        if plans:
            return plans[0]
    return None


def _reviewable_moments(project_id: str):
    return sorted(
        [m for m in list_project_moments(project_id) if (m.metadata or {}).get("availability_status") == "AVAILABLE" and (m.metadata or {}).get("alignment_status") == "ESTIMATED"],
        key=lambda m: ((m.metadata or {}).get("estimated_match_seconds") if (m.metadata or {}).get("estimated_match_seconds") is not None else float((m.metadata or {}).get("match_minute") or 999) * 60.0, m.moment_id),
    )


def _format_time(seconds: float | int | None) -> str:
    if seconds is None:
        return "—"
    total = int(float(seconds))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def _moment_payload(moment) -> dict[str, Any] | None:
    if moment is None:
        return None
    meta = moment.metadata or {}
    return {
        "moment_id": moment.moment_id,
        "source_artifact_id": moment.source_artifact_id,
        "cursor_seconds": moment.peak_seconds,
        "review_cursor": moment.peak_seconds,
        "formatted_cursor": _format_time(moment.peak_seconds),
        "match_minute": meta.get("match_minute"),
        "availability_status": meta.get("availability_status"),
        "alignment_status": meta.get("alignment_status"),
    }


def _default_format() -> str:
    return "SHORT"


def execute_project_action(project_id: str, action: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = payload or {}
    action = str(action or "").strip().lower()
    _log(project_id, action, "PROJECT_ACTION_STARTED")
    try:
        project = get_project(project_id)
        if project is None:
            return _fail(project_id, action, "PROJECT_NOT_FOUND", "Project not found.")

        if action in {"retry_analysis", "retry_research"}:
            from .operator_console import analyze_project, start_research_first_workflow
            result = start_research_first_workflow(project_id) if action == "retry_research" or project.analysis_strategy == "RESEARCH_FIRST" else analyze_project(project_id)
            if not result.get("ok"):
                return _fail(project_id, action, str(result.get("error_code") or "ACTION_FAILED"), str(result.get("error") or "Retry failed."), result=result)
            return _success(project_id, action, "Retry started", state="ALIGNING", redirect=f"/projects/{project_id}", result=result)

        if action == "seed_moments":
            from .research_service import seed_moments_from_research
            research = list_project_research(project_id)
            if not research:
                return _fail(project_id, action, "RESEARCH_REQUIRED", "Research is required before seeding Moments.")
            count = len(seed_moments_from_research(research[-1].research_id, source_duration_seconds=None))
            return _success(project_id, action, "Moments seeded", state="RESEARCH_READY", redirect=f"/projects/{project_id}#review-moments", count=count)

        if action == "review_kickoff":
            return _success(project_id, action, "Review kickoff", state="ALIGNING", redirect=f"/projects/{project_id}#source-clock-review")

        if action in {"confirm_kickoff", "shift_kickoff_earlier", "shift_kickoff_later"}:
            source_artifact_id = str(payload.get("source_artifact_id") or project.source_artifact_id or "")
            if not source_artifact_id:
                return _fail(project_id, action, "SOURCE_REQUIRED", "Source artifact is required.")
            from .source_alignment import BoundedSourceAlignmentService
            source_action = {"confirm_kickoff": "confirm", "shift_kickoff_earlier": "earlier", "shift_kickoff_later": "later"}[action]
            segment_type = str(payload.get("segment_type") or "FIRST_HALF")
            result = BoundedSourceAlignmentService().confirm_source_anchor(project_id, source_artifact_id, segment_type=segment_type, action=source_action)
            if not result.get("ok"):
                return _fail(project_id, action, "ACTION_FAILED", str(result.get("error") or "Kickoff review failed."), result=result)
            state = "MOMENTS_READY" if source_action == "confirm" else "ALIGNING"
            cursor = result.get("cursor_seconds")
            data = {**result, "review_cursor": cursor, "formatted_cursor": _format_time(cursor), "source_artifact_id": source_artifact_id}
            return _success(project_id, action, "Kickoff updated", state=state, redirect=f"/projects/{project_id}", result=result, data=data)

        if action in {"confirm_moment", "shift_moment_earlier", "shift_moment_later", "mark_moment_not_found"}:
            moment_id = str(payload.get("moment_id") or "")
            if not moment_id:
                return _fail(project_id, action, "MOMENT_REQUIRED", "Moment ID is required.")
            source_action = {"confirm_moment": "confirm", "shift_moment_earlier": "earlier", "shift_moment_later": "later", "mark_moment_not_found": "not_found"}[action]
            from .source_alignment import BoundedSourceAlignmentService
            result = BoundedSourceAlignmentService().confirm_moment_alignment(project_id, moment_id, action=source_action, shift_seconds=5.0)
            if not result.get("ok"):
                return _fail(project_id, action, "ACTION_FAILED", str(result.get("error") or "Moment update failed."), result=result)
            reviewable = _reviewable_moments(project_id)
            next_moment = next((m for m in reviewable if m.moment_id != moment_id), None) if source_action in {"confirm", "not_found"} else next((m for m in reviewable if m.moment_id == moment_id), None)
            usable_count = len([m for m in list_project_moments(project_id) if (m.metadata or {}).get("availability_status") == "AVAILABLE" and (m.metadata or {}).get("alignment_status") in {"ALIGNED", "VERIFIED"}])
            next_payload = _moment_payload(next_moment)
            data = {**result, "review_cursor": result.get("cursor_seconds"), "formatted_cursor": _format_time(result.get("cursor_seconds")), "source_artifact_id": result.get("source_artifact_id"), "next_moment": next_payload, "remaining_review_count": len(reviewable), "review_complete": next_moment is None}
            return _success(project_id, action, "Moment updated", state="MOMENTS_READY", redirect=f"/projects/{project_id}", result=result, data=data, moment=data, next_moment=next_payload, reviewable_count=len(reviewable), remaining_review_count=len(reviewable), review_complete=next_moment is None, usable_moment_count=usable_count)

        if action == "find_story":
            usable = [m for m in list_project_moments(project_id) if (m.metadata or {}).get("availability_status") == "AVAILABLE" and (m.metadata or {}).get("alignment_status") in {"ALIGNED", "VERIFIED"}]
            if len(usable) < 2:
                return _fail(project_id, action, "INSUFFICIENT_MOMENTS", "Find Story requires at least 2 usable Moments.")
            from .operator_console import generate_stories
            result = generate_stories(project_id)
            if not result.get("ok"):
                return _fail(project_id, action, str(result.get("error_code") or "STORY_FAILED"), str(result.get("error") or "Could not build story."), result=result)
            return _success(project_id, action, "Story built", state="STORY_READY", redirect=f"/projects/{project_id}#stories", result=result)

        if action == "build_cut":
            story_id = str(payload.get("story_id") or "")
            story = _first_story(project_id) if not story_id else None
            if story_id:
                from .runtime_service import get_story
                story = get_story(story_id)
            if story is None:
                return _fail(project_id, action, "STORY_REQUIRED", "Story is required before building a cut.")
            fmt = str(payload.get("format") or _default_format())
            if story.status != "APPROVED":
                from .runtime_service import update_story_status
                story = update_story_status(story.story_id, "APPROVED")
            from .operator_console import generate_canonical_edit_brief, generate_edit_plan
            briefs = [b for b in list_project_edit_briefs(project_id) if b.story_id == story.story_id and b.format_treatment == fmt and b.status == "READY"]
            if briefs:
                brief_id = briefs[0].edit_brief_id
            else:
                brief_result = generate_canonical_edit_brief(project_id, story.story_id, fmt, dry_run=bool(payload.get("dry_run")))
                if not brief_result.get("ok"):
                    return _fail(project_id, action, str(brief_result.get("error_code") or "BRIEF_FAILED"), str(brief_result.get("error") or "Could not build edit brief."), result=brief_result)
                briefs = [b for b in list_project_edit_briefs(project_id) if b.story_id == story.story_id and b.format_treatment == fmt]
                brief_id = briefs[0].edit_brief_id if briefs else ""
            result = generate_edit_plan(project_id, story.story_id, brief_id)
            if not result.get("ok"):
                return _fail(project_id, action, "EDIT_PLAN_FAILED", str(result.get("error") or "Could not build cut."), result=result)
            return _success(project_id, action, "Cut built", state="EDIT_READY", redirect=f"/projects/{project_id}#rough-cut", result=result)

        if action == "render_rough_cut":
            plan_id = str(payload.get("edit_plan_id") or "")
            plan = _first_edit_plan(project_id) if not plan_id else None
            if plan_id:
                from .runtime_service import get_edit_plan
                plan = get_edit_plan(plan_id)
            if plan is None:
                return _fail(project_id, action, "EDIT_PLAN_REQUIRED", "EditPlan is required before rendering.")
            from .operator_console import generate_editplan_preview
            result = generate_editplan_preview(plan.edit_plan_id, dry_run=bool(payload.get("dry_run")))
            if not result.get("ok"):
                return _fail(project_id, action, "RENDER_FAILED", str(result.get("error") or "Could not render rough cut."), result=result)
            return _success(project_id, action, "Rough cut rendered", state="ROUGH_CUT_READY", redirect=f"/projects/{project_id}#rough-cut", result=result)

        if action == "finish_cut":
            plan_id = str(payload.get("edit_plan_id") or "")
            plan = _first_edit_plan(project_id) if not plan_id else None
            if plan_id:
                from .runtime_service import get_edit_plan
                plan = get_edit_plan(plan_id)
            if plan is None:
                return _fail(project_id, action, "EDIT_PLAN_REQUIRED", "EditPlan is required before finishing.")
            from .operator_console import prepare_chatcut_handoff
            result = prepare_chatcut_handoff(plan.edit_plan_id)
            if not result.get("ok"):
                return _fail(project_id, action, "FINISH_FAILED", str(result.get("error") or "Could not prepare package."), result=result)
            return _success(project_id, action, "Creative package ready", state="FINISH_READY", redirect=f"/edit-plans/{plan.edit_plan_id}/handoff", result=result)

        return _fail(project_id, action, "UNKNOWN_ACTION", f"Unknown action: {action}")
    except Exception as exc:
        return _fail(project_id, action, "ACTION_EXCEPTION", str(exc))


def _success(project_id: str, action: str, message: str, **extra: Any) -> dict[str, Any]:
    _log(project_id, action, "PROJECT_ACTION_SUCCEEDED", metadata={"completed_at": _now()})
    if "data" not in extra:
        extra["data"] = extra.get("result", {}) if isinstance(extra.get("result"), dict) else {}
    return _response(project_id, action, True, message=message, **extra)


def _fail(project_id: str, action: str, code: str, error: str, **extra: Any) -> dict[str, Any]:
    _log(project_id, action, "PROJECT_ACTION_FAILED", error_code=code, metadata={"completed_at": _now(), "error": error})
    safe_message = "Clipper couldn't continue. Try again."
    return _response(project_id, action, False, code=code, error=error, message=safe_message, **extra)
