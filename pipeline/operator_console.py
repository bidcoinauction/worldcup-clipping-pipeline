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

import copy
import json
import os
import csv
import uuid
from pathlib import Path
import sqlite3

from pipeline.configurator import (
    registered_project_profiles,
    resolve_project_profile,
    default_project_profile,
)
from pipeline.config import get_provider
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
    _ACTIVE_ANALYSES,
    _analysis_preflight,
    _ensure_transcription,
    _moments_path,
    _safe_error_message,
    _write_moments,
    analysis_supported,
    build_clip_manifest,
    build_prompt,
    profile_capabilities,
    read_analysis_state,
    recover_interrupted_analysis,
    read_moments,
    run_detection_call,
    update_analysis_state,
    STAGE_FINDING,
    STAGE_PREPARING,
    STAGE_PREPARING_RESULTS,
    STAGE_TRANSCRIBING,
    STAGE_UNDERSTANDING,
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
from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.runtime_service import (
    ANALYSIS_STAGE,
    REUSE_MODES,
    add_story_moment,
    append_pipeline_event,
    create_pipeline_run,
    duplicate_project as runtime_duplicate_project,
    effective_analysis_strategy,
    find_story_by_original_id,
    get_edit_brief,
    get_edl as get_runtime_edl,
    get_latest_pipeline_run,
    get_project_runtime_summary,
    get_render,
    get_story_runtime_summary,
    index_existing_project,
    list_indexed_or_existing_projects,
    list_project_moments,
    list_project_research,
    list_project_research_events,
    list_story_moments,
    mark_interrupted_run,
    register_artifact,
    remove_story_moment,
    update_pipeline_run,
    update_project_moment_review_state,
    update_render_review_state,
    update_project_analysis_strategy,
    update_story_moment,
    update_story_status,
    upsert_edit_brief,
    upsert_edl,
    upsert_export_package,
    upsert_moment,
    upsert_render,
)
from pipeline.channel_models import list_channel_presets, resolve_channel_preset, validate_preset_compatibility
from pipeline.story_adapter import canonical_story_id
from pipeline.story_models import STORY_STATUSES
from pipeline.provider_service import DetectionProviderUnavailable, EditProviderUnavailable, StoryProviderUnavailable, require_detection_provider, require_edit_provider, require_story_provider
from pipeline.batch_service import get_batch_runtime_summary as _batch_summary, list_batch_operations as _list_batches, run_analysis_batch as _run_batch
from pipeline.research_service import load_research_fixture, persist_research_fixture, seed_moments_from_research

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
    try:
        indexed = list_indexed_or_existing_projects(jobs_dir=jobs_dir)
    except (OSError, sqlite3.Error, JobNotFoundError, JobRecordError):
        indexed = []
    if indexed:
        rows: list[dict] = []
        for project in indexed:
            try:
                job = read_job(project.job_id, jobs_dir=jobs_dir)
            except (JobNotFoundError, JobRecordError):
                job = {}
            rows.append({
                "job_id": project.job_id,
                "current_state": project.status,
                "project_id": project.profile,
                "created_at": project.created_at,
                "updated_at": project.updated_at,
                "pilot_id": job.get("pilot_id", project.display_name),
                "source_id": job.get("source_id", ""),
                "analysis_strategy": project.analysis_strategy,
                "derived": bool(project.parent_project_id),
                "reuse_mode": project.reuse_mode,
            })
        return rows

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
    project = show_job(job_id, jobs_dir=jobs_dir)
    try:
        project["runtime"] = get_project_runtime_summary(job_id, jobs_dir=jobs_dir)
    except (OSError, sqlite3.Error, JobNotFoundError, JobRecordError, ValueError):
        project["runtime"] = {"runtime_available": False}
    return project


