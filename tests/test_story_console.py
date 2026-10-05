from __future__ import annotations

import json
import http.client
import threading
from http.server import ThreadingHTTPServer

from pipeline.console_server import ConsoleHandler
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
        assert "Your Matches" in body

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
    assert "AI Found The Story" in html
    assert "Key Moments" in html
    assert "Preview Moment" in html
    assert "Source Coverage" in html
    assert "Why This Cut Works" in html
    assert "Finish Cut" in html
    assert "Moment ID" not in before_advanced


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

    assert "BALOTELLI<br>TOOK<br>OVER<br>THE<br>SEMIFINAL" in before_advanced
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

    assert "moment-preview-trigger" in html
    assert "inline-moment-preview" in html
    assert "data-seek=\"1562.500\"" in html
    assert "watchRoughCut()" in html
    assert "finishCut()" in html
    assert "beat-navigator" in html
    assert "data-seek=\"0.000\"" in html
