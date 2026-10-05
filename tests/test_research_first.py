from __future__ import annotations

from pathlib import Path

import pytest

from pipeline.moment_models import Moment
from pipeline.edit_plan_models import EditBeat, EditPlan, TimelineInstruction
from pipeline.renderers.chatcut_adapter import ChatCutRendererAdapter
from pipeline.research_models import ResearchEvent
from pipeline.research_service import (
    estimate_media_alignment,
    get_transcript_window,
    list_available_research_moments,
    load_research_fixture,
    persist_research_fixture,
    seed_moments_from_research,
)
from pipeline.runtime_service import (
    add_story_moment,
    create_pipeline_run,
    create_story,
    duplicate_project,
    effective_analysis_strategy,
    get_moment,
    register_artifact,
    upsert_edit_beat,
    upsert_edit_plan,
    upsert_edit_brief,
    upsert_timeline_instruction,
    update_moment_review_state,
    update_project_analysis_strategy,
    upsert_moment,
    upsert_project,
)
from tests.test_runtime_managed_analysis import _make_job


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))


def _project(project_id="p1"):
    return upsert_project(
        project_id=project_id,
        job_id=project_id,
        profile="football",
        sport="football",
        display_name="Project",
        status="READY",
    )


def test_project_analysis_strategy_defaults_and_overrides():
    project = _project()
    assert project.analysis_strategy == "TRANSCRIPT_FIRST"
    assert effective_analysis_strategy(project) == "TRANSCRIPT_FIRST"
    updated = update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    assert effective_analysis_strategy(updated) == "RESEARCH_FIRST"
    assert effective_analysis_strategy(updated, run_override="HYBRID") == "HYBRID"
    run = create_pipeline_run(project_id=updated.project_id, stage="analysis", metadata={"analysis_strategy": "HYBRID"})
    assert run.metadata["analysis_strategy"] == "HYBRID"


def test_project_duplication_preserves_strategy_and_review_resets():
    project = update_project_analysis_strategy(_project().project_id, "RESEARCH_FIRST")
    moment = upsert_moment(Moment(
        moment_id="m1", project_id=project.project_id, source_artifact_id=None, sport="football",
        universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2, end_seconds=3,
    ))
    update_moment_review_state(moment.moment_id, "MUST_USE")
    result = duplicate_project(source_project_id=project.project_id, new_project_id="p2", display_name="Copy")
    assert result["project"]["analysis_strategy"] == "RESEARCH_FIRST"
    copied = get_moment(result["cloned_moment_ids"][0])
    assert copied.review_state == "UNREVIEWED"


def test_match_research_persistence_alignment_and_seed_idempotency():
    project = update_project_analysis_strategy(_project().project_id, "RESEARCH_FIRST")
    fixture = load_research_fixture("data/fixtures/research/germany_italy_euro_2012.json")
    research, events = persist_research_fixture(project.project_id, fixture)
    assert research.home_team == "Germany"
    assert [event.universal_event_type for event in events] == ["SCORE", "SCORE", "SCORE"]
    alignment = estimate_media_alignment(events[0], kickoff_media_offset_seconds=300)
    assert events[0].match_minute == 20
    assert alignment.estimated_media_time == 1500
    assert alignment.search_window_start == 1380
    moments = seed_moments_from_research(research.research_id, kickoff_media_offset_seconds=300)
    again = seed_moments_from_research(research.research_id, kickoff_media_offset_seconds=300)
    assert [m.moment_id for m in moments] == [m.moment_id for m in again]
    assert moments[0].metadata["origin"] == "research"
    assert moments[0].metadata["match_minute"] == 20
    assert moments[0].metadata["estimated_media_time"] == 1500
    assert moments[0].metadata["evidence"] == {"research": True, "transcript": False, "audio": False, "visual": False}
    assert moments[0].metadata["availability_status"] == "AVAILABLE"


