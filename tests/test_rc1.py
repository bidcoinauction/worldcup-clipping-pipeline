from __future__ import annotations

import json

import pytest

from pipeline import runtime_db
from pipeline.safety import sanitize_text
from pipeline.system_health import core_health_report, doctor_ok


@pytest.fixture(autouse=True)
def _isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))
    for key in ("OORT_API_KEY", "OORT_BUCKET", "AIRTABLE_API_TOKEN", "AIRTABLE_BASE_ID", "SLACK_WEBHOOK_URL"):
        monkeypatch.delenv(key, raising=False)


# ── Clean-machine simulation ─────────────────────────────────────────────────


def test_clean_machine_initialization(tmp_path, monkeypatch):
    assert not (tmp_path / "runtime.sqlite3").exists()

    db_path = runtime_db.initialize()
    assert db_path.exists()
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION
    assert runtime_db.schema_is_current(db_path)
    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"projects", "moments", "stories", "edit_briefs", "edls", "renders", "export_packages",
            "batch_operations", "moment_relations", "schema_metadata"} <= tables

    from pipeline.integration_service import integration_health_report
    assert all(entry["status"] == "NOT_CONFIGURED" for entry in integration_health_report())

    from pipeline.operator_console import list_projects
    assert list_projects(jobs_dir=tmp_path / "jobs") == []


def test_initialize_is_idempotent(tmp_path, monkeypatch):
    first = runtime_db.initialize()
    runtime_db.initialize()
    with runtime_db.connect(first) as conn:
        count = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()["n"]
    assert count == 0


# ── Schema version / migration failure ───────────────────────────────────────


