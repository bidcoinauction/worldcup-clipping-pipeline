"""Tests for the Operator Console service layer and web server."""

from __future__ import annotations

import json
import socket
import subprocess
import sys
import time
from io import BytesIO
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline.operator_console import (
    confirm_project_rights,
    create_project,
    get_project,
    get_project_status,
    list_available_sports,
    list_projects,
    project_transitions,
    transition_project,
    validate_project_intake,
)
from pipeline.console_server import ConsoleHandler, _html_response, _validation_issues_html
from pipeline.workflow_state import resolve_workflow_state
from tests.test_pilot_intake import build_intake

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "console.py"


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"console media bytes" * 100)
    return path


# ── list_available_sports ────────────────────────────────────────────────────


def test_list_available_sports_includes_football():
    sports = list_available_sports()
    names = [s["name"] for s in sports]
    assert "football" in names
    football = next(s for s in sports if s["name"] == "football")
    assert football["default"] is True
    assert football["production_safe"] is True


def test_list_available_sports_includes_basketball():
    sports = list_available_sports()
    names = [s["name"] for s in sports]
    assert "basketball" in names


def test_list_available_sports_basketball_not_default():
    sports = list_available_sports()
    basketball = next(s for s in sports if s["name"] == "basketball")
    assert basketball["default"] is False


# ── list_projects ────────────────────────────────────────────────────────────


def test_list_projects_empty(jobs_root):
    assert list_projects(jobs_dir=jobs_root) == []


def test_list_projects_returns_created_job(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    projects = list_projects(jobs_dir=jobs_root)
    assert len(projects) == 1
    assert projects[0]["job_id"] == "pilot_alpha_source_alpha"
    assert projects[0]["current_state"] == "READY"


def test_list_projects_enriches_with_job_fields(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    projects = list_projects(jobs_dir=jobs_root)
    p = projects[0]
    assert p["project_id"] == "football"
    assert p["pilot_id"] == "pilot_alpha"
    assert p["source_id"] == "source_alpha"
    assert p["created_at"]


# ── get_project ──────────────────────────────────────────────────────────────


def test_get_project_returns_detail(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    detail = get_project("pilot_alpha_source_alpha", jobs_dir=jobs_root)
    assert detail["job_id"] == "pilot_alpha_source_alpha"
    assert detail["current_state"] == "READY"
    assert "allowed_next_states" in detail
    assert "events" in detail
    assert "readiness_summary" in detail


# ── get_project_status ───────────────────────────────────────────────────────


def test_get_project_status_returns_readiness(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    status = get_project_status("pilot_alpha_source_alpha", jobs_dir=jobs_root)
    assert status["job_id"] == "pilot_alpha_source_alpha"
    assert status["state"] == "READY"
    assert "blockers" in status
    assert "intake" in status
    assert "outputs" in status


# ── validate_project_intake ──────────────────────────────────────────────────


def test_validate_project_intake_valid(media_file):
    report = validate_project_intake(build_intake(str(media_file)))
    assert report["structurally_valid"] is True
    assert report["config_references_valid"] is True
    assert report["execution_ready"] is True


def test_validate_project_intake_skip_source_rights(media_file):
    report = validate_project_intake(build_intake(str(media_file)), check_source=False, check_rights=False)
    assert report["structurally_valid"] is True
    assert report["config_references_valid"] is True
    assert report["execution_ready"] is False


def test_validate_project_intake_invalid():
    report = validate_project_intake({"bad": "data"}, check_source=False, check_rights=False)
    assert report["structurally_valid"] is False


# ── create_project ───────────────────────────────────────────────────────────


def test_create_project_creates_job(media_file, jobs_root):
    intake = build_intake(str(media_file))
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    assert job["job_id"] == "pilot_alpha_source_alpha"
    assert job["current_state"] == "READY"


def test_create_project_persists_source_manifest(media_file, jobs_root):
    intake = build_intake(str(media_file))
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    intake_path = Path(job["intake_manifest_path"])
    assert intake_path.is_file()
    stored = json.loads(intake_path.read_text(encoding="utf-8"))
    assert stored["media"]["local_file_path"] == str(media_file)
    assert job["readiness_summary"]["source_ready"] is True


def test_create_project_source_readiness_and_rights_are_separate(media_file, jobs_root):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED"}})
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    assert job["current_state"] == "AWAITING_RIGHTS"
    assert job["readiness_summary"]["source_ready"] is True
    assert job["readiness_summary"]["rights_cleared"] is False
    assert job["readiness_summary"]["execution_ready"] is False


def test_confirm_rights_recalculates_and_persists_ready_state(media_file, jobs_root):
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "rights_flow"},
        "media": {"source_id": "source_ready"},
        "rights": {"status": "UNCONFIRMED"},
    })
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    assert job["current_state"] == "AWAITING_RIGHTS"
    assert job["readiness_summary"]["source_ready"] is True
    assert job["readiness_summary"]["rights_cleared"] is False

    updated = confirm_project_rights(
        "rights_flow_source_ready",
        confirmation_statement="Client explicitly confirms clipping, storage, review, and delivery.",
        confirmed_by="Rights Owner",
        confirmation_date="2026-10-02",
        operator="test",
        jobs_dir=jobs_root,
    )

    assert updated["current_state"] == "READY"
    assert updated["readiness_summary"]["structurally_valid"] is True
    assert updated["readiness_summary"]["config_references_valid"] is True
    assert updated["readiness_summary"]["source_ready"] is True
    assert updated["readiness_summary"]["rights_cleared"] is True
    assert updated["readiness_summary"]["execution_ready"] is True

    reloaded = get_project("rights_flow_source_ready", jobs_dir=jobs_root)
    assert reloaded["current_state"] == "READY"
    events = reloaded["events"]
    assert [event["event_type"] for event in events] == ["CREATED", "RIGHTS_CONFIRMED", "TRANSITION"]
    assert events[-1]["previous_state"] == "AWAITING_RIGHTS"
    assert events[-1]["new_state"] == "READY"

    stored_job = json.loads((jobs_root / "rights_flow_source_ready.json").read_text(encoding="utf-8"))
    stored_intake = json.loads(Path(stored_job["intake_manifest_path"]).read_text(encoding="utf-8"))
    assert stored_job["current_state"] == "READY"
    assert stored_intake["rights"]["status"] == "CONFIRMED"


def test_confirm_rights_api_returns_ready(media_file, jobs_root):
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "rights_api"},
        "media": {"source_id": "source_ready"},
        "rights": {"status": "UNCONFIRMED"},
    })
    create_project(intake, operator="test", jobs_dir=jobs_root)
    body = json.dumps({
        "confirmation_statement": "Client explicitly confirms rights.",
        "confirmed_by": "Rights Owner",
        "confirmation_date": "2026-10-02",
        "operator": "test",
    }).encode("utf-8")
    fake = _FakeHandler(body)

    ConsoleHandler._api_confirm_rights(fake, "rights_api_source_ready")

    response = _json_body(fake)
    assert fake.status == 200
    assert response["ok"] is True
    assert response["state"] == "READY"