def test_research_event_taxonomy_rejects_parallel_type():
    with pytest.raises(ValueError, match="invalid universal_event_type"):
        ResearchEvent(
            event_id="e", research_id="r", project_id="p", match_minute=1,
            universal_event_type="GOAL", sport_event_type="goal", headline="Goal",
        )


def test_transcript_window_enrichment_is_optional(tmp_path):
    project = _project()
    assert get_transcript_window(project.project_id, 10, 20) == ""
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("[9s - 11s] before\n[15s - 16s] inside\n[30s - 31s] after\n", encoding="utf-8")
    register_artifact(project_id=project.project_id, artifact_type="transcript", path=transcript)
    window = get_transcript_window(project.project_id, 10, 20)
    assert "before" in window
    assert "inside" in window
    assert "after" not in window


def test_research_first_analysis_avoids_full_transcript_detection(tmp_path, monkeypatch):
    from pipeline import operator_console as oc
    from pipeline.runtime_service import index_existing_project, list_project_moments

    job, jobs_dir, _source, _db = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    fixture = load_research_fixture("data/fixtures/research/germany_italy_euro_2012.json")
    research, _events = persist_research_fixture(project.project_id, fixture)

    monkeypatch.setattr(oc, "_analysis_preflight", lambda *_a: {"ok": True, "source_file": str(_source), "match_name": "Germany Italy"})
    monkeypatch.setattr(oc, "_ensure_transcription", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("transcription should not run")))
    monkeypatch.setattr(oc, "run_detection_call", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("detection should not run")))

    result = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)

    assert result["ok"] is True
    assert result["analysis_strategy"] == "RESEARCH_FIRST"
    moments = list_project_moments(project.project_id)
    assert len(moments) == 3
    assert {m.metadata["research_id"] for m in moments} == {research.research_id}


def test_research_event_outside_source_is_unavailable_and_preserved():
    project = update_project_analysis_strategy(_project().project_id, "RESEARCH_FIRST")
    fixture = load_research_fixture("data/fixtures/research/germany_italy_euro_2012.json")
    research, events = persist_research_fixture(project.project_id, fixture)

    alignment = estimate_media_alignment(
        events[-1],
        kickoff_media_offset_seconds=482.5,
        source_duration_seconds=3281.78,
    )
    assert alignment.estimated_media_time == 6002.5
    assert alignment.metadata["availability_status"] == "OUTSIDE_SOURCE"
    assert alignment.metadata["alignment_reason"] == "estimated_media_time_outside_source_duration"

    moments = seed_moments_from_research(
        research.research_id,
        kickoff_media_offset_seconds=482.5,
        source_duration_seconds=3281.78,
    )

    assert len(moments) == 3
    ozil = moments[-1]
    assert ozil.metadata["research_event_id"] == events[-1].event_id
    assert ozil.metadata["availability_status"] == "OUTSIDE_SOURCE"
    assert ozil.metadata["out_of_source"] is True
    assert ozil.metadata["estimated_media_time"] == 6002.5
    assert ozil.peak_seconds == 6002.5


def test_available_research_moments_filter_supports_partial_source_workflow(tmp_path):
    project = update_project_analysis_strategy(_project().project_id, "RESEARCH_FIRST")
    fixture = load_research_fixture("data/fixtures/research/germany_italy_euro_2012.json")
    research, _events = persist_research_fixture(project.project_id, fixture)
    seeded = seed_moments_from_research(
        research.research_id,
        kickoff_media_offset_seconds=482.5,
        source_duration_seconds=3281.78,
    )
    for moment in seeded[:2]:
        upsert_moment(Moment(
            moment_id=moment.moment_id,
            project_id=moment.project_id,
            source_artifact_id=moment.source_artifact_id,
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
            review_state=moment.review_state,
            reviewed_at=moment.reviewed_at,
            reviewed_by=moment.reviewed_by,
            origin_moment_id=moment.origin_moment_id,
            origin_project_id=moment.origin_project_id,
            created_at=moment.created_at,
            metadata={**moment.metadata, "alignment_status": "VERIFIED"},
        ))

    available = list_available_research_moments(project.project_id, research_id=research.research_id)

    assert [m.metadata["match_minute"] for m in available] == [20, 36]
    assert all(m.metadata["availability_status"] == "AVAILABLE" for m in available)
    assert all(m.peak_seconds <= 3281.78 for m in available)