def get_project_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Return operational readiness for a single project (job)."""
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    report = pilot_readiness_report(job_id, jobs_dir=jobs_dir)
    if not report.get("jobs"):
        raise JobNotFoundError(f"job '{job_id}' not found in readiness report")
    status = report["jobs"][0]
    try:
        status["runtime"] = get_project_runtime_summary(job_id, jobs_dir=jobs_dir)
    except (OSError, sqlite3.Error, JobNotFoundError, JobRecordError, ValueError):
        status["runtime"] = {"runtime_available": False}
    return status


def _recover_analysis_if_needed(job_id: str, jobs_dir: str | Path | None = None) -> None:
    """Reconcile orphaned analysis state on operator read paths.

    Analysis runs in the console process, so a persisted ``RUNNING`` state can
    only mean an interrupted run. Never auto-retries expensive analysis.
    """
    try:
        recover_interrupted_analysis(job_id, jobs_dir=jobs_dir)
        project = index_existing_project(job_id, jobs_dir=jobs_dir)
        latest = get_latest_pipeline_run(project.project_id, stage=ANALYSIS_STAGE)
        if latest and latest.status == "RUNNING" and job_id not in _ACTIVE_ANALYSES:
            mark_interrupted_run(latest.run_id)
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


def duplicate_project(job_id: str, *, display_name: str | None = None, profile: str | None = None,
                      reuse_mode: str = "SOURCE_ANALYSIS_AND_MOMENTS",
                      jobs_dir: str | Path | None = None) -> dict:
    """Duplicate an existing project, reusing source intelligence without recomputing.

    Creates a new pilot job (with its own intake manifest) from the source
    intake, records runtime lineage, reuses source artifacts by reference, and
    clones canonical Moments with review state reset. No media/transcript files
    are copied and no transcription/detection runs.
    """
    if reuse_mode not in REUSE_MODES:
        raise ValueError(f"invalid reuse mode: {reuse_mode}")
    source_project = index_existing_project(job_id, jobs_dir=jobs_dir)
    job = read_job(job_id, jobs_dir=jobs_dir)
    intake_path = job.get("intake_manifest_path", "")
    if not isinstance(intake_path, str) or not intake_path.strip() or not Path(intake_path).exists():
        raise ValueError("source project has no readable intake manifest")
    intake = json.loads(Path(intake_path).read_text(encoding="utf-8"))
    if not isinstance(intake, dict):
        raise ValueError("source intake is not an object")

    target_profile = profile or source_project.profile
    if profile:
        resolved = resolve_project_profile(profile)
        if str(resolved.get("sport") or profile) != source_project.sport:
            raise ValueError(f"cannot reuse '{source_project.sport}' intelligence into incompatible profile '{profile}'")

    suffix = uuid.uuid4().hex[:8]
    new_intake = copy.deepcopy(intake)
    pilot = new_intake.get("pilot") if isinstance(new_intake.get("pilot"), dict) else {}
    media = new_intake.get("media") if isinstance(new_intake.get("media"), dict) else {}
    new_intake["pilot"] = {**pilot, "pilot_id": f"{pilot.get('pilot_id', 'pilot')}_{suffix}"}
    new_intake["media"] = {**media, "source_id": f"{media.get('source_id', 'source')}_{suffix}"}
    config = new_intake.get("configuration") if isinstance(new_intake.get("configuration"), dict) else {}
    config["project"] = target_profile
    new_intake["configuration"] = config

    new_job = create_project(new_intake, operator="console.duplicate", source="console.duplicate", jobs_dir=jobs_dir)
    result = runtime_duplicate_project(
        source_project_id=source_project.project_id,
        new_project_id=new_job["job_id"],
        display_name=display_name or f"{source_project.display_name} (Derived)",
        profile=target_profile,
        sport=source_project.sport,
        reuse_mode=reuse_mode,
    )
    return {**result, "job_id": new_job["job_id"]}


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


def _analysis_error_code(status: str, message: str) -> str:
    text = f"{status} {message}".lower()
    if "transcription" in text:
        return "TRANSCRIPTION_FAILED"
    if "execution-ready" in text or "validation" in text or "intake" in text or "source" in text:
        return "ANALYSIS_PREFLIGHT_FAILED"
    if "support" in text and "not available" in text:
        return "ANALYSIS_UNSUPPORTED"
    return "ANALYSIS_FAILED"


def _append_run_event(run_id: str, project_id: str, event_type: str, message: str = "", **metadata):
    return append_pipeline_event(
        project_id=project_id,
        run_id=run_id,
        event_type=event_type,
        stage=ANALYSIS_STAGE,
        message=message,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )


def _register_analysis_artifact(project_id: str, run_id: str, artifact_type: str, path: str | Path):
    artifact = register_artifact(
        project_id=project_id,
        artifact_type=artifact_type,
        path=path,
        metadata={"stage": ANALYSIS_STAGE, "run_id": run_id},
    )
    _append_run_event(
        run_id,
        project_id,
        "ARTIFACT_REGISTERED",
        f"Registered {artifact_type} artifact.",
        artifact_id=artifact.artifact_id,
        artifact_type=artifact.artifact_type,
        path=artifact.path,
    )
    return artifact


def analyze_project(job_id: str, *, jobs_dir: str | Path | None = None,
                    provider: str | None = None, model: str | None = None,
                    dry_run: bool = False, analysis_strategy: str | None = None) -> dict:
    """Run analysis with durable runtime execution records."""
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    project = index_existing_project(job_id, jobs_dir=jobs_dir_path)
    resolved_strategy = effective_analysis_strategy(project, run_override=analysis_strategy)
    run = create_pipeline_run(
        project_id=project.project_id,
        stage=ANALYSIS_STAGE,
        status="QUEUED",
        metadata={"analysis_strategy": resolved_strategy, "analysis_strategy_override": analysis_strategy},
    )
    _append_run_event(run.run_id, project.project_id, "RUN_QUEUED", "Analysis queued.")

    from .provider_service import detection_model as _detection_model
    resolved_detection_provider = provider or get_provider("detection")
    resolved_detection_model = model or _detection_model()

    def fail(message: str, *, code: str | None = None) -> dict:
        safe = message[:500] if message else "Analysis failed. Check the project details for more information."
        error_code = code or _analysis_error_code("FAILED", safe)
        update_pipeline_run(run.run_id, status="FAILED", error_code=error_code, error_message=safe)
        _append_run_event(run.run_id, project.project_id, "RUN_FAILED", safe, error_code=error_code)
        return {"ok": False, "error": safe, "status": "FAILED", "runtime_run_id": run.run_id,
                "error_code": error_code}

    try:
        job = read_job(job_id, jobs_dir=jobs_dir_path)
        project_id = job.get("project_id", "football")
        if not analysis_supported(project_id):
            message = f"Analysis support for {project_id.title()} is not available yet."
            update_analysis_state(job_id, status="NEEDS ATTENTION", stage="", error=message, jobs_dir=jobs_dir_path)
            return fail(message, code="ANALYSIS_UNSUPPORTED")

        update_pipeline_run(run.run_id, status="RUNNING")
        _append_run_event(run.run_id, project.project_id, "RUN_STARTED", "Analysis started.")

        preflight = _analysis_preflight(job, jobs_dir_path)
        if not preflight["ok"]:
            update_analysis_state(
                job_id,
                status="NEEDS ATTENTION",
                stage="",
                error=preflight["error"],
                jobs_dir=jobs_dir_path,
                **preflight.get("extra", {}),
            )
            return fail(preflight["error"], code="ANALYSIS_PREFLIGHT_FAILED")

        _append_run_event(run.run_id, project.project_id, "SOURCE_VALIDATED", "Source validated for analysis.")
        _ACTIVE_ANALYSES.add(job_id)
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_PREPARING, jobs_dir=jobs_dir_path)

        if resolved_strategy == "RESEARCH_FIRST":
            research_items = list_project_research(project.project_id)
            if not research_items:
                return fail("Research-first analysis requires persisted MatchResearch.", code="RESEARCH_REQUIRED")
            _append_run_event(run.run_id, project.project_id, "RESEARCH_STARTED", "Research-first moment seeding started.",
                              analysis_strategy=resolved_strategy)
            seeded = seed_moments_from_research(research_items[-1].research_id)
            update_analysis_state(
                job_id,
                status="COMPLETE",
                stage="",
                jobs_dir=jobs_dir_path,
                analysis_manifest_count=len(seeded),
            )
            update_pipeline_run(run.run_id, status="SUCCEEDED", metadata={"analysis_strategy": resolved_strategy, "seeded_moments": len(seeded)})
            _append_run_event(run.run_id, project.project_id, "RUN_SUCCEEDED", "Research-first analysis completed.",
                              analysis_strategy=resolved_strategy, seeded_moments=len(seeded))
            return {"ok": True, "status": "COMPLETE", "moments_count": len(seeded), "runtime_run_id": run.run_id,
                    "analysis_strategy": resolved_strategy}

        source_file = preflight["source_file"]
        match_name = preflight["match_name"]
        had_existing_transcript = bool(preflight.get("transcript_reuse") or preflight.get("existing_transcript"))

        update_analysis_state(job_id, status="RUNNING", stage=STAGE_TRANSCRIBING, jobs_dir=jobs_dir_path)
        if had_existing_transcript:
            transcript = _ensure_transcription(source_file, match_name, project_id, jobs_dir_path, job_id)
            _append_run_event(run.run_id, project.project_id, "TRANSCRIPT_REUSED", "Existing transcript reused.")
        else:
            _append_run_event(run.run_id, project.project_id, "TRANSCRIPTION_STARTED", "Transcription started.")
            transcript = _ensure_transcription(source_file, match_name, project_id, jobs_dir_path, job_id)
            _append_run_event(run.run_id, project.project_id, "TRANSCRIPTION_COMPLETED", "Transcription completed.")
        _register_analysis_artifact(project.project_id, run.run_id, "transcript", transcript)

        update_analysis_state(job_id, status="RUNNING", stage=STAGE_UNDERSTANDING, jobs_dir=jobs_dir_path)
        prompt_result = build_prompt(transcript=transcript, match_name=match_name, profile=project_id)

        update_analysis_state(job_id, status="RUNNING", stage=STAGE_FINDING, jobs_dir=jobs_dir_path)
        _append_run_event(run.run_id, project.project_id, "DETECTION_STARTED", "Detection started.",
                          provider=resolved_detection_provider, model=resolved_detection_model)
        clips = None
        require_detection_provider()
        clips = run_detection_call(
            prompt_result["prompt"],
            provider=resolved_detection_provider,
            model=resolved_detection_model,
            dry_run=dry_run,
        )
        _append_run_event(run.run_id, project.project_id, "DETECTION_COMPLETED", "Detection completed.",
                          provider=resolved_detection_provider, model=resolved_detection_model)

        update_analysis_state(job_id, status="RUNNING", stage=STAGE_PREPARING_RESULTS, jobs_dir=jobs_dir_path)
        league = resolve_project_profile(project_id).get("league", "WORLD_CUP")
        manifest_result = build_clip_manifest(
            clips,
            league=league,
            match_name=match_name,
            source_video=Path(source_file).name,
            profile=project_id,
        )
        moments_data = manifest_result["rows"]
        moments_path = _write_moments(job_id, jobs_dir_path, moments_data)
        manifest_path = jobs_dir_path / "ANALYSIS" / f"{job_id}_manifest.csv"
        with manifest_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=manifest_result["fieldnames"])
            writer.writeheader()
            writer.writerows(moments_data)

        update_analysis_state(
            job_id,
            status="COMPLETE",
            stage="",
            jobs_dir=jobs_dir_path,
            analysis_moments_path=str(_moments_path(job_id, jobs_dir_path)),
            analysis_manifest_path=str(manifest_path),
            analysis_manifest_count=len(moments_data),
        )
        moments_artifact = _register_analysis_artifact(project.project_id, run.run_id, "analysis_moments", moments_path)
        _register_analysis_artifact(project.project_id, run.run_id, "analysis_manifest", manifest_path)
        try:
            canonical_moments = adapt_detection_to_moments(
                moments_data,
                project_id=project.project_id,
                source_artifact_id=moments_artifact.artifact_id,
                sport="football",
            )
            for moment in canonical_moments:
                upsert_moment(moment)
        except Exception as exc:
            safe = _safe_error_message(exc)
            return fail(f"Moment normalization failed: {safe}", code="MOMENT_NORMALIZATION_FAILED")
        update_pipeline_run(run.run_id, status="SUCCEEDED")
        _append_run_event(run.run_id, project.project_id, "RUN_SUCCEEDED", "Analysis completed.")
        return {
            "ok": True,
            "status": "COMPLETE",
            "moments_count": len(moments_data),
            "manifest_path": str(manifest_path),
            "profile": project_id,
            "match_name": match_name,
            "runtime_run_id": run.run_id,
        }
    except DetectionProviderUnavailable as exc:
        safe_error = _safe_error_message(exc)
        try:
            update_analysis_state(job_id, status="FAILED", stage="", error=safe_error, jobs_dir=jobs_dir_path)
        except Exception:
            pass
        return fail(safe_error, code="DETECTION_PROVIDER_UNAVAILABLE")
    except Exception as exc:
        safe_error = _safe_error_message(exc)
        try:
            state = read_analysis_state(job_id, jobs_dir=jobs_dir_path)
            extra = {}
            if state.get("analysis_stage") == STAGE_TRANSCRIBING:
                extra = {"transcription_status": "FAILED", "transcription_error": safe_error}
            update_analysis_state(job_id, status="FAILED", stage="", error=safe_error, jobs_dir=jobs_dir_path, **extra)
        except Exception:
            pass
        return fail(safe_error)
    finally:
        _ACTIVE_ANALYSES.discard(job_id)


def get_analysis_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read the analysis state for a project (read-only)."""
    _recover_analysis_if_needed(job_id, jobs_dir=jobs_dir)
    try:
        summary = get_project_runtime_summary(job_id, jobs_dir=jobs_dir)
        latest = summary.get("analysis")
        if latest:
            legacy = read_analysis_state(job_id, jobs_dir=jobs_dir)
            legacy.update({
                "analysis_status": latest.get("analysis_status", latest.get("status", "")),
                "analysis_stage": latest.get("analysis_stage", ""),
                "analysis_error": latest.get("error_message") or "",
                "runtime_status": latest.get("status", ""),
                "runtime_operator_status": latest.get("operator_status", ""),
                "runtime_operator_label": latest.get("operator_label", ""),
                "runtime_run_id": latest.get("run_id", ""),
                "runtime_error_code": latest.get("error_code") or "",
                "analysis_started_at": latest.get("started_at") or "",
                "analysis_completed_at": latest.get("finished_at") or "",
                "runtime_events": summary.get("events", []),
                "runtime_artifacts": summary.get("artifacts", []),
                "runtime_moment_summary": summary.get("moment_summary", {}),
                "analysis_run_count": summary.get("analysis_run_count", 0),
                "previous_run_status": summary.get("previous_run_status"),
            })
            return legacy
    except (OSError, sqlite3.Error, JobNotFoundError, JobRecordError, ValueError):
        pass
    return read_analysis_state(job_id, jobs_dir=jobs_dir)