def test_migration_failure_does_not_advance_version_and_preserves_backup(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript("""
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE schema_metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at TEXT NOT NULL);
        """)
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1','j1','football','football','Match','READY','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO schema_metadata(key, value, updated_at) VALUES('schema_version','0','now')")

    failing = [(1, "base", runtime_db._MIGRATION_FOUNDATION), (2, "boom", "CREATE TABLE broken (id INTEGER")]
    monkeypatch.setattr(runtime_db, "SCHEMA_MIGRATIONS", failing)

    with pytest.raises(Exception):
        runtime_db.initialize()

    assert runtime_db.current_schema_version(db_path) == 1
    backups = runtime_db.list_runtime_backups()
    assert len(backups) >= 1
    with runtime_db.connect(db_path) as conn:
        project = conn.execute("SELECT * FROM projects WHERE project_id='p1'").fetchone()
    assert project["display_name"] == "Match"


# ── Upgrade simulations ──────────────────────────────────────────────────────


def test_upgrade_pre_moment_db(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript("""
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE artifacts (artifact_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, artifact_type TEXT NOT NULL, path TEXT NOT NULL, mime_type TEXT NOT NULL, status TEXT NOT NULL, parent_artifact_id TEXT, created_at TEXT NOT NULL, metadata TEXT NOT NULL DEFAULT '{}');
        """)
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1','j1','football','football','Match','READY','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO artifacts(artifact_id, project_id, artifact_type, path, mime_type, status, created_at) VALUES('a1','p1','source_media','/tmp/s.mp4','video/mp4','AVAILABLE','2026-01-01')")

    runtime_db.initialize()
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION
    assert len(runtime_db.list_runtime_backups()) >= 1
    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        project = conn.execute("SELECT * FROM projects WHERE project_id='p1'").fetchone()
        artifact = conn.execute("SELECT * FROM artifacts WHERE artifact_id='a1'").fetchone()
    assert {"moments", "stories", "edit_briefs", "edls", "renders", "export_packages", "moment_relations"} <= tables
    assert project["display_name"] == "Match"
    assert artifact["path"] == "/tmp/s.mp4"


def test_upgrade_phase3_ish_db(tmp_path, monkeypatch):
    db_path = tmp_path / "runtime.sqlite3"
    with runtime_db.transaction(db_path) as conn:
        conn.executescript("""
            CREATE TABLE projects (project_id TEXT PRIMARY KEY, job_id TEXT NOT NULL UNIQUE, profile TEXT NOT NULL, sport TEXT NOT NULL, display_name TEXT NOT NULL, status TEXT NOT NULL, source_artifact_id TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE moments (moment_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, sport TEXT NOT NULL, universal_event_type TEXT NOT NULL, sport_event_type TEXT NOT NULL, start_seconds REAL NOT NULL, end_seconds REAL NOT NULL, review_state TEXT NOT NULL, participants_json TEXT NOT NULL DEFAULT '[]', signals_json TEXT NOT NULL DEFAULT '{}', emotion_json TEXT NOT NULL DEFAULT '[]', metadata_json TEXT NOT NULL DEFAULT '{}', created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
            CREATE TABLE stories (story_id TEXT PRIMARY KEY, project_id TEXT NOT NULL, title TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'SUGGESTED', created_at TEXT NOT NULL, updated_at TEXT NOT NULL, metadata_json TEXT NOT NULL DEFAULT '{}');
        """)
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1','j1','football','football','Match','READY','2026-01-01','2026-01-01')")
        conn.execute("INSERT INTO stories(story_id, project_id, title, created_at, updated_at) VALUES('s1','p1','Story','2026-01-01','2026-01-01')")

    runtime_db.initialize()

    with runtime_db.connect(db_path) as conn:
        story = conn.execute("SELECT * FROM stories WHERE story_id='s1'").fetchone()
    assert story["title"] == "Story"
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION


def test_upgrade_phase5_db_is_noop(tmp_path, monkeypatch):
    db_path = runtime_db.initialize()
    with runtime_db.transaction(db_path) as conn:
        conn.execute("INSERT INTO projects(project_id, job_id, profile, sport, display_name, status, created_at, updated_at) VALUES('p1','j1','football','football','Match','READY','2026-01-01','2026-01-01')")

    before_backups = len(runtime_db.list_runtime_backups())
    runtime_db.initialize()
    assert runtime_db.current_schema_version(db_path) == runtime_db.SCHEMA_VERSION
    assert len(runtime_db.list_runtime_backups()) == before_backups


# ── Backup behavior ──────────────────────────────────────────────────────────


def test_manual_backup_and_no_overwrite(tmp_path, monkeypatch):
    runtime_db.initialize()
    first = runtime_db.create_runtime_backup()
    second = runtime_db.create_runtime_backup()
    assert first.exists()
    assert second.exists()
    assert first.name != second.name
    assert len(runtime_db.list_runtime_backups()) == 2


# ── Doctor / system health ───────────────────────────────────────────────────


def test_doctor_healthy_core(monkeypatch):
    monkeypatch.setattr("pipeline.system_health._ffmpeg_available", lambda name: True)
    monkeypatch.setattr("pipeline.system_health._whisper_importable", lambda: True)
    monkeypatch.setattr("pipeline.system_health._openai_configured", lambda: True)
    monkeypatch.setattr("pipeline.provider_service._ollama_ready", lambda: {"ready": True, "message": "ready"})
    monkeypatch.setattr("pipeline.provider_service._openai_ready", lambda: {"ready": True, "message": "ready"})
    report = {"core": core_health_report(), "integrations": []}
    assert doctor_ok(report) is True
    statuses = {check["status"] for check in report["core"]}
    assert statuses == {"PASS"}


def test_doctor_missing_ffmpeg_is_warn_not_fail(monkeypatch):
    monkeypatch.setattr("pipeline.system_health._ffmpeg_available", lambda name: name != "ffmpeg")
    report = {"core": core_health_report(), "integrations": []}
    assert doctor_ok(report) is True
    ffmpeg = next(c for c in report["core"] if c["check_id"] == "ffmpeg")
    assert ffmpeg["status"] == "WARN"


def test_doctor_strict_promotes_warnings_to_failure(monkeypatch):
    monkeypatch.setattr("pipeline.system_health._ffmpeg_available", lambda name: False)
    report = {"core": core_health_report(), "integrations": []}
    assert doctor_ok(report, strict=True) is False


def test_doctor_runtime_db_failure_is_fail(monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("migration failed")

    monkeypatch.setattr(runtime_db, "initialize", boom)
    report = {"core": core_health_report(), "integrations": []}
    assert doctor_ok(report) is False
    runtime_check = next(c for c in report["core"] if c["check_id"] == "runtime_db")
    assert runtime_check["status"] == "FAIL"
    assert "migration failed" in runtime_check["operator_message"]


# ── Secret safety ────────────────────────────────────────────────────────────


def test_sanitize_secrets_redacts_values():
    text = "key=sk-abcdefghijklmnopqrstuvwxyz1234567890 and token=abc and https://user:pass@host/x"
    cleaned = sanitize_text(text)
    assert "sk-abcdefghijklmnopqrstuvwxyz1234567890" not in cleaned
    assert "token=abc" not in cleaned
    assert "REDACTED" in cleaned


# ── Startup smoke test ───────────────────────────────────────────────────────


def test_startup_smoke(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    db_path = runtime_db.initialize()
    assert runtime_db.schema_is_current(db_path)

    from pipeline.system_health import full_health_report
    report = full_health_report(db_path)
    assert len(report["core"]) > 0
    assert all("check_id" in check for check in report["core"])

    from pipeline.operator_console import list_projects
    assert list_projects(jobs_dir=tmp_path / "jobs") == []

    from tests.test_operator_console import _FakeHandler, _html_body
    from pipeline.console_server import ConsoleHandler
    fake = _FakeHandler()
    ConsoleHandler._render_system(fake)
    html = _html_body(fake)
    assert "System" in html
    assert "Runtime DB" in html or "runtime_db" in html
    assert "Optional Integrations" in html