def test_create_project_duplicate_raises(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    with pytest.raises(Exception, match="already exists"):
        create_project(intake, operator="test", jobs_dir=jobs_root)


class _FakeHandler:
    def __init__(self, body: bytes = b"", content_type: str = "application/json", path: str = "/"):
        self.headers = {"Content-Length": str(len(body)), "Content-Type": content_type}
        self.rfile = BytesIO(body)
        self.wfile = BytesIO()
        self.status = None
        self.path = path

    def send_response(self, status):
        self.status = status

    def send_header(self, _name, _value):
        pass

    def end_headers(self):
        pass

    def _page(self, title: str, content: str) -> str:
        return ConsoleHandler._page(self, title, content)

    def _api_confirm_rights(self, job_id: str):
        return ConsoleHandler._api_confirm_rights(self, job_id)

    def _api_create_project(self):
        return ConsoleHandler._api_create_project(self)

    def _api_transition_project(self, job_id: str):
        return ConsoleHandler._api_transition_project(self, job_id)

    def _api_review_moment(self, job_id: str, moment_id: str):
        return ConsoleHandler._api_review_moment(self, job_id, moment_id)

    def _api_review_story(self, job_id: str, story_id: str):
        return ConsoleHandler._api_review_story(self, job_id, story_id)

    def _api_story_add_moment(self, job_id: str, story_id: str):
        return ConsoleHandler._api_story_add_moment(self, job_id, story_id)

    def _api_story_remove_moment(self, job_id: str, story_id: str):
        return ConsoleHandler._api_story_remove_moment(self, job_id, story_id)

    def _api_story_update_moment(self, job_id: str, story_id: str):
        return ConsoleHandler._api_story_update_moment(self, job_id, story_id)

    def _api_generate_brief(self, job_id: str, story_id: str):
        return ConsoleHandler._api_generate_brief(self, job_id, story_id)

    def _api_build_edl(self, job_id: str, story_id: str):
        return ConsoleHandler._api_build_edl(self, job_id, story_id)

    def _api_build_render(self, job_id: str, story_id: str):
        return ConsoleHandler._api_build_render(self, job_id, story_id)

    def _api_review_render(self, job_id: str, render_id: str):
        return ConsoleHandler._api_review_render(self, job_id, render_id)

    def _api_create_export(self, job_id: str, render_id: str):
        return ConsoleHandler._api_create_export(self, job_id, render_id)

    def _api_start_batch(self):
        return ConsoleHandler._api_start_batch(self)

    def _api_analyze_project(self, job_id: str):
        return ConsoleHandler._api_analyze_project(self, job_id)

    def _api_source_clock_first_half(self, job_id: str):
        return ConsoleHandler._api_source_clock_first_half(self, job_id)

    def _api_project_action(self, job_id: str, action: str):
        return ConsoleHandler._api_project_action(self, job_id, action)


class _OneShotConsoleHandler(ConsoleHandler):
    def do_GET(self):
        return _html_response(self, "<h1>ok</h1>")


class _FailingWrite(BytesIO):
    def __init__(self, exc: BaseException):
        super().__init__()
        self._exc = exc

    def write(self, _data):
        raise self._exc


def _request_handler(wfile: BytesIO) -> _OneShotConsoleHandler:
    handler = _OneShotConsoleHandler.__new__(_OneShotConsoleHandler)
    handler.rfile = BytesIO(b"GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
    handler.wfile = wfile
    handler.client_address = ("127.0.0.1", 12345)
    handler.server = object()
    handler.close_connection = True
    return handler


def _console_new_project_body(source: Path, *, operator_notes: str | None = None, event_name: str = "Netherlands vs Japan - Second Half") -> bytes:
    data = {
        "sport": "football",
        "pilot_id": "netherlands_japan_2026_second_half_v3",
        "source_id": "netherlands_japan_2026_second_half_source_v3",
        "event_name": event_name,
        "reference_deployment": "world_cup",
        "delivery_method": "shared_folder",
        "local_file_path": str(source),
    }
    if operator_notes is not None:
        data["operator_notes"] = operator_notes
    return json.dumps(data).encode("utf-8")


def _json_body(fake: _FakeHandler) -> dict:
    return json.loads(fake.wfile.getvalue().decode("utf-8"))


def _html_body(fake: _FakeHandler) -> str:
    return fake.wfile.getvalue().decode("utf-8")


def _awaiting_rights_project(media_file: Path, jobs_root: Path, *, pilot_id: str = "awaiting_page") -> str:
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": pilot_id},
        "media": {"source_id": "source_ready"},
        "rights": {"status": "UNCONFIRMED"},
    })
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    assert job["current_state"] == "AWAITING_RIGHTS"
    return job["job_id"]