def list_moments(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Read detected moments for a project (read-only)."""
    return read_moments(job_id, jobs_dir=jobs_dir)


def review_moment(job_id: str, moment_id: str, review_state: str, *, reviewed_by: str | None = None,
                  jobs_dir: str | Path | None = None) -> dict:
    """Update canonical Moment review state for one project-scoped moment."""
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    moment = update_project_moment_review_state(
        project.project_id,
        moment_id,
        review_state,
        reviewed_by=reviewed_by,
    )
    return {"ok": True, "moment": moment.to_dict()}


# ── Batch operations ─────────────────────────────────────────────────────────


def start_analysis_batch(project_ids: list[str], *, operator: str | None = None,
                         jobs_dir: str | Path | None = None) -> dict:
    """Run managed analysis sequentially across multiple projects."""
    return _run_batch(project_ids, operator=operator, jobs_dir=jobs_dir)


def get_batch_detail(batch_id: str) -> dict:
    """Return the batch read model with item statuses."""
    return _batch_summary(batch_id)


def list_recent_batches(limit: int = 25) -> list[dict]:
    """List recent batch operations (read-only)."""
    return [batch.to_dict() for batch in _list_batches(limit=limit)]


# ── Research-first intelligence ──────────────────────────────────────────────


def set_project_analysis_strategy(job_id: str, strategy: str, *, jobs_dir: str | Path | None = None) -> dict:
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    updated = update_project_analysis_strategy(project.project_id, strategy)
    return {"ok": True, "project": updated.to_dict()}


def import_research_fixture(job_id: str, fixture_path: str | Path, *, jobs_dir: str | Path | None = None) -> dict:
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    fixture = load_research_fixture(fixture_path)
    research, events = persist_research_fixture(project.project_id, fixture, source_artifact_id=project.source_artifact_id)
    return {"ok": True, "research": research.to_dict(), "events": [event.to_dict() for event in events]}


def get_project_research(job_id: str, *, jobs_dir: str | Path | None = None) -> dict:
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    event_moments = {
        moment.metadata.get("research_event_id"): moment.to_dict()
        for moment in list_project_moments(project.project_id)
        if moment.metadata.get("origin") == "research" and moment.metadata.get("research_event_id")
    }
    events = []
    for event in list_project_research_events(project.project_id):
        item = event.to_dict()
        moment = event_moments.get(event.event_id)
        if moment:
            item["source_availability"] = {
                "availability_status": moment.get("metadata", {}).get("availability_status"),
                "alignment_status": moment.get("metadata", {}).get("alignment_status"),
                "alignment_reason": moment.get("metadata", {}).get("alignment_reason"),
                "source_duration_seconds": moment.get("metadata", {}).get("source_duration_seconds"),
                "kickoff_media_offset_seconds": moment.get("metadata", {}).get("kickoff_media_offset_seconds"),
                "estimated_match_coverage_start_minute": moment.get("metadata", {}).get("estimated_match_coverage_start_minute"),
                "estimated_match_coverage_end_minute": moment.get("metadata", {}).get("estimated_match_coverage_end_minute"),
            }
        events.append(item)
    return {
        "ok": True,
        "research": [item.to_dict() for item in list_project_research(project.project_id)],
        "events": events,
    }


def seed_project_moments_from_research(job_id: str, research_id: str | None = None, *,
                                        kickoff_media_offset_seconds: float | None = None,
                                        halftime_duration_seconds: float | None = None,
                                        source_duration_seconds: float | None = None,
                                        jobs_dir: str | Path | None = None) -> dict:
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    research_items = list_project_research(project.project_id)
    if not research_items:
        return {"ok": False, "error": "No MatchResearch exists for this project."}
    selected = next((item for item in research_items if item.research_id == research_id), research_items[-1])
    moments = seed_moments_from_research(
        selected.research_id,
        kickoff_media_offset_seconds=kickoff_media_offset_seconds,
        halftime_duration_seconds=halftime_duration_seconds,
        source_duration_seconds=source_duration_seconds,
    )
    return {"ok": True, "moments": [moment.to_dict() for moment in moments], "moments_count": len(moments)}


# ── Workflow status / next action (derived, read-only) ───────────────────────


def get_project_workflow_status(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Derive an operator workflow strip and the primary next action from
    canonical runtime records. No persisted duplicate state."""
    summary = get_project_runtime_summary(job_id, jobs_dir=jobs_dir)
    moment_summary = summary.get("moment_summary", {})
    story_summary = summary.get("story_summary", {})
    edit_summary = summary.get("edit_brief_summary", {})
    edl_summary = summary.get("edl_summary", {})
    render_summary = summary.get("render_summary", {})
    export_summary = summary.get("export_summary", {})
    lineage = summary.get("lineage", {})
    stories = summary.get("stories", [])
    project_info = summary.get("project", {}) if isinstance(summary.get("project"), dict) else {}
    strategy = project_info.get("analysis_strategy") or "TRANSCRIPT_FIRST"

    moments = moment_summary.get("moment_count", 0)
    unreviewed = moment_summary.get("unreviewed_count", 0)
    if strategy == "RESEARCH_FIRST":
        try:
            from pipeline.runtime_service import get_research_event, list_project_moments
            active_moments = [
                moment for moment in list_project_moments(project_info.get("project_id", ""))
                if moment.metadata.get("origin") == "research"
                and moment.metadata.get("availability_status") != "OUTSIDE_SOURCE"
                and (((get_research_event(moment.metadata.get("research_event_id")) or type("_E", (), {"metadata": {}})()).metadata or {}).get("source_availability") or {}).get("availability_status") != "OUTSIDE_SOURCE"
            ]
            moments = len(active_moments)
            unreviewed = sum(1 for moment in active_moments if moment.review_state == "UNREVIEWED")
        except Exception:
            pass
    approved_story = any(s.get("status") == "APPROVED" for s in stories)
    ready_brief = edit_summary.get("ready_edit_brief_count", 0) > 0
    ready_edl = edl_summary.get("ready_edl_count", 0) > 0
    ready_render = render_summary.get("ready_render_count", 0) > 0
    approved_render = render_summary.get("approved_render_count", 0) > 0
    exports = export_summary.get("export_count", 0)
    edit_plan_count = 0
    chatcut_ready = False
    try:
        from pipeline.runtime_service import list_project_artifacts, list_story_edit_plans
        for story in stories:
            edit_plan_count += len(list_story_edit_plans(story.get("story_id", "")))
        chatcut_ready = any(a.metadata.get("edit_plan_id") for a in list_project_artifacts(project_info.get("project_id", ""), artifact_type="chatcut_handoff"))
    except Exception:
        edit_plan_count = 0
        chatcut_ready = False

    analysis_ok = moments > 0 or lineage.get("analysis_reused") is True or story_summary.get("story_count", 0) > 0 \
        or edit_summary.get("edit_brief_count", 0) > 0

    if moments == 0 and not analysis_ok:
        next_action = "Seed Moments" if strategy == "RESEARCH_FIRST" else "Analyze Source"
    elif unreviewed > 0:
        next_action = f"Review {unreviewed} Moments"
    elif moments > 0 and not approved_story:
        next_action = "Approve Story"
    elif approved_story and edit_plan_count == 0 and not ready_brief:
        next_action = "Generate Edit"
    elif edit_plan_count == 0:
        next_action = "Generate Edit"
    elif not ready_render:
        next_action = "Generate Rough Preview"
    elif ready_render and not approved_render:
        next_action = "Review Preview"
    elif approved_render and not chatcut_ready:
        next_action = "Prepare ChatCut Handoff"
    elif chatcut_ready and exports == 0:
        next_action = "Continue Creative Finish / Export"
    else:
        next_action = "Project Complete"

    if strategy == "RESEARCH_FIRST":
        story_state = "COMPLETE" if story_summary.get("story_count", 0) else "NOT STARTED"
        edit_state = "COMPLETE" if edit_plan_count else "NOT STARTED"
        preview_state = "NEEDS ATTENTION" if ready_render and not approved_render else ("COMPLETE" if approved_render else "NOT STARTED")
        chatcut_state = "COMPLETE" if chatcut_ready else "NOT STARTED"
        steps = [
            {"step": "Source", "state": "COMPLETE"},
            {"step": "Research", "state": "COMPLETE" if moments or analysis_ok else "CURRENT"},
            {"step": "Moments", "state": "NEEDS ATTENTION" if unreviewed else ("COMPLETE" if moments else "NOT STARTED")},
            {"step": "Story", "state": story_state},
            {"step": "Edit Plan", "state": edit_state},
            {"step": "Preview", "state": preview_state},
            {"step": "ChatCut", "state": chatcut_state},
            {"step": "Review", "state": "COMPLETE" if approved_render else ("CURRENT" if ready_render else "NOT STARTED")},
            {"step": "Export", "state": "COMPLETE" if exports else ("CURRENT" if chatcut_ready else "NOT STARTED")},
        ]
    else:
        steps = [
            {"step": "Source", "state": "Ready" if summary.get("lineage", {}).get("analysis_reused") or True else "—"},
            {"step": "Analysis", "state": "Complete" if analysis_ok else "Pending"},
            {"step": "Moments", "state": str(moments)},
            {"step": "Story", "state": "Approved" if approved_story else ("Suggested" if story_summary.get("story_count", 0) else "—")},
            {"step": "Edit Brief", "state": "Ready" if ready_brief else "—"},
            {"step": "EDL", "state": "Ready" if ready_edl else "—"},
            {"step": "Rough Cut", "state": "Approved" if approved_render else ("Needs Review" if ready_render else "—")},
            {"step": "Export", "state": str(exports) if exports else "—"},
        ]

    if render_summary.get("failed_render_count", 0) > 0 or edl_summary.get("failed_edl_count", 0) > 0 \
            or edit_summary.get("failed_edit_brief_count", 0) > 0:
        attention = "Failed"
    elif unreviewed > 0:
        attention = "Needs Review"
    elif ready_render and not approved_render:
        attention = "Needs Review"
    elif moments == 0 and not analysis_ok:
        attention = "Not Started"
    elif approved_render and exports == 0:
        attention = "Ready"
    else:
        attention = "Complete"

    current_step = "Source"
    if moments == 0 and not analysis_ok:
        current_step = "Research" if strategy == "RESEARCH_FIRST" else "Analyze"
    elif unreviewed > 0:
        current_step = "Review Moments"
    elif not approved_story and moments > 0:
        current_step = "Build Story"
    elif not ready_brief:
        current_step = "Generate Edit"
    elif not ready_edl:
        current_step = "Prepare Cut"
    elif not ready_render:
        current_step = "Generate Rough Cut"
    elif not approved_render:
        current_step = "Review Rough Cut"
    elif exports == 0:
        current_step = "Create Export"
    else:
        current_step = "Complete"

    warnings = []
    if render_summary.get("failed_render_count", 0) > 0:
        warnings.append("A rough cut previously failed. You can generate it again.")
    if edl_summary.get("failed_edl_count", 0) > 0:
        warnings.append("Cut preparation previously failed. You can prepare it again.")

    return {
        "steps": steps,
        "primary_next_action": next_action,
        "current_step": current_step,
        "attention": attention,
        "warnings": warnings,
    }


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
    Preflights the selected Story provider so an unavailable provider fails
    safely without touching preserved Moments.
    """
    try:
        require_story_provider()
    except StoryProviderUnavailable as exc:
        return {"ok": False, "status": "FAILED", "error": str(exc),
                "error_code": "STORY_PROVIDER_UNAVAILABLE"}
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


# ── Canonical Story review / building ────────────────────────────────────────


def _resolve_canonical_story(job_id: str, story_id: str, jobs_dir: str | Path | None = None):
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    from pipeline.runtime_service import get_story
    story = get_story(story_id)
    if story is None:
        story = find_story_by_original_id(project.project_id, story_id)
    if story is None:
        raise ValueError(f"canonical story '{story_id}' not found for project '{job_id}'")
    return project, story


def review_story(job_id: str, story_id: str, status: str, *, jobs_dir: str | Path | None = None) -> dict:
    """Update canonical Story status (Approve/Reject/Archive)."""
    if status not in STORY_STATUSES:
        raise ValueError(f"invalid story status: {status}")
    _project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    updated = update_story_status(story.story_id, status)
    return {"ok": True, "story": updated.to_dict()}


def story_add_moment(job_id: str, story_id: str, moment_id: str, narrative_role: str, sequence_order: int,
                     *, jobs_dir: str | Path | None = None) -> dict:
    _project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    add_story_moment(story.story_id, moment_id, narrative_role, sequence_order)
    return {"ok": True, "story": get_story_runtime_summary(story.story_id)}


def story_remove_moment(job_id: str, story_id: str, moment_id: str, *, sequence_order: int | None = None,
                        jobs_dir: str | Path | None = None) -> dict:
    _project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    remove_story_moment(story.story_id, moment_id, sequence_order=sequence_order)
    return {"ok": True, "story": get_story_runtime_summary(story.story_id)}


def story_update_moment(job_id: str, story_id: str, moment_id: str, *, narrative_role: str | None = None,
                        sequence_order: int | None = None, jobs_dir: str | Path | None = None) -> dict:
    _project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    update_story_moment(story.story_id, moment_id, narrative_role=narrative_role, sequence_order=sequence_order)
    return {"ok": True, "story": get_story_runtime_summary(story.story_id)}


def get_story_detail(job_id: str, story_id: str, *, jobs_dir: str | Path | None = None) -> dict:
    """Read the canonical Story detail read model for the Console."""
    _project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    summary = get_story_runtime_summary(story.story_id)
    from pipeline.runtime_service import get_moment
    ordered = []
    for entry in summary["ordered_moments"]:
        moment = entry.get("moment")
        ordered.append({
            "sequence_order": entry["sequence_order"],
            "narrative_role": entry["narrative_role"],
            "moment_id": moment.get("moment_id") if moment else "",
            "start_seconds": moment.get("start_seconds") if moment else None,
            "universal_event_type": moment.get("universal_event_type") if moment else "",
            "sport_event_type": moment.get("sport_event_type") if moment else "",
            "review_state": moment.get("review_state") if moment else "",
            "team": moment.get("team") if moment else "",
        })
    return {**summary, "ordered_moments": ordered}


# ── Edit Briefs ──────────────────────────────────────────────────────────────


def generate_brief(job_id: str, story_id: str, format_treatment: str,
                   *, jobs_dir: str | Path | None = None,
                   provider: str | None = None, model: str | None = None,
                   dry_run: bool = False) -> dict:
    """Generate an edit brief from a story.

    Delegates to :func:`pipeline.edit_brief.generate_edit_brief`.
    Preflights the selected Edit provider so an unavailable provider fails
    safely without touching the approved Story.
    """
    try:
        require_edit_provider()
    except EditProviderUnavailable as exc:
        return {"ok": False, "status": "FAILED", "error": str(exc),
                "error_code": "EDIT_PROVIDER_UNAVAILABLE"}
    return _brief_generate(
        job_id, story_id, format_treatment,
        jobs_dir=jobs_dir,
        provider=provider,
        model=model,
        dry_run=dry_run,
    )


def generate_canonical_edit_brief(job_id: str, story_id: str, format_treatment: str,
                                  *, jobs_dir: str | Path | None = None,
                                  provider: str | None = None, model: str | None = None,
                                  dry_run: bool = False) -> dict:
    """Generate an Edit Brief for a canonical Story and associate a runtime record.

    The canonical Story must be APPROVED to generate through the normal operator
    flow. The existing Edit Brief artifact is written unchanged; the runtime
    record references the canonical Story, the artifact, and the format.
    """
    project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    if story.status != "APPROVED":
        raise ValueError("only APPROVED stories can generate an edit brief through the operator flow")

    from .edit_brief import FORMAT_TREATMENTS as _formats
    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")

    try:
        require_edit_provider()
    except EditProviderUnavailable as exc:
        return {"ok": False, "status": "FAILED", "error": str(exc),
                "error_code": "EDIT_PROVIDER_UNAVAILABLE"}

    brief_id = upsert_edit_brief(
        project_id=project.project_id,
        story_id=story.story_id,
        format_treatment=format_treatment,
        status="GENERATING",
    )

    result = _brief_generate(
        job_id, story_id, format_treatment,
        jobs_dir=jobs_dir,
        provider=provider,
        model=model,
        dry_run=dry_run,
    )
    if not result.get("ok"):
        upsert_edit_brief(
            project_id=project.project_id,
            story_id=story.story_id,
            format_treatment=format_treatment,
            status="FAILED",
            metadata={"error": result.get("error") or "Edit brief generation failed."},
        )
        return result

    artifact = None
    artifact_path = result.get("artifact_path")
    if isinstance(artifact_path, str) and artifact_path.strip():
        artifact = register_artifact(
            project_id=project.project_id,
            artifact_type="edit_brief",
            path=artifact_path,
            metadata={"story_id": story.story_id, "format_treatment": format_treatment},
        )
    brief = result.get("brief") or {}
    upsert_edit_brief(
        project_id=project.project_id,
        story_id=story.story_id,
        format_treatment=format_treatment,
        status="READY",
        artifact_id=artifact.artifact_id if artifact else None,
        editorial_intent=brief.get("editorial_intent", ""),
        target_duration=brief.get("target_duration"),
        metadata={
            "original_story_id": story.metadata.get("original_story_id", ""),
            "source_artifact_path": artifact_path,
            "provider": provider,
            "model": model,
        },
    )
    return {**result, "canonical": True}


def generate_edit_plan(job_id: str, story_id: str, edit_brief_id: str, *, title: str | None = None,
                       target_platform: str = "TikTok", aspect_ratio: str = "9:16",
                       renderer: str = "FFMPEG", jobs_dir: str | Path | None = None) -> dict:
    project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    from pipeline.edit_plan_service import generate_edit_plan_from_story
    plan = generate_edit_plan_from_story(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=edit_brief_id,
        title=title or story.title,
        target_platform=target_platform,
        aspect_ratio=aspect_ratio,
        renderer=renderer,
        hook_text=story.hook,
        story_archetype=story.archetype,
    )
    return {"ok": True, "edit_plan": plan.to_dict()}


def prepare_chatcut_handoff(edit_plan_id: str, *, output_dir: str | Path | None = None) -> dict:
    from pipeline.renderers import get_renderer_adapter
    from pipeline.runtime_service import get_edit_plan as _get_plan
    plan = _get_plan(edit_plan_id)
    if plan is None:
        return {"ok": False, "error": f"EditPlan '{edit_plan_id}' not found."}
    return get_renderer_adapter("CHATCUT").render_or_export(plan, output_dir=output_dir)


def generate_editplan_preview(edit_plan_id: str, *, output_dir: str | Path | None = None,
                              timeout: int = 300, dry_run: bool = False) -> dict:
    from pipeline.edit_handoff_service import render_ffmpeg_preview_from_edit_plan
    from pipeline.runtime_service import get_edit_plan as _get_plan
    plan = _get_plan(edit_plan_id)
    if plan is None:
        return {"ok": False, "error": f"EditPlan '{edit_plan_id}' not found."}
    return render_ffmpeg_preview_from_edit_plan(plan, output_dir=output_dir, timeout=timeout, dry_run=dry_run)


def get_editplan_execution_report(edit_plan_id: str) -> dict:
    from pipeline.edit_handoff_service import editplan_quality_report, validate_edit_handoff
    from pipeline.runtime_service import get_edit_plan as _get_plan
    plan = _get_plan(edit_plan_id)
    if plan is None:
        return {"ok": False, "error": f"EditPlan '{edit_plan_id}' not found."}
    return {"ok": True, "validation": validate_edit_handoff(plan), "quality_report": editplan_quality_report(plan)}


def list_renderers() -> list[str]:
    from pipeline.renderers import list_renderer_adapters
    return list_renderer_adapters()


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


def generate_canonical_edl(job_id: str, story_id: str, format_treatment: str,
                           *, jobs_dir: str | Path | None = None,
                           source_duration: float | None = None,
                           dry_run: bool = False) -> dict:
    """Generate an EDL for a READY canonical Edit Brief and associate a runtime record.

    Requires a READY canonical Edit Brief for the same format treatment. The
    existing EDL artifact is written unchanged; the runtime record references
    the canonical Story, Edit Brief, and artifact.
    """
    project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    from .edit_brief import FORMAT_TREATMENTS as _formats
    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    from .runtime_service import _edit_brief_id
    brief = get_edit_brief(_edit_brief_id(story.story_id, format_treatment))
    if brief is None:
        raise ValueError(f"no canonical edit brief for format '{format_treatment}'")
    if brief.status != "READY":
        raise ValueError("only READY edit briefs can generate an EDL through the operator flow")

    upsert_edl(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        format_treatment=format_treatment,
        status="GENERATING",
        target_duration=brief.target_duration,
    )

    result = _edl_build(
        job_id, story_id, format_treatment,
        jobs_dir=jobs_dir,
        source_duration=source_duration,
        dry_run=dry_run,
    )
    if not result.get("ok"):
        upsert_edl(
            project_id=project.project_id,
            story_id=story.story_id,
            edit_brief_id=brief.edit_brief_id,
            format_treatment=format_treatment,
            status="FAILED",
            target_duration=brief.target_duration,
            metadata={"error": result.get("error") or "EDL generation failed."},
        )
        return result

    artifact = None
    artifact_path = result.get("artifact_path")
    if isinstance(artifact_path, str) and artifact_path.strip():
        artifact = register_artifact(
            project_id=project.project_id,
            artifact_type="edl",
            path=artifact_path,
            metadata={"story_id": story.story_id, "edit_brief_id": brief.edit_brief_id, "format_treatment": format_treatment},
        )
    edl = result.get("edl") or {}
    upsert_edl(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        format_treatment=format_treatment,
        status="READY",
        artifact_id=artifact.artifact_id if artifact else None,
        target_duration=brief.target_duration,
        estimated_duration=edl.get("timeline_duration"),
        metadata={
            "original_story_id": story.metadata.get("original_story_id", ""),
            "source_artifact_path": artifact_path,
            "segment_count": edl.get("segment_count"),
        },
    )
    return {**result, "canonical": True}


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


def generate_canonical_render(job_id: str, story_id: str, format_treatment: str,
                              *, jobs_dir: str | Path | None = None,
                              mode: str = "REFERENCE",
                              preset_id: str | None = None,
                              timeout: int = 300,
                              dry_run: bool = False) -> dict:
    """Render a rough cut for a READY canonical EDL and associate a runtime record.

    Requires a READY canonical EDL for the same format treatment. When a
    channel/platform preset is given, its settings are validated against the
    format/render profile and the render is associated with that preset.
    """
    project, story = _resolve_canonical_story(job_id, story_id, jobs_dir=jobs_dir)
    from .edit_brief import FORMAT_TREATMENTS as _formats
    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    from .rendering import RENDER_MODES as _modes
    if mode not in _modes:
        raise ValueError(f"invalid render profile: {mode}")
    preset = None
    if preset_id:
        preset = resolve_channel_preset(preset_id)
        validate_preset_compatibility(preset, format_treatment=format_treatment, render_profile=mode)
    from .runtime_service import _edit_brief_id, _edl_id
    brief = get_edit_brief(_edit_brief_id(story.story_id, format_treatment))
    if brief is None:
        raise ValueError(f"no canonical edit brief for format '{format_treatment}'")
    if brief.status != "READY":
        raise ValueError("only READY edit briefs can render a rough cut")
    edl = get_runtime_edl(_edl_id(brief.edit_brief_id, format_treatment))
    if edl is None:
        raise ValueError(f"no canonical edl for format '{format_treatment}'")
    if edl.status != "READY":
        raise ValueError("only READY edls can render a rough cut")

    upsert_render(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        edl_id=edl.edl_id,
        format_treatment=format_treatment,
        render_profile=mode,
        channel_preset_id=preset.preset_id if preset else None,
        platform=preset.platform if preset else None,
        status="RENDERING",
    )

    result = _render_edl(
        job_id, story_id, format_treatment,
        jobs_dir=jobs_dir,
        timeout=timeout,
        dry_run=dry_run,
        mode=mode,
    )
    if not result.get("ok"):
        upsert_render(
            project_id=project.project_id,
            story_id=story.story_id,
            edit_brief_id=brief.edit_brief_id,
            edl_id=edl.edl_id,
            format_treatment=format_treatment,
            render_profile=mode,
            channel_preset_id=preset.preset_id if preset else None,
            platform=preset.platform if preset else None,
            status="FAILED",
            metadata={"error": result.get("error") or "Rough cut generation failed."},
        )
        return result

    artifact = None
    output_path = result.get("output")
    if isinstance(output_path, str) and output_path.strip():
        artifact = register_artifact(
            project_id=project.project_id,
            artifact_type="render_video",
            path=output_path,
            metadata={"story_id": story.story_id, "edl_id": edl.edl_id, "format_treatment": format_treatment, "render_profile": mode},
        )
    upsert_render(
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        edl_id=edl.edl_id,
        format_treatment=format_treatment,
        render_profile=mode,
        channel_preset_id=preset.preset_id if preset else None,
        platform=preset.platform if preset else None,
        status="READY",
        review_state="UNREVIEWED",
        artifact_id=artifact.artifact_id if artifact else None,
        duration_seconds=result.get("duration"),
        width=preset.width if preset else None,
        height=preset.height if preset else None,
        fps=preset.fps if preset else None,
        metadata={
            "original_story_id": story.metadata.get("original_story_id", ""),
            "output_path": output_path,
            "segment_count": result.get("segment_count"),
        },
    )
    return {**result, "canonical": True}


def create_export_package(job_id: str, render_id: str, preset_id: str, *,
                          jobs_dir: str | Path | None = None,
                          caption: str = "", title: str = "", description: str = "",
                          hashtags: list[str] | None = None) -> dict:
    """Create a canonical Export Package for an APPROVED READY render."""
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    render = get_render(render_id)
    if render is None:
        raise ValueError(f"render '{render_id}' not found")
    if render.project_id != project.project_id:
        raise ValueError("render does not belong to project")
    if render.status != "READY" or render.review_state != "APPROVED":
        raise ValueError("only APPROVED READY renders can create an export package")
    preset = resolve_channel_preset(preset_id)
    if render.artifact_id is None:
        raise ValueError("render has no registered video artifact")
    export = upsert_export_package(
        project_id=project.project_id,
        story_id=render.story_id,
        render_id=render.render_id,
        channel_preset_id=preset.preset_id,
        video_artifact_id=render.artifact_id,
        caption=caption,
        title=title,
        description=description,
        hashtags=hashtags or [],
        metadata={"render_profile": render.render_profile, "platform": preset.platform},
    )
    return {"ok": True, "export": export.to_dict()}


def review_render(job_id: str, render_id: str, review_state: str, *, reviewed_by: str | None = None,
                  review_note: str | None = None, jobs_dir: str | Path | None = None) -> dict:
    """Update canonical Render review state for one project-scoped render."""
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    render = get_render(render_id)
    if render is None:
        raise ValueError(f"render '{render_id}' not found")
    if render.project_id != project.project_id:
        raise ValueError("render does not belong to project")
    updated = update_render_review_state(render_id, review_state, reviewed_by=reviewed_by, review_note=review_note)
    return {"ok": True, "render": updated.to_dict()}


def resolve_render_artifact(job_id: str, render_id: str, *, jobs_dir: str | Path | None = None) -> str:
    """Return the safe artifact path for a project-scoped render, or raise."""
    project = index_existing_project(job_id, jobs_dir=jobs_dir)
    render = get_render(render_id)
    if render is None:
        raise ValueError(f"render '{render_id}' not found")
    if render.project_id != project.project_id:
        raise ValueError("render does not belong to project")
    if render.artifact_id is None:
        raise ValueError("render has no registered artifact")
    from pipeline.runtime_service import get_artifact
    artifact = get_artifact(render.artifact_id)
    if artifact is None:
        raise ValueError("render artifact not found")
    return artifact.path
