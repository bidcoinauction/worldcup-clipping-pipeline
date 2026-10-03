"""Operator Console — service layer.

Non-technical surface over the pilot job system and the sport/project
registry. Provides read-only queries for projects (jobs), available sports,
and project detail. Write operations use pilot service functions directly
rather than shelling into CLI scripts.

Read-only and side-effect free except for the explicit write helpers
(create_project, transition_project, analyze_project) which delegate to
pilot or detection services.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from pipeline.configurator import (
    registered_project_profiles,
    resolve_project_profile,
    default_project_profile,
)
from pipeline.pilot import (
    JobExistsError,
    JobNotFoundError,
    JobPathError,
    JobRecordError,
    JobTransitionError,
    JobRevisionError,
    allowed_next_states,
    append_event,
    create_job,
    default_jobs_dir,
    job_id_for_intake,
    list_jobs,
    pilot_readiness_report,
    read_job,
    show_job,
    transition_job,
    validate_intake,
)
from pipeline.detection import (
    analyze_project as _detect_analyze_project,
    analysis_supported,
    profile_capabilities,
    read_analysis_state,
    recover_interrupted_analysis,
    read_moments,
    STAGES,
    ANALYSIS_STATES,
)
from pipeline.transcription import (
    validate_source,
    find_existing_transcript,
    resolve_transcript_path_from_root,
    is_transcript_valid,
)
from pipeline.story_engine import (
    generate_story_suggestions as _story_generate,
    read_story_suggestions,
    read_story_state,
)
from pipeline.edit_brief import (
    generate_edit_brief as _brief_generate,
    read_edit_brief,
    read_brief_state,
    list_edit_briefs,
    FORMAT_TREATMENTS,
)
from pipeline.edl import (
    build_edl as _edl_build,
    read_edl,
    read_edl_state,
    list_edls,
)
from pipeline.rendering import (
    render_edl as _render_edl,
    read_render_state,
    list_render_capabilities,
    CAPABILITY_REGISTRY,
)

# ── Read-only queries ────────────────────────────────────────────────────────


def list_available_sports() -> list[dict]:
    """Return registered sports with metadata for the console.

    Each entry contains: ``name``, ``display_name``, ``production_safe``,
    ``analysis_supported``, ``default``.
    """
    profiles = registered_project_profiles()
    default_name = default_project_profile()
    results: list[dict] = []
    for profile in profiles:
        data = resolve_project_profile(profile)
        caps = profile_capabilities(profile)
        results.append({
            "name": profile,
            "display_name": data.get("display_name", profile.title()),
            "production_safe": data.get("production_capable", False),
            "analysis_supported": caps["analysis_supported"],
            "default": profile == default_name,
        })
    return results


def list_projects(jobs_dir: str | Path | None = None) -> list[dict]:
    """List all project (job) records for the console dashboard.

    Returns a list of dicts with: ``job_id``, ``current_state``,
    ``project_id``, ``created_at``, ``pilot_id``, ``source_id``.
    """
    rows = list_jobs(jobs_dir=jobs_dir)
    enriched: list[dict] = []
    for row in rows:
        try:
            job = read_job(row["job_id"], jobs_dir=jobs_dir)
            enriched.append({
                "job_id": job.get("job_id", row["job_id"]),
                "current_state": job.get("current_state", row.get("current_state", "")),
                "project_id": job.get("project_id", ""),
                "created_at": job.get("created_at", ""),
                "pilot_id": job.get("pilot_id", ""),
                "source_id": job.get("source_id", ""),
            })
        except (JobNotFoundError, JobRecordError):
            enriched.append({
                "job_id": row.get("job_id", ""),
                "current_state": row.get("current_state", ""),
                "project_id": "",
                "created_at": "",
                "pilot_id": "",
                "source_id": "",
            })
    return enriched


def get_project(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return full project detail for the console workspace view.

    Contains job record, allowed next states, event history, and readiness
    summary. Raises :class:`JobNotFoundError` if the job_id does not exist.
    """
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    return show_job(job_id, jobs_dir=jobs_dir)


