from __future__ import annotations

import sqlite3

import pytest

from pipeline import runtime_db


def test_runtime_db_initializes_on_empty_path(tmp_path):
    db_path = tmp_path / "runtime" / "runtime.sqlite3"

    result = runtime_db.initialize(db_path)

    assert result == db_path
    assert db_path.exists()
    with runtime_db.connect(db_path) as conn:
        tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"projects", "artifacts", "pipeline_runs", "pipeline_events", "moments"} <= tables


def test_runtime_db_initialization_is_idempotent(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"

    runtime_db.initialize(db_path)
    runtime_db.initialize(db_path)

    with runtime_db.connect(db_path) as conn:
        count = conn.execute("SELECT COUNT(*) FROM projects").fetchone()[0]
    assert count == 0


def test_runtime_db_foreign_keys_operate(tmp_path):
    db_path = tmp_path / "runtime.sqlite3"
    runtime_db.initialize(db_path)

    with pytest.raises(sqlite3.IntegrityError):
        with runtime_db.transaction(db_path) as conn:
            conn.execute(
                """
                INSERT INTO artifacts(artifact_id, project_id, artifact_type, path, mime_type, status, created_at, metadata)
                VALUES('art_missing', 'missing', 'source_media', 'source.mp4', 'video/mp4', 'AVAILABLE', 'now', '{}')
                """
            )