def test_awaiting_rights_page_renders_operator_actions(media_file, jobs_root):
    job_id = _awaiting_rights_project(media_file, jobs_root)
    fake = _FakeHandler()

    ConsoleHandler._render_project_detail(fake, job_id)

    html = _html_body(fake)
    assert fake.status == 200
    assert "Confirm Rights" in html
    assert "Cancel Project" in html
    assert f'/api/projects/{job_id}/rights/confirm' in html
    assert "doTransition('READY')" not in html
    assert "doTransition(\"READY\")" not in html
    assert "doTransition('VALIDATION_FAILED')" not in html
    assert ">VALIDATION_FAILED</button>" not in html


def test_confirm_rights_route_dispatches_service_and_returns_ready():
    body = json.dumps({
        "confirmation_statement": "Client confirms rights.",
        "confirmed_by": "Rights Owner",
        "confirmation_date": "2026-10-02",
        "operator": "test",
    }).encode("utf-8")
    fake = _FakeHandler(body, path="/api/projects/job123/rights/confirm")

    with patch("pipeline.console_server.confirm_project_rights") as confirm_mock:
        confirm_mock.return_value = {"job_id": "job123", "current_state": "READY"}
        ConsoleHandler.do_POST(fake)

    confirm_mock.assert_called_once()
    assert confirm_mock.call_args.args[0] == "job123"
    response = _json_body(fake)
    assert fake.status == 200
    assert response == {"ok": True, "job_id": "job123", "state": "READY"}


def test_cancel_project_route_requires_reason(media_file, jobs_root):
    job_id = _awaiting_rights_project(media_file, jobs_root, pilot_id="cancel_needs_reason")
    body = json.dumps({"target_state": "CANCELLED", "operator": "test", "client_requested": False}).encode("utf-8")
    fake = _FakeHandler(body)

    ConsoleHandler._api_transition_project(fake, job_id)

    response = _json_body(fake)
    assert fake.status == 400
    assert response["ok"] is False
    assert "missing required field 'reason'" in response["error"]


def test_cancel_project_route_accepts_complete_payload(media_file, jobs_root):
    job_id = _awaiting_rights_project(media_file, jobs_root, pilot_id="cancel_complete")
    body = json.dumps({
        "target_state": "CANCELLED",
        "reason": "Duplicate intake created during testing.",
        "operator": "test",
        "client_requested": False,
    }).encode("utf-8")
    fake = _FakeHandler(body)

    ConsoleHandler._api_transition_project(fake, job_id)

    response = _json_body(fake)
    assert fake.status == 200
    assert response["ok"] is True
    assert get_project(job_id, jobs_dir=jobs_root)["current_state"] == "CANCELLED"


def _ready_project(media_file: Path, jobs_root: Path, *, pilot_id: str = "stale_page") -> str:
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": pilot_id},
        "media": {"source_id": "stale_source"},
    })
    job = create_project(intake, operator="test", jobs_dir=jobs_root)
    assert job["current_state"] == "READY"
    return job["job_id"]


def test_project_page_recovers_stale_running_analysis(media_file, jobs_root):
    """A persisted RUNNING state must not render as running after restart."""
    from pipeline.detection import update_analysis_state

    job_id = _ready_project(media_file, jobs_root, pilot_id="stale_running")
    update_analysis_state(
        job_id, status="RUNNING", stage="TRANSCRIBING",
        jobs_dir=jobs_root, transcription_status="RUNNING",
    )

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job_id)

    html = _html_body(fake)
    assert fake.status == 200
    assert "Running..." not in html
    assert "Transcribing" not in html
    assert "Retry Analysis" in html
    assert "interrupted" in html.lower()


def test_project_page_offers_retry_after_transcription_failure(media_file, jobs_root):
    """Failed transcription must render an operator action, not raw analysis states."""
    from pipeline.detection import update_analysis_state

    job_id = _ready_project(media_file, jobs_root, pilot_id="failed_analysis")
    update_analysis_state(
        job_id, status="FAILED", stage="",
        error="Missing dependency. Run: pip install faster-whisper",
        jobs_dir=jobs_root,
        transcription_status="FAILED",
        transcription_error="Missing dependency. Run: pip install faster-whisper",
    )

    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job_id)

    html = _html_body(fake)
    assert fake.status == 200
    assert "Running..." not in html
    assert "Analyzing..." not in html
    assert "faster-whisper" in html

    # The analysis panel must offer an operator action, never a raw state button.
    start = html.index("Analysis</h3>")
    analysis_panel = html[start:html.index("Actions</h3>", start)]
    assert "Missing dependency" in analysis_panel
    assert ">RUNNING</button>" not in analysis_panel
    assert ">FAILED</button>" not in analysis_panel
    assert ">NEEDS ATTENTION</button>" not in analysis_panel
    assert "Retry Analysis" in analysis_panel
    assert 'onclick="doAnalyze()"' in analysis_panel


def test_confirm_rights_route_surfaces_safe_missing_field_error():
    fake = _FakeHandler(json.dumps({"confirmed_by": "Rights Owner"}).encode("utf-8"))

    ConsoleHandler._api_confirm_rights(fake, "job123")

    response = _json_body(fake)
    assert fake.status == 400
    assert response["ok"] is False
    assert response["error"] == "confirmation_statement, confirmed_by, and confirmation_date are required"