def get_project_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return operational readiness for a single project (job)."""
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    report = pilot_readiness_report(job_id, jobs_dir=jobs_dir)
    if not report.get("jobs"):
        raise JobNotFoundError(f"job '{job_id}' not found in readiness report")
    return report["jobs"][0]


def _recover_analysis_if_needed(job_id: str, jobs_dir: str | Path | None = None) -> None:
    """Reconcile orphaned analysis state on operator read paths.

    Analysis runs in the console process, so a persisted ``RUNNING`` state can
    only mean an interrupted run. Never auto-retries expensive analysis.
    """
    try:
        recover_interrupted_analysis(job_id, jobs_dir=jobs_dir)
    except Exception:
        pass


# ── Intake validation ────────────────────────────────────────────────────────


def validate_project_intake(intake_data: dict, *, intake_root: str | None = None,
                            check_source: bool = True, check_rights: bool = True) -> dict:
    """Validate an intake dictionary without creating a job.

    Returns the readiness report with: ``structurally_valid``,
    ``config_references_valid``, ``source_ready``, ``rights_cleared``,
    ``execution_ready``, ``issues``.
    """
    return validate_intake(
        intake_data,
        intake_root=intake_root,
        check_source=check_source,
        check_rights=check_rights,
    )


# ── Write operations (thin adapters over pilot service layer) ────────────────


def _write_intake_manifest(intake_data: dict, intake_path: Path) -> None:
    intake_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = intake_path.with_name(f".{intake_path.name}.tmp")
    tmp_path.write_text(json.dumps(intake_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp_path, intake_path)


def _default_intake_path(intake_data: dict, jobs_dir: str | Path | None) -> Path | None:
    job_id = job_id_for_intake(intake_data)
    if not job_id:
        return None
    jobs_root = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    return jobs_root.parent / "intakes" / f"{job_id}.json"


def create_project(intake_data: dict, *, intake_path: str | Path | None = None,
                   operator: str | None = None, source: str = "operator_console",
                   jobs_dir: str | Path | None = None) -> dict:
    """Create a new project (job) from an intake dictionary.

    Delegates to :func:`pipeline.pilot.create_job`. Raises
    :class:`JobExistsError` if the derived job_id already exists.
    """
    intake_path_to_store = Path(intake_path) if intake_path else _default_intake_path(intake_data, jobs_dir)
    intake_path_obj = str(intake_path_to_store.resolve()) if intake_path_to_store else None
    if intake_path_to_store is not None:
        _write_intake_manifest(intake_data, Path(intake_path_obj))
    return create_job(
        intake_data,
        intake_path=intake_path_obj,
        operator=operator,
        source=source,
        jobs_dir=jobs_dir,
    )


def refresh_project_readiness(job_id: str, *, operator: str | None = None,
                              jobs_dir: str | Path | None = None,
                              intake_root: str | None = None,
                              source: str = "operator_console.readiness") -> dict:
    """Re-evaluate stored intake and transition to READY when rules allow it."""
    job = read_job(job_id, jobs_dir=jobs_dir)
    intake_path = job.get("intake_manifest_path", "")
    if not isinstance(intake_path, str) or not intake_path.strip():
        raise JobRecordError(f"job '{job_id}' has no intake manifest to revalidate")
    intake = json.loads(Path(intake_path).read_text(encoding="utf-8"))
    report = validate_intake(intake, intake_root=intake_root, check_source=True, check_rights=True)
    current = job.get("current_state", "")
    if report["execution_ready"] and current != "READY" and "READY" in allowed_next_states(current):
        metadata = {"reason": "Stored intake is execution-ready after rights confirmation."}
        if operator:
            metadata["operator"] = operator
        return transition_job(
            job_id,
            "READY",
            metadata=metadata,
            jobs_dir=jobs_dir,
            intake_root=intake_root,
            source=source,
        )
    return job


def confirm_project_rights(job_id: str, *, confirmation_statement: str,
                           confirmed_by: str, confirmation_date: str,
                           permitted_uses: list[str] | None = None,
                           operator: str | None = None,
                           jobs_dir: str | Path | None = None,
                           intake_root: str | None = None) -> dict:
    """Persist explicit rights confirmation, then derive lifecycle readiness."""
    job = read_job(job_id, jobs_dir=jobs_dir)
    intake_path = job.get("intake_manifest_path", "")
    if not isinstance(intake_path, str) or not intake_path.strip():
        raise JobRecordError(f"job '{job_id}' has no intake manifest to update")
    path = Path(intake_path)
    intake = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(intake, dict):
        raise JobRecordError(f"job '{job_id}' intake manifest root must be an object")

    rights = intake.get("rights") if isinstance(intake.get("rights"), dict) else {}
    rights.update({
        "status": "CONFIRMED",
        "confirmation_statement": confirmation_statement.strip(),
        "confirmed_by": confirmed_by.strip(),
        "confirmation_date": confirmation_date.strip(),
        "permitted_uses": permitted_uses or ["clip", "store", "review", "delivery"],
    })
    intake["rights"] = rights
    report = validate_intake(intake, intake_root=intake_root, check_source=True, check_rights=True)
    if not report["structurally_valid"] or not report["config_references_valid"] or not report["rights_cleared"]:
        codes = ", ".join(report["validation_codes"])
        raise JobRecordError(f"rights confirmation did not produce a valid rights-ready intake: {codes}")

    _write_intake_manifest(intake, path)
    append_event(
        job_id,
        "RIGHTS_CONFIRMED",
        previous_state=job.get("current_state", ""),
        new_state=job.get("current_state", ""),
        message="Rights confirmation persisted; readiness will be re-evaluated.",
        related_codes=report["validation_codes"],
        operator=operator,
        source="operator_console.rights",
        jobs_dir=jobs_dir,
    )
    return refresh_project_readiness(
        job_id,
        operator=operator,
        jobs_dir=jobs_dir,
        intake_root=intake_root,
        source="operator_console.rights",
    )


def transition_project(job_id: str, target_state: str, *,
                       metadata: dict | None = None,
                       artifact_references: list[str] | None = None,
                       expected_revision: int | None = None,
                       jobs_dir: str | Path | None = None,
                       intake_root: str | None = None,
                       source: str = "operator_console") -> dict:
    """Transition a project (job) to a new state.

    Delegates to :func:`pipeline.pilot.transition_job`. Raises
    :class:`JobTransitionError` for invalid transitions or
    :class:`JobRevisionError` for stale revisions.
    """
    return transition_job(
        job_id,
        target_state,
        metadata=metadata,
        artifact_references=artifact_references,
        expected_revision=expected_revision,
        jobs_dir=jobs_dir,
        intake_root=intake_root,
        source=source,
    )


def project_transitions(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return allowed next states for a project."""
    job = read_job(job_id, jobs_dir=jobs_dir)
    current = job.get("current_state", "")
    return {
        "job_id": job_id,
        "current_state": current,
        "allowed_next_states": allowed_next_states(current),
    }


