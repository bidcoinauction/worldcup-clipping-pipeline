"""Deployment/runtime helpers for Clipper hosted demo mode."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any


CLIPPER_ENV = "CLIPPER_ENV"
DEMO_ENV_VALUE = "demo"
DEMO_DISABLED_MESSAGE = "Rendering and local media processing are not enabled in this hosted demo environment."


ROOT = Path(__file__).resolve().parents[1]
DEMO_DATASET = Path(__file__).resolve().parent / "console_demo_dataset.json"
DEMO_RUNTIME_ROOT = Path(tempfile.gettempdir()) / "clipper-vercel-demo"
DEMO_RUNTIME_DB = DEMO_RUNTIME_ROOT / "runtime.sqlite3"
DEMO_JOBS_DIR = DEMO_RUNTIME_ROOT / "jobs"
DEMO_INTAKES_DIR = DEMO_RUNTIME_ROOT / "intakes"
DEMO_SEED_MARKER = DEMO_RUNTIME_ROOT / ".seeded-v1"


def is_demo_mode() -> bool:
    return os.environ.get(CLIPPER_ENV, "").strip().lower() == DEMO_ENV_VALUE


def setup_demo_environment() -> None:
    """Point runtime paths at an ephemeral seeded demo dataset when requested."""
    if not is_demo_mode():
        return
    os.environ.setdefault("STADIUM_RUNTIME_DB", str(DEMO_RUNTIME_DB))
    os.environ.setdefault("STADIUM_RUNTIME_BACKUPS", str(DEMO_RUNTIME_ROOT / "backups"))
    os.environ.setdefault("STADIUM_PILOT_JOBS_DIR", str(DEMO_JOBS_DIR))
    os.environ.setdefault("STADIUM_PILOT_INTAKE_ROOT", str(DEMO_RUNTIME_ROOT))
    seed_demo_runtime(force=os.environ.get("CLIPPER_DEMO_RESET", "").strip() == "1")


def demo_disabled_response(action: str | None = None) -> dict[str, Any]:
    label = str(action or "action").replace("_", " ").strip() or "action"
    return {
        "ok": False,
        "error_code": "HOSTED_DEMO_READ_ONLY",
        "error": DEMO_DISABLED_MESSAGE,
        "message": f"{label.title()} is disabled for the hosted demo.",
    }


def seed_demo_runtime(*, force: bool = False) -> None:
    if force and DEMO_RUNTIME_ROOT.exists():
        shutil.rmtree(DEMO_RUNTIME_ROOT)
    DEMO_RUNTIME_ROOT.mkdir(parents=True, exist_ok=True)
    DEMO_JOBS_DIR.mkdir(parents=True, exist_ok=True)
    DEMO_INTAKES_DIR.mkdir(parents=True, exist_ok=True)
    if DEMO_SEED_MARKER.exists() and DEMO_RUNTIME_DB.exists():
        return

    from pipeline import runtime_db

    runtime_db.initialize(DEMO_RUNTIME_DB)
    dataset = json.loads(DEMO_DATASET.read_text(encoding="utf-8"))
    _write_demo_job_files(dataset)
    _seed_demo_sqlite(dataset)
    DEMO_SEED_MARKER.write_text(dataset.get("version", 1).__str__(), encoding="utf-8")


def _write_demo_job_files(dataset: dict[str, Any]) -> None:
    job_id = dataset["job_id"]
    created_at = dataset["created_at"]
    intake_path = DEMO_INTAKES_DIR / f"{job_id}.json"
    job = {
        **dataset["job"],
        "job_id": job_id,
        "created_at": created_at,
        "updated_at": created_at,
        "expected_output_root": "hosted-demo-metadata-only",
        "intake_manifest_path": str(intake_path),
        "event_count": len(dataset.get("events") or []),
        "revision": 0,
    }
    intake_path.write_text(json.dumps(dataset["intake"], indent=2), encoding="utf-8")
    (DEMO_JOBS_DIR / f"{job_id}.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
    (DEMO_JOBS_DIR / f"{job_id}.events.json").write_text(json.dumps([
        {"event_type": "DEMO_DATASET_LOADED", "created_at": created_at, "operator": "clipper_demo", "metadata": {"mode": "hosted_demo"}}
    ], indent=2), encoding="utf-8")


def _seed_demo_sqlite(dataset: dict[str, Any]) -> None:
    project_id = dataset["project_id"]
    job_id = dataset["job_id"]
    created_at = dataset["created_at"]
    source_artifact_id = "demo_artifact_source_metadata_only"
    project = dataset["project"]
    story = dataset["story"]
    brief = dataset["edit_brief"]
    plan = dataset["edit_plan"]
    research = dataset["research"]
    events = dataset.get("events") or []

    with sqlite3.connect(str(DEMO_RUNTIME_DB)) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
        conn.execute(
            """
            INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, source_artifact_id,
                parent_project_id, source_project_id, reuse_mode, created_at, updated_at, analysis_strategy)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (project_id, job_id, project["profile"], project["sport"], project["display_name"], project["status"],
             source_artifact_id, None, None, "", created_at, created_at, project["analysis_strategy"]),
        )
        conn.execute(
            """
            INSERT INTO artifacts(artifact_id, project_id, artifact_type, path, mime_type, status, parent_artifact_id, created_at, metadata)
            VALUES(?,?,?,?,?,?,?,?,?)
            """,
            (source_artifact_id, project_id, "source_video", "demo://media-unavailable/argentina-croatia-2022",
             "video/mp4", "UNAVAILABLE", None, created_at, _dump({"demo_only": True, "media_available": False})),
        )
        conn.execute(
            """
            INSERT INTO match_research(research_id, project_id, source_artifact_id, sport, competition, season,
                match_date, home_team, away_team, home_score, away_score, venue, stage, importance, summary,
                stakes, historical_context, sources_json, created_at, metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (research["research_id"], project_id, source_artifact_id, project["sport"], research["competition"],
             research["season"], research["match_date"], research["home_team"], research["away_team"],
             research["home_score"], research["away_score"], research["venue"], research["stage"],
             research["importance"], research["summary"], research["stakes"], research["historical_context"],
             _dump([{"label": "public_match_facts", "demo_only": True}]), created_at,
             _dump({"demo_only": True, "media_available": False})),
        )
        moment_ids: dict[str, str] = {}
        for event in events:
            event_id = event["event_id"]
            moment_id = event_id.replace("demo_event_", "demo_moment_")
            moment_ids[event_id] = moment_id
            participants = event.get("participants") or []
            conn.execute(
                """
                INSERT INTO research_events(event_id, research_id, project_id, match_minute, match_second_optional,
                    universal_event_type, sport_event_type, team, participants_json, headline, description,
                    score_before, score_after, importance, confidence, source_refs_json, metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (event_id, research["research_id"], project_id, event["match_minute"], None,
                 event["universal_event_type"], event["sport_event_type"], event["team"], _dump(participants),
                 event["headline"], event["description"], event["score_before"], event["score_after"],
                 event["importance"], event["confidence"], _dump([]), _dump({"demo_only": True, "display_minute": f"{event['match_minute']}'"})),
            )
            peak = float(event["match_minute"]) * 60.0
            conn.execute(
                """
                INSERT INTO moments(moment_id, project_id, source_artifact_id, sport, universal_event_type,
                    sport_event_type, start_seconds, peak_seconds, end_seconds, importance, confidence, team,
                    review_state, reviewed_at, reviewed_by, origin_moment_id, origin_project_id, participants_json,
                    signals_json, emotion_json, metadata_json, created_at, updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (moment_id, project_id, source_artifact_id, project["sport"], event["universal_event_type"],
                 event["sport_event_type"], max(0.0, peak - 18.0), peak, peak + 18.0, event["importance"],
                 event["confidence"], event["team"], "KEEP", created_at, "clipper_demo", None, None,
                 _dump(participants), _dump({"demo_media_state": "metadata_only"}),
                 _dump([]),
                 _dump({"demo_only": True, "media_available": False, "availability_status": "METADATA_ONLY", "alignment_status": "DEMO_ONLY", "match_minute": event["match_minute"], "headline": event["headline"]}),
                 created_at, created_at),
            )
        conn.execute(
            """
            INSERT INTO stories(story_id, project_id, title, summary, archetype, status, hook,
                emotional_arc_json, estimated_duration, recommended_formats_json, created_at, updated_at, metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (story["story_id"], project_id, story["title"], story["summary"], story["archetype"], story["status"],
             story["hook"], _dump(story["emotional_arc"]), story["estimated_duration"], _dump(story["recommended_formats"]),
             created_at, created_at, _dump({"demo_only": True, "media_available": False})),
        )
        for index, event in enumerate(events, start=1):
            conn.execute(
                "INSERT INTO story_moments(story_moment_id, story_id, moment_id, narrative_role, sequence_order, created_at, metadata_json) VALUES(?,?,?,?,?,?,?)",
                (f"demo_story_moment_{index}", story["story_id"], moment_ids[event["event_id"]], event["narrative_role"], index, created_at, _dump({"demo_only": True})),
            )
        conn.execute(
            """
            INSERT INTO edit_briefs(edit_brief_id, project_id, story_id, artifact_id, format_treatment, status,
                editorial_intent, target_duration, created_at, updated_at, metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (brief["edit_brief_id"], project_id, story["story_id"], None, brief["format_treatment"], brief["status"],
             brief["editorial_intent"], brief["target_duration"], created_at, created_at, _dump({"demo_only": True})),
        )
        conn.execute(
            """
            INSERT INTO edit_plans(edit_plan_id, project_id, story_id, edit_brief_id, title, target_platform,
                target_duration, aspect_ratio, hook_text, story_archetype, status, renderer, created_at, updated_at, metadata_json)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (plan["edit_plan_id"], project_id, story["story_id"], brief["edit_brief_id"], plan["title"],
             plan["target_platform"], plan["target_duration"], plan["aspect_ratio"], plan["hook_text"],
             plan["story_archetype"], plan["status"], plan["renderer"], created_at, created_at,
             _dump({"demo_only": True, "media_available": False, "quality_report": [
                 {"narrative_role": beat["narrative_role"], "text": beat["description"], "duration": beat["target_duration"], "composition_mode": "metadata_only"}
                 for beat in plan.get("beats", [])
             ]})),
        )
        timeline_start = 0.0
        for beat in plan.get("beats", []):
            beat_id = f"demo_beat_{beat['sequence_order']}"
            moment_id = moment_ids[beat["event_id"]]
            conn.execute(
                """
                INSERT INTO edit_beats(edit_beat_id, edit_plan_id, sequence_order, narrative_role, source_moment_id,
                    purpose, description, source_start, source_end, target_duration, crop_intent, speed_intent,
                    text_overlay, caption_intent, audio_intent, transition_intent, motion_graphic_intent, metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (beat_id, plan["edit_plan_id"], beat["sequence_order"], beat["narrative_role"], moment_id,
                 beat["purpose"], beat["description"], beat["source_start"], beat["source_end"], beat["target_duration"],
                 "vertical metadata review", "normal", beat["text_overlay"], "creator-safe caption", "broadcast audio unavailable", "hard cut", "text card", _dump({"demo_only": True})),
            )
            conn.execute(
                """
                INSERT INTO timeline_instructions(instruction_id, edit_plan_id, edit_beat_id, instruction_type,
                    source_artifact_id, source_in, source_out, timeline_start, timeline_duration, crop_json,
                    scale_json, position_json, speed, freeze_frame, opacity, text, caption_style_json,
                    audio_gain, music_cue, sfx_cue, transition, motion_graphic_template_json,
                    motion_graphic_parameters_json, metadata_json)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (f"demo_instruction_{beat['sequence_order']}", plan["edit_plan_id"], beat_id, "metadata_card",
                 source_artifact_id, beat["source_start"], beat["source_end"], timeline_start, beat["target_duration"],
                 _dump({}), _dump({}), _dump({}), 1.0, 0, 1.0, beat["text_overlay"], _dump({"style": "demo"}),
                 None, "", "", "cut", _dump({"template_id": "DEMO_TEXT_CARD"}), _dump({"text": beat["text_overlay"]}),
                 _dump({"demo_only": True, "media_available": False})),
            )
            timeline_start += float(beat["target_duration"])
        conn.commit()


def _dump(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))
