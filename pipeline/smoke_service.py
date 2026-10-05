"""Real-match smoke orchestration.

Exercises the canonical workflow across real service boundaries with stage
timing and reuse flags. Provider-heavy stages (transcription, detection, edit
brief LLM, EDL, render) are executed through injected executor callables so the
harness is deterministic in ``fast`` mode and can call real services in ``full``
mode. Nothing is published externally.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from . import runtime_db
from .performance import PerformanceReport, log_stage, measure_stage
from .safety import safe_operator_message

SMOKE_STAGES = (
    "source_validation",
    "transcription",
    "detection",
    "moment_normalization",
    "story",
    "edit_brief",
    "edl",
    "render",
)


class SmokeError(Exception):
    """Deterministic smoke-stage failure (injection or real)."""


Executors = dict[str, Callable[..., Any]]


def smoke_run_id(source: Path, profile: str) -> str:
    """Namespaced smoke run/report identifier (``smoke_<hash>``).

    Used for the report output directory and run classification. This is a
    transient run identifier and must not leak into the canonical project/source
    identity used for transcript and artifact reuse.
    """
    digest = hashlib.sha1(f"{source.name}|{profile}".encode("utf-8")).hexdigest()[:10]
    return f"smoke_{digest}"


def smoke_project_id(source: Path, profile: str) -> str:
    """Backwards-compatible alias for :func:`smoke_run_id`."""
    return smoke_run_id(source, profile)


def _smoke_intake_identity(source: Path, profile: str, *, real_media: bool) -> tuple[str, str]:
    """Return the (pilot_id, source_id) used for a smoke intake.

    FULL operator-media smoke uses a production-compatible source identity
    derived from the source slug, so transcript/artifact reuse follows the
    normal production path and repeated smoke runs on the same source converge
    on the same project. FAST fixture smoke keeps an isolated, deterministic
    smoke-derived identity.
    """
    if real_media:
        from .utils import slugify
        slug = slugify(Path(source).stem) or "source"
        return f"pilot_{slug}", f"source_{slug}"
    digest = hashlib.sha1(f"{source.name}|{profile}".encode("utf-8")).hexdigest()[:10]
    return f"smoke_{digest}", f"source_{digest}"


def _build_intake(source: Path, match_name: str, profile: str, pilot_id: str, source_id: str) -> dict:
    return {
        "intake_version": 1,
        "pilot": {"pilot_id": pilot_id, "project": profile, "reference_deployment": "world_cup"},
        "media": {
            "source_id": source_id,
            "local_file_path": str(source),
            "original_filename": source.name,
            "media_type": "video",
            "match_or_event_name": match_name,
            "supplied_by_client": True,
            "source_validation_completed": False,
        },
        "rights": {
            "status": "CONFIRMED",
            "confirmation_statement": "Smoke fixture rights for local validation.",
            "confirmed_by": "smoke",
            "confirmation_date": "2026-01-01",
            "permitted_uses": ["clip", "store", "review", "delivery"],
            "distribution_limitations": ["no_broadcast"],
        },
        "configuration": {
            "project": profile,
            "brand": "world_cup",
            "editorial_taxonomy": "world_cup",
            "operational_taxonomy": "world_cup",
            "detection_template": "prompt",
            "export_profiles": ["vertical_clean", "source"],
            "delivery_destination": "EXPORTS",
        },
        "review_and_delivery": {
            "human_review_required": True,
            "approval_method": "console",
            "delivery_method": "shared_folder",
            "delivery_directory": "EXPORTS",
            "expected_deliverables": ["clips"],
            "publishing_included": False,
        },
    }


def _default_executors(project_id: str, transcript_path: Path, match_name: str,
                       profile: str, jobs_dir: Path) -> Executors:
    """Deterministic fast-mode executors that exercise real service boundaries."""
    from .runtime_service import register_artifact, upsert_edit_brief, upsert_edl, upsert_moment, upsert_render, update_story_status
    from .moment_adapter import adapt_detection_to_moments
    from .story_adapter import canonical_story_id

    def transcribe(_source, _match, _profile, pid):
        reused = transcript_path.exists()
        if not reused:
            transcript_path.write_text(json.dumps({"segments": []}), encoding="utf-8")
        register_artifact(project_id=pid, artifact_type="transcript", path=transcript_path)
        return {"path": str(transcript_path), "reused": reused}

    def detect(_transcript_path, _match, _profile):
        return [
            {"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5, "virality_score": "90"},
            {"clip_id": "002", "category": "SAVE", "start_time": 6, "end_time": 10, "virality_score": "70"},
        ]

    def normalize(clips, pid):
        artifact = register_artifact(project_id=pid, artifact_type="analysis_moments", path=transcript_path.parent / "moments.json")
        moments = adapt_detection_to_moments(clips, project_id=pid, source_artifact_id=artifact.artifact_id)
        for moment in moments:
            upsert_moment(moment)
        return len(moments)

    def story(pid, _count, _jobs_dir):
        from .runtime_service import list_project_moments, upsert_story
        from .story_adapter import adapt_story_suggestions

        moments = list_project_moments(pid)
        ref = moments[0].metadata.get("original_event_id") if moments else "001"
        adapt_story_suggestions([{
            "story_id": "smoke_story", "title": "Smoke Story", "archetype": "COMEBACK",
            "moment_ids": [ref], "recommended_formats": ["SHORT"], "estimated_duration": 45,
        }], project_id=pid, moments=moments)
        upsert_story(project_id=pid, story_id=canonical_story_id(pid, "smoke_story"), title="Smoke Story",
                     archetype="COMEBACK", metadata={"original_story_id": "smoke_story"})
        return len(__import__("pipeline.runtime_service", fromlist=["list_project_stories"]).list_project_stories(pid))

    def edit_brief(job_id, pid, _jobs_dir):
        story_id = canonical_story_id(pid, "smoke_story")
        update_story_status(story_id, "APPROVED")
        brief = upsert_edit_brief(project_id=pid, story_id=story_id, format_treatment="SHORT", status="READY",
                                  editorial_intent="Smoke fixture brief", target_duration=45,
                                  metadata={"fixture": True})
        return {"brief_id": brief.edit_brief_id}

    def edl(job_id, pid, _jobs_dir):
        from .runtime_service import get_edit_brief
        from .runtime_service import _edit_brief_id
        brief = get_edit_brief(_edit_brief_id(canonical_story_id(pid, "smoke_story"), "SHORT"))
        edl = upsert_edl(project_id=pid, story_id=canonical_story_id(pid, "smoke_story"),
                         edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY",
                         estimated_duration=44.0, metadata={"fixture": True})
        return {"edl_id": edl.edl_id}

    def render(job_id, pid, _jobs_dir):
        from .runtime_service import get_edl, _edl_id, get_edit_brief, _edit_brief_id
        brief = get_edit_brief(_edit_brief_id(canonical_story_id(pid, "smoke_story"), "SHORT"))
        edl = get_edl(_edl_id(brief.edit_brief_id, "SHORT"))
        artifact = register_artifact(project_id=pid, artifact_type="render_video", path=transcript_path.parent / "smoke_render.mp4",
                                     metadata={"fixture": True})
        render = upsert_render(project_id=pid, story_id=canonical_story_id(pid, "smoke_story"),
                               edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id,
                               format_treatment="SHORT", render_profile="REFERENCE", status="READY",
                               review_state="UNREVIEWED", artifact_id=artifact.artifact_id,
                               duration_seconds=44.0, metadata={"fixture": True})
        return {"render_id": render.render_id}

    return {
        "transcribe": transcribe,
        "detect": detect,
        "normalize": normalize,
        "story": story,
        "edit_brief": edit_brief,
        "edl": edl,
        "render": render,
    }


def _real_analysis_executors(job_id: str, project_id: str, jobs_dir: Path) -> Executors:
    """Real transcription/detection via the managed analysis path.

    Transcription, detection, and canonical Moment normalization are performed
    by ``operator_console.analyze_project`` against the actual source. If real
    analysis cannot complete, the smoke stage records FAIL instead of faking.

    A failure that occurs after transcription completed (e.g. detection provider
    error) is attributed to the detection stage: transcription is reported as
    REUSED so a provider/detection failure is never mislabeled as a
    transcription failure.
    """
    from .operator_console import analyze_project
    from .runtime_service import list_pipeline_events, list_project_moments

    analysis_blocked = {"reason": None}

    def _transcription_completed(run_id: str) -> bool:
        if not run_id:
            return False
        events = list_pipeline_events(run_id=run_id)
        return any(e.event_type in ("TRANSCRIPT_REUSED", "TRANSCRIPTION_COMPLETED") for e in events)

    def _transcription_reused(run_id: str) -> bool:
        if not run_id:
            return False
        events = list_pipeline_events(run_id=run_id)
        return any(e.event_type == "TRANSCRIPT_REUSED" for e in events)

    def real_transcribe(*_a):
        result = analyze_project(job_id, jobs_dir=jobs_dir)
        run_id = result.get("runtime_run_id") if isinstance(result, dict) else None
        reused = _transcription_reused(run_id)
        if not result.get("ok"):
            err = result.get("error") or ""
            if _transcription_completed(run_id):
                analysis_blocked["reason"] = err
                return {"path": "", "reused": True}
            raise SmokeError(safe_operator_message(err, "real analysis failed"))
        return {"path": "", "reused": reused}

    def real_detect(*_a):
        if analysis_blocked["reason"]:
            raise SmokeError(safe_operator_message(analysis_blocked["reason"], "real detection failed"))
        return []

    def real_normalize(*_a):
        return len(list_project_moments(project_id))

    return {
        "transcribe": real_transcribe,
        "detect": real_detect,
        "normalize": real_normalize,
    }


def run_smoke(*, source_file: Path, match_name: str = "Smoke Match", profile: str = "football",
              jobs_dir: Path, db_path: str | Path | None = None, mode: str = "fast",
              fail_after: str | None = None, executors: Executors | None = None,
              smoke_mode: str = "FAST", source_type: str = "FIXTURE", real_media: bool = False) -> dict[str, Any]:
    """Run the canonical workflow and return a structured smoke report.

    ``fail_after`` names a stage after which a deterministic SmokeError is
    raised (used for recovery validation). ``smoke_mode``/``source_type``/
    ``real_media`` classify the run. When ``real_media`` is true and no explicit
    executors are provided, transcription/detection run through the real managed
    analysis path and never fabricate results.
    """
    from .pilot import job_id_for_intake, read_job
    from .runtime_service import index_existing_project
    from .version import application_version

    db_path = Path(db_path) if db_path is not None else runtime_db.default_runtime_db_path()
    runtime_db.initialize(db_path)
    report = PerformanceReport()
    run_id = smoke_run_id(source_file, profile)
    pilot_id, source_id = _smoke_intake_identity(source_file, profile, real_media=real_media)
    intake = _build_intake(source_file, match_name, profile, pilot_id, source_id)
    expected_job_id = job_id_for_intake(intake) or f"{pilot_id}_{source_id}"

    from .operator_console import create_project
    job = None
    project = None
    try:
        job = create_project(intake, jobs_dir=jobs_dir)
    except Exception:  # job already exists -> reuse
        from .pilot import list_jobs
        row = next((r for r in list_jobs(jobs_dir=jobs_dir) if r["job_id"] == expected_job_id), None)
        if row is None:
            raise SmokeError(safe_operator_message("could not create or locate smoke project"))
        job = read_job(row["job_id"], jobs_dir=jobs_dir)
    job_id = job["job_id"]
    project = index_existing_project(job_id, jobs_dir=jobs_dir)

    if executors is None:
        executors = _default_executors(project.project_id, jobs_dir / "smoke_transcript.json", match_name, profile, jobs_dir)
        if real_media:
            executors = {**executors, **(_real_analysis_executors(job_id, project.project_id, jobs_dir))}

    def _fail(stage: str) -> None:
        if fail_after == stage:
            raise SmokeError(f"injected failure after stage '{stage}'")

    try:
        with measure_stage(report, "source_validation"):
            from .pilot import validate_source
            ok, issues, _duration_checked, _limitation = validate_source(_build_intake(source_file, match_name, profile, pilot_id, source_id))
            if not ok:
                raise SmokeError(safe_operator_message("; ".join(i["message"] for i in issues), "source validation failed"))
            report.counts["source"] = 1

        with measure_stage(report, "transcription"):
            transcript = executors["transcribe"](str(source_file), match_name, profile, project.project_id)
            _fail("transcription")
        report.stages[-1].reused = bool(transcript.get("reused"))
        log_stage("smoke", "transcription", report.stages[-1].duration_seconds, report.stages[-1].reused)

        with measure_stage(report, "detection"):
            clips = executors["detect"](transcript["path"], match_name, profile)
            report.counts["detected"] = len(clips)
            _fail("detection")

        with measure_stage(report, "moment_normalization"):
            moments_count = executors["normalize"](clips, project.project_id)
            report.counts["Moments"] = moments_count
            _fail("moment_normalization")

        with measure_stage(report, "story"):
            story_count = executors.get("story", _default_story)(project.project_id, moments_count, jobs_dir)
            report.counts["Stories"] = story_count
            _fail("story")

        with measure_stage(report, "edit_brief"):
            brief = executors.get("edit_brief", _default_brief)(job_id, project.project_id, jobs_dir)
            _fail("edit_brief")

        with measure_stage(report, "edl"):
            edl = executors.get("edl", _default_edl)(job_id, project.project_id, jobs_dir)
            _fail("edl")

        with measure_stage(report, "render"):
            render = executors.get("render", _default_render)(job_id, project.project_id, jobs_dir)
            report.counts["Renders"] = 1
            _fail("render")
    except SmokeError:
        # report already contains the FAIL timing via measure_stage re-raise
        pass

    report.counts["project_id"] = project.project_id
    data = {
        "application_version": application_version(),
        "schema_version": runtime_db.current_schema_version(db_path),
        "source": str(source_file),
        "source_name": source_file.name,
        "mode": mode,
        "classification": {
            "smoke_mode": smoke_mode,
            "source_type": source_type,
            "real_media": real_media,
        },
        "smoke_run_id": run_id,
        "project_id": project.project_id,
        "timings": report.to_dict(),
        "report_text": report.render(),
    }
    return data


def _default_story(project_id: str, moments_count: int, jobs_dir: Path) -> int:
    from .runtime_service import list_project_moments, upsert_story
    from .story_adapter import adapt_story_suggestions, canonical_story_id

    moments = list_project_moments(project_id)
    suggestions = [{
        "story_id": "smoke_story",
        "title": "Smoke Story",
        "archetype": "COMEBACK",
        "moment_ids": [moment.metadata.get("original_event_id") or moment.moment_id for moment in moments[:1]] or ["001"],
        "recommended_formats": ["SHORT"],
        "estimated_duration": 45,
    }]
    adapt_story_suggestions(suggestions, project_id=project_id, moments=moments)
    upsert_story(project_id=project_id, story_id=canonical_story_id(project_id, "smoke_story"),
                 title="Smoke Story", archetype="COMEBACK", metadata={"original_story_id": "smoke_story"})
    return len(__import__("pipeline.runtime_service", fromlist=["list_project_stories"]).list_project_stories(project_id))


def _default_brief(job_id: str, project_id: str, jobs_dir: Path) -> dict:
    from .runtime_service import update_story_status
    from .story_adapter import canonical_story_id
    from .operator_console import generate_canonical_edit_brief

    story_id = canonical_story_id(project_id, "smoke_story")
    update_story_status(story_id, "APPROVED")
    return generate_canonical_edit_brief(job_id, "smoke_story", "SHORT", jobs_dir=jobs_dir)


def _default_edl(job_id: str, project_id: str, jobs_dir: Path) -> dict:
    from .operator_console import generate_canonical_edl
    return generate_canonical_edl(job_id, "smoke_story", "SHORT", jobs_dir=jobs_dir)


def _default_render(job_id: str, project_id: str, jobs_dir: Path) -> dict:
    from .operator_console import generate_canonical_render
    return generate_canonical_render(job_id, "smoke_story", "SHORT", jobs_dir=jobs_dir)