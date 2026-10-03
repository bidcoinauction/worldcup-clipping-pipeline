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


def _console_new_project_body(source: Path, *, operator_notes: str | None = None) -> bytes:
    data = {
        "sport": "football",
        "pilot_id": "netherlands_japan_2026_second_half_v3",
        "source_id": "netherlands_japan_2026_second_half_source_v3",
        "event_name": "Netherlands vs Japan - Second Half",
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
    assert intake["rights"]["status"] == "UNCONFIRMED"
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
    assert response["state"] == "AWAITING_RIGHTS"
    job = json.loads((jobs_root / f"{response['job_id']}.json").read_text(encoding="utf-8"))
    intake = json.loads(Path(job["intake_manifest_path"]).read_text(encoding="utf-8"))
    assert intake["media"]["local_file_path"] == str(source)
    assert intake["media"]["original_filename"] == source.name
    assert "operator_notes" not in intake["pilot"]
    assert intake["rights"]["status"] == "UNCONFIRMED"
    assert job["readiness_summary"]["structurally_valid"] is True
    assert job["readiness_summary"]["source_ready"] is True
    assert job["readiness_summary"]["rights_cleared"] is False


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

    conn = HTTPConnection("127.0.0.1", port, timeout=2)
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