def test_partial_source_story_and_chatcut_manifest_use_only_available_timestamps(tmp_path):
    project = update_project_analysis_strategy(_project().project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source", path=tmp_path / "source.mp4")
    fixture = load_research_fixture("data/fixtures/research/germany_italy_euro_2012.json")
    research, _events = persist_research_fixture(project.project_id, fixture, source_artifact_id=source.artifact_id)
    seeded = seed_moments_from_research(
        research.research_id,
        kickoff_media_offset_seconds=482.5,
        source_duration_seconds=3281.78,
    )
    for moment in seeded[:2]:
        upsert_moment(Moment(
            moment_id=moment.moment_id,
            project_id=moment.project_id,
            source_artifact_id=source.artifact_id,
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
            review_state=moment.review_state,
            reviewed_at=moment.reviewed_at,
            reviewed_by=moment.reviewed_by,
            origin_moment_id=moment.origin_moment_id,
            origin_project_id=moment.origin_project_id,
            created_at=moment.created_at,
            metadata={**moment.metadata, "alignment_status": "VERIFIED"},
        ))
    available = list_available_research_moments(project.project_id, research_id=research.research_id)
    story = create_story(
        project_id=project.project_id,
        story_id="story_balotelli_takeover",
        title="Balotelli Takes Over the Semifinal",
        archetype="INDIVIDUAL_PERFORMANCE",
        status="SUGGESTED",
        recommended_formats=["SHORT"],
    )
    add_story_moment(story.story_id, available[0].moment_id, "ESCALATION", 1)
    add_story_moment(story.story_id, available[1].moment_id, "CLIMAX", 2)
    brief = upsert_edit_brief(
        project_id=project.project_id,
        story_id=story.story_id,
        format_treatment="SHORT",
        status="READY",
        target_duration=28,
    )
    plan = upsert_edit_plan(EditPlan(
        edit_plan_id="eplan_balotelli_partial",
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        title=story.title,
        target_platform="TikTok / Shorts",
        target_duration=28,
        aspect_ratio="9:16",
        renderer="CHATCUT",
        status="READY",
    ))
    for index, moment in enumerate(available, start=1):
        beat = upsert_edit_beat(EditBeat(
            edit_beat_id=f"beat_{index}",
            edit_plan_id=plan.edit_plan_id,
            sequence_order=index,
            narrative_role="ESCALATION" if index == 1 else "CLIMAX",
            source_moment_id=moment.moment_id,
            source_start=max(0.0, moment.peak_seconds - 8),
            source_end=moment.peak_seconds + 8,
            target_duration=8,
            text_overlay="Balotelli",
        ))
        upsert_timeline_instruction(TimelineInstruction(
            instruction_id=f"inst_{index}",
            edit_plan_id=plan.edit_plan_id,
            edit_beat_id=beat.edit_beat_id,
            instruction_type="CLIP",
            source_artifact_id=source.artifact_id,
            source_in=beat.source_start,
            source_out=beat.source_end,
            timeline_start=(index - 1) * 8,
            timeline_duration=8,
            text=beat.text_overlay,
        ))

    result = ChatCutRendererAdapter().render_or_export(plan, output_dir=tmp_path)

    assert result["ok"] is True
    manifest = Path(result["manifest_path"]).read_text(encoding="utf-8")
    assert "6002.5" not in manifest
    assert "Mesut Ozil" not in manifest
    instructions = __import__("json").loads(manifest)["instructions"]
    assert all(0 <= instruction["source_in"] <= 3281.78 for instruction in instructions)
    assert all(0 <= instruction["source_out"] <= 3281.78 for instruction in instructions)
