"""Service boundary for the local SQLite runtime index."""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
import uuid
import hashlib
from contextlib import closing
from datetime import datetime, timezone
from typing import Any

from . import runtime_db
from .channel_models import validate_preset_compatibility
from .configurator import resolve_project_profile
from .edit_plan_models import EditBeat, EditPlan, TimelineInstruction
from .edit_brief_models import EDIT_BRIEF_STATUSES, CanonicalEditBrief
from .edl_models import EDL_STATUSES, CanonicalEDL
from .export_models import EXPORT_STATUSES, ExportPackage
from .moment_models import Moment, MomentRelation, Participant, REVIEW_STATES
from .pilot import JobNotFoundError, read_job, default_jobs_dir
from .research_models import MatchResearch, ResearchEvent
from .render_models import RENDER_REVIEW_STATES, RENDER_STATUSES, CanonicalRender
from .runtime_models import Artifact, PipelineEvent, PipelineRun, Project, RUN_STATES, normalize_analysis_strategy
from .story_models import STORY_STATUSES, Story, StoryMoment


ANALYSIS_STAGE = "analysis"
ANALYSIS_STATUS_MAP = {
    "QUEUED": {
        "analysis_status": "RUNNING",
        "analysis_stage": "QUEUED",
        "operator_status": "preparing",
        "operator_label": "Analysis queued",
    },
    "RUNNING": {
        "analysis_status": "RUNNING",
        "analysis_stage": "RUNNING",
        "operator_status": "running",
        "operator_label": "Analysis running",
    },
    "SUCCEEDED": {
        "analysis_status": "COMPLETE",
        "analysis_stage": "",
        "operator_status": "complete",
        "operator_label": "Analysis complete",
    },
    "FAILED": {
        "analysis_status": "FAILED",
        "analysis_stage": "",
        "operator_status": "failed",
        "operator_label": "Analysis failed",
    },
    "BLOCKED": {
        "analysis_status": "NEEDS ATTENTION",
        "analysis_stage": "",
        "operator_status": "needs_attention",
        "operator_label": "Needs attention",
    },
    "CANCELLED": {
        "analysis_status": "FAILED",
        "analysis_stage": "",
        "operator_status": "cancelled",
        "operator_label": "Analysis cancelled",
    },
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(data: dict[str, Any] | None) -> str:
    return json.dumps(data or {}, sort_keys=True, separators=(",", ":"))


def _json_dump_any(data: Any) -> str:
    return json.dumps(data, sort_keys=True, separators=(",", ":"))


def _json_loads(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    data = json.loads(raw)
    return data if isinstance(data, dict) else {}


def _json_load_any(raw: str | None, default: Any) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return default


def _row_project(row) -> Project:
    return Project(
        project_id=row["project_id"],
        job_id=row["job_id"],
        profile=row["profile"],
        sport=row["sport"],
        display_name=row["display_name"],
        status=row["status"],
        source_artifact_id=row["source_artifact_id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        parent_project_id=row["parent_project_id"],
        source_project_id=row["source_project_id"],
        reuse_mode=row["reuse_mode"],
        analysis_strategy=row["analysis_strategy"] if "analysis_strategy" in row.keys() else "TRANSCRIPT_FIRST",
    )


def _row_artifact(row) -> Artifact:
    return Artifact(
        artifact_id=row["artifact_id"],
        project_id=row["project_id"],
        artifact_type=row["artifact_type"],
        path=row["path"],
        mime_type=row["mime_type"],
        status=row["status"],
        parent_artifact_id=row["parent_artifact_id"],
        created_at=row["created_at"],
        metadata=_json_loads(row["metadata"]),
    )


def _row_run(row) -> PipelineRun:
    return PipelineRun(
        run_id=row["run_id"],
        project_id=row["project_id"],
        stage=row["stage"],
        status=row["status"],
        started_at=row["started_at"],
        finished_at=row["finished_at"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        error_code=row["error_code"],
        error_message=row["error_message"],
        retry_count=row["retry_count"],
        created_at=row["created_at"],
        metadata=_json_load_any(row["metadata_json"], {}) if "metadata_json" in row.keys() else {},
    )


def _row_event(row) -> PipelineEvent:
    return PipelineEvent(
        event_id=row["event_id"],
        run_id=row["run_id"],
        project_id=row["project_id"],
        event_type=row["event_type"],
        stage=row["stage"],
        message=row["message"],
        progress_current=row["progress_current"],
        progress_total=row["progress_total"],
        created_at=row["created_at"],
        metadata=_json_loads(row["metadata"]),
    )


def _row_moment(row) -> Moment:
    participants = []
    for item in _json_load_any(row["participants_json"], []):
        if isinstance(item, dict):
            participants.append(Participant(
                participant_type=item.get("participant_type"),
                name=item.get("name"),
                role=item.get("role"),
                team=item.get("team"),
            ))
    return Moment(
        moment_id=row["moment_id"],
        project_id=row["project_id"],
        source_artifact_id=row["source_artifact_id"],
        sport=row["sport"],
        universal_event_type=row["universal_event_type"],
        sport_event_type=row["sport_event_type"],
        start_seconds=row["start_seconds"],
        peak_seconds=row["peak_seconds"],
        end_seconds=row["end_seconds"],
        participants=participants,
        team=row["team"],
        signals=_json_load_any(row["signals_json"], {}),
        emotion=_json_load_any(row["emotion_json"], []),
        importance=row["importance"],
        confidence=row["confidence"],
        review_state=row["review_state"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        origin_moment_id=row["origin_moment_id"],
        origin_project_id=row["origin_project_id"],
        created_at=row["created_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_moment_relation(row) -> MomentRelation:
    return MomentRelation(
        relation_id=row["relation_id"],
        project_id=row["project_id"],
        source_moment_id=row["source_moment_id"],
        target_moment_id=row["target_moment_id"],
        relation_type=row["relation_type"],
        weight=row["weight"],
        confidence=row["confidence"],
        created_at=row["created_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_match_research(row) -> MatchResearch:
    return MatchResearch(
        research_id=row["research_id"],
        project_id=row["project_id"],
        source_artifact_id=row["source_artifact_id"],
        sport=row["sport"],
        competition=row["competition"],
        season=row["season"],
        match_date=row["match_date"],
        home_team=row["home_team"],
        away_team=row["away_team"],
        home_score=row["home_score"],
        away_score=row["away_score"],
        venue=row["venue"],
        stage=row["stage"],
        importance=row["importance"],
        summary=row["summary"],
        stakes=row["stakes"],
        historical_context=row["historical_context"],
        sources=_json_load_any(row["sources_json"], []),
        created_at=row["created_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_research_event(row) -> ResearchEvent:
    return ResearchEvent(
        event_id=row["event_id"],
        research_id=row["research_id"],
        project_id=row["project_id"],
        match_minute=row["match_minute"],
        match_second_optional=row["match_second_optional"],
        universal_event_type=row["universal_event_type"],
        sport_event_type=row["sport_event_type"],
        team=row["team"],
        participants=_json_load_any(row["participants_json"], []),
        headline=row["headline"],
        description=row["description"],
        score_before=row["score_before"],
        score_after=row["score_after"],
        importance=row["importance"],
        confidence=row["confidence"],
        source_refs=_json_load_any(row["source_refs_json"], []),
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _project_data(project: Project) -> dict[str, Any]:
    return project.to_dict()


def _run_data(run: PipelineRun | None) -> dict[str, Any] | None:
    if run is None:
        return None
    data = run.to_dict()
    data.update(ANALYSIS_STATUS_MAP.get(run.status, {}))
    return data


def _event_data(event: PipelineEvent) -> dict[str, Any]:
    return {
        "event_id": event.event_id,
        "run_id": event.run_id,
        "project_id": event.project_id,
        "event_type": event.event_type,
        "stage": event.stage,
        "message": event.message,
        "progress_current": event.progress_current,
        "progress_total": event.progress_total,
        "created_at": event.created_at,
    }


def _artifact_data(artifact: Artifact) -> dict[str, Any]:
    return {
        "artifact_id": artifact.artifact_id,
        "project_id": artifact.project_id,
        "artifact_type": artifact.artifact_type,
        "path": artifact.path,
        "mime_type": artifact.mime_type,
        "status": artifact.status,
        "parent_artifact_id": artifact.parent_artifact_id,
        "created_at": artifact.created_at,
    }


def _moment_data(moment: Moment) -> dict[str, Any]:
    return moment.to_dict()


def _story_data(story: Story) -> dict[str, Any]:
    return story.to_dict()


def _row_story(row) -> Story:
    return Story(
        story_id=row["story_id"],
        project_id=row["project_id"],
        title=row["title"],
        summary=row["summary"],
        archetype=row["archetype"],
        status=row["status"],
        hook=row["hook"],
        emotional_arc=_json_load_any(row["emotional_arc_json"], []),
        estimated_duration=row["estimated_duration"],
        recommended_formats=_json_load_any(row["recommended_formats_json"], []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_story_moment(row) -> StoryMoment:
    return StoryMoment(
        story_moment_id=row["story_moment_id"],
        story_id=row["story_id"],
        moment_id=row["moment_id"],
        narrative_role=row["narrative_role"],
        sequence_order=row["sequence_order"],
        created_at=row["created_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_edit_brief(row) -> CanonicalEditBrief:
    return CanonicalEditBrief(
        edit_brief_id=row["edit_brief_id"],
        project_id=row["project_id"],
        story_id=row["story_id"],
        artifact_id=row["artifact_id"],
        format_treatment=row["format_treatment"],
        status=row["status"],
        editorial_intent=row["editorial_intent"],
        target_duration=row["target_duration"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_edl(row) -> CanonicalEDL:
    return CanonicalEDL(
        edl_id=row["edl_id"],
        project_id=row["project_id"],
        story_id=row["story_id"],
        edit_brief_id=row["edit_brief_id"],
        artifact_id=row["artifact_id"],
        format_treatment=row["format_treatment"],
        status=row["status"],
        target_duration=row["target_duration"],
        estimated_duration=row["estimated_duration"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_render(row) -> CanonicalRender:
    return CanonicalRender(
        render_id=row["render_id"],
        project_id=row["project_id"],
        story_id=row["story_id"],
        edit_brief_id=row["edit_brief_id"],
        edl_id=row["edl_id"],
        artifact_id=row["artifact_id"],
        format_treatment=row["format_treatment"],
        render_profile=row["render_profile"],
        channel_preset_id=row["channel_preset_id"],
        platform=row["platform"],
        status=row["status"],
        review_state=row["review_state"],
        duration_seconds=row["duration_seconds"],
        width=row["width"],
        height=row["height"],
        fps=row["fps"],
        reviewed_at=row["reviewed_at"],
        reviewed_by=row["reviewed_by"],
        review_note=row["review_note"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_edit_plan(row) -> EditPlan:
    return EditPlan(
        edit_plan_id=row["edit_plan_id"],
        project_id=row["project_id"],
        story_id=row["story_id"],
        edit_brief_id=row["edit_brief_id"],
        title=row["title"],
        target_platform=row["target_platform"],
        target_duration=row["target_duration"],
        aspect_ratio=row["aspect_ratio"],
        hook_text=row["hook_text"],
        story_archetype=row["story_archetype"],
        status=row["status"],
        renderer=row["renderer"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_edit_beat(row) -> EditBeat:
    return EditBeat(
        edit_beat_id=row["edit_beat_id"],
        edit_plan_id=row["edit_plan_id"],
        sequence_order=row["sequence_order"],
        narrative_role=row["narrative_role"],
        source_moment_id=row["source_moment_id"],
        purpose=row["purpose"],
        description=row["description"],
        source_start=row["source_start"],
        source_end=row["source_end"],
        target_duration=row["target_duration"],
        crop_intent=row["crop_intent"],
        speed_intent=row["speed_intent"],
        text_overlay=row["text_overlay"],
        caption_intent=row["caption_intent"],
        audio_intent=row["audio_intent"],
        transition_intent=row["transition_intent"],
        motion_graphic_intent=row["motion_graphic_intent"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_timeline_instruction(row) -> TimelineInstruction:
    return TimelineInstruction(
        instruction_id=row["instruction_id"],
        edit_plan_id=row["edit_plan_id"],
        edit_beat_id=row["edit_beat_id"],
        instruction_type=row["instruction_type"],
        source_artifact_id=row["source_artifact_id"],
        source_in=row["source_in"],
        source_out=row["source_out"],
        timeline_start=row["timeline_start"],
        timeline_duration=row["timeline_duration"],
        crop=_json_load_any(row["crop_json"], {}),
        scale=_json_load_any(row["scale_json"], {}),
        position=_json_load_any(row["position_json"], {}),
        speed=row["speed"],
        freeze_frame=bool(row["freeze_frame"]),
        opacity=row["opacity"],
        text=row["text"],
        caption_style=_json_load_any(row["caption_style_json"], {}),
        audio_gain=row["audio_gain"],
        music_cue=row["music_cue"],
        sfx_cue=row["sfx_cue"],
        transition=row["transition"],
        motion_graphic_template=_json_load_any(row["motion_graphic_template_json"], {}),
        motion_graphic_parameters=_json_load_any(row["motion_graphic_parameters_json"], {}),
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def _row_export(row) -> ExportPackage:
    return ExportPackage(
        export_id=row["export_id"],
        project_id=row["project_id"],
        story_id=row["story_id"],
        render_id=row["render_id"],
        channel_preset_id=row["channel_preset_id"],
        platform=row["platform"],
        status=row["status"],
        video_artifact_id=row["video_artifact_id"],
        thumbnail_artifact_id=row["thumbnail_artifact_id"],
        caption=row["caption"],
        title=row["title"],
        description=row["description"],
        hashtags=_json_load_any(row["hashtags_json"], []),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        metadata=_json_load_any(row["metadata_json"], {}),
    )


def initialize_runtime_index(db_path: str | Path | None = None) -> Path:
    return runtime_db.initialize(db_path)


def upsert_project(
    *,
    project_id: str,
    job_id: str,
    profile: str,
    sport: str,
    display_name: str,
    status: str,
    source_artifact_id: str | None = None,
    parent_project_id: str | None = None,
    source_project_id: str | None = None,
    reuse_mode: str = "",
    analysis_strategy: str | None = None,
    created_at: str | None = None,
    updated_at: str | None = None,
    db_path: str | Path | None = None,
) -> Project:
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    updated = updated_at or now
    strategy = normalize_analysis_strategy(analysis_strategy)
    if parent_project_id is None and source_project_id is None:
        source_project_id = project_id
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, source_artifact_id,
                                 parent_project_id, source_project_id, reuse_mode, analysis_strategy, created_at, updated_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id) DO UPDATE SET
                job_id=excluded.job_id,
                profile=excluded.profile,
                sport=excluded.sport,
                display_name=excluded.display_name,
                status=excluded.status,
                source_artifact_id=COALESCE(excluded.source_artifact_id, projects.source_artifact_id),
                parent_project_id=excluded.parent_project_id,
                source_project_id=excluded.source_project_id,
                reuse_mode=excluded.reuse_mode,
                analysis_strategy=excluded.analysis_strategy,
                updated_at=excluded.updated_at
            """,
            (project_id, job_id, profile, sport, display_name, status, source_artifact_id,
             parent_project_id, source_project_id, reuse_mode, strategy, created, updated),
        )
        row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
    return _row_project(row)


def effective_analysis_strategy(project: Project, run_override: str | None = None) -> str:
    """Resolve the single canonical analysis strategy for an execution."""
    return normalize_analysis_strategy(run_override or project.analysis_strategy)


def update_project_analysis_strategy(project_id: str, analysis_strategy: str, *, db_path: str | Path | None = None) -> Project:
    strategy = normalize_analysis_strategy(analysis_strategy)
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
        if current is None:
            raise ValueError(f"project '{project_id}' not found")
        conn.execute(
            "UPDATE projects SET analysis_strategy = ?, updated_at = ? WHERE project_id = ?",
            (strategy, _now_iso(), project_id),
        )
        row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
    return _row_project(row)


def get_project(project_id: str, *, db_path: str | Path | None = None) -> Project | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM projects WHERE project_id = ?", (project_id,)).fetchone()
    return _row_project(row) if row else None


def get_project_by_job_id(job_id: str, *, db_path: str | Path | None = None) -> Project | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM projects WHERE job_id = ?", (job_id,)).fetchone()
    return _row_project(row) if row else None


def list_projects(*, db_path: str | Path | None = None) -> list[Project]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM projects ORDER BY updated_at DESC, created_at DESC, project_id").fetchall()
    return [_row_project(row) for row in rows]


# ── Match research service ──────────────────────────────────────────────────


def upsert_match_research(research: MatchResearch, *, db_path: str | Path | None = None) -> MatchResearch:
    initialize_runtime_index(db_path)
    created = research.created_at or _now_iso()
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO match_research(
                research_id, project_id, source_artifact_id, sport, competition, season, match_date,
                home_team, away_team, home_score, away_score, venue, stage, importance, summary,
                stakes, historical_context, sources_json, created_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(research_id) DO UPDATE SET
                source_artifact_id=excluded.source_artifact_id,
                sport=excluded.sport,
                competition=excluded.competition,
                season=excluded.season,
                match_date=excluded.match_date,
                home_team=excluded.home_team,
                away_team=excluded.away_team,
                home_score=excluded.home_score,
                away_score=excluded.away_score,
                venue=excluded.venue,
                stage=excluded.stage,
                importance=excluded.importance,
                summary=excluded.summary,
                stakes=excluded.stakes,
                historical_context=excluded.historical_context,
                sources_json=excluded.sources_json,
                metadata_json=excluded.metadata_json
            """,
            (
                research.research_id, research.project_id, research.source_artifact_id, research.sport,
                research.competition, research.season, research.match_date, research.home_team, research.away_team,
                research.home_score, research.away_score, research.venue, research.stage, research.importance,
                research.summary, research.stakes, research.historical_context,
                _json_dump_any(research.sources), created, _json_dump_any(research.metadata),
            ),
        )
        row = conn.execute("SELECT * FROM match_research WHERE research_id = ?", (research.research_id,)).fetchone()
    return _row_match_research(row)


def get_match_research(research_id: str, *, db_path: str | Path | None = None) -> MatchResearch | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM match_research WHERE research_id = ?", (research_id,)).fetchone()
    return _row_match_research(row) if row else None


def list_project_research(project_id: str, *, db_path: str | Path | None = None) -> list[MatchResearch]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM match_research WHERE project_id = ? ORDER BY created_at, research_id", (project_id,)).fetchall()
    return [_row_match_research(row) for row in rows]


def upsert_research_event(event: ResearchEvent, *, db_path: str | Path | None = None) -> ResearchEvent:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO research_events(
                event_id, research_id, project_id, match_minute, match_second_optional,
                universal_event_type, sport_event_type, team, participants_json, headline,
                description, score_before, score_after, importance, confidence, source_refs_json, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(event_id) DO UPDATE SET
                match_minute=excluded.match_minute,
                match_second_optional=excluded.match_second_optional,
                universal_event_type=excluded.universal_event_type,
                sport_event_type=excluded.sport_event_type,
                team=excluded.team,
                participants_json=excluded.participants_json,
                headline=excluded.headline,
                description=excluded.description,
                score_before=excluded.score_before,
                score_after=excluded.score_after,
                importance=excluded.importance,
                confidence=excluded.confidence,
                source_refs_json=excluded.source_refs_json,
                metadata_json=excluded.metadata_json
            """,
            (
                event.event_id, event.research_id, event.project_id, event.match_minute,
                event.match_second_optional, event.universal_event_type, event.sport_event_type, event.team,
                _json_dump_any(event.participants), event.headline, event.description, event.score_before,
                event.score_after, event.importance, event.confidence, _json_dump_any(event.source_refs),
                _json_dump_any(event.metadata),
            ),
        )
        row = conn.execute("SELECT * FROM research_events WHERE event_id = ?", (event.event_id,)).fetchone()
    return _row_research_event(row)


def get_research_event(event_id: str, *, db_path: str | Path | None = None) -> ResearchEvent | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM research_events WHERE event_id = ?", (event_id,)).fetchone()
    return _row_research_event(row) if row else None


def list_research_events(research_id: str, *, db_path: str | Path | None = None) -> list[ResearchEvent]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM research_events WHERE research_id = ? ORDER BY match_minute, COALESCE(match_second_optional, 0), event_id",
            (research_id,),
        ).fetchall()
    return [_row_research_event(row) for row in rows]


def list_project_research_events(project_id: str, *, db_path: str | Path | None = None) -> list[ResearchEvent]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM research_events WHERE project_id = ? ORDER BY match_minute, COALESCE(match_second_optional, 0), event_id",
            (project_id,),
        ).fetchall()
    return [_row_research_event(row) for row in rows]


def register_artifact(
    *,
    project_id: str,
    artifact_type: str,
    path: str | Path,
    mime_type: str | None = None,
    status: str = "AVAILABLE",
    parent_artifact_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    artifact_id: str | None = None,
    db_path: str | Path | None = None,
) -> Artifact:
    initialize_runtime_index(db_path)
    path_text = str(path)
    guessed_type = mime_type or mimetypes.guess_type(path_text)[0] or "application/octet-stream"
    now = _now_iso()
    with runtime_db.transaction(db_path) as conn:
        existing = conn.execute(
            "SELECT * FROM artifacts WHERE project_id = ? AND artifact_type = ? AND path = ?",
            (project_id, artifact_type, path_text),
        ).fetchone()
        metadata_to_store = dict(metadata or {})
        if existing:
            metadata_to_store = {**_json_load_any(existing["metadata"], {}), **metadata_to_store}
        artifact_key = artifact_id or (existing["artifact_id"] if existing else f"art_{uuid.uuid4().hex}")
        conn.execute(
            """
            INSERT INTO artifacts(artifact_id, project_id, artifact_type, path, mime_type, status, parent_artifact_id, created_at, metadata)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, artifact_type, path) DO UPDATE SET
                mime_type=excluded.mime_type,
                status=excluded.status,
                parent_artifact_id=excluded.parent_artifact_id,
                metadata=excluded.metadata
            """,
            (artifact_key, project_id, artifact_type, path_text, guessed_type, status, parent_artifact_id, now, _json_dumps(metadata_to_store)),
        )
        row = conn.execute("SELECT * FROM artifacts WHERE project_id = ? AND artifact_type = ? AND path = ?", (project_id, artifact_type, path_text)).fetchone()
    return _row_artifact(row)


def get_artifact(artifact_id: str, *, db_path: str | Path | None = None) -> Artifact | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)).fetchone()
    return _row_artifact(row) if row else None


def list_project_artifacts(project_id: str, *, artifact_type: str | None = None, db_path: str | Path | None = None) -> list[Artifact]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if artifact_type:
            rows = conn.execute(
                "SELECT * FROM artifacts WHERE project_id = ? AND artifact_type = ? ORDER BY created_at, artifact_id",
                (project_id, artifact_type),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM artifacts WHERE project_id = ? ORDER BY created_at, artifact_id", (project_id,)).fetchall()
    return [_row_artifact(row) for row in rows]


def upsert_moment(moment: Moment, *, db_path: str | Path | None = None) -> Moment:
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = moment.created_at or now
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO moments(
                moment_id, project_id, source_artifact_id, sport, universal_event_type, sport_event_type,
                start_seconds, peak_seconds, end_seconds, importance, confidence, team, review_state,
                reviewed_at, reviewed_by, origin_moment_id, origin_project_id,
                participants_json, signals_json, emotion_json, metadata_json, created_at, updated_at
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(moment_id) DO UPDATE SET
                project_id=excluded.project_id,
                source_artifact_id=excluded.source_artifact_id,
                sport=excluded.sport,
                universal_event_type=excluded.universal_event_type,
                sport_event_type=excluded.sport_event_type,
                start_seconds=excluded.start_seconds,
                peak_seconds=excluded.peak_seconds,
                end_seconds=excluded.end_seconds,
                importance=excluded.importance,
                confidence=excluded.confidence,
                team=excluded.team,
                participants_json=excluded.participants_json,
                signals_json=excluded.signals_json,
                emotion_json=excluded.emotion_json,
                metadata_json=excluded.metadata_json,
                updated_at=excluded.updated_at
            """,
            (
                moment.moment_id,
                moment.project_id,
                moment.source_artifact_id,
                moment.sport,
                moment.universal_event_type,
                moment.sport_event_type,
                moment.start_seconds,
                moment.peak_seconds,
                moment.end_seconds,
                moment.importance,
                moment.confidence,
                moment.team,
                moment.review_state,
                moment.reviewed_at,
                moment.reviewed_by,
                moment.origin_moment_id,
                moment.origin_project_id,
                _json_dump_any([participant.to_dict() for participant in moment.participants]),
                _json_dump_any(moment.signals),
                _json_dump_any(moment.emotion),
                _json_dump_any(moment.metadata),
                created,
                now,
            ),
        )
        row = conn.execute("SELECT * FROM moments WHERE moment_id = ?", (moment.moment_id,)).fetchone()
    return _row_moment(row)


def get_moment(moment_id: str, *, db_path: str | Path | None = None) -> Moment | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM moments WHERE moment_id = ?", (moment_id,)).fetchone()
    return _row_moment(row) if row else None


def list_project_moments(project_id: str, *, review_state: str | None = None, db_path: str | Path | None = None) -> list[Moment]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if review_state:
            if review_state not in REVIEW_STATES:
                raise ValueError(f"invalid review_state: {review_state}")
            rows = conn.execute(
                "SELECT * FROM moments WHERE project_id = ? AND review_state = ? ORDER BY start_seconds, moment_id",
                (project_id, review_state),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM moments WHERE project_id = ? ORDER BY start_seconds, moment_id", (project_id,)).fetchall()
    return [_row_moment(row) for row in rows]


def update_moment_review_state(moment_id: str, review_state: str, *, reviewed_by: str | None = None, db_path: str | Path | None = None) -> Moment:
    if review_state not in REVIEW_STATES:
        raise ValueError(f"invalid review_state: {review_state}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM moments WHERE moment_id = ?", (moment_id,)).fetchone()
        if current is None:
            raise ValueError(f"moment '{moment_id}' not found")
        now = _now_iso()
        reviewed_at = None if review_state == "UNREVIEWED" else now
        reviewer = None if review_state == "UNREVIEWED" else reviewed_by
        conn.execute(
            "UPDATE moments SET review_state = ?, reviewed_at = ?, reviewed_by = ?, updated_at = ? WHERE moment_id = ?",
            (review_state, reviewed_at, reviewer, now, moment_id),
        )
        row = conn.execute("SELECT * FROM moments WHERE moment_id = ?", (moment_id,)).fetchone()
    return _row_moment(row)


def update_project_moment_review_state(
    project_id: str,
    moment_id: str,
    review_state: str,
    *,
    reviewed_by: str | None = None,
    db_path: str | Path | None = None,
) -> Moment:
    moment = get_moment(moment_id, db_path=db_path)
    if moment is None:
        raise ValueError(f"moment '{moment_id}' not found")
    if moment.project_id != project_id:
        raise ValueError("moment does not belong to project")
    return update_moment_review_state(moment_id, review_state, reviewed_by=reviewed_by, db_path=db_path)


# ── Moment relation service ──────────────────────────────────────────────────


def _moment_relation_id(project_id: str, source_moment_id: str, target_moment_id: str, relation_type: str) -> str:
    raw = f"{project_id}|{source_moment_id}|{target_moment_id}|{relation_type}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"rel_{digest}"


def upsert_moment_relation(
    *,
    project_id: str,
    source_moment_id: str,
    target_moment_id: str,
    relation_type: str,
    weight: float | None = None,
    confidence: float | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> MomentRelation:
    if source_moment_id == target_moment_id:
        raise ValueError("self-relations are not allowed")
    source = get_moment(source_moment_id, db_path=db_path)
    if source is None:
        raise ValueError(f"source moment '{source_moment_id}' not found")
    target = get_moment(target_moment_id, db_path=db_path)
    if target is None:
        raise ValueError(f"target moment '{target_moment_id}' not found")
    if source.project_id != project_id or target.project_id != project_id:
        raise ValueError("moment relations cannot cross projects")
    from .moment_models import RELATION_TYPES

    if relation_type not in RELATION_TYPES:
        raise ValueError(f"invalid relation type: {relation_type}")
    initialize_runtime_index(db_path)
    now = _now_iso()
    rel_key = _moment_relation_id(project_id, source_moment_id, target_moment_id, relation_type)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO moment_relations(
                relation_id, project_id, source_moment_id, target_moment_id, relation_type,
                weight, confidence, created_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(relation_id) DO UPDATE SET
                weight=excluded.weight,
                confidence=excluded.confidence,
                metadata_json=excluded.metadata_json
            """,
            (rel_key, project_id, source_moment_id, target_moment_id, relation_type,
             weight, confidence, now, _json_dump_any(metadata or {})),
        )
        row = conn.execute("SELECT * FROM moment_relations WHERE relation_id = ?", (rel_key,)).fetchone()
    return _row_moment_relation(row)


def get_moment_relation(relation_id: str, *, db_path: str | Path | None = None) -> MomentRelation | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM moment_relations WHERE relation_id = ?", (relation_id,)).fetchone()
    return _row_moment_relation(row) if row else None


def list_project_moment_relations(project_id: str, *, relation_type: str | None = None, db_path: str | Path | None = None) -> list[MomentRelation]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if relation_type:
            from .moment_models import RELATION_TYPES

            if relation_type not in RELATION_TYPES:
                raise ValueError(f"invalid relation type: {relation_type}")
            rows = conn.execute(
                "SELECT * FROM moment_relations WHERE project_id = ? AND relation_type = ? ORDER BY created_at, relation_id",
                (project_id, relation_type),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM moment_relations WHERE project_id = ? ORDER BY created_at, relation_id", (project_id,)).fetchall()
    return [_row_moment_relation(row) for row in rows]


def list_moment_outgoing_relations(moment_id: str, *, db_path: str | Path | None = None) -> list[MomentRelation]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM moment_relations WHERE source_moment_id = ? ORDER BY created_at, relation_id", (moment_id,)
        ).fetchall()
    return [_row_moment_relation(row) for row in rows]


def list_moment_incoming_relations(moment_id: str, *, db_path: str | Path | None = None) -> list[MomentRelation]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM moment_relations WHERE target_moment_id = ? ORDER BY created_at, relation_id", (moment_id,)
        ).fetchall()
    return [_row_moment_relation(row) for row in rows]


def delete_moment_relation(relation_id: str, *, db_path: str | Path | None = None) -> bool:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        cursor = conn.execute("DELETE FROM moment_relations WHERE relation_id = ?", (relation_id,))
    return cursor.rowcount > 0


# ── Story service ────────────────────────────────────────────────────────────


def create_story(
    *,
    project_id: str,
    story_id: str,
    title: str,
    summary: str = "",
    archetype: str = "",
    status: str = "SUGGESTED",
    hook: str = "",
    emotional_arc: list[str] | None = None,
    estimated_duration: int | None = None,
    recommended_formats: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> Story:
    return upsert_story(
        project_id=project_id,
        story_id=story_id,
        title=title,
        summary=summary,
        archetype=archetype,
        status=status,
        hook=hook,
        emotional_arc=emotional_arc,
        estimated_duration=estimated_duration,
        recommended_formats=recommended_formats,
        metadata=metadata,
        created_at=created_at,
        db_path=db_path,
    )


def upsert_story(
    *,
    project_id: str,
    story_id: str,
    title: str,
    summary: str = "",
    archetype: str = "",
    status: str = "SUGGESTED",
    hook: str = "",
    emotional_arc: list[str] | None = None,
    estimated_duration: int | None = None,
    recommended_formats: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> Story:
    if status not in STORY_STATUSES:
        raise ValueError(f"invalid story status: {status}")
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
        effective_status = current["status"] if current and current["status"] not in ("DRAFT", "SUGGESTED") else status
        effective_created = current["created_at"] if current else created
        conn.execute(
            """
            INSERT INTO stories(
                story_id, project_id, title, summary, archetype, status, hook, emotional_arc_json,
                estimated_duration, recommended_formats_json, created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id) DO UPDATE SET
                project_id=excluded.project_id,
                title=excluded.title,
                summary=excluded.summary,
                archetype=excluded.archetype,
                hook=excluded.hook,
                emotional_arc_json=excluded.emotional_arc_json,
                estimated_duration=excluded.estimated_duration,
                recommended_formats_json=excluded.recommended_formats_json,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                story_id,
                project_id,
                title,
                summary,
                archetype,
                effective_status,
                hook,
                _json_dump_any(emotional_arc or []),
                estimated_duration,
                _json_dump_any(recommended_formats or []),
                effective_created,
                now,
                _json_dump_any(metadata or {}),
            ),
        )
        row = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
    return _row_story(row)


def get_story(story_id: str, *, db_path: str | Path | None = None) -> Story | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
    return _row_story(row) if row else None


def list_project_stories(project_id: str, *, status: str | None = None, db_path: str | Path | None = None) -> list[Story]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if status:
            if status not in STORY_STATUSES:
                raise ValueError(f"invalid story status: {status}")
            rows = conn.execute(
                "SELECT * FROM stories WHERE project_id = ? AND status = ? ORDER BY created_at, story_id",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM stories WHERE project_id = ? ORDER BY created_at, story_id", (project_id,)).fetchall()
    return [_row_story(row) for row in rows]


def update_story_status(story_id: str, status: str, *, db_path: str | Path | None = None) -> Story:
    if status not in STORY_STATUSES:
        raise ValueError(f"invalid story status: {status}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
        if current is None:
            raise ValueError(f"story '{story_id}' not found")
        conn.execute(
            "UPDATE stories SET status = ?, updated_at = ? WHERE story_id = ?",
            (status, _now_iso(), story_id),
        )
        row = conn.execute("SELECT * FROM stories WHERE story_id = ?", (story_id,)).fetchone()
    return _row_story(row)


def _story_moment_id(story_id: str, moment_id: str, sequence_order: int) -> str:
    raw = f"{story_id}|{moment_id}|{sequence_order}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"sm_{digest}"


def _validate_story_moment_relationship(story: Story, moment: Moment) -> None:
    if story.project_id != moment.project_id:
        raise ValueError("story moment belongs to a different project")


def add_story_moment(
    story_id: str,
    moment_id: str,
    narrative_role: str,
    sequence_order: int,
    *,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> StoryMoment:
    from .story_engine import NARRATIVE_ROLES as _roles

    if narrative_role not in _roles:
        raise ValueError(f"invalid narrative role: {narrative_role}")
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    moment = get_moment(moment_id, db_path=db_path)
    if moment is None:
        raise ValueError(f"moment '{moment_id}' not found")
    _validate_story_moment_relationship(story, moment)
    initialize_runtime_index(db_path)
    now = _now_iso()
    rel_id = _story_moment_id(story_id, moment_id, sequence_order)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO story_moments(story_moment_id, story_id, moment_id, narrative_role, sequence_order, created_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_moment_id) DO UPDATE SET
                narrative_role=excluded.narrative_role,
                metadata_json=excluded.metadata_json
            """,
            (rel_id, story_id, moment_id, narrative_role, sequence_order, now, _json_dump_any(metadata or {})),
        )
        row = conn.execute("SELECT * FROM story_moments WHERE story_moment_id = ?", (rel_id,)).fetchone()
    return _row_story_moment(row)


def remove_story_moment(story_id: str, moment_id: str, *, sequence_order: int | None = None, db_path: str | Path | None = None) -> bool:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        if sequence_order is not None:
            rel_id = _story_moment_id(story_id, moment_id, sequence_order)
            cursor = conn.execute("DELETE FROM story_moments WHERE story_moment_id = ?", (rel_id,))
        else:
            cursor = conn.execute("DELETE FROM story_moments WHERE story_id = ? AND moment_id = ?", (story_id, moment_id))
    return cursor.rowcount > 0


def list_story_moments(story_id: str, *, db_path: str | Path | None = None) -> list[StoryMoment]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM story_moments WHERE story_id = ? ORDER BY sequence_order, story_moment_id",
            (story_id,),
        ).fetchall()
    return [_row_story_moment(row) for row in rows]


def replace_story_moments(
    story_id: str,
    relationships: list[dict[str, Any]],
    *,
    db_path: str | Path | None = None,
) -> list[StoryMoment]:
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        conn.execute("DELETE FROM story_moments WHERE story_id = ?", (story_id,))
    result: list[StoryMoment] = []
    for rel in relationships:
        result.append(add_story_moment(
            story_id,
            rel["moment_id"],
            rel["narrative_role"],
            rel["sequence_order"],
            metadata=rel.get("metadata"),
            db_path=db_path,
        ))
    return result


def _resequence_story(story_id: str, *, db_path: str | Path | None = None) -> list[StoryMoment]:
    """Normalize story_moment sequence_order to contiguous 1..N values."""
    rels = list_story_moments(story_id, db_path=db_path)
    rels.sort(key=lambda rel: (rel.sequence_order, rel.story_moment_id))
    with runtime_db.transaction(db_path) as conn:
        for index, rel in enumerate(rels, start=1):
            conn.execute("UPDATE story_moments SET sequence_order = ? WHERE story_moment_id = ?", (index, rel.story_moment_id))
    return list_story_moments(story_id, db_path=db_path)


def update_story_moment(
    story_id: str,
    moment_id: str,
    *,
    narrative_role: str | None = None,
    sequence_order: int | None = None,
    db_path: str | Path | None = None,
) -> list[StoryMoment]:
    """Change a StoryMoment's narrative role or editorial sequence, then resequence."""
    from .story_engine import NARRATIVE_ROLES as _roles

    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    if narrative_role is not None and narrative_role not in _roles:
        raise ValueError(f"invalid narrative role: {narrative_role}")
    current = list_story_moments(story_id, db_path=db_path)
    target = next((rel for rel in current if rel.moment_id == moment_id), None)
    if target is None:
        raise ValueError(f"moment '{moment_id}' is not in story '{story_id}'")
    role = narrative_role or target.narrative_role
    seq = sequence_order if sequence_order is not None else target.sequence_order
    rel_id = _story_moment_id(story_id, moment_id, seq)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            "UPDATE story_moments SET narrative_role = ?, sequence_order = ?, story_moment_id = ? WHERE story_moment_id = ?",
            (role, seq, rel_id, target.story_moment_id),
        )
    return _resequence_story(story_id, db_path=db_path)


# ── Edit brief service ───────────────────────────────────────────────────────


def _edit_brief_id(story_id: str, format_treatment: str) -> str:
    raw = f"{story_id}|{format_treatment}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"eb_{digest}"


def upsert_edit_brief(
    *,
    project_id: str,
    story_id: str,
    format_treatment: str,
    status: str = "DRAFT",
    artifact_id: str | None = None,
    editorial_intent: str = "",
    target_duration: int | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> CanonicalEditBrief:
    if status not in EDIT_BRIEF_STATUSES:
        raise ValueError(f"invalid edit brief status: {status}")
    from .edit_brief import FORMAT_TREATMENTS as _formats

    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    if story.project_id != project_id:
        raise ValueError("edit brief story belongs to a different project")
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    brief_id = _edit_brief_id(story_id, format_treatment)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = ?", (brief_id,)).fetchone()
        effective_created = current["created_at"] if current else created
        conn.execute(
            """
            INSERT INTO edit_briefs(
                edit_brief_id, project_id, story_id, artifact_id, format_treatment, status,
                editorial_intent, target_duration, created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(story_id, format_treatment) DO UPDATE SET
                artifact_id=excluded.artifact_id,
                status=excluded.status,
                editorial_intent=excluded.editorial_intent,
                target_duration=excluded.target_duration,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                brief_id,
                project_id,
                story_id,
                artifact_id,
                format_treatment,
                status,
                editorial_intent,
                target_duration,
                effective_created,
                now,
                _json_dump_any(metadata or {}),
            ),
        )
        row = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = ?", (brief_id,)).fetchone()
    return _row_edit_brief(row)


def get_edit_brief(edit_brief_id: str, *, db_path: str | Path | None = None) -> CanonicalEditBrief | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = ?", (edit_brief_id,)).fetchone()
    return _row_edit_brief(row) if row else None


def list_story_edit_briefs(story_id: str, *, db_path: str | Path | None = None) -> list[CanonicalEditBrief]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM edit_briefs WHERE story_id = ? ORDER BY created_at, edit_brief_id", (story_id,)).fetchall()
    return [_row_edit_brief(row) for row in rows]


def list_project_edit_briefs(project_id: str, *, status: str | None = None, db_path: str | Path | None = None) -> list[CanonicalEditBrief]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if status:
            if status not in EDIT_BRIEF_STATUSES:
                raise ValueError(f"invalid edit brief status: {status}")
            rows = conn.execute(
                "SELECT * FROM edit_briefs WHERE project_id = ? AND status = ? ORDER BY created_at, edit_brief_id",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM edit_briefs WHERE project_id = ? ORDER BY created_at, edit_brief_id", (project_id,)).fetchall()
    return [_row_edit_brief(row) for row in rows]


def update_edit_brief_status(edit_brief_id: str, status: str, *, db_path: str | Path | None = None) -> CanonicalEditBrief:
    if status not in EDIT_BRIEF_STATUSES:
        raise ValueError(f"invalid edit brief status: {status}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = ?", (edit_brief_id,)).fetchone()
        if current is None:
            raise ValueError(f"edit brief '{edit_brief_id}' not found")
        conn.execute("UPDATE edit_briefs SET status = ?, updated_at = ? WHERE edit_brief_id = ?", (status, _now_iso(), edit_brief_id))
        row = conn.execute("SELECT * FROM edit_briefs WHERE edit_brief_id = ?", (edit_brief_id,)).fetchone()
    return _row_edit_brief(row)


def find_story_by_original_id(project_id: str, original_story_id: str, *, db_path: str | Path | None = None) -> Story | None:
    for story in list_project_stories(project_id, db_path=db_path):
        if story.metadata.get("original_story_id") == original_story_id:
            return story
    return None


def get_story_runtime_summary(story_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    """Assemble the narrow Story-focused runtime read model."""
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    rels = list_story_moments(story_id, db_path=db_path)
    moments: list[dict[str, Any]] = []
    for rel in rels:
        moment = get_moment(rel.moment_id, db_path=db_path)
        entry = {"sequence_order": rel.sequence_order, "narrative_role": rel.narrative_role}
        if moment:
            entry["moment"] = moment.to_dict()
        moments.append(entry)
    edit_briefs = [_edit_brief_data(brief) for brief in list_story_edit_briefs(story_id, db_path=db_path)]
    edls = [_edl_data(edl) for edl in list_story_edls(story_id, db_path=db_path)]
    renders = [_render_data(render) for render in list_story_renders(story_id, db_path=db_path)]
    exports = [_export_data(export) for export in list_story_exports(story_id, db_path=db_path)]
    return {
        "story": story.to_dict(),
        "ordered_moments": moments,
        "edit_briefs": edit_briefs,
        "edls": edls,
        "renders": renders,
        "exports": exports,
    }


def _edit_brief_data(brief: CanonicalEditBrief) -> dict[str, Any]:
    return brief.to_dict()


def _edl_data(edl: CanonicalEDL) -> dict[str, Any]:
    return edl.to_dict()


# ── EDL service ──────────────────────────────────────────────────────────────


def _edl_id(edit_brief_id: str, format_treatment: str) -> str:
    raw = f"{edit_brief_id}|{format_treatment}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"edl_{digest}"


def _validate_edl_chain(story: Story, brief: CanonicalEditBrief, project_id: str) -> None:
    if story.project_id != project_id or brief.project_id != project_id:
        raise ValueError("edl chain crosses project boundaries")
    if story.story_id != brief.story_id:
        raise ValueError("edit brief does not belong to the story")


def upsert_edl(
    *,
    project_id: str,
    story_id: str,
    edit_brief_id: str,
    format_treatment: str,
    status: str = "DRAFT",
    artifact_id: str | None = None,
    target_duration: int | None = None,
    estimated_duration: float | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> CanonicalEDL:
    if status not in EDL_STATUSES:
        raise ValueError(f"invalid edl status: {status}")
    from .edit_brief import FORMAT_TREATMENTS as _formats

    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    brief = get_edit_brief(edit_brief_id, db_path=db_path)
    if brief is None:
        raise ValueError(f"edit brief '{edit_brief_id}' not found")
    _validate_edl_chain(story, brief, project_id)
    if brief.format_treatment != format_treatment:
        raise ValueError("edl format must match edit brief format treatment")
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    edl_key = _edl_id(edit_brief_id, format_treatment)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM edls WHERE edl_id = ?", (edl_key,)).fetchone()
        effective_created = current["created_at"] if current else created
        conn.execute(
            """
            INSERT INTO edls(
                edl_id, project_id, story_id, edit_brief_id, artifact_id, format_treatment, status,
                target_duration, estimated_duration, created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edit_brief_id, format_treatment) DO UPDATE SET
                artifact_id=excluded.artifact_id,
                status=excluded.status,
                target_duration=excluded.target_duration,
                estimated_duration=excluded.estimated_duration,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                edl_key,
                project_id,
                story_id,
                edit_brief_id,
                artifact_id,
                format_treatment,
                status,
                target_duration,
                estimated_duration,
                effective_created,
                now,
                _json_dump_any(metadata or {}),
            ),
        )
        row = conn.execute("SELECT * FROM edls WHERE edl_id = ?", (edl_key,)).fetchone()
    return _row_edl(row)


def get_edl(edl_id: str, *, db_path: str | Path | None = None) -> CanonicalEDL | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM edls WHERE edl_id = ?", (edl_id,)).fetchone()
    return _row_edl(row) if row else None


def list_story_edls(story_id: str, *, db_path: str | Path | None = None) -> list[CanonicalEDL]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM edls WHERE story_id = ? ORDER BY created_at, edl_id", (story_id,)).fetchall()
    return [_row_edl(row) for row in rows]


def list_edit_brief_edls(edit_brief_id: str, *, db_path: str | Path | None = None) -> list[CanonicalEDL]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM edls WHERE edit_brief_id = ? ORDER BY created_at, edl_id", (edit_brief_id,)).fetchall()
    return [_row_edl(row) for row in rows]


def list_project_edls(project_id: str, *, status: str | None = None, db_path: str | Path | None = None) -> list[CanonicalEDL]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if status:
            if status not in EDL_STATUSES:
                raise ValueError(f"invalid edl status: {status}")
            rows = conn.execute(
                "SELECT * FROM edls WHERE project_id = ? AND status = ? ORDER BY created_at, edl_id",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM edls WHERE project_id = ? ORDER BY created_at, edl_id", (project_id,)).fetchall()
    return [_row_edl(row) for row in rows]


def update_edl_status(edl_id: str, status: str, *, db_path: str | Path | None = None) -> CanonicalEDL:
    if status not in EDL_STATUSES:
        raise ValueError(f"invalid edl status: {status}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM edls WHERE edl_id = ?", (edl_id,)).fetchone()
        if current is None:
            raise ValueError(f"edl '{edl_id}' not found")
        conn.execute("UPDATE edls SET status = ?, updated_at = ? WHERE edl_id = ?", (status, _now_iso(), edl_id))
        row = conn.execute("SELECT * FROM edls WHERE edl_id = ?", (edl_id,)).fetchone()
    return _row_edl(row)


# ── Render service ───────────────────────────────────────────────────────────


def _render_id(edl_id: str, render_profile: str, channel_preset_id: str | None = None) -> str:
    raw = f"{edl_id}|{render_profile}|{channel_preset_id or ''}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"r_{digest}"


def _validate_render_chain(story: Story, brief: CanonicalEditBrief, edl: CanonicalEDL, project_id: str, render_profile: str) -> None:
    if not (story.project_id == project_id == brief.project_id == edl.project_id):
        raise ValueError("render chain crosses project boundaries")
    if not (story.story_id == brief.story_id == edl.story_id):
        raise ValueError("render chain edit brief/edl does not belong to the story")
    if brief.edit_brief_id != edl.edit_brief_id:
        raise ValueError("render chain edit brief/edl mismatch")
    from .rendering import RENDER_MODES as _modes

    if render_profile not in _modes:
        raise ValueError(f"invalid render profile: {render_profile}")


def upsert_render(
    *,
    project_id: str,
    story_id: str,
    edit_brief_id: str,
    edl_id: str,
    render_profile: str,
    format_treatment: str,
    status: str = "QUEUED",
    review_state: str = "UNREVIEWED",
    channel_preset_id: str | None = None,
    platform: str | None = None,
    artifact_id: str | None = None,
    duration_seconds: float | None = None,
    width: int | None = None,
    height: int | None = None,
    fps: float | None = None,
    review_note: str | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> CanonicalRender:
    if status not in RENDER_STATUSES:
        raise ValueError(f"invalid render status: {status}")
    if review_state not in RENDER_REVIEW_STATES:
        raise ValueError(f"invalid render review state: {review_state}")
    from .edit_brief import FORMAT_TREATMENTS as _formats

    if format_treatment not in _formats:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    resolved_platform = platform
    if channel_preset_id:
        from .channel_models import resolve_channel_preset

        preset = resolve_channel_preset(channel_preset_id)
        validate_preset_compatibility(preset, format_treatment=format_treatment, render_profile=render_profile)
        resolved_platform = resolved_platform or preset.platform
    story = get_story(story_id, db_path=db_path)
    if story is None:
        raise ValueError(f"story '{story_id}' not found")
    brief = get_edit_brief(edit_brief_id, db_path=db_path)
    if brief is None:
        raise ValueError(f"edit brief '{edit_brief_id}' not found")
    edl = get_edl(edl_id, db_path=db_path)
    if edl is None:
        raise ValueError(f"edl '{edl_id}' not found")
    _validate_render_chain(story, brief, edl, project_id, render_profile)
    if format_treatment != edl.format_treatment:
        raise ValueError("render format must match edl format treatment")
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    render_key = _render_id(edl_id, render_profile, channel_preset_id)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_key,)).fetchone()
        effective_created = current["created_at"] if current else created
        conn.execute(
            """
            INSERT INTO renders(
                render_id, project_id, story_id, edit_brief_id, edl_id, artifact_id, format_treatment, render_profile,
                channel_preset_id, platform, status, review_state, duration_seconds, width, height, fps,
                reviewed_at, reviewed_by, review_note, created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(render_id) DO UPDATE SET
                artifact_id=excluded.artifact_id,
                status=excluded.status,
                review_state=excluded.review_state,
                duration_seconds=excluded.duration_seconds,
                width=excluded.width,
                height=excluded.height,
                fps=excluded.fps,
                review_note=excluded.review_note,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                render_key,
                project_id,
                story_id,
                edit_brief_id,
                edl_id,
                artifact_id,
                format_treatment,
                render_profile,
                channel_preset_id,
                resolved_platform,
                status,
                review_state,
                duration_seconds,
                width,
                height,
                fps,
                None,
                None,
                review_note,
                effective_created,
                now,
                _json_dump_any(metadata or {}),
            ),
        )
        row = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_key,)).fetchone()
    return _row_render(row)


def get_render(render_id: str, *, db_path: str | Path | None = None) -> CanonicalRender | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_id,)).fetchone()
    return _row_render(row) if row else None


def list_story_renders(story_id: str, *, db_path: str | Path | None = None) -> list[CanonicalRender]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM renders WHERE story_id = ? ORDER BY created_at, render_id", (story_id,)).fetchall()
    return [_row_render(row) for row in rows]


def list_edl_renders(edl_id: str, *, db_path: str | Path | None = None) -> list[CanonicalRender]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM renders WHERE edl_id = ? ORDER BY created_at, render_id", (edl_id,)).fetchall()
    return [_row_render(row) for row in rows]


def list_project_renders(project_id: str, *, status: str | None = None, review_state: str | None = None, db_path: str | Path | None = None) -> list[CanonicalRender]:
    initialize_runtime_index(db_path)
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if status:
        if status not in RENDER_STATUSES:
            raise ValueError(f"invalid render status: {status}")
        clauses.append("status = ?")
        params.append(status)
    if review_state:
        if review_state not in RENDER_REVIEW_STATES:
            raise ValueError(f"invalid render review state: {review_state}")
        clauses.append("review_state = ?")
        params.append(review_state)
    sql = "SELECT * FROM renders WHERE " + " AND ".join(clauses) + " ORDER BY created_at, render_id"
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_render(row) for row in rows]


def update_render_status(render_id: str, status: str, *, db_path: str | Path | None = None) -> CanonicalRender:
    if status not in RENDER_STATUSES:
        raise ValueError(f"invalid render status: {status}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_id,)).fetchone()
        if current is None:
            raise ValueError(f"render '{render_id}' not found")
        conn.execute("UPDATE renders SET status = ?, updated_at = ? WHERE render_id = ?", (status, _now_iso(), render_id))
        row = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_id,)).fetchone()
    return _row_render(row)


def update_render_review_state(
    render_id: str,
    review_state: str,
    *,
    reviewed_by: str | None = None,
    review_note: str | None = None,
    db_path: str | Path | None = None,
) -> CanonicalRender:
    if review_state not in RENDER_REVIEW_STATES:
        raise ValueError(f"invalid render review state: {review_state}")
    initialize_runtime_index(db_path)
    now = _now_iso()
    reviewed_at = None if review_state == "UNREVIEWED" else now
    reviewer = None if review_state == "UNREVIEWED" else reviewed_by
    note = None if review_state == "UNREVIEWED" else review_note
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_id,)).fetchone()
        if current is None:
            raise ValueError(f"render '{render_id}' not found")
        conn.execute(
            "UPDATE renders SET review_state = ?, reviewed_at = ?, reviewed_by = ?, review_note = ?, updated_at = ? WHERE render_id = ?",
            (review_state, reviewed_at, reviewer, note, now, render_id),
        )
        row = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render_id,)).fetchone()
    return _row_render(row)


def _render_data(render: CanonicalRender) -> dict[str, Any]:
    return render.to_dict()


def reconcile_interrupted_renders(project_id: str, *, active_render_ids: set[str] | None = None,
                                  db_path: str | Path | None = None) -> list[CanonicalRender]:
    """Mark stale RENDERING renders as FAILED/RENDER_INTERRUPTED.

    Rendering runs synchronously in the operator process. A persisted
    RENDERING render with no active execution is an interrupted render, not live
    work. Only renders whose ids are absent from ``active_render_ids`` are
    reconciled; never touches live work.
    """
    active = active_render_ids or set()
    reconciled: list[CanonicalRender] = []
    for render in list_project_renders(project_id, status="RENDERING", db_path=db_path):
        if render.render_id in active:
            continue
        updated = update_render_status(render.render_id, "FAILED", db_path=db_path)
        with runtime_db.transaction(db_path) as conn:
            conn.execute(
                "UPDATE renders SET metadata_json = ? WHERE render_id = ?",
                (_json_dump_any({**updated.metadata, "error_code": "RENDER_INTERRUPTED"}), render.render_id),
            )
            row = conn.execute("SELECT * FROM renders WHERE render_id = ?", (render.render_id,)).fetchone()
        reconciled.append(_row_render(row))
    return reconciled


# ── Export package service ───────────────────────────────────────────────────


def _export_id(render_id: str, channel_preset_id: str) -> str:
    raw = f"{render_id}|{channel_preset_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"export_{digest}"


def _validate_export_chain(render: CanonicalRender, project_id: str, story_id: str, channel_preset_id: str) -> None:
    if render.project_id != project_id or render.story_id != story_id:
        raise ValueError("export render does not belong to the story/project")
    from .channel_models import get_channel_preset

    preset = get_channel_preset(channel_preset_id)
    if preset is None:
        raise ValueError(f"unknown channel preset: {channel_preset_id}")


def upsert_export_package(
    *,
    project_id: str,
    story_id: str,
    render_id: str,
    channel_preset_id: str,
    video_artifact_id: str,
    status: str = "READY",
    thumbnail_artifact_id: str | None = None,
    caption: str = "",
    title: str = "",
    description: str = "",
    hashtags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
    created_at: str | None = None,
    db_path: str | Path | None = None,
) -> ExportPackage:
    if status not in EXPORT_STATUSES:
        raise ValueError(f"invalid export status: {status}")
    render = get_render(render_id, db_path=db_path)
    if render is None:
        raise ValueError(f"render '{render_id}' not found")
    _validate_export_chain(render, project_id, story_id, channel_preset_id)
    from .channel_models import get_channel_preset

    preset = get_channel_preset(channel_preset_id)
    if preset is None:
        raise ValueError(f"unknown channel preset: {channel_preset_id}")
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = created_at or now
    export_key = _export_id(render_id, channel_preset_id)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM export_packages WHERE export_id = ?", (export_key,)).fetchone()
        effective_created = current["created_at"] if current else created
        conn.execute(
            """
            INSERT INTO export_packages(
                export_id, project_id, story_id, render_id, channel_preset_id, platform, status,
                video_artifact_id, thumbnail_artifact_id, caption, title, description, hashtags_json,
                created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(render_id, channel_preset_id) DO UPDATE SET
                status=excluded.status,
                thumbnail_artifact_id=excluded.thumbnail_artifact_id,
                caption=excluded.caption,
                title=excluded.title,
                description=excluded.description,
                hashtags_json=excluded.hashtags_json,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                export_key,
                project_id,
                story_id,
                render_id,
                channel_preset_id,
                preset.platform,
                status,
                video_artifact_id,
                thumbnail_artifact_id,
                caption,
                title,
                description,
                _json_dump_any(hashtags or []),
                effective_created,
                now,
                _json_dump_any(metadata or {}),
            ),
        )
        row = conn.execute("SELECT * FROM export_packages WHERE export_id = ?", (export_key,)).fetchone()
    return _row_export(row)


def get_export_package(export_id: str, *, db_path: str | Path | None = None) -> ExportPackage | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM export_packages WHERE export_id = ?", (export_id,)).fetchone()
    return _row_export(row) if row else None


def list_story_exports(story_id: str, *, db_path: str | Path | None = None) -> list[ExportPackage]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM export_packages WHERE story_id = ? ORDER BY created_at, export_id", (story_id,)).fetchall()
    return [_row_export(row) for row in rows]


def list_render_exports(render_id: str, *, db_path: str | Path | None = None) -> list[ExportPackage]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM export_packages WHERE render_id = ? ORDER BY created_at, export_id", (render_id,)).fetchall()
    return [_row_export(row) for row in rows]


def list_project_exports(project_id: str, *, status: str | None = None, db_path: str | Path | None = None) -> list[ExportPackage]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if status:
            if status not in EXPORT_STATUSES:
                raise ValueError(f"invalid export status: {status}")
            rows = conn.execute(
                "SELECT * FROM export_packages WHERE project_id = ? AND status = ? ORDER BY created_at, export_id",
                (project_id, status),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM export_packages WHERE project_id = ? ORDER BY created_at, export_id", (project_id,)).fetchall()
    return [_row_export(row) for row in rows]


def update_export_status(export_id: str, status: str, *, db_path: str | Path | None = None) -> ExportPackage:
    if status not in EXPORT_STATUSES:
        raise ValueError(f"invalid export status: {status}")
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM export_packages WHERE export_id = ?", (export_id,)).fetchone()
        if current is None:
            raise ValueError(f"export package '{export_id}' not found")
        conn.execute("UPDATE export_packages SET status = ?, updated_at = ? WHERE export_id = ?", (status, _now_iso(), export_id))
        row = conn.execute("SELECT * FROM export_packages WHERE export_id = ?", (export_id,)).fetchone()
    return _row_export(row)


def export_package_completeness(export_id: str, *, db_path: str | Path | None = None) -> dict[str, Any]:
    export = get_export_package(export_id, db_path=db_path)
    if export is None:
        raise ValueError(f"export package '{export_id}' not found")
    return {
        "video_present": bool(export.video_artifact_id),
        "caption_present": bool(export.caption.strip()),
        "thumbnail_present": bool(export.thumbnail_artifact_id),
        "ready_for_delivery": bool(export.video_artifact_id) and export.status == "READY",
    }


def _export_data(export: ExportPackage) -> dict[str, Any]:
    return export.to_dict()


# ── Edit Plan service ────────────────────────────────────────────────────────


def upsert_edit_plan(plan: EditPlan, *, db_path: str | Path | None = None) -> EditPlan:
    initialize_runtime_index(db_path)
    now = _now_iso()
    created = plan.created_at or now
    updated = plan.updated_at or now
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO edit_plans(
                edit_plan_id, project_id, story_id, edit_brief_id, title, target_platform,
                target_duration, aspect_ratio, hook_text, story_archetype, status, renderer,
                created_at, updated_at, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edit_plan_id) DO UPDATE SET
                title=excluded.title,
                target_platform=excluded.target_platform,
                target_duration=excluded.target_duration,
                aspect_ratio=excluded.aspect_ratio,
                hook_text=excluded.hook_text,
                story_archetype=excluded.story_archetype,
                status=excluded.status,
                renderer=excluded.renderer,
                updated_at=excluded.updated_at,
                metadata_json=excluded.metadata_json
            """,
            (
                plan.edit_plan_id, plan.project_id, plan.story_id, plan.edit_brief_id, plan.title,
                plan.target_platform, plan.target_duration, plan.aspect_ratio, plan.hook_text,
                plan.story_archetype, plan.status, plan.renderer, created, updated,
                _json_dump_any(plan.metadata),
            ),
        )
        row = conn.execute("SELECT * FROM edit_plans WHERE edit_plan_id = ?", (plan.edit_plan_id,)).fetchone()
    return _row_edit_plan(row)


def get_edit_plan(edit_plan_id: str, *, db_path: str | Path | None = None) -> EditPlan | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM edit_plans WHERE edit_plan_id = ?", (edit_plan_id,)).fetchone()
    return _row_edit_plan(row) if row else None


def list_story_edit_plans(story_id: str, *, db_path: str | Path | None = None) -> list[EditPlan]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM edit_plans WHERE story_id = ? ORDER BY created_at, edit_plan_id", (story_id,)).fetchall()
    return [_row_edit_plan(row) for row in rows]


def upsert_edit_beat(beat: EditBeat, *, db_path: str | Path | None = None) -> EditBeat:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO edit_beats(
                edit_beat_id, edit_plan_id, sequence_order, narrative_role, source_moment_id,
                purpose, description, source_start, source_end, target_duration, crop_intent,
                speed_intent, text_overlay, caption_intent, audio_intent, transition_intent,
                motion_graphic_intent, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(edit_beat_id) DO UPDATE SET
                sequence_order=excluded.sequence_order,
                narrative_role=excluded.narrative_role,
                source_moment_id=excluded.source_moment_id,
                purpose=excluded.purpose,
                description=excluded.description,
                source_start=excluded.source_start,
                source_end=excluded.source_end,
                target_duration=excluded.target_duration,
                crop_intent=excluded.crop_intent,
                speed_intent=excluded.speed_intent,
                text_overlay=excluded.text_overlay,
                caption_intent=excluded.caption_intent,
                audio_intent=excluded.audio_intent,
                transition_intent=excluded.transition_intent,
                motion_graphic_intent=excluded.motion_graphic_intent,
                metadata_json=excluded.metadata_json
            """,
            (
                beat.edit_beat_id, beat.edit_plan_id, beat.sequence_order, beat.narrative_role,
                beat.source_moment_id, beat.purpose, beat.description, beat.source_start, beat.source_end,
                beat.target_duration, beat.crop_intent, beat.speed_intent, beat.text_overlay,
                beat.caption_intent, beat.audio_intent, beat.transition_intent, beat.motion_graphic_intent,
                _json_dump_any(beat.metadata),
            ),
        )
        row = conn.execute("SELECT * FROM edit_beats WHERE edit_beat_id = ?", (beat.edit_beat_id,)).fetchone()
    return _row_edit_beat(row)


def list_edit_beats(edit_plan_id: str, *, db_path: str | Path | None = None) -> list[EditBeat]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute("SELECT * FROM edit_beats WHERE edit_plan_id = ? ORDER BY sequence_order, edit_beat_id", (edit_plan_id,)).fetchall()
    return [_row_edit_beat(row) for row in rows]


def upsert_timeline_instruction(instruction: TimelineInstruction, *, db_path: str | Path | None = None) -> TimelineInstruction:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO timeline_instructions(
                instruction_id, edit_plan_id, edit_beat_id, instruction_type, source_artifact_id,
                source_in, source_out, timeline_start, timeline_duration, crop_json, scale_json,
                position_json, speed, freeze_frame, opacity, text, caption_style_json, audio_gain,
                music_cue, sfx_cue, transition, motion_graphic_template_json,
                motion_graphic_parameters_json, metadata_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(instruction_id) DO UPDATE SET
                instruction_type=excluded.instruction_type,
                source_artifact_id=excluded.source_artifact_id,
                source_in=excluded.source_in,
                source_out=excluded.source_out,
                timeline_start=excluded.timeline_start,
                timeline_duration=excluded.timeline_duration,
                crop_json=excluded.crop_json,
                scale_json=excluded.scale_json,
                position_json=excluded.position_json,
                speed=excluded.speed,
                freeze_frame=excluded.freeze_frame,
                opacity=excluded.opacity,
                text=excluded.text,
                caption_style_json=excluded.caption_style_json,
                audio_gain=excluded.audio_gain,
                music_cue=excluded.music_cue,
                sfx_cue=excluded.sfx_cue,
                transition=excluded.transition,
                motion_graphic_template_json=excluded.motion_graphic_template_json,
                motion_graphic_parameters_json=excluded.motion_graphic_parameters_json,
                metadata_json=excluded.metadata_json
            """,
            (
                instruction.instruction_id, instruction.edit_plan_id, instruction.edit_beat_id,
                instruction.instruction_type, instruction.source_artifact_id, instruction.source_in,
                instruction.source_out, instruction.timeline_start, instruction.timeline_duration,
                _json_dump_any(instruction.crop), _json_dump_any(instruction.scale),
                _json_dump_any(instruction.position), instruction.speed, 1 if instruction.freeze_frame else 0,
                instruction.opacity, instruction.text, _json_dump_any(instruction.caption_style),
                instruction.audio_gain, instruction.music_cue, instruction.sfx_cue, instruction.transition,
                _json_dump_any(instruction.motion_graphic_template), _json_dump_any(instruction.motion_graphic_parameters),
                _json_dump_any(instruction.metadata),
            ),
        )
        row = conn.execute("SELECT * FROM timeline_instructions WHERE instruction_id = ?", (instruction.instruction_id,)).fetchone()
    return _row_timeline_instruction(row)


def list_timeline_instructions(edit_plan_id: str, *, db_path: str | Path | None = None) -> list[TimelineInstruction]:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(
            "SELECT * FROM timeline_instructions WHERE edit_plan_id = ? ORDER BY COALESCE(timeline_start, 0), instruction_id",
            (edit_plan_id,),
        ).fetchall()
    return [_row_timeline_instruction(row) for row in rows]


def create_pipeline_run(
    *,
    project_id: str,
    stage: str,
    status: str = "QUEUED",
    progress_current: int | None = None,
    progress_total: int | None = None,
    run_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> PipelineRun:
    if status not in RUN_STATES:
        raise ValueError(f"invalid pipeline run status: {status}")
    initialize_runtime_index(db_path)
    now = _now_iso()
    started_at = now if status == "RUNNING" else None
    finished_at = now if status in {"SUCCEEDED", "FAILED", "CANCELLED"} else None
    run_key = run_id or f"run_{uuid.uuid4().hex}"
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_runs(run_id, project_id, stage, status, started_at, finished_at, progress_current, progress_total, error_code, error_message, retry_count, created_at, metadata_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, 0, ?, ?)
            """,
            (run_key, project_id, stage, status, started_at, finished_at, progress_current, progress_total, now, _json_dump_any(metadata or {})),
        )
        row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_key,)).fetchone()
    return _row_run(row)


def update_pipeline_run(
    run_id: str,
    *,
    status: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    retry_count: int | None = None,
    metadata: dict[str, Any] | None = None,
    db_path: str | Path | None = None,
) -> PipelineRun:
    initialize_runtime_index(db_path)
    with runtime_db.transaction(db_path) as conn:
        current = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
        if current is None:
            raise ValueError(f"pipeline run '{run_id}' not found")
        next_status = status or current["status"]
        if next_status not in RUN_STATES:
            raise ValueError(f"invalid pipeline run status: {next_status}")
        now = _now_iso()
        started_at = current["started_at"] or (now if next_status == "RUNNING" else None)
        finished_at = current["finished_at"]
        if next_status in {"SUCCEEDED", "FAILED", "CANCELLED"} and not finished_at:
            finished_at = now
        next_metadata = _json_load_any(current["metadata_json"], {}) if "metadata_json" in current.keys() else {}
        if metadata:
            next_metadata.update(metadata)
        conn.execute(
            """
            UPDATE pipeline_runs
            SET status = ?, started_at = ?, finished_at = ?, progress_current = ?, progress_total = ?,
                error_code = ?, error_message = ?, retry_count = ?, metadata_json = ?
            WHERE run_id = ?
            """,
            (
                next_status,
                started_at,
                finished_at,
                progress_current if progress_current is not None else current["progress_current"],
                progress_total if progress_total is not None else current["progress_total"],
                error_code if error_code is not None else current["error_code"],
                error_message if error_message is not None else current["error_message"],
                retry_count if retry_count is not None else current["retry_count"],
                _json_dump_any(next_metadata),
                run_id,
            ),
        )
        row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_run(row)


def get_latest_pipeline_run(project_id: str, *, stage: str | None = None, db_path: str | Path | None = None) -> PipelineRun | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if stage:
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE project_id = ? AND stage = ? ORDER BY rowid DESC LIMIT 1",
                (project_id, stage),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM pipeline_runs WHERE project_id = ? ORDER BY rowid DESC LIMIT 1",
                (project_id,),
            ).fetchone()
    return _row_run(row) if row else None


def get_pipeline_run(run_id: str, *, db_path: str | Path | None = None) -> PipelineRun | None:
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        row = conn.execute("SELECT * FROM pipeline_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_run(row) if row else None


def list_pipeline_runs(
    project_id: str,
    *,
    stage: str | None = None,
    status: str | None = None,
    db_path: str | Path | None = None,
) -> list[PipelineRun]:
    initialize_runtime_index(db_path)
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if stage:
        clauses.append("stage = ?")
        params.append(stage)
    if status:
        clauses.append("status = ?")
        params.append(status)
    sql = "SELECT * FROM pipeline_runs WHERE " + " AND ".join(clauses) + " ORDER BY rowid DESC"
    with closing(runtime_db.connect(db_path)) as conn:
        rows = conn.execute(sql, params).fetchall()
    return [_row_run(row) for row in rows]


def mark_interrupted_run(
    run_id: str,
    *,
    error_code: str = "ANALYSIS_INTERRUPTED",
    error_message: str = "Previous analysis was interrupted before completion.",
    db_path: str | Path | None = None,
) -> PipelineRun:
    run = update_pipeline_run(run_id, status="FAILED", error_code=error_code, error_message=error_message, db_path=db_path)
    append_pipeline_event(
        project_id=run.project_id,
        run_id=run.run_id,
        event_type="RUN_INTERRUPTED",
        stage=run.stage,
        message=error_message,
        metadata={"error_code": error_code},
        db_path=db_path,
    )
    return run


def append_pipeline_event(
    *,
    project_id: str,
    event_type: str,
    stage: str,
    message: str = "",
    run_id: str | None = None,
    progress_current: int | None = None,
    progress_total: int | None = None,
    metadata: dict[str, Any] | None = None,
    event_id: str | None = None,
    db_path: str | Path | None = None,
) -> PipelineEvent:
    initialize_runtime_index(db_path)
    event_key = event_id or f"evt_{uuid.uuid4().hex}"
    now = _now_iso()
    with runtime_db.transaction(db_path) as conn:
        conn.execute(
            """
            INSERT INTO pipeline_events(event_id, run_id, project_id, event_type, stage, message, progress_current, progress_total, created_at, metadata)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (event_key, run_id, project_id, event_type, stage, message, progress_current, progress_total, now, _json_dumps(metadata)),
        )
        row = conn.execute("SELECT * FROM pipeline_events WHERE event_id = ?", (event_key,)).fetchone()
    return _row_event(row)


def list_pipeline_events(*, run_id: str | None = None, project_id: str | None = None, db_path: str | Path | None = None) -> list[PipelineEvent]:
    if not run_id and not project_id:
        raise ValueError("run_id or project_id is required")
    initialize_runtime_index(db_path)
    with closing(runtime_db.connect(db_path)) as conn:
        if run_id and project_id:
            rows = conn.execute(
                "SELECT * FROM pipeline_events WHERE run_id = ? AND project_id = ? ORDER BY rowid",
                (run_id, project_id),
            ).fetchall()
        elif run_id:
            rows = conn.execute("SELECT * FROM pipeline_events WHERE run_id = ? ORDER BY rowid", (run_id,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM pipeline_events WHERE project_id = ? ORDER BY rowid", (project_id,)).fetchall()
    return [_row_event(row) for row in rows]


def get_project_runtime_summary(
    job_id: str,
    *,
    jobs_dir: str | Path | None = None,
    db_path: str | Path | None = None,
    event_limit: int = 20,
) -> dict[str, Any]:
    """Assemble the narrow Operator Console runtime read model for a project."""
    try:
        project = index_existing_project(job_id, jobs_dir=jobs_dir, db_path=db_path)
    except (JobNotFoundError, ValueError, OSError, json.JSONDecodeError):
        project = get_project_by_job_id(job_id, db_path=db_path) or get_project(job_id, db_path=db_path)
        if project is None:
            raise JobNotFoundError(f"project '{job_id}' not found in runtime index") from None
    analysis_runs = list_pipeline_runs(project.project_id, stage=ANALYSIS_STAGE, db_path=db_path)
    latest = analysis_runs[0] if analysis_runs else None
    previous = analysis_runs[1] if len(analysis_runs) > 1 else None
    events = list_pipeline_events(project_id=project.project_id, run_id=latest.run_id, db_path=db_path) if latest else []
    if event_limit > 0:
        events = events[-event_limit:]
    artifacts = list_project_artifacts(project.project_id, db_path=db_path)
    moments = list_project_moments(project.project_id, db_path=db_path)
    moment_counts = {
        "moment_count": len(moments),
        "reviewed_count": len([moment for moment in moments if moment.review_state != "UNREVIEWED"]),
        "unreviewed_count": len([moment for moment in moments if moment.review_state == "UNREVIEWED"]),
        "keep_count": len([moment for moment in moments if moment.review_state == "KEEP"]),
        "reject_count": len([moment for moment in moments if moment.review_state == "REJECT"]),
        "strong_count": len([moment for moment in moments if moment.review_state == "STRONG"]),
        "must_use_count": len([moment for moment in moments if moment.review_state == "MUST_USE"]),
        "relation_count": len(list_project_moment_relations(project.project_id, db_path=db_path)),
    }
    stories = list_project_stories(project.project_id, db_path=db_path)
    story_summary = {
        "story_count": len(stories),
        "suggested_count": len([story for story in stories if story.status == "SUGGESTED"]),
        "approved_count": len([story for story in stories if story.status == "APPROVED"]),
        "rejected_count": len([story for story in stories if story.status == "REJECTED"]),
    }
    story_list = []
    for story in stories:
        story_list.append({
            **story.to_dict(),
            "moment_count": len(list_story_moments(story.story_id, db_path=db_path)),
        })
    edit_briefs = list_project_edit_briefs(project.project_id, db_path=db_path)
    edit_brief_summary = {
        "edit_brief_count": len(edit_briefs),
        "ready_edit_brief_count": len([brief for brief in edit_briefs if brief.status == "READY"]),
        "failed_edit_brief_count": len([brief for brief in edit_briefs if brief.status == "FAILED"]),
    }
    edls = list_project_edls(project.project_id, db_path=db_path)
    edl_summary = {
        "edl_count": len(edls),
        "ready_edl_count": len([edl for edl in edls if edl.status == "READY"]),
        "failed_edl_count": len([edl for edl in edls if edl.status == "FAILED"]),
    }
    renders = list_project_renders(project.project_id, db_path=db_path)
    render_summary = {
        "render_count": len(renders),
        "ready_render_count": len([render for render in renders if render.status == "READY"]),
        "approved_render_count": len([render for render in renders if render.review_state == "APPROVED"]),
        "needs_changes_render_count": len([render for render in renders if render.review_state == "NEEDS_CHANGES"]),
        "failed_render_count": len([render for render in renders if render.status == "FAILED"]),
    }
    exports = list_project_exports(project.project_id, db_path=db_path)
    export_summary = {
        "export_count": len(exports),
        "ready_export_count": len([export for export in exports if export.status == "READY"]),
        "delivered_export_count": len([export for export in exports if export.status == "DELIVERED"]),
        "failed_export_count": len([export for export in exports if export.status == "FAILED"]),
    }
    return {
        "runtime_available": True,
        "project": _project_data(project),
        "analysis": _run_data(latest),
        "events": [_event_data(event) for event in events],
        "artifacts": [_artifact_data(artifact) for artifact in artifacts],
        "moments": [_moment_data(moment) for moment in moments[:20]],
        "moment_summary": moment_counts,
        "stories": story_list,
        "story_summary": story_summary,
        "edit_brief_summary": edit_brief_summary,
        "edl_summary": edl_summary,
        "render_summary": render_summary,
        "export_summary": export_summary,
        "lineage": {
            "parent_project_id": project.parent_project_id,
            "source_project_id": project.source_project_id,
            "is_duplicate": bool(project.parent_project_id),
            "reuse_mode": project.reuse_mode,
            "analysis_reused": bool(project.reuse_mode in ("SOURCE_AND_ANALYSIS", "SOURCE_ANALYSIS_AND_MOMENTS")) and bool(moments),
        },
        "analysis_run_count": len(analysis_runs),
        "latest_run_id": latest.run_id if latest else None,
        "previous_run_status": previous.status if previous else None,
    }


def _project_display_name(job: dict, intake: dict | None) -> str:
    if isinstance(intake, dict):
        match = intake.get("match") if isinstance(intake.get("match"), dict) else {}
        event = match.get("event_name") or match.get("name")
        if isinstance(event, str) and event.strip():
            return event.strip()
        pilot = intake.get("pilot") if isinstance(intake.get("pilot"), dict) else {}
        title = pilot.get("title") or pilot.get("pilot_id")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return str(job.get("pilot_id") or job.get("job_id") or "Project")


def _read_intake(job: dict) -> dict | None:
    path = job.get("intake_manifest_path")
    if not isinstance(path, str) or not path.strip():
        return None
    intake_path = Path(path)
    if not intake_path.exists() or not intake_path.is_file():
        return None
    try:
        data = json.loads(intake_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _register_if_exists(project_id: str, artifact_type: str, path: Path, *, parent_artifact_id: str | None = None, db_path: str | Path | None = None) -> Artifact | None:
    if not path.exists():
        return None
    return register_artifact(
        project_id=project_id,
        artifact_type=artifact_type,
        path=path,
        parent_artifact_id=parent_artifact_id,
        metadata={"indexed_from": "existing_project"},
        db_path=db_path,
    )


def index_existing_project(job_id: str, *, jobs_dir: str | Path | None = None, db_path: str | Path | None = None) -> Project:
    """Lazily index an existing pilot job without modifying its artifacts."""
    jobs_root = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    job = read_job(job_id, jobs_dir=jobs_root)
    intake = _read_intake(job)
    profile = str(job.get("project_id") or "football")
    try:
        sport = str(resolve_project_profile(profile).get("sport") or profile)
    except Exception:
        sport = profile
    project_id = str(job.get("job_id") or job_id)
    existing = get_project(project_id, db_path=db_path)
    parent_id = existing.parent_project_id if existing else None
    source_id = existing.source_project_id if existing else project_id
    reuse = existing.reuse_mode if existing else ""
    config = intake.get("configuration") if isinstance(intake, dict) and isinstance(intake.get("configuration"), dict) else {}
    strategy = existing.analysis_strategy if existing else str(config.get("analysis_strategy") or "TRANSCRIPT_FIRST")
    project = upsert_project(
        project_id=project_id,
        job_id=project_id,
        profile=profile,
        sport=sport,
        display_name=_project_display_name(job, intake),
        status=str(job.get("current_state") or ""),
        parent_project_id=parent_id,
        source_project_id=source_id,
        reuse_mode=reuse,
        analysis_strategy=strategy,
        created_at=str(job.get("created_at") or _now_iso()),
        updated_at=str(job.get("updated_at") or _now_iso()),
        db_path=db_path,
    )

    intake_artifact = None
    intake_path = job.get("intake_manifest_path")
    if isinstance(intake_path, str) and intake_path.strip():
        intake_artifact = _register_if_exists(project.project_id, "intake_manifest", Path(intake_path), db_path=db_path)

    source_artifact = None
    if isinstance(intake, dict):
        media = intake.get("media") if isinstance(intake.get("media"), dict) else {}
        raw_source = media.get("local_file_path")
        if isinstance(raw_source, str) and raw_source.strip():
            source_artifact = _register_if_exists(
                project.project_id,
                "source_media",
                Path(raw_source),
                parent_artifact_id=intake_artifact.artifact_id if intake_artifact else None,
                db_path=db_path,
            )
    if source_artifact:
        project = upsert_project(
            project_id=project.project_id,
            job_id=project.job_id,
            profile=project.profile,
            sport=project.sport,
            display_name=project.display_name,
            status=project.status,
            source_artifact_id=source_artifact.artifact_id,
            parent_project_id=project.parent_project_id,
            source_project_id=project.source_project_id,
            reuse_mode=project.reuse_mode,
            analysis_strategy=project.analysis_strategy,
            created_at=project.created_at,
            updated_at=project.updated_at,
            db_path=db_path,
        )

    analysis_dir = jobs_root / "ANALYSIS"
    for artifact_type, path in (
        ("analysis_state", analysis_dir / f"{job_id}_analysis.json"),
        ("analysis_moments", analysis_dir / f"{job_id}_moments.json"),
        ("analysis_manifest", analysis_dir / f"{job_id}_manifest.csv"),
    ):
        _register_if_exists(project.project_id, artifact_type, path, db_path=db_path)
    return project


def list_indexed_or_existing_projects(*, jobs_dir: str | Path | None = None, db_path: str | Path | None = None) -> list[Project]:
    """Return runtime-indexed projects, lazily indexing existing pilot jobs."""
    from .pilot import list_jobs

    job_ids: list[str] = []
    for row in list_jobs(jobs_dir=jobs_dir):
        job_id = row.get("job_id")
        if not isinstance(job_id, str) or not job_id:
            continue
        job_ids.append(job_id)
        try:
            index_existing_project(job_id, jobs_dir=jobs_dir, db_path=db_path)
        except (JobNotFoundError, ValueError, OSError, json.JSONDecodeError):
            continue
    if not job_ids:
        return []
    indexed = list_projects(db_path=db_path)
    visible = set(job_ids)
    return [project for project in indexed if project.job_id in visible]


# ── Project duplication / reuse ──────────────────────────────────────────────

REUSE_MODES = frozenset({"SOURCE_ONLY", "SOURCE_AND_ANALYSIS", "SOURCE_ANALYSIS_AND_MOMENTS"})
_REUSE_ARTIFACT_TYPES = {
    "SOURCE_ONLY": ("source_media",),
    "SOURCE_AND_ANALYSIS": ("source_media", "transcript", "analysis_state", "analysis_moments", "analysis_manifest"),
    "SOURCE_ANALYSIS_AND_MOMENTS": ("source_media", "transcript", "analysis_state", "analysis_moments", "analysis_manifest"),
}


def _duplicate_moment_id(new_project_id: str, origin_moment_id: str) -> str:
    raw = f"{new_project_id}|{origin_moment_id}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]
    return f"mom_{digest}"


def duplicate_project(
    *,
    source_project_id: str,
    new_project_id: str,
    display_name: str,
    profile: str | None = None,
    sport: str | None = None,
    reuse_mode: str = "SOURCE_ANALYSIS_AND_MOMENTS",
    analysis_strategy: str | None = None,
    db_path: str | Path | None = None,
) -> dict[str, Any]:
    """Create a new project that reuses source intelligence without copying files.

    The duplicated project records lineage, reuses compatible source artifacts
    by reference, and clones canonical Moments with review state reset. It does
    not copy Stories/Edit Briefs/EDLs/Renders/Exports.
    """
    if reuse_mode not in REUSE_MODES:
        raise ValueError(f"invalid reuse mode: {reuse_mode}")
    source = get_project(source_project_id, db_path=db_path)
    if source is None:
        raise ValueError(f"source project '{source_project_id}' not found")
    if get_project(new_project_id, db_path=db_path) is not None:
        raise ValueError(f"project '{new_project_id}' already exists")
    target_profile = profile or source.profile
    target_sport = sport or source.sport
    target_strategy = normalize_analysis_strategy(analysis_strategy or source.analysis_strategy)
    if target_sport != source.sport:
        raise ValueError(f"cannot reuse '{source.sport}' intelligence into '{target_sport}' project")
    if reuse_mode == "SOURCE_ANALYSIS_AND_MOMENTS":
        source_moments = list_project_moments(source.project_id, db_path=db_path)
        if not source_moments:
            raise ValueError(f"source project '{source_project_id}' has no canonical moments to reuse")
    else:
        source_moments = []
    effective_source_id = source.source_project_id or source.project_id

    now = _now_iso()
    project = upsert_project(
        project_id=new_project_id,
        job_id=new_project_id,
        profile=target_profile,
        sport=target_sport,
        display_name=display_name,
        status="READY",
        parent_project_id=source.project_id,
        source_project_id=effective_source_id,
        reuse_mode=reuse_mode,
        analysis_strategy=target_strategy,
        created_at=now,
        updated_at=now,
        db_path=db_path,
    )

    reused_artifacts: list[Artifact] = []
    for artifact in list_project_artifacts(source.project_id, db_path=db_path):
        if artifact.artifact_type not in _REUSE_ARTIFACT_TYPES[reuse_mode]:
            continue
        reused_artifacts.append(register_artifact(
            project_id=new_project_id,
            artifact_type=artifact.artifact_type,
            path=artifact.path,
            mime_type=artifact.mime_type,
            metadata={
                **artifact.metadata,
                "reused": True,
                "reused_from_project_id": source.project_id,
                "reused_from_artifact_id": artifact.artifact_id,
                "reuse_reason": reuse_mode,
            },
            db_path=db_path,
        ))

    cloned_moments: list[Moment] = []
    if reuse_mode == "SOURCE_ANALYSIS_AND_MOMENTS":
        reused_moments_artifact = next((a for a in reused_artifacts if a.artifact_type == "analysis_moments"), None)
        for moment in source_moments:
            cloned = upsert_moment(Moment(
                moment_id=_duplicate_moment_id(new_project_id, moment.moment_id),
                project_id=new_project_id,
                source_artifact_id=reused_moments_artifact.artifact_id if reused_moments_artifact else None,
                sport=moment.sport,
                universal_event_type=moment.universal_event_type,
                sport_event_type=moment.sport_event_type,
                start_seconds=moment.start_seconds,
                peak_seconds=moment.peak_seconds,
                end_seconds=moment.end_seconds,
                participants=moment.participants,
                team=moment.team,
                signals=moment.signals,
                emotion=moment.emotion,
                importance=moment.importance,
                confidence=moment.confidence,
                review_state="UNREVIEWED",
                origin_moment_id=moment.moment_id,
                origin_project_id=source.project_id,
                metadata={**moment.metadata, "duplicated": True, "source_moment_id": moment.moment_id},
            ), db_path=db_path)
            cloned_moments.append(cloned)

    return {
        "ok": True,
        "project": project.to_dict(),
        "source_project_id": effective_source_id,
        "reuse_mode": reuse_mode,
        "reused_artifact_count": len(reused_artifacts),
        "cloned_moment_count": len(cloned_moments),
        "cloned_moment_ids": [moment.moment_id for moment in cloned_moments],
        "reused_artifact_ids": [artifact.artifact_id for artifact in reused_artifacts],
    }
