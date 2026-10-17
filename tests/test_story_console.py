from __future__ import annotations

import json
import http.client
import threading
from http.server import ThreadingHTTPServer

from pipeline.console_server import (
    ConsoleHandler,
    _creator_status_label,
    _group_project_records,
    _preferred_project_record,
    _project_group_key,
    _project_is_test_or_smoke,
    _resolve_project_display_identity,
)
from pipeline.metadata_trust import resolve_metadata
from pipeline.runtime_service import get_story_runtime_summary, update_story_status
from pipeline.story_adapter import adapt_story_suggestions
from tests.test_operator_console import _FakeHandler, _html_body, _json_body
from tests.test_runtime_managed_analysis import _make_job
from tests.test_stories import _sample_suggestion


def _story_fixture(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    from pipeline.runtime_service import index_existing_project, register_artifact, upsert_moment, upsert_story
    from pipeline.moment_adapter import adapt_detection_to_moments
    from pipeline.story_adapter import canonical_story_id
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moments = []
    for row in [
        {"clip_id": "001", "category": "GOAL", "start_time": 10, "end_time": 15},
        {"clip_id": "002", "category": "SAVE", "start_time": 40, "end_time": 44},
    ]:
        moment = adapt_detection_to_moments([row], project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]
        moments.append(upsert_moment(moment))
    story = upsert_story(
        project_id=project.project_id,
        story_id=canonical_story_id(project.project_id, "story_console"),
        title="Console Story",
        archetype="COMEBACK",
        metadata={"original_story_id": "story_console"},
    )
    return job, jobs_dir, project, story, moments


def test_project_display_identity_precedence():
    project = {"job_id": "arg_cro_debug_123_arg_cro_source", "pilot_id": "technical_slug"}
    research = {"research": [{"home_team": "Argentina", "away_team": "Croatia", "competition": "2022 FIFA World Cup", "stage": "Semifinal", "match_date": "2022-12-13"}]}
    intake = {"media": {"match_or_event_name": "Wrong Hint"}}

    identity = _resolve_project_display_identity(project, research, intake)

    assert identity["title"] == "Argentina vs Croatia"
    assert identity["competition"] == "FIFA World Cup"
    assert identity["stage"] == "Semifinal"
    assert identity["year"] == "2022"
    assert identity["source"] == "RESEARCH"


def test_project_display_identity_uses_intake_then_safe_fallback():
    intake_identity = _resolve_project_display_identity({"job_id": "x_y_source"}, {"research": []}, {"media": {"match_or_event_name": "Argentina vs Croatia 2022"}})
    fallback_identity = _resolve_project_display_identity({"job_id": "mystery_match_source", "pilot_id": "mystery_match"}, {"research": []}, {})

    assert intake_identity["title"] == "Argentina vs Croatia"
    assert intake_identity["year"] == "2022"
    assert fallback_identity["title"] == "Mystery Match"


def test_project_group_key_collapses_sparse_and_structured_same_match():
    structured = {"title": "Argentina vs Croatia", "year": "2022", "competition": "2022 FIFA World Cup", "stage": "Semifinal"}
    sparse = {"title": "Argentina vs Croatia", "year": "2022", "competition": "", "stage": ""}

    assert _project_group_key(structured) == _project_group_key(sparse)


def test_metadata_resolver_filename_and_legacy_fallbacks():
    filename = resolve_metadata({"job_id": "unknown"}, {"research": []}, {}, source_paths=["C:/FootballArchive/RAW/portugal_netherlands_2006.mp4"])
    legacy = resolve_metadata({"job_id": "belgium_egypt_2026_first_half", "pilot_id": ""}, {"research": []}, {}, source_paths=[])

    assert filename["title"] == "Portugal vs Netherlands"
    assert filename["year"] == "2006"
    assert filename["fields"]["title"]["source"] == "SOURCE_FILENAME"
    assert legacy["title"] == "Belgium vs Egypt"
    assert legacy["fields"]["title"]["source"] == "LEGACY"


def test_metadata_resolver_health_states_and_missing_year():
    trusted = resolve_metadata({"pilot_id": "arg_cro"}, {"research": [{"home_team": "Argentina", "away_team": "Croatia", "competition": "2022 FIFA World Cup", "stage": "Semifinal", "match_date": "2022-12-13"}]}, {})
    incomplete = resolve_metadata({"pilot_id": "arg_cro"}, {"research": [{"home_team": "Argentina", "away_team": "Croatia", "competition": "", "stage": "", "match_date": ""}]}, {})
    unknown = resolve_metadata({"pilot_id": "Project", "job_id": "source"}, {"research": []}, {})

    assert trusted["health"] == "TRUSTED"
    assert incomplete["health"] == "INCOMPLETE"
    assert incomplete["year"] == ""
    assert unknown["health"] == "UNKNOWN"


def test_metadata_resolver_conflicting_year_and_harmless_team_order():
    conflict = resolve_metadata(
        {"pilot_id": "ita_ger_part1_align"},
        {"research": [{"home_team": "Italy", "away_team": "Germany", "competition": "2006 FIFA World Cup", "stage": "Semifinal", "match_date": "31 May 1962"}]},
        {},
        source_paths=["C:/FootballArchive/RAW/italy_germany_2006_part1.mp4"],
    )
    reversed_order = resolve_metadata(
        {"pilot_id": "germany_italy_2012"},
        {"research": [{"home_team": "Germany", "away_team": "Italy", "competition": "UEFA Euro 2012", "stage": "Semi-final", "match_date": "2012-06-28"}]},
        {},
        source_paths=["C:/FootballArchive/RAW/italy_germany_2012.mp4"],
    )

    assert conflict["health"] == "CONFLICT"
    assert conflict["year"] == ""
    assert any(row["field"] == "year" for row in conflict["conflicts"])
    assert reversed_order["health"] == "TRUSTED"


def test_metadata_resolver_repair_preview_preserves_original_and_rejects_unsafe():
    safe = resolve_metadata({"pilot_id": "arg_cro"}, {"research": [{"home_team": "Argentina", "away_team": "Croatia", "competition": "2022 FIFA World Cup", "stage": "Semifinal", "match_date": ""}]}, {})
    unsafe = resolve_metadata({"pilot_id": "ita_ger"}, {"research": [{"home_team": "Italy", "away_team": "Germany", "competition": "2006 FIFA World Cup", "stage": "Semifinal", "match_date": "1962-05-31"}]}, {})

    assert safe["repair"]["safe"] is True
    assert any(item["field"] == "year" and item["normalized_value"] == "2022" for item in safe["repair"]["recommended"])
    assert safe["fields"]["competition"]["original_value"] == "2022 FIFA World Cup"
    assert unsafe["health"] == "CONFLICT"
    assert unsafe["repair"]["safe"] is False


def test_project_grouping_prefers_furthest_state_sources_then_time_without_mutation():
    original = [
        {"job_id": "old", "group_key": "arg-cro", "workflow_rank": 30, "source_count": 1, "updated_at": "2026-10-01T00:00:00", "created_at": "2026-10-01T00:00:00", "is_test": False},
        {"job_id": "preferred", "group_key": "arg-cro", "workflow_rank": 40, "source_count": 2, "updated_at": "2026-10-02T00:00:00", "created_at": "2026-10-02T00:00:00", "is_test": False},
        {"job_id": "smoke", "group_key": "arg-cro", "workflow_rank": 100, "source_count": 9, "updated_at": "2026-10-03T00:00:00", "created_at": "2026-10-03T00:00:00", "is_test": True},
    ]
    records = [dict(row) for row in original]

    groups, test_records = _group_project_records(records)

    assert groups[0]["preferred"]["job_id"] == "preferred"
    assert [row["job_id"] for row in groups[0]["previous"]] == ["old"]
    assert [row["job_id"] for row in test_records] == ["smoke"]
    assert records == original


def test_preferred_project_selection_tiebreakers():
    records = [
        {"job_id": "one_source", "workflow_rank": 50, "source_count": 1, "updated_at": "2026-10-05T00:00:00", "created_at": "2026-10-05T00:00:00"},
        {"job_id": "two_sources", "workflow_rank": 50, "source_count": 2, "updated_at": "2026-10-04T00:00:00", "created_at": "2026-10-04T00:00:00"},
    ]

    assert _preferred_project_record(records)["job_id"] == "two_sources"


def test_smoke_project_filter_and_creator_status_labels():
    assert _project_is_test_or_smoke({"job_id": "smoke_transcript", "pilot_id": "Project"}) is True
    assert _project_is_test_or_smoke({"job_id": "arg_cro_real", "pilot_id": "arg_cro"}) is False
    assert _creator_status_label({"primary_next_action": "Review 2 Moments", "attention": "Needs Review"}) == "Needs your review"
    assert _creator_status_label({"primary_next_action": "Approve Story", "attention": "Complete"}) == "Story ready"
    assert _creator_status_label({"primary_next_action": "Generate Rough Preview", "attention": "Complete"}) == "Cut ready"
    assert _creator_status_label({"primary_next_action": "Review Preview", "attention": "Needs Review"}) == "Ready to watch"


def test_projects_dashboard_groups_duplicates_and_keeps_advanced_records(monkeypatch):
    import pipeline.console_server as cs
    projects = [
        {"job_id": "arg_old", "current_state": "READY", "project_id": "football", "created_at": "2026-10-01T00:00:00", "updated_at": "2026-10-01T00:00:00", "pilot_id": "arg_cro_align", "source_id": "arg_cro_source", "analysis_strategy": "RESEARCH_FIRST"},
        {"job_id": "arg_preferred", "current_state": "READY", "project_id": "football", "created_at": "2026-10-02T00:00:00", "updated_at": "2026-10-02T00:00:00", "pilot_id": "arg_cro_evidence", "source_id": "arg_cro_source", "analysis_strategy": "RESEARCH_FIRST"},
        {"job_id": "smoke_transcript", "current_state": "READY", "project_id": "football", "created_at": "2026-10-03T00:00:00", "updated_at": "2026-10-03T00:00:00", "pilot_id": "Project", "source_id": "", "analysis_strategy": "TRANSCRIPT_FIRST"},
    ]
    monkeypatch.setattr(cs, "list_projects", lambda: projects)
    monkeypatch.setattr(cs, "list_available_sports", lambda: [])
    monkeypatch.setattr(cs, "get_project_research", lambda job_id: {"research": [{"home_team": "Argentina", "away_team": "Croatia", "competition": "2022 FIFA World Cup", "stage": "Semifinal", "match_date": "2022-12-13"}], "events": []} if job_id.startswith("arg_") else {"research": [], "events": []})
    monkeypatch.setattr(cs, "get_project_workflow_status", lambda job_id: {"primary_next_action": "Approve Story" if job_id == "arg_preferred" else "Review 2 Moments", "attention": "Complete" if job_id == "arg_preferred" else "Needs Review"})
    monkeypatch.setattr(cs, "get_story_status", lambda job_id: {"story_status": ""})
    monkeypatch.setattr(cs.ConsoleHandler, "_read_intake_for_detail", staticmethod(lambda _detail: {}))
    monkeypatch.setattr("pipeline.pilot.read_job", lambda job_id: {"job_id": job_id})
    monkeypatch.setattr("pipeline.runtime_service.get_project_runtime_summary", lambda job_id: {"artifacts": ([{"artifact_type": "source_media"}, {"artifact_type": "source_media"}] if job_id == "arg_preferred" else ([{"artifact_type": "source_media"}] if job_id == "arg_old" else []))})

    fake = _FakeHandler()
    ConsoleHandler._render_projects(fake)
    html = _html_body(fake)
    normal = html.split("Advanced Project List", 1)[0]

    assert normal.count("Argentina vs Croatia") == 1
    assert 'href="/projects/arg_preferred"' in normal
    assert "1 previous run" in normal
    assert "smoke_transcript" not in normal.split("Test / Development Projects", 1)[0]
    assert "Test / Development Projects (1)" in html
    assert "arg_old" in html and "arg_preferred" in html and "smoke_transcript" in html


def test_console_story_review_action_persists(tmp_path, monkeypatch):
    job, jobs_dir, _project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    body = json.dumps({"status": "APPROVED"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/review")

    ConsoleHandler.do_POST(fake)

    assert _json_body(fake)["ok"] is True
    assert get_story_runtime_summary(story.story_id)["story"]["status"] == "APPROVED"


def test_root_route_first_request_returns_complete_html(tmp_path, monkeypatch):
    job, jobs_dir, _project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    server = ThreadingHTTPServer(("127.0.0.1", 0), ConsoleHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/")
        resp = conn.getresponse()
        body = resp.read().decode("utf-8")
        conn.close()
        assert resp.status == 200
        assert "</html>" in body
        assert "Creative Library" in body

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/")
        second = conn.getresponse()
        second_body = second.read().decode("utf-8")
        conn.close()
        assert second.status == 200
        assert "</html>" in second_body

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", "/style.css")
        css = conn.getresponse()
        assert css.status == 200
        assert b"cinematic-hero" in css.read()
        conn.close()

        conn = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
        conn.request("GET", f"/projects/{job['job_id']}")
        project_resp = conn.getresponse()
        project_body = project_resp.read().decode("utf-8")
        conn.close()
        assert project_resp.status == 200
        assert "</html>" in project_body
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_console_story_detail_shows_ordered_moments_and_brief_surface(tmp_path, monkeypatch):
    job, jobs_dir, _project, story, moments = _story_fixture(tmp_path, monkeypatch)
    from pipeline.runtime_service import add_story_moment
    add_story_moment(story.story_id, moments[0].moment_id, "HOOK", 0)
    add_story_moment(story.story_id, moments[1].moment_id, "CLIMAX", 1)
    fake = _FakeHandler()

    ConsoleHandler._render_story_detail(fake, job["job_id"], story.story_id)
    html = _html_body(fake)

    assert "Ordered Moments" in html
    assert "HOOK" in html
    assert "CLIMAX" in html
    assert "Edit Briefs" in html


def test_console_story_add_remove_moment_persists(tmp_path, monkeypatch):
    job, jobs_dir, _project, story, moments = _story_fixture(tmp_path, monkeypatch)
    body = json.dumps({"moment_id": moments[0].moment_id, "narrative_role": "CLIMAX", "sequence_order": 0}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/moments/add")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True
    assert len(get_story_runtime_summary(story.story_id)["ordered_moments"]) == 1

    body = json.dumps({"moment_id": moments[0].moment_id}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/moments/remove")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True
    assert get_story_runtime_summary(story.story_id)["ordered_moments"] == []


def test_console_story_update_moment_role_persists(tmp_path, monkeypatch):
    job, jobs_dir, _project, story, moments = _story_fixture(tmp_path, monkeypatch)
    from pipeline.runtime_service import add_story_moment
    add_story_moment(story.story_id, moments[0].moment_id, "HOOK", 0)
    body = json.dumps({"moment_id": moments[0].moment_id, "narrative_role": "CLIMAX"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/moments/update")
    ConsoleHandler.do_POST(fake)
    assert _json_body(fake)["ok"] is True
    assert get_story_runtime_summary(story.story_id)["ordered_moments"][0]["narrative_role"] == "CLIMAX"


def test_console_canonical_edit_brief_generation(tmp_path, monkeypatch):
    import pipeline.operator_console as oc
    job, jobs_dir, _project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "APPROVED")
    brief_path = tmp_path / "brief.json"
    brief = {"job_id": job["job_id"], "story_id": "story_console", "format": "SHORT", "editorial_intent": "Arena erupts.", "beats": []}
    brief_path.write_text(json.dumps(brief), encoding="utf-8")
    monkeypatch.setattr(oc, "_brief_generate", lambda *_a, **_k: {
        "ok": True, "status": "COMPLETE", "brief": brief, "artifact_path": str(brief_path),
    })
    monkeypatch.setattr(oc, "require_edit_provider", lambda: None)

    body = json.dumps({"format": "SHORT"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/brief")
    ConsoleHandler.do_POST(fake)

    result = _json_body(fake)
    assert result["ok"] is True
    briefs = get_story_runtime_summary(story.story_id)["edit_briefs"]
    assert len(briefs) == 1
    assert briefs[0]["status"] == "READY"
    assert briefs[0]["format_treatment"] == "SHORT"


def test_console_rejected_story_cannot_generate_brief(tmp_path, monkeypatch):
    job, jobs_dir, _project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    update_story_status(story.story_id, "REJECTED")
    body = json.dumps({"format": "SHORT"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/stories/{story.story_id}/brief")
    ConsoleHandler.do_POST(fake)
    assert fake.status == 400
    assert _json_body(fake)["ok"] is False


def test_console_story_workspace_shows_editplan_preview_and_handoff(tmp_path, monkeypatch):
    from pipeline.edit_plan_models import EditBeat, EditPlan, TimelineInstruction
    from pipeline.edit_handoff_service import prepare_chatcut_handoff_v1
    from pipeline.runtime_service import (
        add_story_moment,
        register_artifact,
        upsert_edit_beat,
        upsert_edit_brief,
        upsert_edit_plan,
        upsert_render,
        upsert_timeline_instruction,
        upsert_edl,
    )
    job, _jobs_dir, project, story, moments = _story_fixture(tmp_path, monkeypatch)
    add_story_moment(story.story_id, moments[0].moment_id, "SETUP", 1)
    add_story_moment(story.story_id, moments[1].moment_id, "CLIMAX", 2)
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4", metadata={"duration_seconds": 100})
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=12)
    plan = upsert_edit_plan(EditPlan(
        edit_plan_id="plan_console", project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Console Edit", target_platform="TikTok / Shorts", target_duration=12, aspect_ratio="9:16", renderer="CHATCUT", status="READY",
        metadata={"motion_graphic_templates": [{"template_id": "TITLE", "template_name": "Title", "renderer": "CHATCUT", "parameters": {}}]},
    ))
    for order, role, moment, start in [(1, "HOOK", moments[1], 30), (2, "SETUP", moments[0], 10), (3, "CLIMAX", moments[1], 40)]:
        beat = upsert_edit_beat(EditBeat(
            edit_beat_id=f"beat_console_{order}", edit_plan_id=plan.edit_plan_id, sequence_order=order, narrative_role=role,
            source_moment_id=moment.moment_id, source_start=start, source_end=start + 4, target_duration=4,
            crop_intent="vertical", text_overlay=f"{role} text", audio_intent="bed",
        ))
        upsert_timeline_instruction(TimelineInstruction(
            instruction_id=f"inst_console_{order}", edit_plan_id=plan.edit_plan_id, edit_beat_id=beat.edit_beat_id,
            instruction_type="CLIP", source_artifact_id=source.artifact_id, source_in=start, source_out=start + 4,
            timeline_start=(order - 1) * 4, timeline_duration=4, crop={"intent": "vertical"}, text=f"{role} text",
        ))
    handoff = prepare_chatcut_handoff_v1(plan, output_dir=tmp_path, source_duration_seconds=100)
    preview_artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "preview.mp4", metadata={"preview": True, "renderer": "ffmpeg", "edit_plan_id": plan.edit_plan_id, "deferred_features": ["motion_graphics_not_rendered"]})
    edl = upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id, format_treatment="SHORT", render_profile="REFERENCE", status="READY", artifact_id=preview_artifact.artifact_id, duration_seconds=12, width=1080, height=1920, metadata={"preview": True, "renderer": "ffmpeg", "edit_plan_id": plan.edit_plan_id, "deferred_features": ["motion_graphics_not_rendered"]})

    fake = _FakeHandler()
    ConsoleHandler._render_story_detail(fake, job["job_id"], story.story_id)
    html = _html_body(fake)

    assert "Renderer target: CHATCUT" in html
    assert "HOOK text" in html
    assert "edit-timeline" in html
    assert "Rough Cut" in html
    assert "Approve" in html and "Needs Changes" in html and "Reject" in html
    assert "Finish Cut" in html
    assert "Creative package ready" in html
    assert "Open Creative Package" in html
    assert handoff["artifact_id"] in html


def test_console_handoff_detail_view_is_readable(tmp_path, monkeypatch):
    # Reuse the workspace setup from the previous test shape with a smaller direct package.
    from pipeline.edit_plan_models import EditBeat, EditPlan, TimelineInstruction
    from pipeline.edit_handoff_service import prepare_chatcut_handoff_v1
    from pipeline.runtime_service import register_artifact, upsert_edit_beat, upsert_edit_brief, upsert_edit_plan, upsert_timeline_instruction
    job, _jobs_dir, project, story, moments = _story_fixture(tmp_path, monkeypatch)
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4", metadata={"duration_seconds": 50})
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=4)
    plan = upsert_edit_plan(EditPlan(edit_plan_id="plan_handoff", project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, title="Handoff", target_platform="TikTok", target_duration=4, aspect_ratio="9:16", renderer="CHATCUT", status="READY"))
    beat = upsert_edit_beat(EditBeat(edit_beat_id="beat_handoff", edit_plan_id=plan.edit_plan_id, sequence_order=1, narrative_role="HOOK", source_moment_id=moments[0].moment_id, source_start=1, source_end=5, target_duration=4, text_overlay="Overlay"))
    upsert_timeline_instruction(TimelineInstruction(instruction_id="inst_handoff", edit_plan_id=plan.edit_plan_id, edit_beat_id=beat.edit_beat_id, instruction_type="CLIP", source_artifact_id=source.artifact_id, source_in=1, source_out=5, timeline_start=0, timeline_duration=4, text="Overlay"))
    prepare_chatcut_handoff_v1(plan, output_dir=tmp_path, source_duration_seconds=50)

    fake = _FakeHandler()
    ConsoleHandler._render_handoff_detail(fake, plan.edit_plan_id)
    html = _html_body(fake)

    assert "ChatCut Handoff V1.0" in html
    assert "Source Media" in html
    assert "Timeline Sequence" in html
    assert "Text Overlays / Captions" in html
    assert "Overlay" in html


def test_moment_preview_page_uses_source_media_and_seek_time(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import register_artifact, upsert_moment
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video bytes")
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source)
    moment = upsert_moment(Moment(
        moment_id="mom_preview", project_id=project.project_id, source_artifact_id=artifact.artifact_id, sport="football",
        universal_event_type="SCORE", sport_event_type="goal", start_seconds=10, peak_seconds=12, end_seconds=20,
        participants=[Participant(name="Mario Balotelli")], team="Italy",
        metadata={"origin": "research", "availability_status": "AVAILABLE", "match_minute": 20, "search_window": {"start": 1562.5, "end": 1862.5}},
    ))
    fake = _FakeHandler()
    ConsoleHandler._render_moment_preview(fake, job["job_id"], moment.moment_id)
    html = _html_body(fake)
    assert f"/source_video/{project.project_id}/{artifact.artifact_id}" in html
    assert "video.currentTime = 1562.500" in html
    assert "Mario Balotelli" in html

    fake = _FakeHandler()
    ConsoleHandler._serve_source_video(fake, project.project_id, artifact.artifact_id)
    assert fake.status == 200
    assert fake.wfile.getvalue() == b"fake video bytes"


def test_project_detail_review_kickoff_uses_safe_source_preview_url(tmp_path, monkeypatch):
    from pipeline.runtime_service import register_artifact, upsert_project
    from pipeline.runtime_service import index_existing_project
    job, jobs_dir, _source_path, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    source = tmp_path / "source.mp4"
    source.write_bytes(b"fake video bytes")
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, metadata={"source_clock": {"version": "source_clock_v1", "status": "NEEDS_OPERATOR", "review": {"cursor_seconds": 180.0}, "segments": [{"segment_type": "FIRST_HALF", "source_time_start": 0.0, "match_clock_start_seconds": 0.0, "confidence": "LOW", "method": "source_start_heuristic"}]}})
    upsert_project(project_id=project.project_id, job_id=project.job_id, profile=project.profile, sport=project.sport, display_name=project.display_name, status=project.status, source_artifact_id=artifact.artifact_id)

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)

    assert "Help Clipper find kickoff" in html
    assert f'src="/source_video/{project.project_id}/{artifact.artifact_id}"' in html
    assert "kickoffReviewCursor" in html
    assert "handleKickoffActionSuccess" in html
    assert "seekVideo(video, cursor)" in html


def _normal_creator_html(html: str) -> str:
    return html.split('<summary>Advanced Details</summary>')[0]


def test_creator_flow_shows_only_kickoff_current_task(tmp_path, monkeypatch):
    from pipeline.runtime_service import register_artifact, upsert_project
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, metadata={"source_clock": {"version": "source_clock_v1", "status": "NEEDS_OPERATOR", "review": {"cursor_seconds": 180.0}, "segments": [{"segment_type": "FIRST_HALF", "source_time_start": 0.0, "match_clock_start_seconds": 0.0, "confidence": "LOW", "method": "source_start_heuristic"}]}})
    upsert_project(project_id=project.project_id, job_id=project.job_id, profile=project.profile, sport=project.sport, display_name=project.display_name, status=project.status, source_artifact_id=artifact.artifact_id)
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "Help Clipper find kickoff" in normal
    assert "Yes, this is kickoff" in normal
    assert "Edit Timeline" not in normal
    assert "Watch The Cut" not in normal
    assert normal.count("btn btn-primary") == 1


def test_creator_flow_story_ready_hides_future_empty_sections(tmp_path, monkeypatch):
    job, _jobs_dir, _project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "Your story is ready" in normal
    assert "Build Cut" in normal
    assert "Generate Rough Cut" not in normal
    assert "Rough cut will appear here" not in normal
    assert normal.count("btn btn-primary") == 1


def test_creator_flow_cut_ready_uses_user_language(tmp_path, monkeypatch):
    from pipeline.edit_plan_models import EditPlan
    from pipeline.runtime_service import upsert_edit_brief, upsert_edit_plan
    job, _jobs_dir, project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=34)
    upsert_edit_plan(EditPlan(edit_plan_id="plan_cut_ready", project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, title="Cut", target_platform="TikTok", target_duration=34, aspect_ratio="9:16", renderer="CHATCUT"))
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "Your cut is ready" in normal
    assert "Generate Rough Cut" in normal
    assert "EditPlan" not in normal
    assert "EditBrief" not in normal
    assert normal.count("btn btn-primary") == 1


def test_creator_flow_watch_ready_focuses_video(tmp_path, monkeypatch):
    from pipeline.edit_plan_models import EditPlan
    from pipeline.runtime_service import register_artifact, upsert_edit_brief, upsert_edit_plan, upsert_edl, upsert_render
    job, _jobs_dir, project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=12)
    plan = upsert_edit_plan(EditPlan(edit_plan_id="plan_watch_ready", project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, title="Cut", target_platform="TikTok", target_duration=12, aspect_ratio="9:16", renderer="CHATCUT"))
    artifact = register_artifact(project_id=project.project_id, artifact_type="render_video", path=tmp_path / "preview.mp4", metadata={"preview": True})
    edl = upsert_edl(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, format_treatment="SHORT", status="READY")
    upsert_render(project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id, edl_id=edl.edl_id, format_treatment="SHORT", render_profile="REFERENCE", status="READY", artifact_id=artifact.artifact_id, duration_seconds=12, width=1080, height=1920, metadata={"preview": True, "edit_plan_id": plan.edit_plan_id})
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "Your rough cut is ready" in normal
    assert "Finish" in normal
    assert "<video" in normal
    assert "disabled title=\"Change requests" not in normal
    assert "Change requests coming soon." not in normal
    assert '<a class="flow-rail-item' not in normal
    assert '<span class="flow-rail-item' not in normal
    assert 'creator-progress-line' in normal


def test_creator_moment_review_is_video_first_without_redundant_controls(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import index_existing_project, register_artifact, upsert_moment, update_project_analysis_strategy
    job, jobs_dir, _source_path, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    upsert_moment(Moment(
        moment_id="review_34", project_id=project.project_id, source_artifact_id=source.artifact_id,
        sport="football", universal_event_type="SCORE", sport_event_type="penalty goal",
        start_seconds=2028, peak_seconds=2040, end_seconds=2058,
        participants=[Participant(name="Lionel Messi")], team="Argentina",
        metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": 34, "estimated_match_seconds": 2040},
    ))

    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))

    assert "Check this moment" in normal
    assert "Late Penalty" in normal
    assert "34'" in normal
    assert "Lionel Messi" in normal
    assert "momentReviewVideo" in normal
    assert "Review Moment" not in normal
    assert "Earlier" in normal and "Yes, that's it" in normal and "Later" in normal and "Not found" in normal
    assert "alert(" not in normal
    assert "creator-progress-line" in normal
    assert "flow-nav" not in normal
    assert "EditBrief" not in normal


def test_creator_pending_moment_review_wins_over_story_ready(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import index_existing_project, register_artifact, update_project_analysis_strategy, upsert_moment
    job, jobs_dir, _source_path, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source1 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "first.mp4")
    source2 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "second.mp4")
    upsert_moment(Moment(moment_id="m34", project_id=project.project_id, source_artifact_id=source1.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=22, peak_seconds=34, end_seconds=52, metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": 34, "estimated_match_seconds": 2040}))
    upsert_moment(Moment(moment_id="m39", project_id=project.project_id, source_artifact_id=source1.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=27, peak_seconds=39, end_seconds=57, metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": 39, "estimated_match_seconds": 2340}))
    upsert_moment(Moment(moment_id="m69", project_id=project.project_id, source_artifact_id=source2.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=57, peak_seconds=69, end_seconds=87, metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": 69, "estimated_match_seconds": 4140}))

    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))

    assert "Check this moment" in normal
    assert "69'" in normal
    assert "3 of 3" in normal
    assert "Build the story" not in normal


def test_creator_story_ready_after_review_queue_complete(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import index_existing_project, register_artifact, update_project_analysis_strategy, upsert_moment
    job, jobs_dir, _source_path, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    for mid, minute in [("m34", 34), ("m39", 39)]:
        upsert_moment(Moment(moment_id=mid, project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=minute, peak_seconds=minute + 1, end_seconds=minute + 2, metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": minute, "estimated_match_seconds": minute * 60.0}))
    upsert_moment(Moment(moment_id="m69", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=69, peak_seconds=70, end_seconds=71, metadata={"origin": "research", "availability_status": "NOT_FOUND", "alignment_status": "UNALIGNED", "match_minute": 69, "estimated_match_seconds": 4140.0}))

    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))

    assert "Build the story" in normal
    assert "Check this moment" not in normal


def test_creator_review_queue_orders_counts_and_switches_sources(tmp_path, monkeypatch):
    from dataclasses import replace
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import get_moment, index_existing_project, register_artifact, update_project_analysis_strategy, upsert_moment
    job, jobs_dir, _source_path, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source1 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "first.mp4")
    source2 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "second.mp4")
    for mid, minute, artifact in [("m34", 34, source1), ("m39", 39, source1), ("m69", 69, source2)]:
        upsert_moment(Moment(moment_id=mid, project_id=project.project_id, source_artifact_id=artifact.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=minute, peak_seconds=minute, end_seconds=minute + 10, metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": minute, "estimated_match_seconds": minute * 60.0}))

    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "34'" in normal and "1 of 3" in normal
    assert f'data-source-artifact-id="{source1.artifact_id}"' in normal

    moment = get_moment("m34")
    upsert_moment(replace(moment, metadata={**moment.metadata, "alignment_status": "VERIFIED"}))
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "39'" in normal and "2 of 3" in normal

    moment = get_moment("m39")
    upsert_moment(replace(moment, metadata={**moment.metadata, "alignment_status": "VERIFIED"}))
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))
    assert "69'" in normal and "3 of 3" in normal
    assert f'data-source-artifact-id="{source2.artifact_id}"' in normal


def test_platform_normal_ui_has_no_alerts_or_fake_story_controls(tmp_path, monkeypatch):
    job, _jobs_dir, _project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    fake = _FakeHandler(); ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    normal = _normal_creator_html(_html_body(fake))

    assert "alert(" not in normal
    assert "Try Another Story" not in normal
    assert "Review Alignment" not in normal


def test_outside_source_moment_has_no_preview_player(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import upsert_moment
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    moment = upsert_moment(Moment(
        moment_id="mom_outside", project_id=project.project_id, source_artifact_id=None, sport="football",
        universal_event_type="SCORE", sport_event_type="penalty_goal", start_seconds=5882.5, peak_seconds=6002.5, end_seconds=6182.5,
        participants=[Participant(name="Mesut Ozil")], team="Germany",
        metadata={"origin": "research", "availability_status": "OUTSIDE_SOURCE", "match_minute": 92},
    ))
    fake = _FakeHandler()
    ConsoleHandler._render_moment_preview(fake, job["job_id"], moment.moment_id)
    html = _html_body(fake)
    assert "Not available in this source" in html
    assert "<video" not in html


def test_research_first_project_hides_legacy_chaos_from_default_moment_cards(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import register_artifact, update_project_analysis_strategy, upsert_moment
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    upsert_moment(Moment(
        moment_id="mom_research_active", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football",
        universal_event_type="SCORE", sport_event_type="goal", start_seconds=10, peak_seconds=12, end_seconds=20,
        participants=[Participant(name="Mario Balotelli")], team="Italy",
        metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": 20},
    ))
    upsert_moment(Moment(
        moment_id="mom_legacy_chaos", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football",
        universal_event_type="OTHER", sport_event_type="chaos", start_seconds=70, peak_seconds=72, end_seconds=80,
        metadata={"origin": "transcript_detection"},
    ))
    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)
    assert "Mario Balotelli" in html
    assert "Additional Detected Signals" in html
    assert "mom_legacy_chaos" not in html.split("Additional Detected Signals")[0]
    assert "Review 1 Moments" in html or "Review Moments" in html


def test_project_detail_uses_cinematic_language_and_hides_ids_by_default(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import register_artifact, update_project_analysis_strategy, upsert_moment
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    upsert_moment(Moment(
        moment_id="mom_research_active", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football",
        universal_event_type="SCORE", sport_event_type="goal", start_seconds=10, peak_seconds=12, end_seconds=20,
        participants=[Participant(name="Mario Balotelli")], team="Italy",
        metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": 20},
    ))

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)
    before_advanced = html.split("Advanced Details", 1)[0]

    assert "Clipper found a story" in html
    assert "Your story is ready" in before_advanced
    assert "Build Cut" in before_advanced
    assert "Key Moments" not in before_advanced
    assert "Source Coverage" in before_advanced
    assert "source_clock" not in before_advanced
    assert "Why This Cut Works" not in before_advanced
    assert "Moment ID" not in before_advanced
    assert "Metadata Diagnostics" in html
    assert "Metadata health" in html


def test_project_detail_empty_project_does_not_claim_story_or_cut_ready(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)
    before_advanced = html.split("Advanced Details", 1)[0]

    assert "Ready to start" in before_advanced
    assert "Start processing" in before_advanced
    assert "Clipper found a story" not in before_advanced
    assert "Your story is ready" not in before_advanced
    assert "Cut ready to build" not in before_advanced
    assert "Creative Package Ready" not in before_advanced


def test_primary_creative_story_prefers_editplan_story_over_smoke(tmp_path, monkeypatch):
    from pipeline.edit_plan_models import EditPlan
    from pipeline.runtime_service import upsert_edit_brief, upsert_edit_plan, upsert_story
    job, _jobs_dir, project, _story, _moments = _story_fixture(tmp_path, monkeypatch)
    upsert_story(project_id=project.project_id, story_id="story_smoke", title="Smoke Story", archetype="CHAOS")
    real_story = upsert_story(project_id=project.project_id, story_id="story_balotelli_real", title="Balotelli Takes Over the Semifinal", archetype="INDIVIDUAL_PERFORMANCE")
    brief = upsert_edit_brief(project_id=project.project_id, story_id=real_story.story_id, format_treatment="SHORT", status="READY", target_duration=34)
    upsert_edit_plan(EditPlan(
        edit_plan_id="plan_balotelli_real", project_id=project.project_id, story_id=real_story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Balotelli Cut", target_platform="Shorts", target_duration=34, aspect_ratio="9:16", renderer="CHATCUT", status="READY",
    ))

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)
    before_advanced = html.split("Advanced Details", 1)[0]

    assert "Your cut is ready" in before_advanced
    assert "SMOKE STORY" not in before_advanced


def test_flow_ui_has_inline_preview_and_cut_seek_hooks(tmp_path, monkeypatch):
    from pipeline.edit_plan_models import EditBeat, EditPlan, TimelineInstruction
    from pipeline.moment_models import Moment, Participant
    from pipeline.runtime_service import (
        register_artifact,
        update_project_analysis_strategy,
        upsert_edit_beat,
        upsert_edit_brief,
        upsert_edit_plan,
        upsert_moment,
        upsert_timeline_instruction,
    )
    job, _jobs_dir, project, story, _moments = _story_fixture(tmp_path, monkeypatch)
    update_project_analysis_strategy(project.project_id, "RESEARCH_FIRST")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    moment = upsert_moment(Moment(
        moment_id="mom_research_active", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football",
        universal_event_type="SCORE", sport_event_type="goal", start_seconds=10, peak_seconds=12, end_seconds=20,
        participants=[Participant(name="Mario Balotelli")], team="Italy",
        metadata={"origin": "research", "availability_status": "AVAILABLE", "alignment_status": "VERIFIED", "match_minute": 20, "search_window": {"start": 1562.5, "end": 1862.5}},
    ))
    brief = upsert_edit_brief(project_id=project.project_id, story_id=story.story_id, format_treatment="SHORT", status="READY", target_duration=8)
    plan = upsert_edit_plan(EditPlan(
        edit_plan_id="plan_flow", project_id=project.project_id, story_id=story.story_id, edit_brief_id=brief.edit_brief_id,
        title="Flow Cut", target_platform="Shorts", target_duration=8, aspect_ratio="9:16", renderer="CHATCUT", status="READY",
    ))
    beat = upsert_edit_beat(EditBeat(edit_beat_id="beat_flow", edit_plan_id=plan.edit_plan_id, sequence_order=1, narrative_role="HOOK", source_moment_id=moment.moment_id, source_start=10, source_end=18, target_duration=8, text_overlay="First strike"))
    upsert_timeline_instruction(TimelineInstruction(instruction_id="inst_flow", edit_plan_id=plan.edit_plan_id, edit_beat_id=beat.edit_beat_id, instruction_type="CLIP", source_artifact_id=source.artifact_id, source_in=10, source_out=18, timeline_start=0, timeline_duration=8, text="First strike"))

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"], {})
    html = _html_body(fake)

    before_advanced = html.split("Advanced Details", 1)[0]
    assert "Your cut is ready" in before_advanced
    assert "Generate Rough Cut" in before_advanced
    assert "moment-preview-trigger" not in before_advanced
    assert "inline-moment-preview" not in before_advanced
    assert "watchRoughCut()" in html
    assert "executeProjectAction('render_rough_cut'" in html
    assert "beat-navigator" not in before_advanced
    assert "data-seek=\"0.000\"" in html