def test_new_project_form_field_reaches_service_layer(tmp_path):
    source = tmp_path / "untitled folder" / "source.ts"
    source.parent.mkdir()
    source.write_bytes(b"fake ts bytes" * 100)
    body = _console_new_project_body(source)
    fake = _FakeHandler(body)

    with patch("pipeline.console_server.create_project") as create_mock:
        create_mock.return_value = {
            "job_id": "netherlands_japan_2026_second_half_v3_netherlands_japan_2026_second_half_source_v3",
            "current_state": "AWAITING_RIGHTS",
        }
        ConsoleHandler._api_create_project(fake)

    intake = create_mock.call_args.args[0]
    report = validate_project_intake(intake)
    assert report["structurally_valid"] is True
    assert intake["media"]["local_file_path"] == str(source)
    assert intake["media"]["original_filename"] == "source.ts"
    assert intake["rights"]["status"] == "CONFIRMED"
    assert intake["media"]["source_validation_completed"] is True
    assert "operator_notes" not in intake["pilot"]


def test_new_project_preserves_explicit_operator_notes(tmp_path):
    source = tmp_path / "untitled folder" / "source.ts"
    source.parent.mkdir()
    source.write_bytes(b"fake ts bytes" * 100)
    fake = _FakeHandler(_console_new_project_body(source, operator_notes="Reviewed local source path only."))

    with patch("pipeline.console_server.create_project") as create_mock:
        create_mock.return_value = {
            "job_id": "netherlands_japan_2026_second_half_v3_netherlands_japan_2026_second_half_source_v3",
            "current_state": "AWAITING_RIGHTS",
        }
        ConsoleHandler._api_create_project(fake)

    intake = create_mock.call_args.args[0]
    assert intake["pilot"]["operator_notes"] == "Reviewed local source path only."


def test_new_project_omits_blank_operator_notes(tmp_path):
    source = tmp_path / "untitled folder" / "source.ts"
    source.parent.mkdir()
    source.write_bytes(b"fake ts bytes" * 100)
    fake = _FakeHandler(_console_new_project_body(source, operator_notes="   "))

    with patch("pipeline.console_server.create_project") as create_mock:
        create_mock.return_value = {
            "job_id": "netherlands_japan_2026_second_half_v3_netherlands_japan_2026_second_half_source_v3",
            "current_state": "AWAITING_RIGHTS",
        }
        ConsoleHandler._api_create_project(fake)

    intake = create_mock.call_args.args[0]
    assert "operator_notes" not in intake["pilot"]


def test_new_project_real_world_ts_with_spaces_persists_and_is_source_ready(tmp_path, jobs_root):
    source = tmp_path / "untitled folder" / "netherlands_japan_2026_06_14_second_half.ts"
    source.parent.mkdir()
    source.write_bytes(b"fake ts bytes" * 100)
    fake = _FakeHandler(_console_new_project_body(source))

    ConsoleHandler._api_create_project(fake)

    response = _json_body(fake)
    assert fake.status == 200
    assert response["ok"] is True
    assert response["state"] == "READY"
    assert response["workflow"]["analysis_strategy"] == "RESEARCH_FIRST"
    job = json.loads((jobs_root / f"{response['job_id']}.json").read_text(encoding="utf-8"))
    intake = json.loads(Path(job["intake_manifest_path"]).read_text(encoding="utf-8"))
    assert intake["media"]["local_file_path"] == str(source)
    assert intake["media"]["original_filename"] == source.name
    assert "operator_notes" not in intake["pilot"]
    assert intake["rights"]["status"] == "CONFIRMED"
    assert job["readiness_summary"]["structurally_valid"] is True
    assert job["readiness_summary"]["source_ready"] is True
    assert job["readiness_summary"]["rights_cleared"] is True
    from pipeline.runtime_service import get_project as get_runtime_project, list_project_artifacts
    runtime_project = get_runtime_project(response["job_id"])
    assert runtime_project.analysis_strategy == "RESEARCH_FIRST"
    source_artifacts = list_project_artifacts(response["job_id"], artifact_type="source_media")
    assert source_artifacts
    assert source_artifacts[-1].path == str(source)
    assert source_artifacts[-1].metadata["match_hint"] == "Netherlands vs Japan - Second Half"


def test_new_project_urlencoded_form_preserves_spaces(tmp_path):
    source = tmp_path / "untitled folder" / "source.ts"
    source.parent.mkdir()
    source.write_bytes(b"fake ts bytes" * 100)
    body = (
        "sport=football&pilot_id=pilot_form&source_id=source_form&"
        f"local_file_path={str(source).replace(' ', '+')}&delivery_method=shared_folder"
    ).encode("utf-8")
    fake = _FakeHandler(body, content_type="application/x-www-form-urlencoded")

    with patch("pipeline.console_server.create_project") as create_mock:
        create_mock.return_value = {"job_id": "pilot_form_source_form", "current_state": "AWAITING_RIGHTS"}
        ConsoleHandler._api_create_project(fake)

    intake = create_mock.call_args.args[0]
    assert intake["media"]["local_file_path"] == str(source)


def test_add_match_start_invokes_research_first_workflow(tmp_path):
    source = tmp_path / "argentina_croatia.mp4"
    source.write_bytes(b"media")
    body = _console_new_project_body(source, event_name="Argentina vs Croatia 2022 World Cup semifinal")
    fake = _FakeHandler(body)

    with patch("pipeline.console_server.create_project") as create_mock, patch("pipeline.console_server.start_research_first_workflow") as start_mock:
        create_mock.return_value = {"job_id": "argentina_croatia_source", "current_state": "READY"}
        start_mock.return_value = {"ok": True, "analysis_strategy": "RESEARCH_FIRST", "match_hint": "Argentina vs Croatia 2022 World Cup semifinal"}
        ConsoleHandler._api_create_project(fake)

    intake = create_mock.call_args.args[0]
    assert intake["media"]["match_or_event_name"] == "Argentina vs Croatia 2022 World Cup semifinal"
    assert intake["rights"]["status"] == "CONFIRMED"
    start_mock.assert_called_once_with("argentina_croatia_source")
    response = _json_body(fake)
    assert response["workflow"]["analysis_strategy"] == "RESEARCH_FIRST"