# ── Analysis ─────────────────────────────────────────────────────────────────


def analyze_project(job_id: str, *, jobs_dir: str | Path | None = None,
                    provider: str | None = None, model: str | None = None,
                    dry_run: bool = False) -> dict:
    """Run the full analysis workflow for a project.

    Delegates to :func:`pipeline.detection.analyze_project`. Returns a
    result dict with ``ok``, ``status``, and either ``moments_count`` or
    ``error``.
    """
    return _detect_analyze_project(
        job_id,
        jobs_dir=jobs_dir,
        provider=provider,
        model=model,
        dry_run=dry_run,
    )


def get_analysis_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read the analysis state for a project (read-only)."""
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    return read_analysis_state(job_id, jobs_dir=jobs_dir)


def list_moments(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Read detected moments for a project (read-only)."""
    return read_moments(job_id, jobs_dir=jobs_dir)


def get_project_capabilities(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return capability flags for a project's sport/profile."""
    job = read_job(job_id, jobs_dir=jobs_dir)
    project_id = job.get("project_id", "football")
    caps = profile_capabilities(project_id)
    return {
        "job_id": job_id,
        "project_id": project_id,
        **caps,
    }


def get_transcription_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return transcription status for a project.

    Checks both the persisted analysis state and the actual transcript
    file on disk.
    """
    from pipeline.utils import ROOT
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    analysis = read_analysis_state(job_id, jobs_dir=jobs_dir)
    job = read_job(job_id, jobs_dir=jobs_dir)
    project_id = job.get("project_id", "football")

    # Get source file from intake
    source_file = ""
    intake_path = job.get("intake_manifest_path", "")
    if intake_path:
        try:
            intake_data = json.loads(Path(intake_path).read_text(encoding="utf-8"))
            source_file = intake_data.get("media", {}).get("local_file_path", "")
        except Exception:
            pass

    if not source_file:
        return {
            "job_id": job_id,
            "transcription_status": "NEEDS ATTENTION",
            "transcript_path": "",
            "reused": False,
            "segments": 0,
            "error": "No source file configured",
        }

    league = resolve_project_profile(project_id).get("league", "WORLD_CUP")
    transcript_path = find_existing_transcript(source_file, ROOT, league)
    reused = analysis.get("transcription_reused", False)
    persisted_status = analysis.get("transcription_status", "")
    persisted_error = analysis.get("transcription_error", "")

    if transcript_path is not None:
        return {
            "job_id": job_id,
            "transcription_status": "READY",
            "transcript_path": str(transcript_path),
            "reused": reused,
            "segments": analysis.get("analysis_manifest_count", 0),
            "error": "",
        }

    if persisted_status == "FAILED":
        return {
            "job_id": job_id,
            "transcription_status": "NEEDS ATTENTION",
            "transcript_path": "",
            "reused": False,
            "segments": 0,
            "error": persisted_error or analysis.get("analysis_error") or "Transcription failed.",
        }

    if persisted_status == "READY":
        return {
            "job_id": job_id,
            "transcription_status": "READY",
            "transcript_path": analysis.get("transcription_reference", ""),
            "reused": reused,
            "segments": analysis.get("analysis_manifest_count", 0),
            "error": "",
        }

    # Check if analysis is running (transcription may be in progress)
    analysis_status = analysis.get("analysis_status", "")
    analysis_stage = analysis.get("analysis_stage", "")
    if persisted_status == "RUNNING" or (analysis_status == "RUNNING" and analysis_stage == "TRANSCRIBING"):
        return {
            "job_id": job_id,
            "transcription_status": "RUNNING",
            "transcript_path": "",
            "reused": False,
            "segments": 0,
            "error": "",
        }

    return {
        "job_id": job_id,
        "transcription_status": "WAITING",
        "transcript_path": "",
        "reused": False,
        "segments": 0,
        "error": "",
    }


# ── Stories ──────────────────────────────────────────────────────────────────


def generate_stories(job_id: str, *, jobs_dir: str | Path | None = None,
                     provider: str | None = None, model: str | None = None,
                     dry_run: bool = False) -> dict:
    """Generate story suggestions for a project.

    Delegates to :func:`pipeline.story_engine.generate_story_suggestions`.
    """
    return _story_generate(
        job_id,
        jobs_dir=jobs_dir,
        provider=provider,
        model=model,
        dry_run=dry_run,
    )


def get_story_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read the story generation state for a project (read-only)."""
    return read_story_state(job_id, jobs_dir=jobs_dir)


def list_stories(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Read story suggestions for a project (read-only)."""
    return read_story_suggestions(job_id, jobs_dir=jobs_dir)


# ── Edit Briefs ──────────────────────────────────────────────────────────────


def generate_brief(job_id: str, story_id: str, format_treatment: str,
                   *, jobs_dir: str | Path | None = None,
                   provider: str | None = None, model: str | None = None,
                   dry_run: bool = False) -> dict:
    """Generate an edit brief from a story.

    Delegates to :func:`pipeline.edit_brief.generate_edit_brief`.
    """
    return _brief_generate(
        job_id, story_id, format_treatment,
        jobs_dir=jobs_dir,
        provider=provider,
        model=model,
        dry_run=dry_run,
    )


def get_brief(job_id: str, story_id: str, fmt: str,
              jobs_dir: str | Path | None = None) -> dict | None:
    """Read an edit brief (read-only)."""
    return read_edit_brief(job_id, story_id, fmt, jobs_dir=jobs_dir)


def get_brief_status(job_id: str, story_id: str,
                     jobs_dir: str | Path | None = None) -> dict:
    """Read the edit-brief generation state for a story."""
    return read_brief_state(job_id, story_id, jobs_dir=jobs_dir)


def list_all_briefs(job_id: str, story_id: str | None = None,
                    jobs_dir: str | Path | None = None) -> list[dict]:
    """List edit briefs for a project."""
    return list_edit_briefs(job_id, story_id=story_id, jobs_dir=jobs_dir)


# ── EDL / Timeline ──────────────────────────────────────────────────────────


def build_timeline(job_id: str, story_id: str, fmt: str,
                   *, jobs_dir: str | Path | None = None,
                   source_duration: float | None = None,
                   dry_run: bool = False) -> dict:
    """Build an EDL timeline from an approved Edit Brief.

    Delegates to :func:`pipeline.edl.build_edl`.
    """
    return _edl_build(
        job_id, story_id, fmt,
        jobs_dir=jobs_dir,
        source_duration=source_duration,
        dry_run=dry_run,
    )


def get_edl(job_id: str, story_id: str, fmt: str,
            jobs_dir: str | Path | None = None) -> dict | None:
    """Read an EDL artifact (read-only)."""
    return read_edl(job_id, story_id, fmt, jobs_dir=jobs_dir)


def get_edl_status(job_id: str, story_id: str,
                   jobs_dir: str | Path | None = None) -> dict:
    """Read the EDL generation state for a story."""
    return read_edl_state(job_id, story_id, jobs_dir=jobs_dir)


def list_all_edls(job_id: str, story_id: str | None = None,
                  jobs_dir: str | Path | None = None) -> list[dict]:
    """List EDLs for a project."""
    return list_edls(job_id, story_id=story_id, jobs_dir=jobs_dir)


# ── Rendering ────────────────────────────────────────────────────────────────


def render_rough_cut(job_id: str, story_id: str, fmt: str,
                     *, jobs_dir: str | Path | None = None,
                     timeout: int = 300,
                     dry_run: bool = False,
                     mode: str = "REFERENCE") -> dict:
    """Render an EDL into a playable rough cut.

    mode: "REFERENCE" (clean assembly) or "EDITORIAL" (executes supported effects).

    Delegates to :func:`pipeline.rendering.render_edl`.
    """
    return _render_edl(
        job_id, story_id, fmt,
        jobs_dir=jobs_dir,
        timeout=timeout,
        dry_run=dry_run,
        mode=mode,
    )


def get_render_status(job_id: str, story_id: str, fmt: str,
                      jobs_dir: str | Path | None = None,
                      mode: str = "REFERENCE") -> dict | None:
    """Read the render state for a story/format/mode."""
    return read_render_state(job_id, story_id, fmt, jobs_dir=jobs_dir, mode=mode)


def get_render_capabilities() -> dict:
    """Return the renderer capability registry."""
    return list_render_capabilities()
