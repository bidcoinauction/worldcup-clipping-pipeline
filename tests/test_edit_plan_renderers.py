from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline.edit_plan_service import aura_edit_fixture_plan, build_timeline_instructions, generate_edit_plan_from_story
from pipeline.edit_plan_models import EditBeat, EditPlan, TimelineInstruction
from pipeline.edit_handoff_service import (
    build_chatcut_handoff_manifest,
    editplan_quality_report,
    prepare_chatcut_handoff_v1,
    render_ffmpeg_preview_from_edit_plan,
    validate_edit_handoff,
)
from pipeline.moment_models import Moment
from pipeline.renderers import get_renderer_adapter
from pipeline.runtime_service import (
    add_story_moment,
    create_story,
    list_edit_beats,
    list_story_edit_plans,
    list_timeline_instructions,
    register_artifact,
    upsert_edit_beat,
    upsert_edit_plan,
    upsert_timeline_instruction,
    upsert_edit_brief,
    upsert_moment,
    upsert_project,
)


@pytest.fixture(autouse=True)
def _runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))


def _story_project(sport="football"):
    project = upsert_project(
        project_id=f"{sport}_project", job_id=f"{sport}_job", profile=sport,
        sport=sport, display_name="Project", status="READY",
    )
    moment = upsert_moment(Moment(
        moment_id=f"{sport}_moment", project_id=project.project_id, source_artifact_id=None,
        sport=sport, universal_event_type="SCORE", sport_event_type="score",
        start_seconds=10, peak_seconds=12, end_seconds=20,
    ))
    story = create_story(
        project_id=project.project_id, story_id=f"{sport}_story", title="Story",
        archetype="INDIVIDUAL_PERFORMANCE", status="APPROVED", recommended_formats=["SHORT"], estimated_duration=22,
    )
    add_story_moment(story.story_id, moment.moment_id, "HOOK", 1)
    brief = upsert_edit_brief(
        project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT",
        status="READY", target_duration=22, editorial_intent="Make it mythic.",
    )
    return project, story, brief


def _timeline_plan(tmp_path, *, outside_source=False):
    project, story, brief = _story_project()
    source = register_artifact(
        project_id=project.project_id,
        artifact_type="source_media",
        path=tmp_path / "source.mp4",
        metadata={"duration_seconds": 100.0},
    )
    moment = upsert_moment(Moment(
        moment_id="available_moment",
        project_id=project.project_id,
        source_artifact_id=source.artifact_id,
        sport="football",
        universal_event_type="SCORE",
        sport_event_type="goal",
        start_seconds=10,
        peak_seconds=12,
        end_seconds=20,
        metadata={"availability_status": "OUTSIDE_SOURCE" if outside_source else "AVAILABLE", "alignment_status": "VERIFIED"},
    ))
    plan = upsert_edit_plan(EditPlan(
        edit_plan_id="timeline_plan",
        project_id=project.project_id,
        story_id=story.story_id,
        edit_brief_id=brief.edit_brief_id,
        title="Timeline Plan",
        target_platform="TikTok / Shorts",
        target_duration=12,
        aspect_ratio="9:16",
        renderer="CHATCUT",
        status="READY",
        metadata={
            "caption_intent": "minimal",
            "music_style": "rise",
            "motion_graphic_templates": [{"template_id": "TITLE", "template_name": "Title", "renderer": "CHATCUT", "parameters": {"title": "A"}}],
        },
    ))
    specs = [
        (1, "HOOK", 30.0, 34.0, 0.0, "Cold open", "TITLE"),
        (2, "SETUP", 10.0, 14.0, 4.0, "Setup", None),
        (3, "CLIMAX", 50.0, 58.0, 8.0, "Finish", None),
    ]
    for order, role, source_in, source_out, timeline_start, text, template_id in specs:
        beat = upsert_edit_beat(EditBeat(
            edit_beat_id=f"beat_{order}",
            edit_plan_id=plan.edit_plan_id,
            sequence_order=order,
            narrative_role=role,
            source_moment_id=moment.moment_id,
            source_start=source_in,
            source_end=source_out,
            target_duration=4,
            text_overlay=text,
            crop_intent="vertical",
        ))
        upsert_timeline_instruction(TimelineInstruction(
            instruction_id=f"inst_{order}",
            edit_plan_id=plan.edit_plan_id,
            edit_beat_id=beat.edit_beat_id,
            instruction_type="CLIP",
            source_artifact_id=source.artifact_id,
            source_in=source_in,
            source_out=source_out,
            timeline_start=timeline_start,
            timeline_duration=4,
            crop={"intent": "vertical", "aspect_ratio": "9:16"},
            text=text,
            caption_style={"intent": "minimal"},
            motion_graphic_template={"template_id": template_id} if template_id else {},
        ))
    return plan, source


def test_edit_plan_persistence_beats_and_instructions():
    project, story, brief = _story_project()
    plan = generate_edit_plan_from_story(
        project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Plan", renderer="FFMPEG", target_platform="TikTok",
    )
    assert list_story_edit_plans(story.story_id)[0].edit_plan_id == plan.edit_plan_id
    beats = list_edit_beats(plan.edit_plan_id)
    assert beats[0].sequence_order == 1
    instructions = build_timeline_instructions(plan, beats)
    assert list_timeline_instructions(plan.edit_plan_id)[0].instruction_id == instructions[0].instruction_id
    assert instructions[0].metadata["narrative_role"] == "HOOK"