def test_workflow_state_endpoint_reports_blocked_state(media_file, jobs_root):
    intake = build_intake(str(media_file), overrides={"rights": {"status": "UNCONFIRMED", "permitted_uses": ["review"]}})
    job = create_project(intake, jobs_dir=jobs_root)
    fake = _FakeHandler(path=f"/projects/{job['job_id']}/workflow-state")

    ConsoleHandler._api_workflow_state(fake, job["job_id"])

    body = _json_body(fake)
    assert body["state"] == "BLOCKED"
    assert "Rights" in body["blocked_reason"]


def test_workflow_resolver_action_gating_and_completed_state():
    detail = {"readiness_summary": {"source_ready": True, "rights_cleared": True, "execution_ready": True}}
    empty = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media"}]})
    assert empty.state == "SOURCE_READY"
    assert empty.actions["find_story"]["enabled"] is False
    assert empty.actions["find_story"]["reason"] == "requires usable Moments"
    ready = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media"}], "moments": [{"metadata": {"availability_status": "AVAILABLE", "alignment_status": "ALIGNED"}}, {"metadata": {"availability_status": "AVAILABLE", "alignment_status": "VERIFIED"}}], "stories": [{"story_id": "s1"}], "edit_plan_summary": {"edit_plan_count": 1}, "render_summary": {"ready_render_count": 1}})
    assert ready.state == "ROUGH_CUT_READY"
    assert ready.actions["find_story"]["enabled"] is True
    assert ready.actions["build_cut"]["enabled"] is True
    assert ready.actions["generate_rough_cut"]["enabled"] is True


