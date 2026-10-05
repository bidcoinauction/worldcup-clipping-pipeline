"""SQLite runtime database for the local Operator Console index."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
from typing import Iterator
import uuid

from .pilot import PILOT_RUNTIME_DIR


RUNTIME_DB_ENV = "STADIUM_RUNTIME_DB"
RUNTIME_BACKUPS_ENV = "STADIUM_RUNTIME_BACKUPS"
DEFAULT_RUNTIME_DB_NAME = "runtime.sqlite3"
BACKUP_DIR_NAME = "backups"

SCHEMA_VERSION = 14


def default_runtime_db_path() -> Path:
    """Resolve the default runtime index path.

    The index lives with pilot runtime data rather than in the source tree. Tests
    and local deployments can override it with ``STADIUM_RUNTIME_DB``.
    """
    env = os.environ.get(RUNTIME_DB_ENV)
    return Path(env) if env else PILOT_RUNTIME_DIR / DEFAULT_RUNTIME_DB_NAME


def default_backups_dir() -> Path:
    env = os.environ.get(RUNTIME_BACKUPS_ENV)
    return Path(env) if env else PILOT_RUNTIME_DIR / BACKUP_DIR_NAME


def connect(path: str | Path | None = None) -> sqlite3.Connection:
    db_path = Path(path) if path is not None else default_runtime_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


@contextmanager
def transaction(path: str | Path | None = None) -> Iterator[sqlite3.Connection]:
    conn = connect(path)
    try:
        yield conn
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


# ── Schema history (ordered, additive, idempotent) ────────────────────────────

_MIGRATION_FOUNDATION = """
CREATE TABLE IF NOT EXISTS projects (
    project_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL UNIQUE,
    profile TEXT NOT NULL,
    sport TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL,
    source_artifact_id TEXT,
    parent_project_id TEXT,
    source_project_id TEXT,
    reuse_mode TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(artifact_id) DEFERRABLE INITIALLY DEFERRED,
    FOREIGN KEY (parent_project_id) REFERENCES projects(project_id),
    FOREIGN KEY (source_project_id) REFERENCES projects(project_id)
);
CREATE TABLE IF NOT EXISTS artifacts (
    artifact_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    mime_type TEXT NOT NULL,
    status TEXT NOT NULL,
    parent_artifact_id TEXT,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    UNIQUE(project_id, artifact_type, path),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    stage TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'CANCELLED', 'BLOCKED')),
    started_at TEXT,
    finished_at TEXT,
    progress_current INTEGER,
    progress_total INTEGER,
    error_code TEXT,
    error_message TEXT,
    retry_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS pipeline_events (
    event_id TEXT PRIMARY KEY,
    run_id TEXT,
    project_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    stage TEXT NOT NULL,
    message TEXT NOT NULL,
    progress_current INTEGER,
    progress_total INTEGER,
    created_at TEXT NOT NULL,
    metadata TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (run_id) REFERENCES pipeline_runs(run_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_projects_job_id ON projects(job_id);
CREATE INDEX IF NOT EXISTS idx_artifacts_project_type ON artifacts(project_id, artifact_type);
CREATE INDEX IF NOT EXISTS idx_runs_project_stage_status ON pipeline_runs(project_id, stage, status);
CREATE INDEX IF NOT EXISTS idx_events_run_time ON pipeline_events(run_id, created_at);
CREATE INDEX IF NOT EXISTS idx_events_project_time ON pipeline_events(project_id, created_at);
"""

_MIGRATION_MOMENTS = """
CREATE TABLE IF NOT EXISTS moments (
    moment_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_artifact_id TEXT,
    sport TEXT NOT NULL,
    universal_event_type TEXT NOT NULL,
    sport_event_type TEXT NOT NULL,
    start_seconds REAL NOT NULL,
    peak_seconds REAL,
    end_seconds REAL NOT NULL,
    importance REAL,
    confidence REAL,
    team TEXT,
    review_state TEXT NOT NULL CHECK(review_state IN ('UNREVIEWED', 'KEEP', 'REJECT', 'STRONG', 'MUST_USE')),
    reviewed_at TEXT,
    reviewed_by TEXT,
    origin_moment_id TEXT,
    origin_project_id TEXT,
    participants_json TEXT NOT NULL DEFAULT '[]',
    signals_json TEXT NOT NULL DEFAULT '{}',
    emotion_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    CHECK(start_seconds >= 0),
    CHECK(end_seconds >= start_seconds),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_moments_project ON moments(project_id);
CREATE INDEX IF NOT EXISTS idx_moments_project_review ON moments(project_id, review_state);
CREATE INDEX IF NOT EXISTS idx_moments_project_event ON moments(project_id, universal_event_type);
CREATE INDEX IF NOT EXISTS idx_moments_project_start ON moments(project_id, start_seconds);
"""

_MIGRATION_STORIES = """
CREATE TABLE IF NOT EXISTS stories (
    story_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    title TEXT NOT NULL,
    summary TEXT NOT NULL DEFAULT '',
    archetype TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'SUGGESTED' CHECK(status IN ('DRAFT', 'SUGGESTED', 'APPROVED', 'REJECTED', 'ARCHIVED')),
    hook TEXT NOT NULL DEFAULT '',
    emotional_arc_json TEXT NOT NULL DEFAULT '[]',
    estimated_duration INTEGER,
    recommended_formats_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS story_moments (
    story_moment_id TEXT PRIMARY KEY,
    story_id TEXT NOT NULL,
    moment_id TEXT NOT NULL,
    narrative_role TEXT NOT NULL,
    sequence_order INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (moment_id) REFERENCES moments(moment_id)
);
CREATE INDEX IF NOT EXISTS idx_stories_project ON stories(project_id);
CREATE INDEX IF NOT EXISTS idx_stories_project_status ON stories(project_id, status);
CREATE INDEX IF NOT EXISTS idx_story_moments_story ON story_moments(story_id, sequence_order);
CREATE INDEX IF NOT EXISTS idx_story_moments_moment ON story_moments(moment_id);
"""

_MIGRATION_EDIT_BRIEFS = """
CREATE TABLE IF NOT EXISTS edit_briefs (
    edit_brief_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT NOT NULL,
    artifact_id TEXT,
    format_treatment TEXT NOT NULL CHECK(format_treatment IN ('SHORT', 'MEDIUM', 'LONG')),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'GENERATING', 'READY', 'FAILED', 'ARCHIVED')),
    editorial_intent TEXT NOT NULL DEFAULT '',
    target_duration INTEGER,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(story_id, format_treatment),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_edit_briefs_story ON edit_briefs(story_id);
CREATE INDEX IF NOT EXISTS idx_edit_briefs_project ON edit_briefs(project_id);
CREATE INDEX IF NOT EXISTS idx_edit_briefs_status ON edit_briefs(project_id, status);
"""

_MIGRATION_EDLS = """
CREATE TABLE IF NOT EXISTS edls (
    edl_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT NOT NULL,
    edit_brief_id TEXT NOT NULL,
    artifact_id TEXT,
    format_treatment TEXT NOT NULL CHECK(format_treatment IN ('SHORT', 'MEDIUM', 'LONG')),
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'GENERATING', 'READY', 'FAILED', 'ARCHIVED')),
    target_duration INTEGER,
    estimated_duration REAL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(edit_brief_id, format_treatment),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (edit_brief_id) REFERENCES edit_briefs(edit_brief_id) ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_edls_project ON edls(project_id);
CREATE INDEX IF NOT EXISTS idx_edls_story ON edls(story_id);
CREATE INDEX IF NOT EXISTS idx_edls_edit_brief ON edls(edit_brief_id);
CREATE INDEX IF NOT EXISTS idx_edls_project_status ON edls(project_id, status);
"""

_MIGRATION_RENDERS = """
CREATE TABLE IF NOT EXISTS renders (
    render_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT NOT NULL,
    edit_brief_id TEXT NOT NULL,
    edl_id TEXT NOT NULL,
    artifact_id TEXT,
    format_treatment TEXT NOT NULL CHECK(format_treatment IN ('SHORT', 'MEDIUM', 'LONG')),
    render_profile TEXT NOT NULL CHECK(render_profile IN ('REFERENCE', 'EDITORIAL')),
    channel_preset_id TEXT,
    platform TEXT,
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'RENDERING', 'READY', 'FAILED', 'ARCHIVED')),
    review_state TEXT NOT NULL DEFAULT 'UNREVIEWED' CHECK(review_state IN ('UNREVIEWED', 'APPROVED', 'NEEDS_CHANGES', 'REJECTED')),
    duration_seconds REAL,
    width INTEGER,
    height INTEGER,
    fps REAL,
    reviewed_at TEXT,
    reviewed_by TEXT,
    review_note TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(edl_id, render_profile, channel_preset_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (edit_brief_id) REFERENCES edit_briefs(edit_brief_id) ON DELETE CASCADE,
    FOREIGN KEY (edl_id) REFERENCES edls(edl_id) ON DELETE CASCADE,
    FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_renders_project ON renders(project_id);
CREATE INDEX IF NOT EXISTS idx_renders_story ON renders(story_id);
CREATE INDEX IF NOT EXISTS idx_renders_edl ON renders(edl_id);
CREATE INDEX IF NOT EXISTS idx_renders_project_status ON renders(project_id, status);
CREATE INDEX IF NOT EXISTS idx_renders_project_review ON renders(project_id, review_state);
"""

_MIGRATION_EXPORTS = """
CREATE TABLE IF NOT EXISTS export_packages (
    export_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT NOT NULL,
    render_id TEXT NOT NULL,
    channel_preset_id TEXT NOT NULL,
    platform TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT', 'READY', 'DELIVERED', 'FAILED', 'ARCHIVED')),
    video_artifact_id TEXT NOT NULL,
    thumbnail_artifact_id TEXT,
    caption TEXT NOT NULL DEFAULT '',
    title TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    hashtags_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(render_id, channel_preset_id),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (render_id) REFERENCES renders(render_id) ON DELETE CASCADE,
    FOREIGN KEY (video_artifact_id) REFERENCES artifacts(artifact_id),
    FOREIGN KEY (thumbnail_artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_exports_project ON export_packages(project_id);
CREATE INDEX IF NOT EXISTS idx_exports_story ON export_packages(story_id);
CREATE INDEX IF NOT EXISTS idx_exports_render ON export_packages(render_id);
CREATE INDEX IF NOT EXISTS idx_exports_project_status ON export_packages(project_id, status);
CREATE INDEX IF NOT EXISTS idx_exports_platform ON export_packages(platform);
"""

_MIGRATION_BATCH = """
CREATE TABLE IF NOT EXISTS batch_operations (
    batch_id TEXT PRIMARY KEY,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('ANALYZE')),
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'RUNNING', 'PARTIAL', 'SUCCEEDED', 'FAILED', 'CANCELLED')),
    total_items INTEGER NOT NULL DEFAULT 0,
    queued_count INTEGER NOT NULL DEFAULT 0,
    running_count INTEGER NOT NULL DEFAULT 0,
    succeeded_count INTEGER NOT NULL DEFAULT 0,
    failed_count INTEGER NOT NULL DEFAULT 0,
    blocked_count INTEGER NOT NULL DEFAULT 0,
    cancelled_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS batch_items (
    batch_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    operation_type TEXT NOT NULL CHECK(operation_type IN ('ANALYZE')),
    status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'RUNNING', 'SUCCEEDED', 'FAILED', 'BLOCKED', 'CANCELLED')),
    pipeline_run_id TEXT,
    error_code TEXT,
    error_message TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (batch_id) REFERENCES batch_operations(batch_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (pipeline_run_id) REFERENCES pipeline_runs(run_id) ON DELETE SET NULL
);
CREATE INDEX IF NOT EXISTS idx_batch_ops_status ON batch_operations(status);
CREATE INDEX IF NOT EXISTS idx_batch_ops_created ON batch_operations(created_at);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch ON batch_items(batch_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_project ON batch_items(project_id);
CREATE INDEX IF NOT EXISTS idx_batch_items_batch_status ON batch_items(batch_id, status);
"""

_MIGRATION_MOMENT_RELATIONS = """
CREATE TABLE IF NOT EXISTS moment_relations (
    relation_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_moment_id TEXT NOT NULL,
    target_moment_id TEXT NOT NULL,
    relation_type TEXT NOT NULL CHECK(relation_type IN ('CAUSES', 'ESCALATES', 'CALLBACK_TO', 'CONTRASTS', 'RESPONDS_TO', 'LEADS_TO', 'SAME_SEQUENCE')),
    weight REAL,
    confidence REAL,
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    CHECK(source_moment_id <> target_moment_id),
    CHECK(weight IS NULL OR (weight >= 0.0 AND weight <= 1.0)),
    CHECK(confidence IS NULL OR (confidence >= 0.0 AND confidence <= 1.0)),
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (source_moment_id) REFERENCES moments(moment_id) ON DELETE CASCADE,
    FOREIGN KEY (target_moment_id) REFERENCES moments(moment_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_moment_relations_project ON moment_relations(project_id);
CREATE INDEX IF NOT EXISTS idx_moment_relations_source ON moment_relations(source_moment_id);
CREATE INDEX IF NOT EXISTS idx_moment_relations_target ON moment_relations(target_moment_id);
CREATE INDEX IF NOT EXISTS idx_moment_relations_project_type ON moment_relations(project_id, relation_type);
"""

_MIGRATION_PROJECT_STRATEGY = """
ALTER TABLE projects ADD COLUMN analysis_strategy TEXT NOT NULL DEFAULT 'TRANSCRIPT_FIRST'
    CHECK(analysis_strategy IN ('TRANSCRIPT_FIRST', 'RESEARCH_FIRST', 'HYBRID'));
"""

_MIGRATION_RESEARCH = """
CREATE TABLE IF NOT EXISTS match_research (
    research_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    source_artifact_id TEXT,
    sport TEXT NOT NULL,
    competition TEXT NOT NULL,
    season TEXT,
    match_date TEXT NOT NULL,
    home_team TEXT NOT NULL,
    away_team TEXT NOT NULL,
    home_score INTEGER,
    away_score INTEGER,
    venue TEXT NOT NULL DEFAULT '',
    stage TEXT NOT NULL DEFAULT '',
    importance REAL,
    summary TEXT NOT NULL DEFAULT '',
    stakes TEXT NOT NULL DEFAULT '',
    historical_context TEXT NOT NULL DEFAULT '',
    sources_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE TABLE IF NOT EXISTS research_events (
    event_id TEXT PRIMARY KEY,
    research_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    match_minute INTEGER NOT NULL,
    match_second_optional INTEGER,
    universal_event_type TEXT NOT NULL CHECK(universal_event_type IN ('SCORE','ATTEMPT','DEFENSIVE_PLAY','FOUL','PENALTY','CARD','TURNOVER','SAVE','CELEBRATION','CROWD_REACTION','CONFRONTATION','TACTICAL_SHIFT','MOMENTUM_SHIFT','OTHER')),
    sport_event_type TEXT NOT NULL,
    team TEXT,
    participants_json TEXT NOT NULL DEFAULT '[]',
    headline TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    score_before TEXT NOT NULL DEFAULT '',
    score_after TEXT NOT NULL DEFAULT '',
    importance REAL,
    confidence REAL,
    source_refs_json TEXT NOT NULL DEFAULT '[]',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (research_id) REFERENCES match_research(research_id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_match_research_project ON match_research(project_id);
CREATE INDEX IF NOT EXISTS idx_research_events_project_time ON research_events(project_id, match_minute, match_second_optional);
CREATE INDEX IF NOT EXISTS idx_research_events_research ON research_events(research_id);
"""

_MIGRATION_EDIT_PLANS = """
CREATE TABLE IF NOT EXISTS edit_plans (
    edit_plan_id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL,
    story_id TEXT NOT NULL,
    edit_brief_id TEXT NOT NULL,
    title TEXT NOT NULL,
    target_platform TEXT NOT NULL,
    target_duration REAL,
    aspect_ratio TEXT NOT NULL,
    hook_text TEXT NOT NULL DEFAULT '',
    story_archetype TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'DRAFT' CHECK(status IN ('DRAFT','READY','RENDERING','FAILED','ARCHIVED')),
    renderer TEXT NOT NULL DEFAULT 'FFMPEG' CHECK(renderer IN ('FFMPEG','CHATCUT')),
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
    FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
    FOREIGN KEY (edit_brief_id) REFERENCES edit_briefs(edit_brief_id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS edit_beats (
    edit_beat_id TEXT PRIMARY KEY,
    edit_plan_id TEXT NOT NULL,
    sequence_order INTEGER NOT NULL,
    narrative_role TEXT NOT NULL,
    source_moment_id TEXT,
    purpose TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    source_start REAL,
    source_end REAL,
    target_duration REAL,
    crop_intent TEXT NOT NULL DEFAULT '',
    speed_intent TEXT NOT NULL DEFAULT '',
    text_overlay TEXT NOT NULL DEFAULT '',
    caption_intent TEXT NOT NULL DEFAULT '',
    audio_intent TEXT NOT NULL DEFAULT '',
    transition_intent TEXT NOT NULL DEFAULT '',
    motion_graphic_intent TEXT NOT NULL DEFAULT '',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    UNIQUE(edit_plan_id, sequence_order),
    FOREIGN KEY (edit_plan_id) REFERENCES edit_plans(edit_plan_id) ON DELETE CASCADE,
    FOREIGN KEY (source_moment_id) REFERENCES moments(moment_id)
);
CREATE TABLE IF NOT EXISTS timeline_instructions (
    instruction_id TEXT PRIMARY KEY,
    edit_plan_id TEXT NOT NULL,
    edit_beat_id TEXT,
    instruction_type TEXT NOT NULL,
    source_artifact_id TEXT,
    source_in REAL,
    source_out REAL,
    timeline_start REAL,
    timeline_duration REAL,
    crop_json TEXT NOT NULL DEFAULT '{}',
    scale_json TEXT NOT NULL DEFAULT '{}',
    position_json TEXT NOT NULL DEFAULT '{}',
    speed REAL,
    freeze_frame INTEGER NOT NULL DEFAULT 0,
    opacity REAL,
    text TEXT NOT NULL DEFAULT '',
    caption_style_json TEXT NOT NULL DEFAULT '{}',
    audio_gain REAL,
    music_cue TEXT NOT NULL DEFAULT '',
    sfx_cue TEXT NOT NULL DEFAULT '',
    transition TEXT NOT NULL DEFAULT '',
    motion_graphic_template_json TEXT NOT NULL DEFAULT '{}',
    motion_graphic_parameters_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (edit_plan_id) REFERENCES edit_plans(edit_plan_id) ON DELETE CASCADE,
    FOREIGN KEY (edit_beat_id) REFERENCES edit_beats(edit_beat_id) ON DELETE CASCADE,
    FOREIGN KEY (source_artifact_id) REFERENCES artifacts(artifact_id)
);
CREATE INDEX IF NOT EXISTS idx_edit_plans_project ON edit_plans(project_id);
CREATE INDEX IF NOT EXISTS idx_edit_plans_story ON edit_plans(story_id);
CREATE INDEX IF NOT EXISTS idx_edit_beats_plan_order ON edit_beats(edit_plan_id, sequence_order);
CREATE INDEX IF NOT EXISTS idx_timeline_instructions_plan ON timeline_instructions(edit_plan_id, timeline_start);
"""

_MIGRATION_PIPELINE_RUN_METADATA = """
ALTER TABLE pipeline_runs ADD COLUMN metadata_json TEXT NOT NULL DEFAULT '{}';
"""

SCHEMA_MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (1, "runtime foundation", _MIGRATION_FOUNDATION),
    (2, "canonical moments", _MIGRATION_MOMENTS),
    (3, "story runtime", _MIGRATION_STORIES),
    (4, "edit briefs", _MIGRATION_EDIT_BRIEFS),
    (5, "edls", _MIGRATION_EDLS),
    (6, "render runtime", _MIGRATION_RENDERS),
    (7, "export packages", _MIGRATION_EXPORTS),
    (8, "lineage columns", ""),
    (9, "batch orchestration", _MIGRATION_BATCH),
    (10, "moment relations", _MIGRATION_MOMENT_RELATIONS),
    (11, "project analysis strategy", _MIGRATION_PROJECT_STRATEGY),
    (12, "match research", _MIGRATION_RESEARCH),
    (13, "edit plans", _MIGRATION_EDIT_PLANS),
    (14, "pipeline run metadata", _MIGRATION_PIPELINE_RUN_METADATA),
)


# ── Schema metadata / version tracking ───────────────────────────────────────


def _ensure_schema_metadata(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        )
        """
    )


def _read_schema_version(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM schema_metadata WHERE key = 'schema_version'").fetchone()
    if row is None:
        return 0
    try:
        return int(row["value"])
    except (TypeError, ValueError):
        return 0


def _write_schema_version(conn: sqlite3.Connection, version: int) -> None:
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO schema_metadata(key, value, updated_at) VALUES('schema_version', ?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
        """,
        (str(version), now),
    )


def current_schema_version(path: str | Path | None = None) -> int:
    db_path = Path(path) if path is not None else default_runtime_db_path()
    if not db_path.exists():
        return 0
    with connect(db_path) as conn:
        _ensure_schema_metadata(conn)
        return _read_schema_version(conn)


def schema_is_current(path: str | Path | None = None) -> bool:
    return current_schema_version(path) >= SCHEMA_VERSION


# ── Backup / recovery ────────────────────────────────────────────────────────


def backup_db(src_path: str | Path, dest_path: str | Path) -> Path:
    """Create a consistent SQLite backup using the online backup API."""
    src = Path(src_path)
    dest = Path(dest_path)
    if not src.exists():
        raise FileNotFoundError(f"runtime database not found: {src}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(str(src)) as source_conn, sqlite3.connect(str(dest)) as target_conn:
        source_conn.backup(target_conn)
    return dest


def create_runtime_backup(db_path: str | Path | None = None, backups_dir: str | Path | None = None) -> Path:
    """Backup the runtime DB before an upgrade. Never overwrites an existing backup."""
    src = Path(db_path) if db_path is not None else default_runtime_db_path()
    if not src.exists():
        raise FileNotFoundError(f"runtime database not found: {src}")
    backups = Path(backups_dir) if backups_dir is not None else default_backups_dir()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
    dest = backups / f"{src.name}.backup-{timestamp}-{uuid.uuid4().hex[:8]}"
    return backup_db(src, dest)


def list_runtime_backups(backups_dir: str | Path | None = None) -> list[Path]:
    backups = Path(backups_dir) if backups_dir is not None else default_backups_dir()
    if not backups.exists():
        return []
    return sorted(backups.glob("runtime.sqlite3.backup-*"), key=lambda p: p.name)


# ── Migration application ────────────────────────────────────────────────────


def _apply_column_migrations(conn: sqlite3.Connection, version: int) -> None:
    if version == 6:
        _migrate_renders_presets(conn)
    if version == 8:
        _ensure_column(conn, "moments", "reviewed_at", "TEXT")
        _ensure_column(conn, "moments", "reviewed_by", "TEXT")
        _ensure_column(conn, "moments", "origin_moment_id", "TEXT")
        _ensure_column(conn, "moments", "origin_project_id", "TEXT")
        _ensure_column(conn, "projects", "parent_project_id", "TEXT")
        _ensure_column(conn, "projects", "source_project_id", "TEXT")
        _ensure_column(conn, "projects", "reuse_mode", "TEXT NOT NULL DEFAULT ''")


def _maybe_backup_before_migration(db_path: Path, current_version: int) -> Path | None:
    """Backup a non-empty existing DB before an upgrade."""
    if current_version >= SCHEMA_VERSION:
        return None
    if not db_path.exists():
        return None
    with connect(db_path) as conn:
        table = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='projects'").fetchone()
        if table is None:
            return None
        row = conn.execute("SELECT COUNT(*) AS n FROM projects").fetchone()
    if not row or row["n"] == 0:
        return None
    return create_runtime_backup(db_path)


def initialize(path: str | Path | None = None) -> Path:
    """Initialize the runtime schema, applying ordered migrations safely.

    Idempotent. Existing databases are backed up before an upgrade. The schema
    version is recorded only after each migration succeeds.
    """
    db_path = Path(path) if path is not None else default_runtime_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = connect(db_path)
    try:
        _ensure_schema_metadata(conn)
        version = _read_schema_version(conn)
        if version >= SCHEMA_VERSION:
            return db_path
        backup = _maybe_backup_before_migration(db_path, version)
        for mig_version, _label, ddl in SCHEMA_MIGRATIONS:
            if mig_version <= version:
                continue
            if ddl:
                try:
                    conn.executescript(ddl)
                except sqlite3.OperationalError as exc:
                    if not _is_already_applied_migration(conn, mig_version, exc):
                        raise
            _apply_column_migrations(conn, mig_version)
            _write_schema_version(conn, mig_version)
            conn.commit()
        return db_path
    except BaseException:
        conn.rollback()
        raise
    finally:
        conn.close()


def _is_already_applied_migration(conn: sqlite3.Connection, version: int, exc: sqlite3.OperationalError) -> bool:
    message = str(exc).lower()
    if version == 11 and "duplicate column name" in message and "analysis_strategy" in message:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(projects)")}
        return "analysis_strategy" in columns
    return False


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    columns = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
    if column not in columns:
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_renders_presets(conn: sqlite3.Connection) -> None:
    """Rebuild a legacy renders table to add preset columns.

    The legacy renders table used UNIQUE(edl_id, render_profile) and had no
    channel_preset_id/platform columns. This migration preserves rows and swaps
    in the preset-aware uniqueness rule.
    """
    columns = {row[1] for row in conn.execute("PRAGMA table_info(renders)")}
    if "channel_preset_id" in columns:
        return
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript(
            """
            BEGIN;
            CREATE TABLE renders_new (
                render_id TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                story_id TEXT NOT NULL,
                edit_brief_id TEXT NOT NULL,
                edl_id TEXT NOT NULL,
                artifact_id TEXT,
                format_treatment TEXT NOT NULL CHECK(format_treatment IN ('SHORT', 'MEDIUM', 'LONG')),
                render_profile TEXT NOT NULL CHECK(render_profile IN ('REFERENCE', 'EDITORIAL')),
                channel_preset_id TEXT,
                platform TEXT,
                status TEXT NOT NULL DEFAULT 'QUEUED' CHECK(status IN ('QUEUED', 'RENDERING', 'READY', 'FAILED', 'ARCHIVED')),
                review_state TEXT NOT NULL DEFAULT 'UNREVIEWED' CHECK(review_state IN ('UNREVIEWED', 'APPROVED', 'NEEDS_CHANGES', 'REJECTED')),
                duration_seconds REAL,
                width INTEGER,
                height INTEGER,
                fps REAL,
                reviewed_at TEXT,
                reviewed_by TEXT,
                review_note TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}',
                UNIQUE(edl_id, render_profile, channel_preset_id),
                FOREIGN KEY (project_id) REFERENCES projects(project_id) ON DELETE CASCADE,
                FOREIGN KEY (story_id) REFERENCES stories(story_id) ON DELETE CASCADE,
                FOREIGN KEY (edit_brief_id) REFERENCES edit_briefs(edit_brief_id) ON DELETE CASCADE,
                FOREIGN KEY (edl_id) REFERENCES edls(edl_id) ON DELETE CASCADE,
                FOREIGN KEY (artifact_id) REFERENCES artifacts(artifact_id)
            );
            INSERT INTO renders_new(
                render_id, project_id, story_id, edit_brief_id, edl_id, artifact_id, format_treatment,
                render_profile, status, review_state, duration_seconds, width, height, fps,
                reviewed_at, reviewed_by, review_note, created_at, updated_at, metadata_json
            )
            SELECT render_id, project_id, story_id, edit_brief_id, edl_id, artifact_id, format_treatment,
                render_profile, status, review_state, duration_seconds, width, height, fps,
                reviewed_at, reviewed_by, review_note, created_at, updated_at, metadata_json
            FROM renders;
            DROP TABLE renders;
            ALTER TABLE renders_new RENAME TO renders;
            CREATE INDEX idx_renders_project ON renders(project_id);
            CREATE INDEX idx_renders_story ON renders(story_id);
            CREATE INDEX idx_renders_edl ON renders(edl_id);
            CREATE INDEX idx_renders_project_status ON renders(project_id, status);
            CREATE INDEX idx_renders_project_review ON renders(project_id, review_state);
            COMMIT;
            """
        )
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
