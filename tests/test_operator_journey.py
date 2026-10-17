"""Full operator journey through the real Console API + service boundaries.

New project -> Analyze -> Moments -> Must Use -> Story -> Approve -> Edit ->
Rough Cut -> Approve -> TikTok variant -> Export. Only expensive
external/model/media work is mocked; runtime/service boundaries are real.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from pipeline import operator_console as oc
from pipeline.runtime_service import (
    index_existing_project,
    list_project_moments,
    list_project_renders,
    list_project_stories,
    list_story_exports,
)
from pipeline.story_adapter import adapt_story_suggestions


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(tmp_path / "jobs"))


def _patch_analysis(monkeypatch, tmp_path):
    transcript = tmp_path / "transcript.json"
    transcript.write_text('{"segments": []}', encoding="utf-8")

    monkeypatch.setattr(oc, "_analysis_preflight", lambda *_a: {
        "ok": True, "source_file": str(tmp_path / "source.mp4"), "match_name": "Journey Match",
        "existing_transcript": str(transcript),
    })
    monkeypatch.setattr(oc, "_ensure_transcription", lambda *_a, **_k: transcript)
    monkeypatch.setattr(oc, "build_prompt", lambda **_k: {"prompt": "p"})
    monkeypatch.setattr(oc, "require_detection_provider", lambda: None)
    monkeypatch.setattr(oc, "run_detection_call", lambda *_a, **_k: [
        {"category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:05"},
        {"category": "SAVE", "start_time": "00:00:06", "end_time": "00:00:10"},
    ])
    monkeypatch.setattr(oc, "build_clip_manifest", lambda *_a, **_k: {
        "fieldnames": ["clip_id", "category", "start_time", "end_time"],
        "rows": [
            {"clip_id": "001", "category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:05"},
            {"clip_id": "002", "category": "SAVE", "start_time": "00:00:06", "end_time": "00:00:10"},
        ],
    })


def _patch_expensive(monkeypatch, tmp_path):
    brief_path = tmp_path / "brief.json"
    edl_path = tmp_path / "edl.json"
    render_path = tmp_path / "rough.mp4"

    monkeypatch.setattr(oc, "require_story_provider", lambda: None)
    monkeypatch.setattr(oc, "require_edit_provider", lambda: None)
    monkeypatch.setattr(oc, "_brief_generate", lambda *_a, **_k: (
        brief_path.write_text("{}", encoding="utf-8"),
        {"ok": True, "status": "COMPLETE", "brief": {"format": "SHORT", "beats": []}, "artifact_path": str(brief_path)}
    )[1])
    monkeypatch.setattr(oc, "_edl_build", lambda *_a, **_k: (
        edl_path.write_text("{}", encoding="utf-8"),
        {"ok": True, "status": "COMPLETE", "edl": {"segment_count": 2, "timeline_duration": 44.0}, "artifact_path": str(edl_path)}
    )[1])
    monkeypatch.setattr(oc, "_render_edl", lambda *_a, **_k: (
        render_path.write_bytes(b"video"),
        {"ok": True, "status": "COMPLETE", "output": str(render_path), "duration": 44.0, "segment_count": 2, "mode": "REFERENCE"}
    )[1])


def _post(ConsoleHandler, _FakeHandler, path: str, body: dict):
    fake = _FakeHandler(json.dumps(body).encode("utf-8"), path=path)
    ConsoleHandler.do_POST(fake)
    return fake.status, json.loads(fake.wfile.getvalue().decode("utf-8"))


def test_full_operator_journey(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _json_body
    from pipeline.console_server import ConsoleHandler

    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    _patch_analysis(monkeypatch, tmp_path)
    _patch_expensive(monkeypatch, tmp_path)

    # 1. Create project
    body = {
        "sport": "football", "pilot_id": "journey_pilot", "source_id": "journey_source",
        "event_name": "Journey Match", "local_file_path": str(source), "delivery_method": "shared_folder",
        "analysis_strategy": "TRANSCRIPT_FIRST",
    }
    status, result = _post(ConsoleHandler, _FakeHandler, "/api/projects/create", body)
    assert status == 200 and result["ok"] is True
    job_id = result["job_id"]
    project = index_existing_project(job_id)

    # 2. Analyze
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/analyze", {})
    assert status == 200 and result["ok"] is True

    moments = list_project_moments(project.project_id)
    assert len(moments) == 2

    # 3. Review moments (Must Use both so the workflow reaches completion)
    for moment in moments:
        status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/moments/{moment.moment_id}/review", {"review_state": "MUST_USE"})
        assert status == 200 and result["ok"] is True

    # 4. Build + approve a canonical Story
    adapt_story_suggestions([{
        "story_id": "journey_story", "title": "Journey Story", "archetype": "COMEBACK",
        "moment_ids": [moment.metadata.get("original_event_id") for moment in moments],
        "recommended_formats": ["SHORT"], "estimated_duration": 45,
    }], project_id=project.project_id, moments=moments)
    stories = list_project_stories(project.project_id)
    assert len(stories) == 1
    story_id = stories[0].story_id
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/stories/{story_id}/review", {"status": "APPROVED"})
    assert status == 200 and result["ok"] is True

    # 5. Generate Edit (brief)
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/stories/{story_id}/brief", {"format": "SHORT"})
    assert status == 200 and result["ok"] is True

    # 6. Prepare Cut (EDL)
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/stories/{story_id}/edl", {"format": "SHORT"})
    assert status == 200 and result["ok"] is True

    # 7. Generate Rough Cut
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/stories/{story_id}/render", {"format": "SHORT", "mode": "REFERENCE"})
    assert status == 200 and result["ok"] is True

    renders = list_project_renders(project.project_id)
    assert len(renders) == 1

    # 8. Approve Rough Cut
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/renders/{renders[0].render_id}/review", {"review_state": "APPROVED"})
    assert status == 200 and result["ok"] is True

    # 9. Create TikTok platform variant
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/stories/{story_id}/render",
                           {"format": "SHORT", "mode": "REFERENCE", "preset_id": "tiktok_vertical"})
    assert status == 200 and result["ok"] is True
    renders = list_project_renders(project.project_id)
    assert len(renders) == 2
    tiktok = next(r for r in renders if r.channel_preset_id == "tiktok_vertical")

    # Approve the variant, then export
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/renders/{tiktok.render_id}/review", {"review_state": "APPROVED"})
    assert status == 200 and result["ok"] is True
    status, result = _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/renders/{tiktok.render_id}/export",
                           {"preset_id": "tiktok_vertical", "caption": "Journey caption"})
    assert status == 200 and result["ok"] is True

    exports = list_story_exports(stories[0].story_id)
    assert len(exports) == 1
    assert exports[0].platform == "TIKTOK"
    assert exports[0].caption == "Journey caption"

    # Workflow state reflects completion
    workflow = oc.get_project_workflow_status(job_id)
    assert workflow["attention"] in ("Complete", "Ready")


def test_journey_dashboard_and_detail_render(tmp_path, monkeypatch):
    from tests.test_operator_console import _FakeHandler, _html_body
    from pipeline.console_server import ConsoleHandler
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    _patch_analysis(monkeypatch, tmp_path)
    _patch_expensive(monkeypatch, tmp_path)

    status, result = _post(ConsoleHandler, _FakeHandler, "/api/projects/create", {
        "sport": "football", "pilot_id": "journey_b", "source_id": "journey_b_s", "event_name": "Journey B",
        "local_file_path": str(source), "delivery_method": "shared_folder",
    })
    job_id = result["job_id"]
    _post(ConsoleHandler, _FakeHandler, f"/api/projects/{job_id}/analyze", {})

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job_id)
    html = _html_body(fake)
    assert "Workflow" in html
    assert "Ready for" not in html  # header uses attention badge instead
    assert "review-moments" in html

    fake = _FakeHandler()
    ConsoleHandler._render_projects(fake)
    html = _html_body(fake)
    assert "Attention" in html