def test_moment_shift_action_http_persists_five_second_cursor_and_logs_terminal_events(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import get_moment, list_pipeline_events, register_artifact, upsert_moment, upsert_project

    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    project = upsert_project(project_id="p_actions", job_id="p_actions", profile="football", sport="football", display_name="Argentina vs Croatia", status="READY")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source1.mp4")
    upsert_moment(Moment(moment_id="m34", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="penalty goal", start_seconds=2028, peak_seconds=2040, end_seconds=2058, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": 34}))

    fake = _FakeHandler(json.dumps({"moment_id": "m34", "current_cursor_seconds": 2040, "direction": "later"}).encode("utf-8"), path="/api/projects/p_actions/actions/shift_moment_later")
    fake._api_project_action(project.project_id, "shift_moment_later")
    result = _json_body(fake)

    assert fake.status == 200
    assert result["ok"] is True
    assert result["moment"]["cursor_seconds"] == 2045.0
    assert result["data"]["review_cursor"] == 2045.0
    assert result["data"]["formatted_cursor"] == "34:05"
    assert result["data"]["source_artifact_id"] == source.artifact_id
    assert get_moment("m34").peak_seconds == 2045.0
    event_types = [event.event_type for event in list_pipeline_events(project_id=project.project_id)]
    assert "PROJECT_ACTION_STARTED" in event_types
    assert "PROJECT_ACTION_SUCCEEDED" in event_types
    assert "PROJECT_ACTION_FAILED" not in event_types

    fake = _FakeHandler(json.dumps({"moment_id": "m34", "current_cursor_seconds": 2045, "direction": "earlier"}).encode("utf-8"), path="/api/projects/p_actions/actions/shift_moment_earlier")
    fake._api_project_action(project.project_id, "shift_moment_earlier")
    result = _json_body(fake)
    assert fake.status == 200
    assert result["moment"]["cursor_seconds"] == 2040.0
    assert result["data"]["review_cursor"] == 2040.0
    assert get_moment("m34").metadata["review_cursor_seconds"] == 2040.0


def test_moment_confirm_and_not_found_http_actions_return_terminal_success(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import get_moment, register_artifact, upsert_moment, upsert_project

    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    project = upsert_project(project_id="p_confirm", job_id="p_confirm", profile="football", sport="football", display_name="Argentina vs Croatia", status="READY")
    source1 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source1.mp4")
    source2 = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source2.mp4")
    upsert_moment(Moment(moment_id="m39", project_id=project.project_id, source_artifact_id=source1.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=2328, peak_seconds=2340, end_seconds=2358, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": 39}))
    upsert_moment(Moment(moment_id="m69", project_id=project.project_id, source_artifact_id=source2.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1428, peak_seconds=1440, end_seconds=1458, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED", "match_minute": 69}))

    fake = _FakeHandler(json.dumps({"moment_id": "m39"}).encode("utf-8"), path="/api/projects/p_confirm/actions/confirm_moment")
    fake._api_project_action(project.project_id, "confirm_moment")
    result = _json_body(fake)
    assert fake.status == 200
    assert result["moment"]["alignment_status"] == "VERIFIED"
    assert result["next_moment"]["moment_id"] == "m69"
    assert result["next_moment"]["source_artifact_id"] == source2.artifact_id
    assert result["data"]["next_moment"]["moment_id"] == "m69"
    assert result["data"]["remaining_review_count"] == 1
    assert result["data"]["review_complete"] is False
    assert get_moment("m39").metadata["alignment_status"] == "VERIFIED"

    fake = _FakeHandler(json.dumps({"moment_id": "m69"}).encode("utf-8"), path="/api/projects/p_confirm/actions/mark_moment_not_found")
    fake._api_project_action(project.project_id, "mark_moment_not_found")
    result = _json_body(fake)
    assert fake.status == 200
    assert result["moment"]["availability_status"] == "NOT_FOUND"
    assert result["next_moment"] is None
    assert result["data"]["review_complete"] is True
    assert get_moment("m69").metadata["availability_status"] == "NOT_FOUND"


def test_moment_shift_action_does_not_invoke_expensive_alignment(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import register_artifact, upsert_moment, upsert_project

    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    project = upsert_project(project_id="p_cheap", job_id="p_cheap", profile="football", sport="football", display_name="Argentina vs Croatia", status="READY")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    upsert_moment(Moment(moment_id="m34", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=10, end_seconds=20, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED"}))

    def fail_align(*_args, **_kwargs):
        raise AssertionError("shift must not invoke alignment")

    monkeypatch.setattr("pipeline.source_alignment.BoundedSourceAlignmentService.align_event", fail_align)
    monkeypatch.setattr("pipeline.source_alignment.BoundedSourceAlignmentService.align_research", fail_align)
    fake = _FakeHandler(json.dumps({"moment_id": "m34"}).encode("utf-8"), path="/api/projects/p_cheap/actions/shift_moment_later")
    fake._api_project_action(project.project_id, "shift_moment_later")
    assert fake.status == 200


def test_review_resolution_actions_do_not_invoke_expensive_alignment(tmp_path, monkeypatch):
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import register_artifact, upsert_moment, upsert_project

    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    project = upsert_project(project_id="p_resolve_cheap", job_id="p_resolve_cheap", profile="football", sport="football", display_name="Argentina vs Croatia", status="READY")
    source = register_artifact(project_id=project.project_id, artifact_type="source_media", path=tmp_path / "source.mp4")
    upsert_moment(Moment(moment_id="m34", project_id=project.project_id, source_artifact_id=source.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=10, end_seconds=20, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED"}))

    def fail_align(*_args, **_kwargs):
        raise AssertionError("review resolution must not invoke alignment")

    monkeypatch.setattr("pipeline.source_alignment.BoundedSourceAlignmentService.align_event", fail_align)
    monkeypatch.setattr("pipeline.source_alignment.BoundedSourceAlignmentService.align_research", fail_align)
    fake = _FakeHandler(json.dumps({"moment_id": "m34"}).encode("utf-8"), path="/api/projects/p_resolve_cheap/actions/confirm_moment")
    fake._api_project_action(project.project_id, "confirm_moment")
    assert fake.status == 200


def test_kickoff_shift_action_returns_review_cursor_contract(tmp_path, monkeypatch):
    project = _action_project(tmp_path, monkeypatch, project_id="p_kickoff_contract")
    from pipeline.runtime_service import register_artifact, upsert_project
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, metadata={"duration_seconds": 200, "source_clock": {"version": "source_clock_v1", "status": "NEEDS_OPERATOR", "review": {"segment_type": "FIRST_HALF", "cursor_seconds": 45.0}, "segments": []}})
    upsert_project(project_id=project.project_id, job_id=project.job_id, profile=project.profile, sport=project.sport, display_name=project.display_name, status=project.status, source_artifact_id=artifact.artifact_id)

    fake, body = _post_action(project.project_id, "shift_kickoff_earlier", {"source_artifact_id": artifact.artifact_id})

    assert fake.status == 200
    assert body["data"]["review_cursor"] == 30.0
    assert body["data"]["formatted_cursor"] == "0:30"
    assert body["data"]["source_artifact_id"] == artifact.artifact_id


def test_dashboard_batches_system_and_new_project_routes_are_real(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(tmp_path / "jobs"))

    for renderer in (ConsoleHandler._render_projects, ConsoleHandler._render_batches, ConsoleHandler._render_system):
        fake = _FakeHandler()
        renderer(fake)
        html = _html_body(fake)
        assert fake.status == 200
        assert "</html>" in html
        assert "alert(" not in html

    fake = _FakeHandler()
    ConsoleHandler._render_new_project(fake, {})
    html = _html_body(fake)
    assert fake.status == 200
    assert "Start" in html
    assert "new-project-error" in html
    assert "alert(" not in html


def test_new_project_invalid_source_returns_structured_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(tmp_path / "jobs"))
    body = json.dumps({"sport": "football", "pilot_id": "bad_source", "source_id": "bad_source_file", "local_file_path": str(tmp_path / "missing.mp4"), "event_name": "Missing"}).encode("utf-8")
    fake = _FakeHandler(body, path="/api/projects/create")
    fake._api_create_project()
    result = _json_body(fake)
    assert fake.status == 400
    assert result["ok"] is False
    assert "readiness" in result
    assert "Project is not execution-ready" in result["error"]


def test_workflow_resolver_confirmation_required_blocks_downstream():
    detail = {"readiness_summary": {"source_ready": True, "rights_cleared": True, "execution_ready": True}}
    state = resolve_workflow_state(detail, runtime={
        "project": {"updated_at": "now"},
        "artifacts": [{"artifact_type": "source_media"}],
        "pipeline_runs": [{"status": "BLOCKED", "stage": "analysis", "error_message": "Please confirm", "metadata": {"status": "NEEDS_CONFIRMATION", "identity_candidate": {"team_a": "Argentina", "team_b": "Croatia"}}}],
    })
    assert state.state == "IDENTITY_CONFIRMATION_REQUIRED"
    assert state.confirmation_required is True
    assert state.actions["find_story"]["enabled"] is False
    assert state.actions["build_cut"]["enabled"] is False


def test_story_readiness_requires_two_usable_moments():
    detail = {"readiness_summary": {"source_ready": True, "rights_cleared": True, "execution_ready": True}}
    one = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media"}], "moments": [{"metadata": {"availability_status": "AVAILABLE", "alignment_status": "ALIGNED"}}]})
    two = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media"}], "moments": [{"metadata": {"availability_status": "AVAILABLE", "alignment_status": "ALIGNED"}}, {"metadata": {"availability_status": "AVAILABLE", "alignment_status": "VERIFIED"}}]})
    assert one.actions["find_story"]["enabled"] is False
    assert one.actions["find_story"]["reason"] == "requires at least 2 usable Moments"
    assert two.actions["find_story"]["enabled"] is True


def test_workflow_state_keeps_moments_current_for_kickoff_review():
    detail = {"readiness_summary": {"source_ready": True, "rights_cleared": True, "execution_ready": True}}
    state = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media", "metadata": {"source_clock": {"status": "NEEDS_OPERATOR"}}}], "research_events": [{"event_id": "e1"}]})
    assert state.state == "ALIGNING"
    assert state.current_stage == "Moments"
    assert state.actions["review_kickoff"]["enabled"] is True


def test_workflow_state_review_kickoff_cta_clears_after_confirmation():
    detail = {"readiness_summary": {"source_ready": True, "rights_cleared": True, "execution_ready": True}}
    state = resolve_workflow_state(detail, runtime={"project": {"updated_at": "now"}, "artifacts": [{"artifact_type": "source_media", "metadata": {"source_clock": {"status": "VERIFIED"}}}], "research_events": [{"event_id": "e1"}]})
    assert state.actions["review_kickoff"]["enabled"] is False
    assert state.state == "RESEARCH_READY"


def _action_project(tmp_path, monkeypatch, project_id="p_action"):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    from pipeline.runtime_service import register_artifact, upsert_project
    project = upsert_project(project_id=project_id, job_id=project_id, profile="football", sport="football", display_name=project_id, status="READY")
    source = tmp_path / f"{project_id}.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id=project_id, artifact_type="source_media", path=source)
    return upsert_project(project_id=project_id, job_id=project_id, profile="football", sport="football", display_name=project_id, status="READY", source_artifact_id=artifact.artifact_id)


def _action_source_id(project_id="p_action"):
    from pipeline.runtime_service import get_project
    return get_project(project_id).source_artifact_id


def _post_action(project_id: str, action: str, payload: dict | None = None) -> tuple[_FakeHandler, dict]:
    fake = _FakeHandler(json.dumps(payload or {}).encode("utf-8"), path=f"/api/projects/{project_id}/actions/{action}")
    fake._api_project_action(project_id, action)
    return fake, _json_body(fake)


def test_find_story_action_executes_and_persists_story(tmp_path, monkeypatch):
    _action_project(tmp_path, monkeypatch)
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import list_project_stories, upsert_moment, upsert_story
    source_id = _action_source_id()
    upsert_moment(Moment(moment_id="m1", project_id="p_action", source_artifact_id=source_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2, end_seconds=3, metadata={"availability_status": "AVAILABLE", "alignment_status": "VERIFIED"}))
    upsert_moment(Moment(moment_id="m2", project_id="p_action", source_artifact_id=source_id, sport="football", universal_event_type="SAVE", sport_event_type="save", start_seconds=4, peak_seconds=5, end_seconds=6, metadata={"availability_status": "AVAILABLE", "alignment_status": "ALIGNED"}))
    def fake_generate(job_id):
        upsert_story(project_id=job_id, story_id="story_action", title="Built Story", archetype="CLUTCH")
        return {"ok": True, "stories": [{"story_id": "story_action"}]}
    with patch("pipeline.operator_console.generate_stories", side_effect=fake_generate):
        fake, body = _post_action("p_action", "find_story")
    assert fake.status == 200
    assert body["ok"] is True
    assert body["redirect"].endswith("#stories")
    assert list_project_stories("p_action")


def test_find_story_action_failure_response_for_insufficient_moments(tmp_path, monkeypatch):
    _action_project(tmp_path, monkeypatch)
    fake, body = _post_action("p_action", "find_story")
    assert fake.status == 400
    assert body["ok"] is False
    assert body["code"] == "INSUFFICIENT_MOMENTS"


def test_build_cut_action_executes_and_persists_edit_plan(tmp_path, monkeypatch):
    _action_project(tmp_path, monkeypatch)
    from pipeline.edit_plan_service import generate_edit_plan_from_story
    from pipeline.runtime_service import list_story_edit_plans, upsert_edit_brief, upsert_story
    story = upsert_story(project_id="p_action", story_id="story_action", title="Story", archetype="CLUTCH")
    def fake_brief(job_id, story_id, fmt, **kwargs):
        upsert_edit_brief(project_id=job_id, story_id=story_id, format_treatment=fmt, status="READY")
        return {"ok": True, "brief": {"editorial_intent": "test"}}
    def fake_plan(job_id, story_id, brief_id, **kwargs):
        plan = generate_edit_plan_from_story(project_id=job_id, story_id=story_id, edit_brief_id=brief_id, title="Plan")
        return {"ok": True, "edit_plan": plan.to_dict()}
    with patch("pipeline.operator_console.generate_canonical_edit_brief", side_effect=fake_brief), patch("pipeline.operator_console.generate_edit_plan", side_effect=fake_plan):
        fake, body = _post_action("p_action", "build_cut", {"story_id": story.story_id})
    assert fake.status == 200
    assert body["state"] == "EDIT_READY"
    assert list_story_edit_plans(story.story_id)


def test_generate_rough_cut_action_starts_render(tmp_path, monkeypatch):
    _action_project(tmp_path, monkeypatch)
    from pipeline.edit_plan_service import generate_edit_plan_from_story
    from pipeline.runtime_service import upsert_edit_brief, upsert_story
    story = upsert_story(project_id="p_action", story_id="story_action", title="Story", archetype="CLUTCH")
    brief = upsert_edit_brief(project_id="p_action", story_id=story.story_id, format_treatment="SHORT", status="READY")
    plan = generate_edit_plan_from_story(project_id="p_action", story_id=story.story_id, edit_brief_id=brief.edit_brief_id, title="Plan")
    with patch("pipeline.operator_console.generate_editplan_preview", return_value={"ok": True, "output": "preview.mp4"}) as preview:
        fake, body = _post_action("p_action", "render_rough_cut", {"edit_plan_id": plan.edit_plan_id})
    assert fake.status == 200
    assert body["state"] == "ROUGH_CUT_READY"
    preview.assert_called_once()


def test_review_kickoff_actions_use_canonical_route(tmp_path, monkeypatch):
    project = _action_project(tmp_path, monkeypatch)
    from pipeline.runtime_service import register_artifact, upsert_project
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, metadata={"duration_seconds": 200})
    upsert_project(project_id=project.project_id, job_id=project.job_id, profile=project.profile, sport=project.sport, display_name=project.display_name, status=project.status, source_artifact_id=artifact.artifact_id)
    fake, body = _post_action(project.project_id, "shift_kickoff_later")
    assert fake.status == 200
    assert body["ok"] is True
    assert body["result"]["cursor_seconds"] == 15.0


def test_moment_alignment_actions_use_canonical_route(tmp_path, monkeypatch):
    _action_project(tmp_path, monkeypatch)
    from pipeline.moment_models import Moment
    from pipeline.runtime_service import get_moment, upsert_moment
    upsert_moment(Moment(moment_id="m1", project_id="p_action", source_artifact_id=_action_source_id(), sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2, end_seconds=3, metadata={"availability_status": "AVAILABLE", "alignment_status": "ESTIMATED"}))
    fake, body = _post_action("p_action", "mark_moment_not_found", {"moment_id": "m1"})
    assert fake.status == 200
    assert body["ok"] is True
    assert get_moment("m1").metadata["availability_status"] == "NOT_FOUND"


def test_retry_research_action_invokes_real_workflow_route(tmp_path, monkeypatch):
    project = _action_project(tmp_path, monkeypatch)
    from pipeline.runtime_service import upsert_project
    upsert_project(project_id=project.project_id, job_id=project.job_id, profile=project.profile, sport=project.sport, display_name=project.display_name, status=project.status, analysis_strategy="RESEARCH_FIRST")
    with patch("pipeline.operator_console.start_research_first_workflow", return_value={"ok": True}) as retry:
        fake, body = _post_action(project.project_id, "retry_research")
    assert fake.status == 200
    assert body["action"] == "retry_research"
    retry.assert_called_once_with(project.project_id)


def test_project_page_primary_actions_are_not_fake_anchor_only(media_file, jobs_root):
    job_id = _ready_project(media_file, jobs_root, pilot_id="primary_actions")
    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job_id, {})
    html = _html_body(fake)
    assert 'href="#stories">Find Story</a>' not in html
    assert '>Build Cut</a>' not in html
    assert "function executeProjectAction" in html


def test_review_kickoff_route_invokes_source_clock_service(tmp_path, monkeypatch):
    fake = _FakeHandler(b'{"action":"later"}', path="/api/projects/p1/source-clock/first-half")
    project = type("Project", (), {"project_id": "p1", "source_artifact_id": "art_source"})()
    with patch("pipeline.runtime_service.get_project", return_value=project), patch("pipeline.source_alignment.BoundedSourceAlignmentService") as service_cls:
        service_cls.return_value.confirm_source_anchor.return_value = {"ok": True, "cursor_seconds": 195.0, "recalculated_count": 0, "rechecked_count": 0}
        fake._api_source_clock_first_half("p1")

    body = _json_body(fake)
    assert fake.status == 200
    assert body["cursor_seconds"] == 195.0
    service_cls.return_value.confirm_source_anchor.assert_called_once_with("p1", "art_source", segment_type="FIRST_HALF", action="later")


def test_validation_errors_render_safe_and_specific():
    html = _validation_issues_html({
        "issues": [{"path": "media.local_file_path", "code": "SOURCE_MISSING", "message": "source <missing>"}],
    })
    assert "SOURCE_MISSING" in html
    assert "media.local_file_path" in html
    assert "&lt;missing&gt;" in html


# ── project_transitions ──────────────────────────────────────────────────────


def test_project_transitions(media_file, jobs_root):
    intake = build_intake(str(media_file))
    create_project(intake, operator="test", jobs_dir=jobs_root)
    result = project_transitions("pilot_alpha_source_alpha", jobs_dir=jobs_root)
    assert result["current_state"] == "READY"
    assert isinstance(result["allowed_next_states"], list)


# ── Web server integration ───────────────────────────────────────────────────


def test_console_cli_help():
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "Operator Console" in result.stdout


def test_console_direct_launcher_imports_from_repo_root():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    proc = subprocess.Popen(
        [sys.executable, "scripts/console.py", "--port", str(port)],
        cwd=SCRIPT.parents[1],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        time.sleep(0.5)
        assert proc.poll() is None, proc.stderr.read()
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=2)


def test_console_server_starts_and_stops():
    import threading
    from http.client import HTTPConnection

    from pipeline.console_server import run_server

    port = 18420
    server_thread = threading.Thread(target=lambda: run_server(port=port), daemon=True)
    server_thread.start()

    import time
    time.sleep(0.3)

    conn = HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request("GET", "/")
    resp = conn.getresponse()
    assert resp.status == 200
    body = resp.read().decode()
    assert "Stadium Signal" in body
    conn.close()


@pytest.mark.parametrize("exc", [BrokenPipeError(), ConnectionAbortedError(), ConnectionResetError()])
def test_console_client_disconnect_during_response_write_is_safe(exc):
    handler = _request_handler(_FailingWrite(exc))

    _OneShotConsoleHandler.handle_one_request(handler)

    assert handler.close_connection is True


def test_console_serves_subsequent_request_after_client_disconnect():
    disconnected = _request_handler(_FailingWrite(ConnectionAbortedError()))
    _OneShotConsoleHandler.handle_one_request(disconnected)

    body = BytesIO()
    follow_up = _request_handler(body)
    _OneShotConsoleHandler.handle_one_request(follow_up)

    response = body.getvalue().decode("iso-8859-1")
    assert "200 OK" in response
    assert "<h1>ok</h1>" in response


def test_console_unexpected_write_error_is_not_swallowed():
    handler = _request_handler(_FailingWrite(OSError("unexpected write failure")))

    with pytest.raises(OSError, match="unexpected write failure"):
        _OneShotConsoleHandler.handle_one_request(handler)