def test_ffmpeg_adapter_accepts_simple_plan_and_rejects_creative_plan():
    project, story, brief = _story_project()
    simple = generate_edit_plan_from_story(
        project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Simple", renderer="FFMPEG",
    )
    adapter = get_renderer_adapter("FFMPEG")
    assert adapter.can_render(simple)[0] is True
    creative = aura_edit_fixture_plan(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, renderer="FFMPEG")
    assert adapter.can_render(creative)[0] is False


def test_chatcut_adapter_writes_deterministic_handoff(tmp_path):
    project, story, brief = _story_project()
    plan = aura_edit_fixture_plan(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id)
    beats = list_edit_beats(plan.edit_plan_id)
    build_timeline_instructions(plan, beats)
    result = get_renderer_adapter("CHATCUT").render_or_export(plan, output_dir=tmp_path)
    assert result["ok"] is True
    manifest = json.loads((tmp_path / project.project_id / plan.edit_plan_id / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["renderer"] == "CHATCUT"
    assert manifest["handoff_version"] == "1.0"
    assert manifest["edit_plan"]["metadata"]["motion_graphic_templates"][0]["template_id"] == "AURA_TITLE"


def test_renderer_layer_is_sport_agnostic_for_basketball(tmp_path):
    project, story, brief = _story_project(sport="basketball")
    plan = generate_edit_plan_from_story(
        project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Basketball Plan", renderer="CHATCUT", target_platform="TikTok",
    )
    build_timeline_instructions(plan, list_edit_beats(plan.edit_plan_id))
    result = get_renderer_adapter("CHATCUT").render_or_export(plan, output_dir=tmp_path)
    assert result["ok"] is True
    assert "football" not in json.loads((tmp_path / project.project_id / plan.edit_plan_id / "manifest.json").read_text(encoding="utf-8"))["notes"].lower()


def test_handoff_v1_schema_source_media_and_sequence_authority(tmp_path):
    plan, source = _timeline_plan(tmp_path)
    result = prepare_chatcut_handoff_v1(plan, output_dir=tmp_path, source_duration_seconds=100)
    manifest = json.loads((tmp_path / plan.project_id / plan.edit_plan_id / "manifest.json").read_text(encoding="utf-8"))
    assert result["ok"] is True
    assert result["artifact_id"]
    assert manifest["handoff_version"] == "1.0"
    assert manifest["project_id"] == plan.project_id
    assert manifest["story_id"] == plan.story_id
    assert manifest["edit_plan_id"] == plan.edit_plan_id
    assert manifest["source_media"] == [{"artifact_id": source.artifact_id, "source_path": str(tmp_path / "source.mp4"), "duration_seconds": 100, "width": None, "height": None, "fps": None}]
    assert [item["sequence_order"] for item in manifest["timeline"]] == [1, 2, 3]
    assert [item["source_in"] for item in manifest["timeline"]] == [30.0, 10.0, 50.0]
    assert [item["timeline_start"] for item in manifest["timeline"]] == [0.0, 4.0, 8.0]
    assert all(item["composition_mode"] for item in manifest["timeline"])
    assert manifest["timeline"][0]["composition"]["foreground_fit"]


def test_handoff_rejects_outside_source_and_invalid_ranges(tmp_path):
    plan, _source = _timeline_plan(tmp_path, outside_source=True)
    validation = validate_edit_handoff(plan, source_duration_seconds=100)
    assert validation["ok"] is False
    assert any("OUTSIDE_SOURCE" in error for error in validation["errors"])

    upsert_timeline_instruction(TimelineInstruction(
        instruction_id="bad_inst",
        edit_plan_id=plan.edit_plan_id,
        edit_beat_id="beat_1",
        instruction_type="CLIP",
        source_artifact_id=list_timeline_instructions(plan.edit_plan_id)[0].source_artifact_id,
        source_in=99,
        source_out=101,
        timeline_start=12,
        timeline_duration=1,
    ))
    validation = validate_edit_handoff(plan, source_duration_seconds=100)
    assert any("exceeds source duration" in error for error in validation["errors"])


def test_deterministic_handoff_captions_overlays_and_motion_graphics(tmp_path):
    plan, _source = _timeline_plan(tmp_path)
    first = prepare_chatcut_handoff_v1(plan, output_dir=tmp_path, source_duration_seconds=100)
    manifest_path = tmp_path / plan.project_id / plan.edit_plan_id / "manifest.json"
    first_text = manifest_path.read_text(encoding="utf-8")
    second = prepare_chatcut_handoff_v1(plan, output_dir=tmp_path, source_duration_seconds=100)
    assert first["ok"] and second["ok"]
    assert first_text == manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(first_text)
    assert Path(first["caption_path"]).name == "captions.srt"
    assert "Cold open" in Path(first["caption_path"]).read_text(encoding="utf-8")
    assert manifest["text_overlays"][0]["template_id"] == "TITLE"
    assert manifest["motion_graphics"][0]["template_id"] == "TITLE"
    assert editplan_quality_report(plan)[0]["motion_graphic"] == "TITLE"


def test_ffmpeg_preview_dry_run_consumes_same_timeline_instructions(tmp_path):
    plan, _source = _timeline_plan(tmp_path)
    result = render_ffmpeg_preview_from_edit_plan(plan, output_dir=tmp_path, source_duration_seconds=100, dry_run=True)
    manifest = build_chatcut_handoff_manifest(plan, source_duration_seconds=100)
    assert result["ok"] is True
    assert result["command"][0] == "ffmpeg"
    assert [(i["source_in"], i["source_out"]) for i in manifest["timeline"]] == [(30.0, 34.0), (10.0, 14.0), (50.0, 58.0)]
