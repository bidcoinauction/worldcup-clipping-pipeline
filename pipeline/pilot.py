"""Managed pilot intake and job records.

Read-only intake validation, an explicit rights gate, local-file source
validation, and a durable append-only local job record for managed pilot
operations. Standard library only: no database, no network access, no media
processing, and no secrets.

This module is the operational wrapper that sits *around* the existing World
Cup pipeline. It never replaces match manifests, schedule data, or clip
manifests, and it never invokes ffmpeg, models, or external services.

Public surface:

* :func:`validate_intake` — structural, rights, and source validation.
* :func:`rights_cleared` — the rights gate.
* :func:`validate_source` — read-only local-file validation.
* :func:`create_job`, :func:`read_job`, :func:`show_job`, :func:`list_jobs`
  — durable job-record API.
* :func:`append_event` — append-only event history.

Runtime roots can be overridden with ``STADIUM_PILOT_JOBS_DIR`` and
``STADIUM_PILOT_INTAKE_ROOT`` (or explicit function arguments). Nothing is
written outside the configured job-record root.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import platform as _platform
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from .config_errors import ConfigurationError
from .configurator import (
    resolve_operational_categories,
    resolve_brand_profile,
    resolve_export_profile,
    resolve_output_root,
    resolve_project_identity,
    resolve_template,
    select_platforms,
    validate_editorial_taxonomy,
)
from .utils import ROOT

# ── Constants ────────────────────────────────────────────────────────────────

INTAKE_SCHEMA_VERSION = 1
JOB_SCHEMA_VERSION = 1
EVENT_SCHEMA_VERSION = 1
OUTPUT_MANIFEST_SCHEMA_VERSION = 1
DELIVERY_PACKAGE_SCHEMA_VERSION = 1
DELIVERY_CONFIRMATION_SCHEMA_VERSION = 1
PIPELINE_RUN_SCHEMA_VERSION = 1
EXECUTION_PLAN_SCHEMA_VERSION = 1

# Runtime roots. `data/pilot/` is gitignored; client intake and job records
# are never committed.
PILOT_RUNTIME_DIR = ROOT / "data" / "pilot"
PILOT_INTAKE_DIR = PILOT_RUNTIME_DIR / "intakes"
PILOT_JOBS_DIR = PILOT_RUNTIME_DIR / "jobs"

_EDITORIAL_DIR = ROOT / "config" / "editorial"

# Explicitly allowed media extensions (video + audio). Everything else is
# rejected as unsupported for a local-file pilot.
ALLOWED_MEDIA_EXTENSIONS = frozenset({
    ".mp4", ".mov", ".m4v", ".mkv", ".webm", ".ts", ".avi", ".mpg", ".mpeg",
    ".wav", ".mp3", ".m4a", ".aac", ".flac",
})

MEDIA_TYPES = frozenset({"video", "audio"})

RIGHTS_STATUSES = frozenset({
    "UNCONFIRMED", "CONFIRMED", "RESTRICTED", "EXPIRED", "REJECTED",
})

DELIVERY_METHODS = frozenset({"shared_folder", "local_directory", "manual"})

JOB_STATES = frozenset({
    "INTAKE_RECEIVED", "VALIDATION_FAILED", "AWAITING_RIGHTS", "READY",
    "RUNNING", "REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY",
    "DELIVERED", "FAILED", "CANCELLED",
})

TERMINAL_JOB_STATES = frozenset({"DELIVERED", "CANCELLED"})

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "READY": frozenset({"RUNNING", "CANCELLED", "FAILED"}),
    "RUNNING": frozenset({"REVIEW_REQUIRED", "FAILED", "CANCELLED"}),
    "REVIEW_REQUIRED": frozenset({"APPROVED", "RUNNING", "FAILED", "CANCELLED"}),
    "APPROVED": frozenset({"DELIVERY_READY", "REVIEW_REQUIRED", "FAILED", "CANCELLED"}),
    "DELIVERY_READY": frozenset({"DELIVERED", "REVIEW_REQUIRED", "FAILED", "CANCELLED"}),
    "AWAITING_RIGHTS": frozenset({"READY", "VALIDATION_FAILED", "CANCELLED"}),
    "VALIDATION_FAILED": frozenset({"READY", "AWAITING_RIGHTS", "CANCELLED"}),
    "FAILED": frozenset({"READY", "RUNNING"}),
    "DELIVERED": frozenset(),
    "CANCELLED": frozenset(),
    "INTAKE_RECEIVED": frozenset({"READY", "AWAITING_RIGHTS", "VALIDATION_FAILED", "CANCELLED"}),
}

FAILURE_CATEGORIES = frozenset({
    "SOURCE", "CONFIGURATION", "RIGHTS", "PROCESSING", "REVIEW",
    "DELIVERY", "OPERATOR", "UNKNOWN",
})

RIGHTS_REVALIDATION_STATES = frozenset({"READY", "RUNNING", "DELIVERY_READY", "DELIVERED"})

OUTPUT_TYPES = frozenset({
    "VIDEO_CLIP", "CAPTION", "THUMBNAIL", "TRANSCRIPT", "CLIP_MANIFEST",
    "REVIEW_DASHBOARD", "METADATA", "OTHER",
})
OUTPUT_REVIEW_STATUSES = frozenset({"PENDING", "APPROVED", "REJECTED", "CHANGES_REQUESTED", "EXCLUDED"})
OUTPUT_REGISTRATION_STATES = frozenset({"RUNNING", "REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY"})
OUTPUT_FILE_EXTENSIONS = {
    "VIDEO_CLIP": frozenset({".mp4", ".mov", ".m4v", ".mkv", ".webm", ".ts"}),
    "CAPTION": frozenset({".txt", ".srt", ".vtt", ".json", ".md"}),
    "THUMBNAIL": frozenset({".jpg", ".jpeg", ".png", ".webp", ".txt"}),
    "TRANSCRIPT": frozenset({".txt", ".json", ".srt", ".vtt"}),
    "CLIP_MANIFEST": frozenset({".csv", ".json"}),
    "REVIEW_DASHBOARD": frozenset({".html", ".htm"}),
    "METADATA": frozenset({".json", ".csv", ".yaml", ".yml", ".txt"}),
    "OTHER": frozenset({".txt", ".json", ".csv", ".pdf", ".zip"}),
}
DIRECTORY_OUTPUT_TYPES = frozenset({"REVIEW_DASHBOARD", "OTHER"})
DELIVERY_PACKAGE_STATES = frozenset({"APPROVED", "DELIVERY_READY"})
PIPELINE_RUN_JOB_STATES = frozenset({"READY", "RUNNING"})
PIPELINE_RUN_STATUSES = frozenset({"PLANNED", "STARTED", "SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED", "ABORTED"})
PIPELINE_RUN_FINAL_STATUSES = frozenset({"SUCCEEDED", "PARTIALLY_SUCCEEDED", "FAILED", "ABORTED"})
PIPELINE_STAGE_STATUSES = frozenset({"NOT_STARTED", "RUNNING", "SUCCEEDED", "SKIPPED", "FAILED"})
PIPELINE_STAGES = (
    "SOURCE_INTAKE", "CONCATENATION", "TRANSCRIPTION", "RESEARCH",
    "PROMPT_GENERATION", "DETECTION", "CLIP_MANIFEST", "ASSET_PROMPTS",
    "CLIP_EXPORT", "REVIEW_DASHBOARD", "OUTPUT_REGISTRATION",
)
PIPELINE_ENTRY_POINTS = {
    "process-match": "scripts/process_match.py",
    "process-from-manifest": "scripts/process_from_manifest.py",
    "process-scheduled-match": "scripts/process_scheduled_match.py",
    "transcribe-match": "scripts/transcribe_match.py",
    "generate-claude-prompt": "scripts/generate_claude_prompt.py",
    "run-gpt-detection": "scripts/run_gpt_detection.py",
    "build-clip-manifest": "scripts/build_clip_manifest.py",
    "generate-asset-prompts": "scripts/generate_asset_prompts.py",
    "export-clips-ffmpeg": "scripts/export_clips_ffmpeg.py",
    "export-research-windows": "scripts/export_research_windows.py",
    "build-stadium-dashboard": "scripts/build_stadium_dashboard.py",
    "pilot-output-register": "scripts/pilot_job.py",
}
EXECUTION_PLAN_STATUSES = frozenset({"DRAFT", "READY", "SUPERSEDED", "INVALIDATED"})
EXECUTION_PLAN_WORKFLOWS = frozenset({"local-match-file", "recording-manifest"})
PRODUCTION_PROJECTS = frozenset({"football"})

_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CHECKSUM_RE = re.compile(r"^(?:sha256:)?[0-9a-fA-F]{64}$")
_URL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://")
_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|token|password|passwd|secret|credential|authorization|"
    r"private[_-]?key|payment|card[_-]?(num|no)|publish[_-]?token|"
    r"access[_-]?(key|token)|client[_-]?(secret|id))",
    re.IGNORECASE,
)
_SECRET_VALUE_RE = re.compile(r"(sk-|ghp_|gho_|AKIA[0-9A-Z]{16}|-----BEGIN)")
_URL_CREDENTIAL_RE = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*://[^/\s]+@")
_BASE64_MEDIA_RE = re.compile(r"^[A-Za-z0-9+/]{200,}={0,2}$")

# Validation codes (stable identifiers used in reports, events, and tests).
C_INTAKE_OK = "INTAKE_OK"
C_MISSING_KEY = "MISSING_KEY"
C_UNKNOWN_KEY = "UNKNOWN_KEY"
C_BAD_TYPE = "BAD_TYPE"
C_BAD_ID = "BAD_ID"
C_BAD_DATE = "BAD_DATE"
C_BAD_CHECKSUM = "BAD_CHECKSUM"
C_SECRET_KEY = "SECRET_KEY"
C_SECRET_VALUE = "SECRET_VALUE"
C_BAD_COUNT_RANGE = "BAD_COUNT_RANGE"
C_CONFIG_UNKNOWN_PROJECT = "CONFIG_UNKNOWN_PROJECT"
C_CONFIG_UNKNOWN_BRAND = "CONFIG_UNKNOWN_BRAND"
C_CONFIG_UNKNOWN_EDITORIAL = "CONFIG_UNKNOWN_EDITORIAL"
C_CONFIG_UNKNOWN_TEMPLATE = "CONFIG_UNKNOWN_TEMPLATE"
C_CONFIG_UNKNOWN_EXPORT = "CONFIG_UNKNOWN_EXPORT_PROFILE"
C_CONFIG_BAD_DESTINATION = "CONFIG_BAD_DESTINATION"
C_RIGHTS_INVALID_STATUS = "RIGHTS_INVALID_STATUS"
C_RIGHTS_MISSING_STATEMENT = "RIGHTS_MISSING_STATEMENT"
C_RIGHTS_MISSING_CONFIRMER = "RIGHTS_MISSING_CONFIRMER"
C_RIGHTS_MISSING_DATE = "RIGHTS_MISSING_DATE"
C_RIGHTS_EMPTY_PERMISSIONS = "RIGHTS_EMPTY_PERMISSIONS"
C_RIGHTS_EXPIRED = "RIGHTS_EXPIRED"
C_RIGHTS_NOT_CONFIRMED = "RIGHTS_NOT_CONFIRMED"
C_PUBLISHING_NOT_PERMITTED = "PUBLISHING_NOT_PERMITTED"
C_PUBLISHING_MISSING_DISTRIBUTION = "PUBLISHING_MISSING_DISTRIBUTION_LIMITS"
C_SOURCE_URL_NOT_ALLOWED = "SOURCE_URL_NOT_ALLOWED"
C_SOURCE_UNSAFE_PATH = "SOURCE_UNSAFE_PATH"
C_SOURCE_PATH_NOT_ABSOLUTE = "SOURCE_PATH_NOT_ABSOLUTE"
C_SOURCE_MISSING = "SOURCE_MISSING"
C_SOURCE_IS_DIRECTORY = "SOURCE_IS_DIRECTORY"
C_SOURCE_UNREADABLE = "SOURCE_UNREADABLE"
C_SOURCE_EMPTY = "SOURCE_EMPTY"
C_SOURCE_UNSUPPORTED_EXTENSION = "SOURCE_UNSUPPORTED_EXTENSION"
C_SOURCE_CHECKSUM_MISMATCH = "SOURCE_CHECKSUM_MISMATCH"
C_SOURCE_DURATION_MISMATCH = "SOURCE_DURATION_MISMATCH"
C_SOURCE_OUTSIDE_ROOT = "SOURCE_OUTSIDE_INTAKE_ROOT"
C_REVIEW_REQUIRED = "HUMAN_REVIEW_REQUIRED"


# ── Exceptions ───────────────────────────────────────────────────────────────


class IntakeValidationError(Exception):
    """Raised when an intake dictionary fails structural validation.

    Carries the complete ``issues`` list (each with a full field path) so the
    CLI and callers can report every problem at once.
    """

    def __init__(self, issues: list[dict]) -> None:
        self.issues = issues
        super().__init__("; ".join(f"{i['path']}: {i['message']}" for i in issues))


class JobRecordError(Exception):
    """Base class for job-record failures (duplicate, missing, unsafe path)."""


class JobExistsError(JobRecordError):
    """Raised when creating a job whose identifier already exists."""


class JobNotFoundError(JobRecordError):
    """Raised when reading a job that does not exist."""


class JobPathError(JobRecordError):
    """Raised when a job path would escape the configured job-record root."""


class JobTransitionError(JobRecordError):
    """Raised for expected operational state-transition failures."""


class JobRevisionError(JobTransitionError):
    """Raised when optimistic concurrency detects a stale revision."""


class OutputManifestError(JobRecordError):
    """Raised for expected output-manifest registration/review failures."""

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        self.issues = issues or []
        super().__init__(message)


class DeliveryPackageError(JobRecordError):
    """Raised for expected delivery-package and confirmation failures."""

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        self.issues = issues or []
        super().__init__(message)


class PipelineRunError(JobRecordError):
    """Raised for expected manual pipeline-run record failures."""

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        self.issues = issues or []
        super().__init__(message)


class ExecutionPlanError(JobRecordError):
    """Raised for expected execution-plan generation/lifecycle failures."""

    def __init__(self, message: str, issues: list[dict] | None = None) -> None:
        self.issues = issues or []
        super().__init__(message)


# ── Runtime root helpers ─────────────────────────────────────────────────────


def default_jobs_dir() -> Path:
    """Resolve the job-record root: env override, else the module default."""
    env = os.environ.get("STADIUM_PILOT_JOBS_DIR")
    return Path(env) if env else PILOT_JOBS_DIR


def default_intake_root() -> str | None:
    """Resolve the allowed source intake root, or None when not configured."""
    return os.environ.get("STADIUM_PILOT_INTAKE_ROOT")


# ── Small validation helpers ─────────────────────────────────────────────────


def _issue(path: str, code: str, message: str) -> dict:
    return {"path": path, "code": code, "message": message}


def _is_valid_id(value: object) -> bool:
    return isinstance(value, str) and bool(_ID_RE.fullmatch(value))


def _is_iso_date(value: object) -> bool:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        _dt.date.fromisoformat(value)
        return True
    except ValueError:
        return False


def _str_field(data: dict, key: str, issues: list, path: str, *, optional: bool = False) -> None:
    if key not in data:
        if not optional:
            issues.append(_issue(f"{path}.{key}", C_MISSING_KEY, "is required"))
        return
    value = data[key]
    if not isinstance(value, str) or not value.strip():
        issues.append(_issue(f"{path}.{key}", C_BAD_TYPE, "expected a non-empty string"))


def _bool_field(data: dict, key: str, issues: list, path: str, *, optional: bool = False) -> None:
    if key not in data:
        if not optional:
            issues.append(_issue(f"{path}.{key}", C_MISSING_KEY, "is required"))
        return
    if not isinstance(data[key], bool):
        issues.append(_issue(f"{path}.{key}", C_BAD_TYPE, "expected a boolean"))


def _str_list(data: dict, key: str, issues: list, path: str, *, required: bool = False) -> None:
    if key not in data:
        if required:
            issues.append(_issue(f"{path}.{key}", C_MISSING_KEY, "is required"))
        return
    value = data[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        issues.append(_issue(f"{path}.{key}", C_BAD_TYPE, "expected a non-empty list of strings"))
    elif required and not value:
        issues.append(_issue(f"{path}.{key}", C_BAD_TYPE, "expected a non-empty list of strings"))


def _reject_unknown(obj: dict, allowed: tuple[str, ...], path: str, issues: list) -> None:
    for key in obj:
        if key.startswith("_"):
            continue  # underscore-prefixed keys are documentation-only comments
        if key not in allowed:
            issues.append(_issue(f"{path}.{key}", C_UNKNOWN_KEY, "is not a recognized intake key"))


def _scan_secrets(data: dict, path: str, issues: list) -> None:
    """Reject intake keys/values that look like credentials or payment data."""
    for key, value in data.items():
        if _SECRET_KEY_RE.search(key):
            issues.append(_issue(f"{path}.{key}", C_SECRET_KEY, "must not contain credentials or payment information"))
            continue
        if isinstance(value, dict):
            _scan_secrets(value, f"{path}.{key}", issues)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _scan_secrets(item, f"{path}.{key}[{index}]", issues)
                elif isinstance(item, str) and _SECRET_VALUE_RE.search(item):
                    issues.append(_issue(f"{path}.{key}[{index}]", C_SECRET_VALUE, "must not contain credential-like values"))
        elif isinstance(value, str) and _SECRET_VALUE_RE.search(value):
            issues.append(_issue(f"{path}.{key}", C_SECRET_VALUE, "must not contain credential-like values"))


# ── Structural validation ────────────────────────────────────────────────────

_TOP_KEYS = ("intake_version", "pilot", "media", "rights", "configuration", "review_and_delivery")
_PILOT_KEYS = (
    "pilot_id", "project", "reference_deployment", "requested_clip_count",
    "requested_delivery_date", "operator_notes",
)
_MEDIA_KEYS = (
    "source_id", "local_file_path", "original_filename", "media_type",
    "match_or_event_name", "event_date", "duration_seconds", "checksum",
    "supplied_by_client", "source_validation_completed",
)
_RIGHTS_KEYS = (
    "status", "confirmation_statement", "confirmed_by", "confirmation_date",
    "permitted_uses", "distribution_limitations", "territory_limitations",
    "expiration_date", "source_of_permission", "notes",
)
_CONFIG_KEYS = (
    "project", "brand", "editorial_taxonomy", "operational_taxonomy",
    "detection_template", "export_profiles", "delivery_destination",
)
_REVIEW_KEYS = (
    "human_review_required", "approval_method", "delivery_method",
    "delivery_directory", "expected_deliverables", "publishing_included",
)


def _validate_pilot_section(data: dict, issues: list) -> None:
    path = "pilot"
    _reject_unknown(data, _PILOT_KEYS, path, issues)
    pilot_id = data.get("pilot_id")
    if not _is_valid_id(pilot_id):
        issues.append(_issue(f"{path}.pilot_id", C_BAD_ID,
                             "expected a non-empty identifier using only letters, digits, '_' or '-'"))
    for key in ("project", "reference_deployment"):
        _str_field(data, key, issues, path)
    _str_field(data, "operator_notes", issues, path, optional=True)

    if "requested_clip_count" in data:
        count = data["requested_clip_count"]
        if isinstance(count, dict):
            if "min" in count and (isinstance(count["min"], bool) or not isinstance(count["min"], int) or count["min"] < 0):
                issues.append(_issue(f"{path}.requested_clip_count.min", C_BAD_TYPE, "expected a non-negative integer"))
            if "max" in count and (isinstance(count["max"], bool) or not isinstance(count["max"], int) or count["max"] < 0):
                issues.append(_issue(f"{path}.requested_clip_count.max", C_BAD_TYPE, "expected a non-negative integer"))
            if "min" in count and "max" in count and isinstance(count["min"], int) and isinstance(count["max"], int):
                if count["min"] > count["max"]:
                    issues.append(_issue(f"{path}.requested_clip_count", C_BAD_COUNT_RANGE, "min must not exceed max"))
        elif isinstance(count, bool) or not isinstance(count, int) or count < 0:
            issues.append(_issue(f"{path}.requested_clip_count", C_BAD_TYPE, "expected a non-negative integer or {min,max} object"))

    if "requested_delivery_date" in data and data["requested_delivery_date"] is not None:
        if not _is_iso_date(data["requested_delivery_date"]):
            issues.append(_issue(f"{path}.requested_delivery_date", C_BAD_DATE, "expected YYYY-MM-DD"))


def _validate_media_section(data: dict, issues: list) -> None:
    path = "media"
    _reject_unknown(data, _MEDIA_KEYS, path, issues)
    source_id = data.get("source_id")
    if not _is_valid_id(source_id):
        issues.append(_issue(f"{path}.source_id", C_BAD_ID,
                             "expected a non-empty identifier using only letters, digits, '_' or '-'"))
    file_path = data.get("local_file_path")
    if not isinstance(file_path, str) or not file_path.strip():
        issues.append(_issue(f"{path}.local_file_path", C_BAD_TYPE, "expected a non-empty local file path"))
    _str_field(data, "original_filename", issues, path)

    media_type = data.get("media_type")
    if media_type is None:
        issues.append(_issue(f"{path}.media_type", C_MISSING_KEY, "is required"))
    elif not isinstance(media_type, str) or media_type not in MEDIA_TYPES:
        issues.append(_issue(f"{path}.media_type", C_BAD_TYPE, f"expected one of {', '.join(sorted(MEDIA_TYPES))}"))

    _str_field(data, "match_or_event_name", issues, path, optional=True)
    if "event_date" in data and data["event_date"] is not None and not _is_iso_date(data["event_date"]):
        issues.append(_issue(f"{path}.event_date", C_BAD_DATE, "expected YYYY-MM-DD"))
    if "duration_seconds" in data and data["duration_seconds"] is not None:
        duration = data["duration_seconds"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or duration <= 0:
            issues.append(_issue(f"{path}.duration_seconds", C_BAD_TYPE, "expected a positive number of seconds"))
    if "checksum" in data and data["checksum"] is not None:
        checksum = data["checksum"]
        if not isinstance(checksum, str) or not _CHECKSUM_RE.fullmatch(checksum):
            issues.append(_issue(f"{path}.checksum", C_BAD_CHECKSUM,
                                 "expected a 64-character hex SHA-256 checksum (optionally prefixed with 'sha256:')"))
    _bool_field(data, "supplied_by_client", issues, path)
    _bool_field(data, "source_validation_completed", issues, path)


def _validate_rights_section(data: dict, issues: list) -> None:
    path = "rights"
    _reject_unknown(data, _RIGHTS_KEYS, path, issues)
    status = data.get("status")
    if status is None:
        issues.append(_issue(f"{path}.status", C_MISSING_KEY, "is required"))
    elif not isinstance(status, str) or status not in RIGHTS_STATUSES:
        issues.append(_issue(f"{path}.status", C_RIGHTS_INVALID_STATUS,
                             f"expected one of {', '.join(sorted(RIGHTS_STATUSES))}"))

    confirmation_required = status == "CONFIRMED"
    statement = data.get("confirmation_statement")
    if confirmation_required and not (isinstance(statement, str) and statement.strip()):
        issues.append(_issue(f"{path}.confirmation_statement", C_RIGHTS_MISSING_STATEMENT,
                             "a confirmation statement is required for CONFIRMED rights"))
    elif statement is not None and not isinstance(statement, str):
        issues.append(_issue(f"{path}.confirmation_statement", C_BAD_TYPE,
                             f"expected a string, got {type(statement).__name__}"))

    confirmer = data.get("confirmed_by")
    if confirmation_required and not (isinstance(confirmer, str) and confirmer.strip()):
        issues.append(_issue(f"{path}.confirmed_by", C_RIGHTS_MISSING_CONFIRMER,
                             "a confirmer identity is required for CONFIRMED rights"))
    elif confirmer is not None and not isinstance(confirmer, str):
        issues.append(_issue(f"{path}.confirmed_by", C_BAD_TYPE,
                             f"expected a string, got {type(confirmer).__name__}"))

    confirmation_date = data.get("confirmation_date")
    if confirmation_date is None:
        if confirmation_required:
            issues.append(_issue(f"{path}.confirmation_date", C_RIGHTS_MISSING_DATE, "is required for CONFIRMED rights"))
    elif not _is_iso_date(confirmation_date):
        issues.append(_issue(f"{path}.confirmation_date", C_BAD_DATE, "expected YYYY-MM-DD"))

    _str_list(data, "permitted_uses", issues, path, required=True)
    _str_list(data, "distribution_limitations", issues, path, required=False)
    _str_field(data, "territory_limitations", issues, path, optional=True)
    _str_field(data, "source_of_permission", issues, path, optional=True)
    _str_field(data, "notes", issues, path, optional=True)

    if "expiration_date" in data and data["expiration_date"] is not None:
        if not _is_iso_date(data["expiration_date"]):
            issues.append(_issue(f"{path}.expiration_date", C_BAD_DATE, "expected YYYY-MM-DD"))


def _validate_config_section(data: dict, issues: list) -> None:
    path = "configuration"
    _reject_unknown(data, _CONFIG_KEYS, path, issues)
    for key in ("project", "brand", "editorial_taxonomy", "operational_taxonomy", "detection_template", "delivery_destination"):
        _str_field(data, key, issues, path)
    _str_list(data, "export_profiles", issues, path, required=True)
    destination = data.get("delivery_destination")
    if isinstance(destination, str) and destination.strip():
        if _URL_RE.match(destination):
            issues.append(_issue(f"{path}.delivery_destination", C_CONFIG_BAD_DESTINATION, "must be a local folder, not a URL"))
        elif ".." in Path(destination).parts or Path(destination).is_absolute():
            issues.append(_issue(f"{path}.delivery_destination", C_CONFIG_BAD_DESTINATION,
                                 "must be a repository-relative folder without path traversal"))


def _validate_review_section(data: dict, issues: list) -> None:
    path = "review_and_delivery"
    _reject_unknown(data, _REVIEW_KEYS, path, issues)
    _bool_field(data, "human_review_required", issues, path)
    if data.get("human_review_required") is False:
        issues.append(_issue(f"{path}.human_review_required", C_REVIEW_REQUIRED,
                             "managed pilot operations require human review before approval or delivery"))
    _str_field(data, "approval_method", issues, path)
    delivery_method = data.get("delivery_method")
    if delivery_method is None:
        issues.append(_issue(f"{path}.delivery_method", C_MISSING_KEY, "is required"))
    elif not isinstance(delivery_method, str) or delivery_method not in DELIVERY_METHODS:
        issues.append(_issue(f"{path}.delivery_method", C_BAD_TYPE,
                             f"expected one of {', '.join(sorted(DELIVERY_METHODS))}"))
    _str_field(data, "delivery_directory", issues, path)
    _str_list(data, "expected_deliverables", issues, path, required=True)
    _bool_field(data, "publishing_included", issues, path, optional=True)


def _validate_structure(data: object) -> list[dict]:
    """Strictly validate the intake dictionary. Returns a list of issues;
    raises :class:`IntakeValidationError` when the root is not an object."""
    if not isinstance(data, dict):
        raise IntakeValidationError([_issue("intake", C_BAD_TYPE, "root must be an object")])
    issues: list[dict] = []
    _scan_secrets(data, "intake", issues)
    _reject_unknown(data, _TOP_KEYS, "intake", issues)

    if "intake_version" not in data:
        issues.append(_issue("intake.intake_version", C_MISSING_KEY, "is required"))
    elif data["intake_version"] != INTAKE_SCHEMA_VERSION:
        issues.append(_issue("intake.intake_version", C_BAD_TYPE, f"expected schema version {INTAKE_SCHEMA_VERSION}"))

    for key in _TOP_KEYS[1:]:
        if key not in data:
            issues.append(_issue(f"intake.{key}", C_MISSING_KEY, "is required"))
        elif not isinstance(data[key], dict):
            issues.append(_issue(f"intake.{key}", C_BAD_TYPE, "expected an object"))

    if isinstance(data.get("pilot"), dict):
        _validate_pilot_section(data["pilot"], issues)
    if isinstance(data.get("media"), dict):
        _validate_media_section(data["media"], issues)
    if isinstance(data.get("rights"), dict):
        _validate_rights_section(data["rights"], issues)
    if isinstance(data.get("configuration"), dict):
        _validate_config_section(data["configuration"], issues)
    if isinstance(data.get("review_and_delivery"), dict):
        _validate_review_section(data["review_and_delivery"], issues)

    return issues


# ── Configuration reference validation ───────────────────────────────────────


def _validate_config_refs(data: dict) -> list[dict]:
    """Resolve every configuration reference through the canonical resolvers.

    Returns issues with full field paths. Performs no network access and no
    file mutation beyond reading registered configuration files.
    """
    issues: list[dict] = []
    config = data.get("configuration") or {}
    project = config.get("project")
    if not isinstance(project, str) or not project.strip():
        issues.append(_issue("configuration.project", C_CONFIG_UNKNOWN_PROJECT, "project reference is required"))
        return issues

    try:
        resolve_project_identity(project)
    except ConfigurationError as exc:
        issues.append(_issue("configuration.project", C_CONFIG_UNKNOWN_PROJECT, str(exc)))

    brand = config.get("brand")
    if isinstance(brand, str) and brand:
        try:
            resolve_brand_profile(brand)
        except ConfigurationError as exc:
            issues.append(_issue("configuration.brand", C_CONFIG_UNKNOWN_BRAND, str(exc)))
    else:
        issues.append(_issue("configuration.brand", C_CONFIG_UNKNOWN_BRAND, "brand reference is required"))

    editorial = config.get("editorial_taxonomy")
    if isinstance(editorial, str) and editorial:
        editorial_path = _EDITORIAL_DIR / f"{editorial}.json"
        if not editorial_path.exists():
            issues.append(_issue("configuration.editorial_taxonomy", C_CONFIG_UNKNOWN_EDITORIAL,
                                 f"editorial taxonomy file not found at '{editorial_path}'"))
        else:
            try:
                validate_editorial_taxonomy(json.loads(editorial_path.read_text(encoding="utf-8")),
                                            source=editorial_path.name)
            except (ConfigurationError, OSError, json.JSONDecodeError) as exc:
                issues.append(_issue("configuration.editorial_taxonomy", C_CONFIG_UNKNOWN_EDITORIAL, str(exc)))
    else:
        issues.append(_issue("configuration.editorial_taxonomy", C_CONFIG_UNKNOWN_EDITORIAL,
                             "editorial taxonomy reference is required"))

    if not isinstance(config.get("operational_taxonomy"), str) or not config.get("operational_taxonomy"):
        issues.append(_issue("configuration.operational_taxonomy", C_MISSING_KEY, "is required"))

    template = config.get("detection_template")
    if isinstance(template, str) and template:
        try:
            resolve_template(template, profile=project if isinstance(project, str) else "football")
        except ConfigurationError as exc:
            issues.append(_issue("configuration.detection_template", C_CONFIG_UNKNOWN_TEMPLATE, str(exc)))
    else:
        issues.append(_issue("configuration.detection_template", C_CONFIG_UNKNOWN_TEMPLATE, "detection template reference is required"))

    export_profiles = config.get("export_profiles")
    if isinstance(export_profiles, list) and export_profiles:
        for index, profile_id in enumerate(export_profiles):
            if not isinstance(profile_id, str) or not profile_id:
                issues.append(_issue(f"configuration.export_profiles[{index}]", C_CONFIG_UNKNOWN_EXPORT, "must be a non-empty profile identifier"))
                continue
            try:
                resolve_export_profile(profile_id)
            except ConfigurationError as exc:
                issues.append(_issue(f"configuration.export_profiles[{index}]", C_CONFIG_UNKNOWN_EXPORT, str(exc)))
    elif export_profiles is not None and not isinstance(export_profiles, list):
        issues.append(_issue("configuration.export_profiles", C_BAD_TYPE, "expected a list of profile identifiers"))

    return issues


# ── Rights gate ──────────────────────────────────────────────────────────────


def _evaluate_rights(data: dict) -> tuple[bool, str | None, list[dict]]:
    """Evaluate the rights gate. Returns (cleared, status, issues).

    Only ``CONFIRMED`` rights that are not expired clear the gate. The
    structural issues were already collected by :func:`_validate_structure`;
    this layer records the *gate* decision.
    """
    rights = data.get("rights") or {}
    status = rights.get("status")
    issues: list[dict] = []
    cleared = False

    if status == "CONFIRMED":
        cleared = True
        expiration = rights.get("expiration_date")
        if expiration is not None:
            try:
                if _dt.date.fromisoformat(expiration) < _dt.date.today():
                    cleared = False
                    issues.append(_issue("rights.expiration_date", C_RIGHTS_EXPIRED,
                                         "rights confirmation has expired"))
            except ValueError:
                cleared = False
        if cleared:
            uses = rights.get("permitted_uses") or []
            if not uses:
                cleared = False
                issues.append(_issue("rights.permitted_uses", C_RIGHTS_EMPTY_PERMISSIONS,
                                     "CONFIRMED rights must list at least one permitted use"))
            publishing = (data.get("review_and_delivery") or {}).get("publishing_included")
            if publishing and not any(u.lower() in {"publish", "public_distribution"} for u in uses):
                cleared = False
                issues.append(_issue("rights.permitted_uses", C_PUBLISHING_NOT_PERMITTED,
                                     "publishing is requested but 'publish'/'public_distribution' is not a permitted use"))
            if publishing and not (rights.get("distribution_limitations") or []):
                cleared = False
                issues.append(_issue("rights.distribution_limitations", C_PUBLISHING_MISSING_DISTRIBUTION,
                                     "publishing is requested but no distribution limitations are recorded"))
        if not cleared:
            issues.append(_issue("rights.status", C_RIGHTS_NOT_CONFIRMED,
                                 "rights are not cleared for execution (CONFIRMED and unexpired required)"))
    elif status == "RESTRICTED":
        issues.append(_issue("rights.status", C_RIGHTS_NOT_CONFIRMED,
                             "RESTRICTED rights validate structurally but require an explicit supported-use check before execution"))
    elif status in ("UNCONFIRMED", "EXPIRED", "REJECTED"):
        issues.append(_issue("rights.status", C_RIGHTS_NOT_CONFIRMED,
                             f"rights status {status} is not cleared for execution; only CONFIRMED passes the gate"))
    return cleared, status, issues


def rights_cleared(data: dict) -> tuple[bool, str | None]:
    """Public rights-gate check. Returns (cleared, rights_status)."""
    cleared, status, _ = _evaluate_rights(data)
    return cleared, status


# ── Source validation (read-only) ────────────────────────────────────────────


def _ffprobe_duration(path: Path) -> float | None:
    """Best-effort duration probe. Returns None when ffprobe is unavailable."""
    if not shutil.which("ffprobe"):
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "json", str(path)],
            capture_output=True, text=True, check=True,
        )
        return float(json.loads(result.stdout)["format"]["duration"])
    except Exception:
        return None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _within_dir(path: Path, root: Path) -> bool:
    try:
        return path.resolve().relative_to(root.resolve()) is not None
    except ValueError:
        return False


def _resolve_source_path(data: dict, intake_root: str | None) -> tuple[Path | None, list[dict]]:
    """Resolve and sanity-check the local file path. Returns (path, issues)."""
    media = data.get("media") or {}
    raw = media.get("local_file_path")
    issues: list[dict] = []

    if not isinstance(raw, str) or not raw.strip():
        issues.append(_issue("media.local_file_path", C_SOURCE_MISSING, "no local file path is configured"))
        return None, issues

    if _URL_RE.match(raw.strip()):
        issues.append(_issue("media.local_file_path", C_SOURCE_URL_NOT_ALLOWED,
                             "network URLs are not accepted in this local-file pilot format"))
        return None, issues

    candidate = Path(raw)
    if candidate.is_absolute():
        resolved = candidate.resolve()
    else:
        if ".." in candidate.parts:
            issues.append(_issue("media.local_file_path", C_SOURCE_UNSAFE_PATH,
                                 "relative source paths must not contain '..' traversal"))
            return None, issues
        base = Path(intake_root).resolve() if intake_root else ROOT
        resolved = (base / candidate).resolve()

    if intake_root:
        root_path = Path(intake_root).resolve()
        if not _within_dir(resolved, root_path):
            issues.append(_issue("media.local_file_path", C_SOURCE_OUTSIDE_ROOT,
                                 f"source path escapes the configured intake root '{root_path}'"))
            return None, issues

    return resolved, issues


def _validate_source(data: dict, *, intake_root: str | None = None) -> tuple[bool, list[dict], bool, str | None]:
    """Validate the local source file. Returns (ok, issues, duration_checked,
    duration_limitation). Read-only: never modifies, moves, copies, or
    transcodes the media and makes no network requests."""
    issues: list[dict] = []
    media = data.get("media") or {}
    resolved, path_issues = _resolve_source_path(data, intake_root)
    issues.extend(path_issues)
    if resolved is None:
        return False, issues, False, None

    if not resolved.exists():
        issues.append(_issue("media.local_file_path", C_SOURCE_MISSING, f"source file not found: {resolved}"))
    elif resolved.is_dir():
        issues.append(_issue("media.local_file_path", C_SOURCE_IS_DIRECTORY, "source path points to a directory, not a media file"))
    else:
        if not os.access(resolved, os.R_OK):
            issues.append(_issue("media.local_file_path", C_SOURCE_UNREADABLE, "source file is not readable"))
        if resolved.stat().st_size == 0:
            issues.append(_issue("media.local_file_path", C_SOURCE_EMPTY, "source file is empty"))
        extension = resolved.suffix.lower()
        if extension not in ALLOWED_MEDIA_EXTENSIONS:
            issues.append(_issue("media.local_file_path", C_SOURCE_UNSUPPORTED_EXTENSION,
                                 f"extension '{extension or '(none)'}' is not in the allowed media set: "
                                 f"{', '.join(sorted(ALLOWED_MEDIA_EXTENSIONS))}"))

    checksum = media.get("checksum")
    if resolved.exists() and resolved.is_file() and checksum:
        expected = checksum.removeprefix("sha256:")
        actual = _sha256(resolved)
        if actual.lower() != expected.lower():
            issues.append(_issue("media.checksum", C_SOURCE_CHECKSUM_MISMATCH,
                                 "provided checksum does not match the source file"))

    duration_checked = False
    duration_limitation: str | None = None
    provided_duration = media.get("duration_seconds")
    if provided_duration is not None and resolved.exists() and resolved.is_file():
        actual = _ffprobe_duration(resolved)
        if actual is None:
            duration_limitation = ("ffprobe is unavailable on this machine; duration was not verified "
                                   "(reported as an environmental limitation, not silent success)")
        else:
            duration_checked = True
            if abs(actual - float(provided_duration)) > 5.0:
                issues.append(_issue("media.duration_seconds", C_SOURCE_DURATION_MISMATCH,
                                     f"recorded duration {provided_duration}s does not match probed duration {actual:.1f}s"))

    ok = not issues
    return ok, issues, duration_checked, duration_limitation


def validate_source(data: dict, *, intake_root: str | None = None) -> tuple[bool, list[dict], bool, str | None]:
    """Public read-only source validator. Returns (ok, issues, duration_checked,
    duration_limitation)."""
    return _validate_source(data, intake_root=intake_root)


# ── Intake validation ────────────────────────────────────────────────────────


def validate_intake(data: object, *, intake_root: str | None = None,
                    check_source: bool = True, check_rights: bool = True) -> dict:
    """Validate an intake dictionary and return a readiness report.

    The report separates the four readiness layers so the CLI can report them
    distinctly:

    * ``structurally_valid`` — schema and field validation passed.
    * ``config_references_valid`` — every registered reference resolves.
    * ``source_ready`` — the local media file passed read-only validation.
    * ``rights_cleared`` — the rights gate passed.
    * ``execution_ready`` — all four layers passed.

    Read-only: performs no network access and mutates nothing.
    """
    issues: list[dict] = []

    try:
        structural_issues = _validate_structure(data)
    except IntakeValidationError as exc:
        structural_issues = exc.issues
    structural_valid = not structural_issues
    issues.extend(structural_issues)

    config_ok = False
    config_is_dict = isinstance(data, dict) and isinstance(data.get("configuration"), dict)
    if config_is_dict:
        config_issues = _validate_config_refs(data)
        config_ok = not config_issues
        issues.extend(config_issues)
    else:
        issues.append(_issue("configuration", C_MISSING_KEY,
                             "configuration references not checked because the intake is not structurally valid"))

    rights_ok = False
    rights_status: str | None = None
    if structural_valid and config_ok and check_rights:
        rights_ok, rights_status, rights_issues = _evaluate_rights(data)
        issues.extend(rights_issues)
    elif structural_valid and check_rights:
        rights_status = str((data.get("rights") or {}).get("status")) if isinstance(data.get("rights"), dict) else None
        issues.append(_issue("rights", C_MISSING_KEY,
                             "rights gate not evaluated because configuration references did not resolve"))
    elif structural_valid:
        rights_status = str((data.get("rights") or {}).get("status")) if isinstance(data.get("rights"), dict) else None

    source_ok = False
    duration_checked = False
    duration_limitation: str | None = None
    if structural_valid and config_ok and check_source:
        source_ok, source_issues, duration_checked, duration_limitation = _validate_source(data, intake_root=intake_root)
        issues.extend(source_issues)
    elif structural_valid and check_source:
        issues.append(_issue("media", C_MISSING_KEY,
                             "source not validated because configuration references did not resolve"))

    execution_ready = bool(structural_valid and config_ok and rights_ok and source_ok)
    codes = [issue["code"] for issue in issues] or [C_INTAKE_OK]

    return {
        "structurally_valid": structural_valid,
        "config_references_valid": config_ok,
        "source_ready": source_ok,
        "rights_cleared": rights_ok,
        "rights_status": rights_status,
        "execution_ready": execution_ready,
        "duration_checked": duration_checked,
        "duration_limitation": duration_limitation,
        "validation_codes": codes,
        "issues": issues,
    }


def job_id_for_intake(data: object) -> str | None:
    """Derive a stable job identifier from pilot_id and source_id, or None."""
    if not isinstance(data, dict):
        return None
    pilot = data.get("pilot") if isinstance(data.get("pilot"), dict) else {}
    media = data.get("media") if isinstance(data.get("media"), dict) else {}
    pilot_id = pilot.get("pilot_id")
    source_id = media.get("source_id")
    if _is_valid_id(pilot_id) and _is_valid_id(source_id):
        return f"{pilot_id}_{source_id}"
    return None


def project_for_intake(data: object) -> str:
    if isinstance(data, dict) and isinstance(data.get("configuration"), dict):
        project = data["configuration"].get("project")
        if isinstance(project, str) and project:
            return project
    return "football"


# ── Job records ──────────────────────────────────────────────────────────────


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _atomic_write_text(path: Path, text: str) -> None:
    """Write *text* to *path* atomically (temp file + os.replace). Explicit
    utf-8 encoding, stable formatting, no shell execution."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _atomic_write_json(path: Path, data: object) -> None:
    _atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def _job_files(job_id: str, jobs_dir: Path) -> tuple[Path, Path]:
    if not _is_valid_id(job_id):
        raise JobPathError(f"invalid job identifier '{job_id}'")
    record = Path(jobs_dir) / f"{job_id}.json"
    events = Path(jobs_dir) / f"{job_id}.events.json"
    if not _within_dir(record, Path(jobs_dir)):
        raise JobPathError(f"job record for '{job_id}' would escape the job-record root '{jobs_dir}'")
    return record, events


def _read_json_file(path: Path, default: object) -> object:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def _read_events(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, events_path = _job_files(job_id, jobs_dir_path)
    raw = _read_json_file(events_path, [])
    return raw if isinstance(raw, list) else []


def _normalized_revision(job: dict) -> int:
    revision = job.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def _normalized_job(job: dict) -> dict:
    normalized = dict(job)
    normalized.setdefault("revision", _normalized_revision(job))
    normalized.setdefault("readiness_summary", {})
    normalized.setdefault("event_count", 0)
    return normalized


def _next_event_sequence(events: list[dict]) -> int:
    max_sequence = 0
    for index, event in enumerate(events, start=1):
        sequence = event.get("sequence")
        if isinstance(sequence, int) and sequence > max_sequence:
            max_sequence = sequence
        else:
            max_sequence = max(max_sequence, index)
    return max_sequence + 1


def _event_id(job_id: str, sequence: int, event_type: str) -> str:
    return f"{job_id}-{sequence:06d}-{event_type.lower()}"


def _latest_state_event(events: list[dict]) -> dict | None:
    for event in reversed(events):
        if event.get("new_state"):
            return event
    return None


def _ensure_state_consistency(job: dict, events: list[dict]) -> None:
    latest = _latest_state_event(events)
    if not latest:
        return
    latest_state = latest.get("new_state")
    current_state = job.get("current_state")
    if latest_state != current_state:
        raise JobRecordError(
            f"job '{job.get('job_id', '')}' state mismatch: record is '{current_state}' "
            f"but latest event is '{latest_state}'"
        )


def create_job(intake_data: object, *, intake_path: str | Path | None = None,
               jobs_dir: str | Path | None = None, operator: str | None = None,
               source: str = "pilot_job.create",
               intake_root: str | None = None) -> dict:
    """Create a durable job record for an intake.

    State decision (deterministic):

    * structurally valid + execution-ready -> ``READY``
    * structurally valid + source-ready but rights not cleared -> ``AWAITING_RIGHTS``
    * structurally valid but source/config validation failed -> ``VALIDATION_FAILED``
    * not structurally valid (and identifiers derivable) -> ``VALIDATION_FAILED``

    Refuses duplicate job identifiers. Writes nothing outside *jobs_dir*.
    Never processes media and makes no network requests.
    """
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    report = validate_intake(intake_data, intake_root=intake_root)

    job_id = job_id_for_intake(intake_data)
    if job_id is None:
        raise JobRecordError(
            "cannot create a job record without valid pilot.pilot_id and media.source_id: "
            + "; ".join(f"{i['path']}: {i['message']}" for i in report["issues"])
        )

    record_path, events_path = _job_files(job_id, jobs_dir_path)
    if record_path.exists():
        raise JobExistsError(f"job '{job_id}' already exists at '{record_path}'")

    valid_for_state = report["structurally_valid"] and report["config_references_valid"]
    if valid_for_state:
        if report["execution_ready"]:
            state = "READY"
            message = "Intake validated as execution-ready; job created and ready to run."
        elif report["source_ready"] and not report["rights_cleared"]:
            state = "AWAITING_RIGHTS"
            message = "Intake is structurally valid and source-ready, but rights are not cleared."
        else:
            state = "VALIDATION_FAILED"
            message = "Intake failed source validation; job record created for tracing."
    else:
        state = "VALIDATION_FAILED"
        message = "Intake failed structural or configuration validation; job record created for tracing."

    now = _now_iso()
    pilot = intake_data["pilot"] if isinstance(intake_data, dict) else {}
    media = intake_data["media"] if isinstance(intake_data, dict) else {}
    config = intake_data["configuration"] if isinstance(intake_data, dict) else {}

    try:
        expected_output_root = resolve_output_root(project_for_intake(intake_data))
    except ConfigurationError:
        expected_output_root = resolve_output_root()

    job: dict = {
        "schema_version": JOB_SCHEMA_VERSION,
        "job_id": job_id,
        "pilot_id": pilot.get("pilot_id", ""),
        "source_id": media.get("source_id", ""),
        "project_id": config.get("project", "football"),
        "created_at": now,
        "updated_at": now,
        "revision": 0,
        "current_state": state,
        "intake_manifest_path": str(Path(intake_path).resolve()) if intake_path else "",
        "expected_output_root": expected_output_root,
        "readiness_summary": {
            "structurally_valid": report["structurally_valid"],
            "source_ready": report["source_ready"],
            "rights_cleared": report["rights_cleared"],
            "execution_ready": report["execution_ready"],
        },
        "event_count": 1,
    }

    _atomic_write_json(record_path, job)
    append_event(
        job_id,
        "CREATED",
        new_state=state,
        previous_state=None,
        message=message,
        related_codes=report["validation_codes"],
        operator=operator,
        source=source,
        jobs_dir=jobs_dir_path,
    )
    return job


def read_job(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read a job record. Raises :class:`JobNotFoundError` when absent."""
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, _ = _job_files(job_id, jobs_dir_path)
    if not record_path.exists():
        raise JobNotFoundError(f"job '{job_id}' not found in '{jobs_dir_path}'")
    return _normalized_job(json.loads(record_path.read_text(encoding="utf-8")))


def append_event(job_id: str, event_type: str, *, new_state: str | None = None,
                 previous_state: str | None = None, message: str = "",
                 related_codes: list[str] | None = None,
                 operator: str | None = None, source: str = "pilot",
                 jobs_dir: str | Path | None = None) -> dict:
    """Append a state-decision event to a job's event history.

    The event history is append-only through this API: existing events are
    preserved and never rewritten in place.
    """
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, events_path = _job_files(job_id, jobs_dir_path)
    events = _read_events(job_id, jobs_dir_path)
    sequence = _next_event_sequence(events)

    event = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _event_id(job_id, sequence, event_type),
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": _now_iso(),
        "event_type": event_type,
        "previous_state": previous_state,
        "new_state": new_state,
        "message": message,
        "metadata": {},
        "artifact_references": [],
        "related_codes": sorted(related_codes or []),
        "operator": operator,
        "source": source,
    }
    events.append(event)
    _atomic_write_json(events_path, events)
    return event


def allowed_next_states(state: str) -> list[str]:
    """Return the explicit allowed destination states for *state*."""
    return sorted(ALLOWED_TRANSITIONS.get(state, frozenset()))


def _require_non_empty(metadata: dict, key: str, job_id: str, current: str, target: str) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobTransitionError(
            _transition_error(job_id, current, target, f"missing required field '{key}'")
        )
    return value.strip()


def _require_bool(metadata: dict, key: str, job_id: str, current: str, target: str) -> bool:
    value = metadata.get(key)
    if not isinstance(value, bool):
        raise JobTransitionError(
            _transition_error(job_id, current, target, f"missing required boolean field '{key}'")
        )
    return value


def _require_positive_int(metadata: dict, key: str, job_id: str, current: str, target: str) -> int:
    value = metadata.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise JobTransitionError(
            _transition_error(job_id, current, target, f"missing required positive integer field '{key}'")
        )
    return value


def _transition_error(job_id: str, current: str, target: str, detail: str) -> str:
    allowed = allowed_next_states(current)
    allowed_text = ", ".join(allowed) if allowed else "(none)"
    return (
        f"job '{job_id}' cannot transition from '{current}' to '{target}': {detail}; "
        f"allowed next states from '{current}': {allowed_text}"
    )


def _validate_text_for_secrets(value: str, path: str) -> None:
    if _SECRET_VALUE_RE.search(value) or _URL_CREDENTIAL_RE.match(value):
        raise JobTransitionError(f"{path}: transition metadata must not contain credentials or secret-like values")


def _validate_metadata(metadata: dict, path: str = "metadata") -> dict:
    if not isinstance(metadata, dict):
        raise JobTransitionError(f"{path}: expected an object of transition metadata")
    issues: list[dict] = []
    _scan_secrets(metadata, path, issues)
    if issues:
        first = issues[0]
        raise JobTransitionError(f"{first['path']}: {first['message']}")
    clean: dict = {}
    for key, value in metadata.items():
        if not isinstance(key, str) or not key.strip():
            raise JobTransitionError(f"{path}: metadata keys must be non-empty strings")
        if isinstance(value, str):
            _validate_text_for_secrets(value, f"{path}.{key}")
            clean[key] = value.strip()
        elif isinstance(value, (bool, int)) or value is None:
            clean[key] = value
        elif isinstance(value, list):
            clean[key] = _validate_artifacts(value, field_path=f"{path}.{key}")
        else:
            raise JobTransitionError(f"{path}.{key}: unsupported metadata value type {type(value).__name__}")
    return clean


def _validate_artifacts(artifacts: object, *, field_path: str = "artifact_references") -> list[str]:
    if artifacts is None:
        return []
    if not isinstance(artifacts, list):
        raise JobTransitionError(f"{field_path}: expected a list of artifact references")
    clean: list[str] = []
    for index, value in enumerate(artifacts):
        path = f"{field_path}[{index}]"
        if not isinstance(value, str) or not value.strip():
            raise JobTransitionError(f"{path}: expected a non-empty string artifact reference")
        ref = value.strip()
        _validate_text_for_secrets(ref, path)
        if ".." in Path(ref).parts:
            raise JobTransitionError(f"{path}: path traversal is not allowed in artifact references")
        clean.append(ref)
    return clean


def _load_job_and_events(job_id: str, jobs_dir: Path) -> tuple[Path, Path, dict, list[dict]]:
    record_path, events_path = _job_files(job_id, jobs_dir)
    if not record_path.exists():
        raise JobNotFoundError(f"job '{job_id}' not found in '{jobs_dir}'")
    job = _normalized_job(json.loads(record_path.read_text(encoding="utf-8")))
    events = _read_events(job_id, jobs_dir)
    _ensure_state_consistency(job, events)
    return record_path, events_path, job, events


def _load_stored_intake(job: dict) -> dict:
    intake_path = job.get("intake_manifest_path")
    if not isinstance(intake_path, str) or not intake_path.strip():
        raise JobTransitionError(
            f"job '{job.get('job_id', '')}' has no intake_manifest_path; cannot revalidate source/rights"
        )
    path = Path(intake_path)
    if not path.is_file():
        raise JobTransitionError(
            f"job '{job.get('job_id', '')}' intake manifest not found: {path}"
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise JobTransitionError(f"job '{job.get('job_id', '')}' intake manifest root must be an object")
    return data


def _require_intake_readiness(job: dict, target: str, *, intake_root: str | None = None) -> dict:
    intake = _load_stored_intake(job)
    check_source = target in {"READY", "RUNNING"}
    report = validate_intake(intake, intake_root=intake_root, check_source=check_source, check_rights=True)
    rights_ok = report["rights_cleared"]
    source_ok = report["source_ready"] if check_source else True
    if not rights_ok or not source_ok or not report["structurally_valid"] or not report["config_references_valid"]:
        codes = ", ".join(report["validation_codes"])
        raise JobTransitionError(
            f"job '{job.get('job_id', '')}' cannot transition to '{target}': stored intake is not ready "
            f"(rights_cleared={rights_ok}, source_ready={source_ok}); validation codes: {codes}"
        )
    review = intake.get("review_and_delivery") if isinstance(intake.get("review_and_delivery"), dict) else {}
    if target in {"REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY", "DELIVERED"}:
        if review.get("human_review_required") is not True:
            raise JobTransitionError(
                f"job '{job.get('job_id', '')}' cannot transition to '{target}': "
                "review_and_delivery.human_review_required must be true"
            )
    return report


def _validate_transition_requirements(job: dict, current: str, target: str, metadata: dict,
                                      artifacts: list[str], *, intake_root: str | None,
                                      jobs_dir: str | Path | None = None) -> tuple[dict | None, list[str]]:
    job_id = job.get("job_id", "")
    if target == "RUNNING":
        _require_non_empty(metadata, "operator", job_id, current, target)
        if current == "FAILED":
            _require_non_empty(metadata, "recovery_reason", job_id, current, target)
            _require_bool(metadata, "recovery_confirmed", job_id, current, target)
            if metadata.get("recovery_confirmed") is not True:
                raise JobTransitionError(_transition_error(job_id, current, target, "recovery_confirmed must be true"))
        return _require_intake_readiness(job, target, intake_root=intake_root), []

    if target == "READY":
        if current == "FAILED":
            _require_non_empty(metadata, "operator", job_id, current, target)
            _require_non_empty(metadata, "recovery_reason", job_id, current, target)
            _require_bool(metadata, "recovery_confirmed", job_id, current, target)
            if metadata.get("recovery_confirmed") is not True:
                raise JobTransitionError(_transition_error(job_id, current, target, "recovery_confirmed must be true"))
        return _require_intake_readiness(job, target, intake_root=intake_root), []

    if target == "REVIEW_REQUIRED":
        _require_non_empty(metadata, "operator", job_id, current, target)
        _require_non_empty(metadata, "reason", job_id, current, target)
        if not artifacts:
            raise JobTransitionError(_transition_error(job_id, current, target, "missing required field 'artifact_references'"))
        _require_intake_readiness(job, target, intake_root=intake_root)
        return None, artifacts

    if target == "APPROVED":
        _require_non_empty(metadata, "operator", job_id, current, target)
        statement = metadata.get("approval_statement") or metadata.get("reason")
        if not isinstance(statement, str) or not statement.strip():
            raise JobTransitionError(_transition_error(job_id, current, target, "missing required field 'approval_statement'"))
        metadata["approval_statement"] = statement.strip()
        metadata["approval_timestamp"] = _now_iso()
        deliverable_count = _require_positive_int(metadata, "deliverable_count", job_id, current, target)
        _require_intake_readiness(job, target, intake_root=intake_root)
        _require_output_readiness(job_id, deliverable_count, target, intake_root=intake_root, jobs_dir=jobs_dir)
        return None, artifacts

    if target == "DELIVERY_READY":
        deliverable_count = _require_positive_int(metadata, "deliverable_count", job_id, current, target)
        package = _require_delivery_package_ready(
            job, deliverable_count, target, jobs_dir=jobs_dir, intake_root=intake_root,
            package_id=metadata.get("delivery_package_id") if isinstance(metadata.get("delivery_package_id"), str) else None,
        )
        metadata["delivery_package_id"] = package["package_id"]
        metadata.setdefault("delivery_method", package.get("delivery_method"))
        metadata.setdefault("delivery_destination", package.get("delivery_destination"))
        for key in ("delivery_method", "delivery_destination"):
            if metadata.get(key) != package.get(key):
                raise JobTransitionError(_transition_error(job_id, current, target, f"{key} must match delivery package"))
        if not artifacts:
            jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
            artifacts = [
                str(_delivery_package_path(job_id, package["package_id"], jobs_dir_path)),
                str(_delivery_checklist_path(job_id, package["package_id"], jobs_dir_path)),
            ]
        report = _require_intake_readiness(job, target, intake_root=intake_root)
        _require_output_readiness(job_id, deliverable_count, target, intake_root=intake_root, jobs_dir=jobs_dir)
        return report, artifacts

    if target == "DELIVERED":
        _require_non_empty(metadata, "operator", job_id, current, target)
        _require_non_empty(metadata, "confirmation", job_id, current, target)
        delivered_count = _require_positive_int(metadata, "delivered_item_count", job_id, current, target)
        package = _require_delivery_confirmation_ready(
            job, delivered_count, target, jobs_dir=jobs_dir, intake_root=intake_root,
            package_id=metadata.get("delivery_package_id") if isinstance(metadata.get("delivery_package_id"), str) else None,
        )
        metadata["delivery_package_id"] = package["package_id"]
        metadata.setdefault("delivery_method", package.get("delivery_method"))
        metadata.setdefault("delivery_destination", package.get("delivery_destination"))
        if not artifacts:
            jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
            artifacts = [
                str(_delivery_package_path(job_id, package["package_id"], jobs_dir_path)),
                str(_delivery_confirmation_path(job_id, package["package_id"], jobs_dir_path)),
            ]
        report = _require_intake_readiness(job, target, intake_root=intake_root)
        _require_output_readiness(job_id, delivered_count, target, intake_root=intake_root, jobs_dir=jobs_dir)
        return report, artifacts

    if target == "FAILED":
        _require_non_empty(metadata, "reason", job_id, current, target)
        category = _require_non_empty(metadata, "failure_category", job_id, current, target).upper()
        if category not in FAILURE_CATEGORIES:
            raise JobTransitionError(
                _transition_error(job_id, current, target,
                                  f"failure_category must be one of {', '.join(sorted(FAILURE_CATEGORIES))}")
            )
        metadata["failure_category"] = category
        _require_bool(metadata, "retry_allowed", job_id, current, target)
        if not metadata.get("operator") and not metadata.get("source"):
            raise JobTransitionError(_transition_error(job_id, current, target, "missing required field 'operator' or 'source'"))
        return None, artifacts

    if target == "CANCELLED":
        _require_non_empty(metadata, "reason", job_id, current, target)
        _require_non_empty(metadata, "operator", job_id, current, target)
        _require_bool(metadata, "client_requested", job_id, current, target)
        return None, artifacts

    if target in {"AWAITING_RIGHTS", "VALIDATION_FAILED"}:
        _require_non_empty(metadata, "reason", job_id, current, target)
        return None, artifacts

    return None, artifacts


def _require_output_readiness(job_id: str, expected_count: int, target: str, *, intake_root: str | None,
                              jobs_dir: str | Path | None = None) -> dict:
    summary = output_summary(job_id, jobs_dir=jobs_dir, intake_root=intake_root)
    approved_count = summary["approved_delivery_included_count"]
    if summary["manifest_count"] == 0:
        raise JobTransitionError(f"job '{job_id}' cannot transition to '{target}': no output manifests are registered")
    if not summary["review_complete"]:
        issue_codes = ", ".join(issue.get("code", "") for issue in summary.get("issues", [])[:5])
        if not issue_codes and summary.get("rights_issues"):
            issue_codes = "; ".join(summary["rights_issues"][:1])
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': output review is not complete "
            f"(included={summary['delivery_included_count']}, approved_included={approved_count}, "
            f"missing={summary['missing_file_count']}, invalid={summary['invalid_reference_count']}); {issue_codes}"
        )
    if approved_count != expected_count:
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': deliverable count {expected_count} "
            f"does not match approved delivery-included output count {approved_count}"
        )
    return summary


def transition_job(job_id: str, target_state: str, *, metadata: dict | None = None,
                   artifact_references: list[str] | None = None,
                   expected_revision: int | None = None,
                   jobs_dir: str | Path | None = None,
                   intake_root: str | None = None,
                   source: str = "pilot_job.transition") -> dict:
    """Validate and record a manual job-state transition.

    The function never runs the clipping pipeline, copies files, deletes files,
    publishes, or makes network requests. Failed transitions append no events.
    """
    if target_state not in JOB_STATES:
        raise JobTransitionError(f"requested state '{target_state}' is not recognized; known states: {', '.join(sorted(JOB_STATES))}")

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    current = job.get("current_state", "")
    if current not in JOB_STATES:
        raise JobTransitionError(f"job '{job_id}' current state '{current}' is not recognized")

    revision = _normalized_revision(job)
    if expected_revision is not None and expected_revision != revision:
        raise JobRevisionError(
            f"job '{job_id}' stale revision: expected {expected_revision}, current revision {revision}; "
            f"no transition from '{current}' to '{target_state}' was recorded"
        )

    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target_state not in allowed:
        raise JobTransitionError(_transition_error(job_id, current, target_state, "transition is not allowed"))

    metadata_clean = _validate_metadata(dict(metadata or {}))
    artifacts_clean = _validate_artifacts(artifact_references or [])
    report, artifacts_clean = _validate_transition_requirements(
        job, current, target_state, metadata_clean, artifacts_clean, intake_root=intake_root, jobs_dir=jobs_dir_path
    )

    sequence = _next_event_sequence(events)
    operator = metadata_clean.get("operator") if isinstance(metadata_clean.get("operator"), str) else None
    reason = metadata_clean.get("reason") or metadata_clean.get("approval_statement") or metadata_clean.get("confirmation")
    if not isinstance(reason, str) or not reason.strip():
        reason = f"Manual transition {current} -> {target_state}"

    event = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _event_id(job_id, sequence, "TRANSITION"),
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": _now_iso(),
        "event_type": "TRANSITION",
        "previous_state": current,
        "new_state": target_state,
        "operator": operator,
        "message": reason.strip(),
        "metadata": metadata_clean,
        "source": source,
        "related_codes": report["validation_codes"] if report else [],
        "artifact_references": artifacts_clean,
    }
    new_events = [*events, event]

    now = _now_iso()
    new_revision = revision + 1
    updated_job = dict(job)
    updated_job.update({
        "schema_version": JOB_SCHEMA_VERSION,
        "current_state": target_state,
        "updated_at": now,
        "revision": new_revision,
        "event_count": len(new_events),
    })
    if report:
        previous_summary = job.get("readiness_summary", {}) if isinstance(job.get("readiness_summary"), dict) else {}
        source_ready = report["source_ready"] if target_state in {"READY", "RUNNING"} else previous_summary.get("source_ready", report["source_ready"])
        updated_job["readiness_summary"] = {
            "structurally_valid": report["structurally_valid"],
            "source_ready": source_ready,
            "rights_cleared": report["rights_cleared"],
            "execution_ready": bool(report["structurally_valid"] and report["config_references_valid"] and report["rights_cleared"] and source_ready),
        }
    updated_job["latest_event"] = {
        "event_id": event["event_id"],
        "timestamp": event["timestamp"],
        "event_type": event["event_type"],
        "previous_state": current,
        "new_state": target_state,
        "message": event["message"],
    }

    # Write both files only after every validation has passed. The event is
    # prepared before the state update and written with the updated job record.
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return updated_job


def read_history(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Return privacy-safe event history for a job."""
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, events = _load_job_and_events(job_id, jobs_dir_path)
    _ensure_state_consistency(job, events)
    history: list[dict] = []
    for index, event in enumerate(events, start=1):
        history.append({
            "sequence": event.get("sequence", index),
            "event_id": event.get("event_id", ""),
            "timestamp": event.get("timestamp", ""),
            "event_type": event.get("event_type", ""),
            "previous_state": event.get("previous_state"),
            "new_state": event.get("new_state"),
            "operator": event.get("operator"),
            "message": event.get("message", ""),
            "source": event.get("source", ""),
        })
    return history


# ── Execution plan manifests ─────────────────────────────────────────────────

_EXECUTION_PLAN_KEYS = (
    "schema_version", "plan_id", "job_id", "pilot_id", "project_id", "source_id",
    "created_at", "created_by", "updated_at", "revision", "job_revision_snapshot",
    "job_revision_after_generation", "status", "workflow", "repository", "working_directory",
    "python_executable", "readiness_snapshot", "provenance", "required_tools",
    "required_environment_variables", "stages", "manual_run", "expected_inputs",
    "expected_outputs", "completion_evidence", "command_previews", "supersedes_plan_id",
    "invalidated_at", "invalidated_by", "invalidation_reason",
)
_PLAN_STAGE_KEYS = (
    "sequence", "stage_id", "classification", "enabled", "skip_reason", "entry_point",
    "script_path", "arguments", "working_directory", "inputs", "expected_outputs",
    "configuration_references", "required_tools", "required_environment_variables",
    "completion_evidence", "command_preview",
)
_PLAN_STAGE_ENTRY_POINTS = {
    "SOURCE_INTAKE": "process-match",
    "CONCATENATION": "process-from-manifest",
    "TRANSCRIPTION": "transcribe-match",
    "RESEARCH": "export-research-windows",
    "PROMPT_GENERATION": "generate-claude-prompt",
    "DETECTION": "run-gpt-detection",
    "CLIP_MANIFEST": "build-clip-manifest",
    "ASSET_PROMPTS": "generate-asset-prompts",
    "CLIP_EXPORT": "export-clips-ffmpeg",
    "REVIEW_DASHBOARD": "build-stadium-dashboard",
    "OUTPUT_REGISTRATION": "pilot-output-register",
}
_PLAN_STAGE_CLASSIFICATIONS = {
    "SOURCE_INTAKE": "required",
    "CONCATENATION": "optional",
    "TRANSCRIPTION": "required",
    "RESEARCH": "optional",
    "PROMPT_GENERATION": "required",
    "DETECTION": "required",
    "CLIP_MANIFEST": "required",
    "ASSET_PROMPTS": "optional",
    "CLIP_EXPORT": "required",
    "REVIEW_DASHBOARD": "optional",
    "OUTPUT_REGISTRATION": "required",
}
_PLAN_ENVIRONMENT_NAMES = ("FOOTBALL_ARCHIVE_ROOT", "OPENAI_API_KEY", "ANTHROPIC_API_KEY")


def _plan_issue(path: str, code: str, message: str) -> dict:
    return {"path": path, "code": code, "message": message}


def _execution_plan_dir(job_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(job_id):
        raise JobPathError(f"invalid job identifier '{job_id}'")
    directory = Path(jobs_dir) / f"{job_id}.plans"
    if not _within_dir(directory, Path(jobs_dir)):
        raise JobPathError(f"execution plan directory for '{job_id}' would escape '{jobs_dir}'")
    return directory


def _execution_plan_path(job_id: str, plan_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(plan_id):
        raise ExecutionPlanError(f"plan_id '{plan_id}' is invalid; use letters, digits, '_' or '-'")
    path = _execution_plan_dir(job_id, jobs_dir) / f"{plan_id}.json"
    if not _within_dir(path, Path(jobs_dir)):
        raise JobPathError(f"execution plan path for '{job_id}/{plan_id}' would escape '{jobs_dir}'")
    return path


def _execution_plan_checklist_path(job_id: str, plan_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(plan_id):
        raise ExecutionPlanError(f"plan_id '{plan_id}' is invalid; use letters, digits, '_' or '-'")
    path = _execution_plan_dir(job_id, jobs_dir) / f"{plan_id}.txt"
    if not _within_dir(path, Path(jobs_dir)):
        raise JobPathError(f"execution plan checklist path for '{job_id}/{plan_id}' would escape '{jobs_dir}'")
    return path


def _read_execution_plan(job_id: str, plan_id: str, jobs_dir: Path) -> dict:
    path = _execution_plan_path(job_id, plan_id, jobs_dir)
    if not path.exists():
        raise ExecutionPlanError(f"execution plan '{plan_id}' not found for job '{job_id}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ExecutionPlanError(f"execution plan '{plan_id}' root must be an object")
    return data


def _job_plan_ids(job: dict, jobs_dir: Path) -> list[str]:
    values = job.get("execution_plans")
    if isinstance(values, list):
        return [v for v in values if isinstance(v, str)]
    directory = _execution_plan_dir(job.get("job_id", ""), jobs_dir)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def _repository_dirty_flag() -> bool | None:
    # Dirty state is intentionally not probed through git subprocesses here.
    # Operators can record the exact dirty flag on manual run records.
    return None


def _validate_plan_text(value: str, path: str) -> None:
    if _SECRET_VALUE_RE.search(value) or _URL_CREDENTIAL_RE.match(value):
        raise ExecutionPlanError(f"{path}: secret-like values and credential URLs are not allowed")
    if _BASE64_MEDIA_RE.fullmatch(value.strip()):
        raise ExecutionPlanError(f"{path}: embedded base64 media or binary data is not allowed")


def _require_plan_safe_args(entry_point: str, args: list[str], *, path: str = "arguments") -> list[str]:
    try:
        return _validate_command_args(entry_point, args)
    except PipelineRunError as exc:
        raise ExecutionPlanError(f"{path}: {exc}") from exc


def _entry_point_script(entry_point: str) -> str:
    script = PIPELINE_ENTRY_POINTS.get(entry_point)
    if not script:
        raise ExecutionPlanError(f"entry_point '{entry_point}' is not recognized")
    if not (ROOT / script).is_file():
        raise ExecutionPlanError(f"entry_point '{entry_point}' references missing repository script {script}")
    return script


def _plan_args(entry_point: str, script: str, *, job: dict, intake: dict, workflow: str,
               recording_manifest: str | None) -> list[str]:
    media = intake.get("media") if isinstance(intake.get("media"), dict) else {}
    config = intake.get("configuration") if isinstance(intake.get("configuration"), dict) else {}
    source_path = str(media.get("local_file_path", ""))
    match_name = str(media.get("match_or_event_name") or job.get("source_id") or job.get("job_id"))
    project = str(config.get("project") or job.get("project_id") or "football")
    exports = config.get("export_profiles") if isinstance(config.get("export_profiles"), list) else []
    first_export = str(exports[0]) if exports else "vertical_clean"
    recording_ref = recording_manifest or "data/manifests/REPLACE_WITH_RECORDING_MANIFEST.json"
    clip_manifest = f"data/clip_manifests/{job['job_id']}.csv"
    transcript = f"data/transcripts/{job['job_id']}.json"
    research = f"data/research/{job['job_id']}_research.json"
    output_manifest = f"data/pilot/output_manifests/{job['job_id']}.json"
    mapping = {
        "process-match": [script, "--input", source_path, "--league", "WORLD_CUP", "--match-name", match_name],
        "process-from-manifest": [script, "--manifest", recording_ref],
        "transcribe-match": [script, source_path, "--match-id", job["job_id"]],
        "export-research-windows": [script, "--research", research, "--source", source_path],
        "generate-claude-prompt": [script, "--transcript", transcript, "--match-id", job["job_id"]],
        "run-gpt-detection": [script, "--prompt", f"prompts/generated/{job['job_id']}.txt", "--output", clip_manifest],
        "build-clip-manifest": [script, "--detections", f"data/detections/{job['job_id']}.json", "--output", clip_manifest],
        "generate-asset-prompts": [script, "--clip-manifest", clip_manifest, "--output-dir", f"data/asset_prompts/{job['job_id']}"],
        "export-clips-ffmpeg": [script, "--clip-manifest", clip_manifest, "--profile", first_export],
        "build-stadium-dashboard": [script, "--clip-manifest", clip_manifest, "--output", f"data/review/{job['job_id']}.html"],
        "pilot-output-register": [script, "outputs", "register", job["job_id"], output_manifest, "--operator", "REPLACE_WITH_OPERATOR"],
    }
    if workflow == "recording-manifest" and entry_point == "process-match":
        return [script, "--input", f"RAW/WORLD_CUP/{job['job_id']}.ts", "--league", "WORLD_CUP", "--match-name", match_name]
    return mapping[entry_point]


def _build_plan_stages(job: dict, intake: dict, *, workflow: str, recording_manifest: str | None) -> list[dict]:
    stages: list[dict] = []
    config_refs = [
        "config/pipeline_config.json",
        "config/brands/world_cup.json",
        "config/editorial/world_cup.json",
        "config/export/world_cup.json",
    ]
    for sequence, stage_id in enumerate(PIPELINE_STAGES, start=1):
        entry_point = _PLAN_STAGE_ENTRY_POINTS[stage_id]
        script = _entry_point_script(entry_point)
        enabled = True
        skip_reason = None
        if stage_id == "CONCATENATION" and workflow == "local-match-file":
            enabled = False
            skip_reason = "Local match-file workflow uses a single validated source file; no recording concat is planned."
        args = _require_plan_safe_args(entry_point, _plan_args(entry_point, script, job=job, intake=intake,
                                                               workflow=workflow, recording_manifest=recording_manifest),
                                      path=f"stages[{sequence}].arguments")
        stages.append({
            "sequence": sequence,
            "stage_id": stage_id,
            "classification": _PLAN_STAGE_CLASSIFICATIONS[stage_id],
            "enabled": enabled,
            "skip_reason": skip_reason,
            "entry_point": entry_point,
            "script_path": script,
            "arguments": args,
            "working_directory": str(ROOT),
            "inputs": ["validated pilot intake", "validated source media"] + (["recording manifest"] if stage_id == "CONCATENATION" else []),
            "expected_outputs": [f"operator-recorded evidence for {stage_id}"],
            "configuration_references": config_refs,
            "required_tools": ["python3"] + (["ffmpeg"] if stage_id in {"CLIP_EXPORT", "CONCATENATION"} else []),
            "required_environment_variables": list(_PLAN_ENVIRONMENT_NAMES),
            "completion_evidence": [f"Manual run record stage update for {stage_id}"],
            "command_preview": shlex.join(args),
        })
    return stages


def _plan_provenance(job: dict, intake: dict, *, intake_root: str | None, recording_manifest: str | None) -> dict:
    config = intake.get("configuration") if isinstance(intake.get("configuration"), dict) else {}
    media = intake.get("media") if isinstance(intake.get("media"), dict) else {}
    project = str(config.get("project") or job.get("project_id") or "football")
    brand = str(config.get("brand") or "world_cup")
    editorial = str(config.get("editorial_taxonomy") or "world_cup")
    return {
        "pilot_intake_manifest": _safe_file_reference(job.get("intake_manifest_path", ""), field_path="plan.provenance.pilot_intake_manifest", require_exists=True),
        "source_media": _safe_file_reference(media.get("local_file_path"), field_path="plan.provenance.source_media", require_exists=True),
        "recording_manifest": _optional_file_reference(recording_manifest, field_path="plan.provenance.recording_manifest"),
        "project_configuration": _provenance_file(ROOT / "config" / "pipeline_config.json", label="project_configuration"),
        "brand_profile": _provenance_file(ROOT / "config" / "brands" / f"{brand}.json", label="brand_profile"),
        "editorial_taxonomy": _provenance_file(ROOT / "config" / "editorial" / f"{editorial}.json", label="editorial_taxonomy"),
        "export_profiles": _provenance_file(ROOT / "config" / "export" / "world_cup.json", label="export_profiles"),
        "operational_categories": {"project": project, "categories": resolve_operational_categories(project)},
        "intake_root": intake_root,
    }


def _execution_plan_checklist(plan: dict) -> str:
    lines = [
        f"Execution Plan Checklist: {plan['plan_id']}",
        f"Job: {plan['job_id']}",
        f"Workflow: {plan['workflow']}",
        f"Status: {plan['status']} revision={plan['revision']}",
        "",
        "Guardrails:",
        "- This plan does not execute commands.",
        "- This plan does not process media, call models/APIs, access network services, copy/move/delete/upload/publish files, or deliver outputs.",
        "- Operators must run commands manually and record execution through pipeline-run records.",
        "",
        "Stages:",
    ]
    for stage in plan.get("stages", []):
        status = "enabled" if stage.get("enabled") else f"disabled ({stage.get('skip_reason')})"
        lines.append(f"{stage['sequence']}. {stage['stage_id']} [{stage['classification']}] {status}")
        lines.append(f"   entry_point: {stage['entry_point']}")
        lines.append(f"   argv: {json.dumps(stage['arguments'])}")
        lines.append(f"   preview: {stage['command_preview']}")
        lines.append(f"   evidence: {', '.join(stage.get('completion_evidence', []))}")
    lines.extend(["", "Required environment variable names only:", ", ".join(plan.get("required_environment_variables", [])), ""])
    return "\n".join(lines)


def _write_plan_job_and_events(plan_path: Path, checklist_path: Path, plan: dict,
                               record_path: Path, job: dict, events_path: Path, events: list[dict]) -> None:
    _atomic_write_json(plan_path, plan)
    _atomic_write_text(checklist_path, _execution_plan_checklist(plan))
    _atomic_write_json(events_path, events)
    _atomic_write_json(record_path, job)


def validate_execution_plan(data: object, *, job: dict | None = None, jobs_dir: str | Path | None = None) -> dict:
    issues: list[dict] = []
    if not isinstance(data, dict):
        issues.append(_plan_issue("plan", "BAD_TYPE", "root must be an object"))
        return {"valid": False, "issues": issues, "validation_codes": ["BAD_TYPE"]}
    _output_secret_scan(data, "plan", issues)
    _reject_output_unknown(data, _EXECUTION_PLAN_KEYS, "plan", issues)
    if data.get("schema_version") != EXECUTION_PLAN_SCHEMA_VERSION:
        issues.append(_plan_issue("plan.schema_version", "BAD_SCHEMA_VERSION", f"expected {EXECUTION_PLAN_SCHEMA_VERSION}"))
    for key in ("plan_id", "job_id", "pilot_id", "project_id", "source_id", "created_at", "created_by", "workflow", "working_directory", "python_executable"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issues.append(_plan_issue(f"plan.{key}", "MISSING_KEY", "expected a non-empty string"))
    if not _is_valid_id(data.get("plan_id")):
        issues.append(_plan_issue("plan.plan_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
    if data.get("workflow") not in EXECUTION_PLAN_WORKFLOWS:
        issues.append(_plan_issue("plan.workflow", "UNSUPPORTED_WORKFLOW", f"expected one of {', '.join(sorted(EXECUTION_PLAN_WORKFLOWS))}"))
    if data.get("status") not in EXECUTION_PLAN_STATUSES:
        issues.append(_plan_issue("plan.status", "UNKNOWN_PLAN_STATUS", f"expected one of {', '.join(sorted(EXECUTION_PLAN_STATUSES))}"))
    elif data.get("status") != "READY":
        issues.append(_plan_issue("plan.status", "PLAN_NOT_READY", "only READY plans can be used for runs"))
    for key in ("revision", "job_revision_snapshot", "job_revision_after_generation"):
        if isinstance(data.get(key), bool) or not isinstance(data.get(key), int) or data.get(key) < 0:
            issues.append(_plan_issue(f"plan.{key}", "BAD_TYPE", "expected a non-negative integer"))
    if job is not None:
        if data.get("job_id") != job.get("job_id"):
            issues.append(_plan_issue("plan.job_id", "JOB_MISMATCH", f"does not match target job '{job.get('job_id')}'"))
        current_revision = _normalized_revision(job)
        if data.get("job_revision_after_generation") != current_revision:
            issues.append(_plan_issue("plan.job_revision_after_generation", "STALE_JOB_REVISION",
                                      f"plan expects current job revision {data.get('job_revision_after_generation')}, current revision {current_revision}"))
    env_names = data.get("required_environment_variables")
    if not isinstance(env_names, list) or not all(isinstance(v, str) and v and "=" not in v for v in env_names):
        issues.append(_plan_issue("plan.required_environment_variables", "BAD_TYPE", "expected environment variable names only"))
    stages = data.get("stages")
    if not isinstance(stages, list) or len(stages) != len(PIPELINE_STAGES):
        issues.append(_plan_issue("plan.stages", "BAD_STAGE_LIST", "expected the complete ordered pipeline stage list"))
    else:
        seen_sequences: set[int] = set()
        seen_stages: set[str] = set()
        for index, stage in enumerate(stages):
            path = f"plan.stages[{index}]"
            if not isinstance(stage, dict):
                issues.append(_plan_issue(path, "BAD_TYPE", "expected an object"))
                continue
            _reject_output_unknown(stage, _PLAN_STAGE_KEYS, path, issues)
            sequence = stage.get("sequence")
            if sequence != index + 1 or sequence in seen_sequences:
                issues.append(_plan_issue(f"{path}.sequence", "BAD_SEQUENCE", "stage sequence must be unique and ordered from 1"))
            if isinstance(sequence, int):
                seen_sequences.add(sequence)
            stage_id = stage.get("stage_id")
            if stage_id != PIPELINE_STAGES[index] or stage_id in seen_stages:
                issues.append(_plan_issue(f"{path}.stage_id", "UNKNOWN_STAGE", "stage must match the supported ordered stage model"))
            if isinstance(stage_id, str):
                seen_stages.add(stage_id)
            if stage.get("classification") not in {"required", "optional"}:
                issues.append(_plan_issue(f"{path}.classification", "BAD_TYPE", "expected required or optional"))
            if not isinstance(stage.get("enabled"), bool):
                issues.append(_plan_issue(f"{path}.enabled", "BAD_TYPE", "expected true/false"))
            if stage.get("enabled") is False and not stage.get("skip_reason"):
                issues.append(_plan_issue(f"{path}.skip_reason", "MISSING_KEY", "disabled stages require a skip reason"))
            entry_point = stage.get("entry_point")
            if entry_point not in PIPELINE_ENTRY_POINTS:
                issues.append(_plan_issue(f"{path}.entry_point", "UNKNOWN_ENTRY_POINT", "entry point is not recognized"))
            else:
                script = PIPELINE_ENTRY_POINTS[entry_point]
                if not (ROOT / script).is_file():
                    issues.append(_plan_issue(f"{path}.entry_point", "ENTRY_POINT_MISSING", f"script not found: {script}"))
                if stage.get("script_path") != script:
                    issues.append(_plan_issue(f"{path}.script_path", "ENTRY_POINT_MISMATCH", f"expected {script}"))
                try:
                    _require_plan_safe_args(entry_point, stage.get("arguments"), path=f"{path}.arguments")
                except ExecutionPlanError as exc:
                    issues.append(_plan_issue(f"{path}.arguments", "UNSAFE_ARGUMENT", str(exc)))
            for key in ("inputs", "expected_outputs", "configuration_references", "required_tools", "required_environment_variables", "completion_evidence"):
                if not isinstance(stage.get(key), list) or not all(isinstance(v, str) and v.strip() for v in stage.get(key, [])):
                    issues.append(_plan_issue(f"{path}.{key}", "BAD_TYPE", "expected a list of strings"))
            for key in ("working_directory", "command_preview"):
                if not isinstance(stage.get(key), str) or not stage.get(key, "").strip():
                    issues.append(_plan_issue(f"{path}.{key}", "MISSING_KEY", "expected a non-empty string"))
    manual_run = data.get("manual_run") if isinstance(data.get("manual_run"), dict) else {}
    if manual_run.get("entry_point") not in PIPELINE_ENTRY_POINTS:
        issues.append(_plan_issue("plan.manual_run.entry_point", "UNKNOWN_ENTRY_POINT", "manual run entry point is not recognized"))
    else:
        try:
            _require_plan_safe_args(manual_run["entry_point"], manual_run.get("arguments"), path="plan.manual_run.arguments")
        except ExecutionPlanError as exc:
            issues.append(_plan_issue("plan.manual_run.arguments", "UNSAFE_ARGUMENT", str(exc)))
    return {"valid": not issues, "issues": issues, "validation_codes": [issue["code"] for issue in issues] or [C_INTAKE_OK]}


def generate_execution_plan(job_id: str, *, plan_id: str, operator: str, expected_job_revision: int,
                            workflow: str = "local-match-file", recording_manifest: str | None = None,
                            jobs_dir: str | Path | None = None, intake_root: str | None = None,
                            source: str = "pilot_job.plans.generate") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    issues: list[dict] = []
    if not _is_valid_id(plan_id):
        issues.append(_plan_issue("plan.plan_id", "BAD_ID", "plan ID must use letters, digits, '_' or '-'"))
    if not operator or not operator.strip():
        issues.append(_plan_issue("plan.operator", "MISSING_OPERATOR", "operator is required"))
    if workflow not in EXECUTION_PLAN_WORKFLOWS:
        issues.append(_plan_issue("plan.workflow", "unsupported_workflow", f"supported workflows: {', '.join(sorted(EXECUTION_PLAN_WORKFLOWS))}"))
    current = job.get("current_state", "")
    revision = _normalized_revision(job)
    if expected_job_revision != revision:
        raise JobRevisionError(
            f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {revision}; no execution plan generated"
        )
    if current != "READY":
        issues.append(_plan_issue("job.current_state", "job_not_ready", f"job '{job_id}' is {current}; execution plans require READY"))
    if job.get("project_id") not in PRODUCTION_PROJECTS:
        issues.append(_plan_issue("job.project_id", "unsupported_project", "production execution plans are limited to the football project"))
    plan_path = _execution_plan_path(job_id, plan_id, jobs_dir_path) if _is_valid_id(plan_id) else None
    checklist_path = _execution_plan_checklist_path(job_id, plan_id, jobs_dir_path) if _is_valid_id(plan_id) else None
    if plan_path and (plan_path.exists() or (checklist_path and checklist_path.exists())):
        issues.append(_plan_issue("plan.plan_id", "duplicate_plan_id", f"plan '{plan_id}' already exists for job '{job_id}'"))
    try:
        readiness = pilot_readiness_report(job_id, jobs_dir=jobs_dir_path, intake_root=intake_root)["jobs"][0]
        if readiness.get("state") != "READY":
            issues.append(_plan_issue("readiness.state", "job_not_ready", "readiness report does not show READY"))
        for blocker in readiness.get("blockers", []):
            issues.append(_plan_issue("readiness.blockers", blocker, "readiness blocker prevents execution-plan generation"))
        intake_status = readiness.get("intake", {}) if isinstance(readiness.get("intake"), dict) else {}
        if not intake_status.get("rights_cleared"):
            issues.append(_plan_issue("readiness.intake.rights_cleared", "rights_not_cleared", "rights are not cleared"))
        if not intake_status.get("source_ready"):
            issues.append(_plan_issue("readiness.intake.source_ready", "source_not_ready", "source is not ready"))
        if not intake_status.get("config_references_valid"):
            issues.append(_plan_issue("readiness.intake.config_references_valid", "configuration_invalid", "configuration references are invalid"))
    except JobRecordError as exc:
        readiness = {"blockers": ["intake_unavailable"], "error": str(exc)}
        issues.append(_plan_issue("readiness", "intake_unavailable", str(exc)))
    try:
        intake = _load_stored_intake(job)
        intake_report = validate_intake(intake, intake_root=intake_root, check_source=True, check_rights=True)
        if not intake_report["execution_ready"]:
            for code in intake_report["validation_codes"]:
                issues.append(_plan_issue("intake.validation", code.lower(), "stored intake is not execution-ready"))
        if workflow == "recording-manifest" and recording_manifest:
            _safe_file_reference(recording_manifest, field_path="plan.recording_manifest", require_exists=True)
    except (JobRecordError, PipelineRunError) as exc:
        intake = {}
        issues.append(_plan_issue("intake", "intake_unavailable", str(exc)))
    for entry_point, script in PIPELINE_ENTRY_POINTS.items():
        if entry_point in set(_PLAN_STAGE_ENTRY_POINTS.values()) and not (ROOT / script).is_file():
            issues.append(_plan_issue("entry_points", "entry_point_missing", f"{entry_point} -> {script}"))
    if issues:
        raise ExecutionPlanError(f"execution plan '{plan_id}' for job '{job_id}' is blocked", issues)

    stages = _build_plan_stages(job, intake, workflow=workflow, recording_manifest=recording_manifest)
    primary = next(stage for stage in stages if stage["enabled"])
    now = _now_iso()
    sequence = _next_event_sequence(events)
    event = {"event_schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(job_id, sequence, "EXECUTION_PLAN_GENERATED"),
             "job_id": job_id, "sequence": sequence, "timestamp": now, "event_type": "EXECUTION_PLAN_GENERATED",
             "previous_state": current, "new_state": current, "operator": operator.strip(),
             "message": f"Generated execution plan {plan_id}", "metadata": {"plan_id": plan_id, "workflow": workflow},
             "source": source, "related_codes": [C_INTAKE_OK], "artifact_references": [str(plan_path), str(checklist_path)]}
    new_events = [*events, event]
    updated_job = dict(job)
    plan_ids = _job_plan_ids(job, jobs_dir_path)
    plan_ids.append(plan_id)
    updated_job.update({"revision": revision + 1, "updated_at": now, "event_count": len(new_events),
                        "execution_plans": sorted(set(plan_ids)), "active_execution_plan_id": plan_id,
                        "latest_event": {"event_id": event["event_id"], "timestamp": now, "event_type": event["event_type"],
                                         "previous_state": current, "new_state": current, "message": event["message"]}})
    plan = {"schema_version": EXECUTION_PLAN_SCHEMA_VERSION, "plan_id": plan_id, "job_id": job_id,
            "pilot_id": job.get("pilot_id", ""), "project_id": job.get("project_id", ""), "source_id": job.get("source_id", ""),
            "created_at": now, "created_by": operator.strip(), "updated_at": now, "revision": 0,
            "job_revision_snapshot": revision, "job_revision_after_generation": updated_job["revision"], "status": "READY",
            "workflow": workflow, "repository": {"commit": _current_git_commit(), "dirty": _repository_dirty_flag(),
                                                   "dirty_state": "not_checked_no_subprocess"},
            "working_directory": str(ROOT), "python_executable": sys.executable or "python3",
            "readiness_snapshot": readiness, "provenance": _plan_provenance(job, intake, intake_root=intake_root,
                                                                              recording_manifest=recording_manifest),
            "required_tools": sorted({tool for stage in stages for tool in stage.get("required_tools", [])}),
            "required_environment_variables": list(_PLAN_ENVIRONMENT_NAMES), "stages": stages,
            "manual_run": {"entry_point": primary["entry_point"], "arguments": primary["arguments"]},
            "expected_inputs": ["validated pilot intake", "validated source media"],
            "expected_outputs": ["manual run record", "stage updates", "operator-reviewed output manifest"],
            "completion_evidence": ["pipeline run record linked by plan_id", "manual stage evidence", "registered output manifest"],
            "command_previews": [stage["command_preview"] for stage in stages],
            "supersedes_plan_id": None, "invalidated_at": None, "invalidated_by": None, "invalidation_reason": None}
    report = validate_execution_plan(plan, job=updated_job, jobs_dir=jobs_dir_path)
    if not report["valid"]:
        raise ExecutionPlanError("execution plan validation failed", report["issues"])
    assert plan_path is not None and checklist_path is not None
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    _write_plan_job_and_events(plan_path, checklist_path, plan, record_path, updated_job, events_path, new_events)
    return {"job": updated_job, "plan": plan, "plan_path": str(plan_path), "checklist_path": str(checklist_path)}


def list_execution_plans(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, _events = _load_job_and_events(job_id, jobs_dir_path)
    rows = []
    for plan_id in _job_plan_ids(job, jobs_dir_path):
        try:
            plan = _read_execution_plan(job_id, plan_id, jobs_dir_path)
        except ExecutionPlanError:
            continue
        rows.append({"plan_id": plan_id, "status": plan.get("status", ""), "revision": plan.get("revision", 0),
                     "workflow": plan.get("workflow", ""), "created_at": plan.get("created_at", "")})
    return rows


def show_execution_plan(job_id: str, plan_id: str, jobs_dir: str | Path | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    return _read_execution_plan(job_id, plan_id, jobs_dir_path)


def read_execution_plan_checklist(job_id: str, plan_id: str, jobs_dir: str | Path | None = None) -> str:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    path = _execution_plan_checklist_path(job_id, plan_id, jobs_dir_path)
    if not path.exists():
        raise ExecutionPlanError(f"execution plan checklist for '{plan_id}' not found for job '{job_id}'")
    return path.read_text(encoding="utf-8")


def invalidate_execution_plan(job_id: str, plan_id: str, *, operator: str, reason: str,
                              expected_job_revision: int, expected_plan_revision: int,
                              jobs_dir: str | Path | None = None,
                              source: str = "pilot_job.plans.invalidate") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    job_revision = _normalized_revision(job)
    if expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no execution plan invalidated")
    if not operator or not operator.strip() or not reason or not reason.strip():
        raise ExecutionPlanError("operator and reason are required to invalidate an execution plan")
    _validate_metadata({"operator": operator, "reason": reason})
    plan_path = _execution_plan_path(job_id, plan_id, jobs_dir_path)
    plan = _read_execution_plan(job_id, plan_id, jobs_dir_path)
    plan_revision = plan.get("revision", 0) if isinstance(plan.get("revision"), int) else 0
    if expected_plan_revision != plan_revision:
        raise JobRevisionError(f"execution plan '{plan_id}' stale revision: expected {expected_plan_revision}, current revision {plan_revision}; no execution plan invalidated")
    if plan.get("status") == "INVALIDATED":
        raise ExecutionPlanError(f"execution plan '{plan_id}' is already INVALIDATED for job '{job_id}'")
    now = _now_iso()
    updated_plan = dict(plan)
    updated_plan.update({"status": "INVALIDATED", "revision": plan_revision + 1, "updated_at": now,
                         "invalidated_at": now, "invalidated_by": operator.strip(), "invalidation_reason": reason.strip()})
    sequence = _next_event_sequence(events)
    current = job.get("current_state", "")
    event = {"event_schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(job_id, sequence, "EXECUTION_PLAN_INVALIDATED"),
             "job_id": job_id, "sequence": sequence, "timestamp": now, "event_type": "EXECUTION_PLAN_INVALIDATED",
             "previous_state": current, "new_state": current, "operator": operator.strip(), "message": reason.strip(),
             "metadata": {"plan_id": plan_id, "plan_revision": updated_plan["revision"]}, "source": source,
             "related_codes": [], "artifact_references": [str(plan_path)]}
    new_events = [*events, event]
    updated_job = dict(job)
    updated_job.update({"revision": job_revision + 1, "updated_at": now, "event_count": len(new_events),
                        "active_execution_plan_id": None if job.get("active_execution_plan_id") == plan_id else job.get("active_execution_plan_id"),
                        "latest_event": {"event_id": event["event_id"], "timestamp": now, "event_type": event["event_type"],
                                         "previous_state": current, "new_state": current, "message": event["message"]}})
    _atomic_write_json(plan_path, updated_plan)
    _atomic_write_text(_execution_plan_checklist_path(job_id, plan_id, jobs_dir_path), _execution_plan_checklist(updated_plan))
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return {"job": updated_job, "plan": updated_plan, "plan_path": str(plan_path)}


def _require_run_plan_link(job: dict, *, plan_id: str | None, entry_point: str, command_args: list[str], jobs_dir: Path) -> dict | None:
    if plan_id is None or plan_id == "":
        return None
    if not _is_valid_id(plan_id):
        raise PipelineRunError(f"plan_id '{plan_id}' is invalid; use letters, digits, '_' or '-'")
    plan = _read_execution_plan(job["job_id"], plan_id, jobs_dir)
    if plan.get("job_id") != job.get("job_id"):
        raise PipelineRunError(f"execution plan '{plan_id}' does not belong to job '{job.get('job_id')}'")
    report = validate_execution_plan(plan, job=job, jobs_dir=jobs_dir)
    if not report["valid"]:
        raise PipelineRunError(f"execution plan '{plan_id}' is not valid for job '{job.get('job_id')}'", report["issues"])
    manual_run = plan.get("manual_run") if isinstance(plan.get("manual_run"), dict) else {}
    expected_args = manual_run.get("arguments") if isinstance(manual_run.get("arguments"), list) else []
    if entry_point != manual_run.get("entry_point") or command_args != expected_args:
        raise PipelineRunError(f"pipeline run arguments do not match execution plan '{plan_id}'")
    return plan


# ── Manual pipeline-run records ──────────────────────────────────────────────

_PIPELINE_RUN_KEYS = (
    "schema_version", "run_id", "job_id", "pilot_id", "project_id", "source_id",
    "created_at", "created_by", "started_at", "completed_at", "status",
    "revision", "job_revision_at_creation", "plan_id", "plan_revision", "plan_workflow",
    "planned_stage_ids", "manual_deviations", "entry_point", "command",
    "command_args", "working_directory", "python_executable", "operator_notes",
    "dry_run", "manual_execution_confirmed", "host_platform",
    "repository_commit", "repository_dirty", "start_reason", "provenance",
    "stages", "completion_summary", "failure_category", "failure_summary",
    "partial_success_explanation",
)


def _pipeline_run_dir(job_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(job_id):
        raise JobPathError(f"invalid job identifier '{job_id}'")
    directory = Path(jobs_dir) / f"{job_id}.runs"
    if not _within_dir(directory, Path(jobs_dir)):
        raise JobPathError(f"pipeline run directory for '{job_id}' would escape '{jobs_dir}'")
    return directory


def _pipeline_run_path(job_id: str, run_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(run_id):
        raise PipelineRunError(f"run_id '{run_id}' is invalid; use letters, digits, '_' or '-'")
    path = _pipeline_run_dir(job_id, jobs_dir) / f"{run_id}.json"
    if not _within_dir(path, Path(jobs_dir)):
        raise JobPathError(f"pipeline run path for '{job_id}/{run_id}' would escape '{jobs_dir}'")
    return path


def _read_pipeline_run(job_id: str, run_id: str, jobs_dir: Path) -> dict:
    path = _pipeline_run_path(job_id, run_id, jobs_dir)
    if not path.exists():
        raise PipelineRunError(f"pipeline run '{run_id}' not found for job '{job_id}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise PipelineRunError(f"pipeline run '{run_id}' root must be an object")
    return data


def _normalized_run_revision(run: dict) -> int:
    revision = run.get("revision", 0)
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        return 0
    return revision


def _job_run_ids(job: dict, jobs_dir: Path) -> list[str]:
    values = job.get("pipeline_runs")
    if isinstance(values, list):
        return [v for v in values if isinstance(v, str)]
    directory = _pipeline_run_dir(job.get("job_id", ""), jobs_dir)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def _current_git_commit() -> str | None:
    head = ROOT / ".git" / "HEAD"
    if not head.exists():
        return None
    value = head.read_text(encoding="utf-8").strip()
    if value.startswith("ref:"):
        ref = ROOT / ".git" / value.split(" ", 1)[1]
        return ref.read_text(encoding="utf-8").strip() if ref.exists() else None
    return value or None


def _validate_command_args(entry_point: str, command_args: object) -> list[str]:
    if entry_point not in PIPELINE_ENTRY_POINTS:
        raise PipelineRunError(
            f"entry_point '{entry_point}' is not recognized; allowed entry points: {', '.join(sorted(PIPELINE_ENTRY_POINTS))}"
        )
    if not isinstance(command_args, list) or not command_args:
        raise PipelineRunError("command_args: expected a non-empty list")
    clean: list[str] = []
    unsafe_tokens = (";", "&&", "||", "|", ">", "<", "`", "$(", "${", "\n", "\r")
    for index, arg in enumerate(command_args):
        path = f"command_args[{index}]"
        if not isinstance(arg, str) or not arg.strip():
            raise PipelineRunError(f"{path}: expected a non-empty string")
        value = arg.strip()
        _validate_text_for_secrets(value, path)
        if _URL_RE.match(value):
            raise PipelineRunError(f"{path}: URLs are not accepted in run commands")
        if any(token in value for token in unsafe_tokens):
            raise PipelineRunError(f"{path}: shell substitutions, separators, pipes, or redirections are not allowed")
        lowered = value.lower()
        if any(marker in lowered for marker in ("api_key=", "token=", "password=", "cookie=", "authorization=")):
            raise PipelineRunError(f"{path}: command arguments must not contain environment dumps or credentials")
        clean.append(value)
    first = clean[0]
    script_arg = clean[1] if Path(first).name in {"python", "python3", "python.exe"} or first == sys.executable else first
    if Path(script_arg).as_posix() != PIPELINE_ENTRY_POINTS[entry_point]:
        raise PipelineRunError(f"command_args: entry point '{entry_point}' must reference {PIPELINE_ENTRY_POINTS[entry_point]}")
    return clean


def _initial_stages() -> list[dict]:
    return [{"stage_id": stage, "status": "NOT_STARTED", "started_at": None, "completed_at": None,
             "command_ref": None, "function_ref": None, "input_references": [], "output_references": [],
             "log_reference": None, "error_category": None, "error_summary": None, "warnings": [],
             "metrics": {}, "operator": None, "notes": None} for stage in PIPELINE_STAGES]


def _stage_counts(run: dict) -> dict[str, int]:
    counts = {status: 0 for status in sorted(PIPELINE_STAGE_STATUSES)}
    for stage in run.get("stages", []):
        if isinstance(stage, dict) and stage.get("status") in counts:
            counts[stage["status"]] += 1
    return counts


def _safe_file_reference(raw_path: object, *, field_path: str, require_exists: bool = False,
                         checksum: bool = True) -> dict:
    if not isinstance(raw_path, str) or not raw_path.strip():
        raise PipelineRunError(f"{field_path}: expected a non-empty path")
    value = raw_path.strip()
    _validate_text_for_secrets(value, field_path)
    if _URL_RE.match(value):
        raise PipelineRunError(f"{field_path}: URLs are not accepted in run records")
    if ".." in Path(value).parts:
        raise PipelineRunError(f"{field_path}: path traversal is not allowed")
    path = Path(value)
    resolved = path.resolve() if path.is_absolute() else (ROOT / path).resolve()
    exists = resolved.exists()
    if require_exists and not exists:
        raise PipelineRunError(f"{field_path}: path not found: {resolved}")
    ref = {"path": value, "resolved_path": str(resolved), "exists": exists,
           "validation_succeeded": exists if require_exists else True}
    if exists:
        stat = resolved.stat()
        ref["file_size"] = stat.st_size if resolved.is_file() else None
        ref["modified_at"] = _dt.datetime.fromtimestamp(stat.st_mtime, tz=_dt.timezone.utc).isoformat()
        if checksum and resolved.is_file():
            ref["sha256"] = _sha256(resolved)
    return ref


def _optional_file_reference(raw_path: object, *, field_path: str) -> dict | None:
    if raw_path is None or raw_path == "":
        return None
    return _safe_file_reference(raw_path, field_path=field_path, require_exists=False)


def _provenance_file(path: Path, *, label: str) -> dict:
    return {"label": label, **_safe_file_reference(str(path), field_path=f"provenance.{label}", require_exists=False)}


def _build_run_provenance(job: dict, *, intake_root: str | None = None, recording_manifest: str | None = None,
                          research_file: str | None = None, match_id: str | None = None,
                          schedule_row: str | None = None, models: dict | None = None) -> dict:
    intake = _load_stored_intake(job)
    report = validate_intake(intake, intake_root=intake_root, check_source=True, check_rights=True)
    config = intake.get("configuration") if isinstance(intake.get("configuration"), dict) else {}
    media = intake.get("media") if isinstance(intake.get("media"), dict) else {}
    project = config.get("project", job.get("project_id", "football"))
    brand = config.get("brand", "world_cup")
    editorial = config.get("editorial_taxonomy", "world_cup")
    template = config.get("detection_template", "prompt")
    export_profiles = config.get("export_profiles") if isinstance(config.get("export_profiles"), list) else []
    return {
        "pilot_intake_manifest": _safe_file_reference(job.get("intake_manifest_path", ""), field_path="provenance.pilot_intake_manifest", require_exists=True),
        "source_media": _safe_file_reference(media.get("local_file_path"), field_path="provenance.source_media", require_exists=True),
        "recording_manifest": _optional_file_reference(recording_manifest, field_path="provenance.recording_manifest"),
        "project_configuration": _provenance_file(ROOT / "config" / "pipeline_config.json", label="project_configuration"),
        "brand_profile": _provenance_file(ROOT / "config" / "brands" / f"{brand}.json", label="brand_profile"),
        "editorial_taxonomy": _provenance_file(ROOT / "config" / "editorial" / f"{editorial}.json", label="editorial_taxonomy"),
        "operational_categories": {"project": project, "categories": resolve_operational_categories(project)},
        "detection_template": {"template_id": template, **_provenance_file(ROOT / "prompts" / "world_cup_detection_prompt.txt", label="detection_template")},
        "export_profiles": {"profile_ids": [str(v) for v in export_profiles], "config": _provenance_file(ROOT / "config" / "export" / "world_cup.json", label="export_profiles")},
        "research_file": _optional_file_reference(research_file, field_path="provenance.research_file"),
        "schedule": {"match_id": match_id, "row_reference": schedule_row},
        "repository": {"commit": _current_git_commit()},
        "models": models or {},
        "validation": {"source_ready": report["source_ready"], "rights_cleared": report["rights_cleared"],
                       "config_references_valid": report["config_references_valid"], "validation_codes": report["validation_codes"]},
    }


def validate_pipeline_run(data: object, *, job: dict | None = None) -> dict:
    issues: list[dict] = []
    if not isinstance(data, dict):
        issues.append(_output_issue("run", "BAD_TYPE", "root must be an object"))
        return {"valid": False, "issues": issues, "validation_codes": ["BAD_TYPE"]}
    _output_secret_scan(data, "run", issues)
    _reject_output_unknown(data, _PIPELINE_RUN_KEYS, "run", issues)
    if data.get("schema_version") != PIPELINE_RUN_SCHEMA_VERSION:
        issues.append(_output_issue("run.schema_version", "BAD_SCHEMA_VERSION", f"expected {PIPELINE_RUN_SCHEMA_VERSION}"))
    for key in ("run_id", "job_id", "pilot_id", "project_id", "source_id", "created_at", "created_by", "entry_point", "command", "working_directory", "python_executable"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issues.append(_output_issue(f"run.{key}", "MISSING_KEY", "expected a non-empty string"))
    if not _is_valid_id(data.get("run_id")):
        issues.append(_output_issue("run.run_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
    if data.get("entry_point") not in PIPELINE_ENTRY_POINTS:
        issues.append(_output_issue("run.entry_point", "UNKNOWN_ENTRY_POINT", "entry point is not recognized"))
    if data.get("status") not in PIPELINE_RUN_STATUSES:
        issues.append(_output_issue("run.status", "UNKNOWN_RUN_STATUS", f"expected one of {', '.join(sorted(PIPELINE_RUN_STATUSES))}"))
    for key in ("revision", "job_revision_at_creation"):
        if isinstance(data.get(key), bool) or not isinstance(data.get(key), int) or data.get(key) < 0:
            issues.append(_output_issue(f"run.{key}", "BAD_TYPE", "expected a non-negative integer"))
    if data.get("manual_execution_confirmed") is not True:
        issues.append(_output_issue("run.manual_execution_confirmed", "MANUAL_CONFIRMATION_REQUIRED", "must be true"))
    if not isinstance(data.get("dry_run"), bool):
        issues.append(_output_issue("run.dry_run", "BAD_TYPE", "expected true/false"))
    if job is not None and data.get("job_id") != job.get("job_id"):
        issues.append(_output_issue("run.job_id", "JOB_MISMATCH", f"does not match target job '{job.get('job_id')}'"))
    if data.get("plan_id") is not None:
        if not _is_valid_id(data.get("plan_id")):
            issues.append(_output_issue("run.plan_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
        if isinstance(data.get("plan_revision"), bool) or not isinstance(data.get("plan_revision"), int) or data.get("plan_revision") < 0:
            issues.append(_output_issue("run.plan_revision", "BAD_TYPE", "expected a non-negative integer"))
        if data.get("plan_workflow") not in EXECUTION_PLAN_WORKFLOWS:
            issues.append(_output_issue("run.plan_workflow", "UNSUPPORTED_WORKFLOW", "workflow is not supported"))
    if not isinstance(data.get("planned_stage_ids"), list):
        issues.append(_output_issue("run.planned_stage_ids", "BAD_TYPE", "expected a list"))
    if not isinstance(data.get("manual_deviations"), list):
        issues.append(_output_issue("run.manual_deviations", "BAD_TYPE", "expected a list"))
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        issues.append(_output_issue("run.stages", "MISSING_KEY", "expected non-empty stage list"))
    else:
        seen = set()
        for index, stage in enumerate(stages):
            path = f"run.stages[{index}]"
            if not isinstance(stage, dict):
                issues.append(_output_issue(path, "BAD_TYPE", "expected an object"))
                continue
            stage_id = stage.get("stage_id")
            if stage_id not in PIPELINE_STAGES:
                issues.append(_output_issue(f"{path}.stage_id", "UNKNOWN_STAGE", "stage is not supported"))
            elif stage_id in seen:
                issues.append(_output_issue(f"{path}.stage_id", "DUPLICATE_STAGE", "stage IDs must be unique"))
            else:
                seen.add(stage_id)
            if stage.get("status") not in PIPELINE_STAGE_STATUSES:
                issues.append(_output_issue(f"{path}.status", "UNKNOWN_STAGE_STATUS", f"expected one of {', '.join(sorted(PIPELINE_STAGE_STATUSES))}"))
            if stage.get("status") in {"SUCCEEDED", "FAILED"} and not stage.get("started_at"):
                issues.append(_output_issue(f"{path}.started_at", "MISSING_KEY", "completed or failed stages must have started_at"))
            if stage.get("status") == "FAILED" and (not stage.get("error_category") or not stage.get("error_summary")):
                issues.append(_output_issue(f"{path}.error_summary", "MISSING_KEY", "failed stages require error category and summary"))
    return {"valid": not issues, "issues": issues, "validation_codes": [issue["code"] for issue in issues] or [C_INTAKE_OK]}


def _append_run_event(job: dict, events: list[dict], event_type: str, *, message: str, operator: str,
                      run_id: str, metadata: dict | None = None, artifacts: list[str] | None = None,
                      source: str = "pilot_job.runs") -> dict:
    sequence = _next_event_sequence(events)
    return {"event_schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(job["job_id"], sequence, event_type),
            "job_id": job["job_id"], "sequence": sequence, "timestamp": _now_iso(), "event_type": event_type,
            "previous_state": job.get("current_state"), "new_state": job.get("current_state"), "operator": operator,
            "message": message, "metadata": {"run_id": run_id, **(metadata or {})}, "source": source,
            "related_codes": [], "artifact_references": artifacts or []}


def _write_run_and_job(run_path: Path, run: dict, record_path: Path, job: dict, events_path: Path, events: list[dict]) -> None:
    _atomic_write_json(run_path, run)
    _atomic_write_json(events_path, events)
    _atomic_write_json(record_path, job)


def create_pipeline_run(job_id: str, *, run_id: str, operator: str, entry_point: str, command_args: list[str],
                        manual_confirmed: bool, jobs_dir: str | Path | None = None,
                        expected_job_revision: int | None = None, working_directory: str | None = None,
                        python_executable: str | None = None, dry_run: bool = False,
                        operator_notes: str | None = None, host_platform: str | None = None,
                        repository_commit: str | None = None, repository_dirty: bool | None = None,
                        start_reason: str | None = None, recording_manifest: str | None = None,
                        research_file: str | None = None, match_id: str | None = None,
                        schedule_row: str | None = None, models: dict | None = None,
                        plan_id: str | None = None, manual_deviations: list[str] | None = None,
                        intake_root: str | None = None, source: str = "pilot_job.runs.create") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    current = job.get("current_state", "")
    if current not in PIPELINE_RUN_JOB_STATES:
        raise PipelineRunError(f"job '{job_id}' in state '{current}' cannot create a pipeline run; allowed states: {', '.join(sorted(PIPELINE_RUN_JOB_STATES))}")
    job_revision = _normalized_revision(job)
    if expected_job_revision is not None and expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no pipeline run created")
    if not _is_valid_id(run_id):
        raise PipelineRunError(f"run_id '{run_id}' is invalid; use letters, digits, '_' or '-'")
    if not operator or not operator.strip():
        raise PipelineRunError("operator is required for pipeline run creation")
    if manual_confirmed is not True:
        raise PipelineRunError("manual execution confirmation is required; no command will be executed by this record")
    command_clean = _validate_command_args(entry_point, command_args)
    plan = _require_run_plan_link(job, plan_id=plan_id, entry_point=entry_point, command_args=command_clean, jobs_dir=jobs_dir_path)
    _validate_metadata({"operator": operator, "operator_notes": operator_notes or "", "start_reason": start_reason or ""})
    deviations = _validate_artifacts(manual_deviations or [], field_path="manual_deviations")
    if repository_dirty is not None and not isinstance(repository_dirty, bool):
        raise PipelineRunError("repository_dirty: expected true/false")
    run_path = _pipeline_run_path(job_id, run_id, jobs_dir_path)
    if run_path.exists():
        raise PipelineRunError(f"pipeline run '{run_id}' already exists for job '{job_id}'")
    _require_intake_readiness(job, "RUNNING", intake_root=intake_root)
    provenance = _build_run_provenance(job, intake_root=intake_root, recording_manifest=recording_manifest,
                                       research_file=research_file, match_id=match_id, schedule_row=schedule_row,
                                       models=models)
    now = _now_iso()
    working = working_directory or str(ROOT)
    _validate_text_for_secrets(working, "working_directory")
    if ".." in Path(working).parts:
        raise PipelineRunError("working_directory: path traversal is not allowed")
    run = {"schema_version": PIPELINE_RUN_SCHEMA_VERSION, "run_id": run_id, "job_id": job_id,
           "pilot_id": job.get("pilot_id", ""), "project_id": job.get("project_id", ""), "source_id": job.get("source_id", ""),
            "created_at": now, "created_by": operator.strip(), "started_at": None, "completed_at": None,
            "status": "PLANNED", "revision": 0, "job_revision_at_creation": job_revision,
            "plan_id": plan.get("plan_id") if plan else None,
            "plan_revision": plan.get("revision") if plan else None,
            "plan_workflow": plan.get("workflow") if plan else None,
            "planned_stage_ids": [stage.get("stage_id") for stage in plan.get("stages", []) if isinstance(stage, dict)] if plan else [],
            "manual_deviations": deviations,
            "entry_point": entry_point, "command": shlex.join(command_clean), "command_args": command_clean,
           "working_directory": working, "python_executable": python_executable or sys.executable or "python3",
           "operator_notes": operator_notes, "dry_run": bool(dry_run), "manual_execution_confirmed": True,
           "host_platform": host_platform or f"{_platform.system()} {_platform.release()} {_platform.machine()}",
           "repository_commit": repository_commit or _current_git_commit(),
           "repository_dirty": repository_dirty, "start_reason": start_reason, "provenance": provenance,
           "stages": _initial_stages(), "completion_summary": None, "failure_category": None,
           "failure_summary": None, "partial_success_explanation": None}
    report = validate_pipeline_run(run, job=job)
    if not report["valid"]:
        raise PipelineRunError("pipeline run validation failed", report["issues"])
    event = _append_run_event(job, events, "PIPELINE_RUN_CREATED", message=f"Created pipeline run {run_id}",
                              operator=operator.strip(), run_id=run_id,
                              metadata={"plan_id": plan.get("plan_id")} if plan else None,
                              artifacts=[str(run_path)], source=source)
    new_events = [*events, event]
    run_ids = [v for v in job.get("pipeline_runs", []) if isinstance(v, str)] if isinstance(job.get("pipeline_runs"), list) else []
    run_ids.append(run_id)
    updated_job = dict(job)
    updated_job.update({"revision": job_revision + 1, "updated_at": now, "event_count": len(new_events),
                        "pipeline_runs": sorted(set(run_ids)), "active_pipeline_run_id": run_id,
                        "latest_event": {"event_id": event["event_id"], "timestamp": event["timestamp"],
                                         "event_type": event["event_type"], "previous_state": current,
                                         "new_state": current, "message": event["message"]}})
    run_path.parent.mkdir(parents=True, exist_ok=True)
    _write_run_and_job(run_path, run, record_path, updated_job, events_path, new_events)
    return {"job": updated_job, "run": run, "run_path": str(run_path)}


def start_pipeline_run(job_id: str, run_id: str, *, operator: str, jobs_dir: str | Path | None = None,
                       expected_job_revision: int | None = None, expected_run_revision: int | None = None,
                       stage: str | None = None, intake_root: str | None = None,
                       source: str = "pilot_job.runs.start") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    run_path = _pipeline_run_path(job_id, run_id, jobs_dir_path)
    run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
    if job.get("current_state") not in PIPELINE_RUN_JOB_STATES:
        raise PipelineRunError(f"job '{job_id}' in state '{job.get('current_state')}' cannot start pipeline run '{run_id}'")
    job_revision = _normalized_revision(job)
    run_revision = _normalized_run_revision(run)
    if expected_job_revision is not None and expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no pipeline run started")
    if expected_run_revision is not None and expected_run_revision != run_revision:
        raise JobRevisionError(f"pipeline run '{run_id}' stale revision: expected {expected_run_revision}, current revision {run_revision}; no pipeline run started")
    if run.get("status") != "PLANNED":
        raise PipelineRunError(f"pipeline run '{run_id}' cannot start from status '{run.get('status')}'; expected PLANNED")
    if not operator or not operator.strip():
        raise PipelineRunError("operator is required to start a pipeline run")
    _require_intake_readiness(job, "RUNNING", intake_root=intake_root)
    now = _now_iso()
    updated_run = dict(run)
    updated_run.update({"status": "STARTED", "started_at": now, "revision": run_revision + 1})
    if stage:
        if stage not in PIPELINE_STAGES:
            raise PipelineRunError(f"stage '{stage}' is not recognized; allowed stages: {', '.join(PIPELINE_STAGES)}")
        stages = [dict(s) for s in updated_run.get("stages", [])]
        for item in stages:
            if item.get("stage_id") == stage:
                item.update({"status": "RUNNING", "started_at": now, "operator": operator.strip()})
        updated_run["stages"] = stages
    event = _append_run_event(job, events, "PIPELINE_RUN_STARTED", message=f"Started pipeline run {run_id}",
                              operator=operator.strip(), run_id=run_id, artifacts=[str(run_path)], source=source)
    new_events = [*events, event]
    updated_job = dict(job)
    updated_job.update({"revision": job_revision + 1, "updated_at": now, "event_count": len(new_events),
                        "active_pipeline_run_id": run_id,
                        "latest_event": {"event_id": event["event_id"], "timestamp": event["timestamp"],
                                         "event_type": event["event_type"], "previous_state": job.get("current_state"),
                                         "new_state": job.get("current_state"), "message": event["message"]}})
    _write_run_and_job(run_path, updated_run, record_path, updated_job, events_path, new_events)
    return {"job": updated_job, "run": updated_run, "run_path": str(run_path)}


def update_pipeline_stage(job_id: str, run_id: str, stage_id: str, *, status: str, operator: str,
                          jobs_dir: str | Path | None = None, expected_job_revision: int | None = None,
                          expected_run_revision: int | None = None, command_ref: str | None = None,
                          function_ref: str | None = None, inputs: list[str] | None = None,
                          outputs: list[str] | None = None, log_reference: str | None = None,
                          error_category: str | None = None, error_summary: str | None = None,
                          warnings: list[str] | None = None, metrics: dict | None = None,
                          notes: str | None = None, source: str = "pilot_job.runs.stage") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    run_path = _pipeline_run_path(job_id, run_id, jobs_dir_path)
    run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
    job_revision = _normalized_revision(job)
    run_revision = _normalized_run_revision(run)
    if expected_job_revision is not None and expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no stage update recorded")
    if expected_run_revision is not None and expected_run_revision != run_revision:
        raise JobRevisionError(f"pipeline run '{run_id}' stale revision: expected {expected_run_revision}, current revision {run_revision}; no stage update recorded")
    if run.get("status") != "STARTED":
        raise PipelineRunError(f"pipeline run '{run_id}' must be STARTED before stage updates; current status is '{run.get('status')}'")
    if stage_id not in PIPELINE_STAGES:
        raise PipelineRunError(f"stage '{stage_id}' is not recognized; allowed stages: {', '.join(PIPELINE_STAGES)}")
    if status not in PIPELINE_STAGE_STATUSES or status == "NOT_STARTED":
        raise PipelineRunError("stage update status must be one of RUNNING, SUCCEEDED, SKIPPED, FAILED")
    if not operator or not operator.strip():
        raise PipelineRunError("operator is required for stage updates")
    for value, field in ((command_ref, "command_ref"), (function_ref, "function_ref"), (notes, "notes"), (error_summary, "error_summary")):
        if value:
            _validate_text_for_secrets(value, field)
    input_refs = _validate_artifacts(inputs or [], field_path="input_references")
    output_refs = _validate_artifacts(outputs or [], field_path="output_references")
    log_ref = _validate_artifacts([log_reference], field_path="log_reference")[0] if log_reference else None
    warning_values = _validate_artifacts(warnings or [], field_path="warnings")
    if metrics is not None:
        if not isinstance(metrics, dict):
            raise PipelineRunError("metrics: expected a JSON object")
        metric_issues: list[dict] = []
        _scan_secrets(metrics, "metrics", metric_issues)
        if metric_issues:
            first = metric_issues[0]
            raise PipelineRunError(f"{first['path']}: {first['message']}")
        for key, value in metrics.items():
            if not isinstance(key, str) or not key.strip():
                raise PipelineRunError("metrics: keys must be non-empty strings")
            if not (value is None or isinstance(value, (str, int, float, bool))):
                raise PipelineRunError(f"metrics.{key}: expected a string, number, boolean, or null")
            if isinstance(value, str):
                _validate_text_for_secrets(value, f"metrics.{key}")
    if status == "FAILED" and (not error_category or not error_summary):
        raise PipelineRunError("failed stage updates require error_category and error_summary")
    if error_category and error_category.upper() not in FAILURE_CATEGORIES:
        raise PipelineRunError(f"error_category must be one of {', '.join(sorted(FAILURE_CATEGORIES))}")
    stages = [dict(s) for s in run.get("stages", [])]
    stage = next((s for s in stages if s.get("stage_id") == stage_id), None)
    if stage is None:
        raise PipelineRunError(f"pipeline run '{run_id}' does not contain stage '{stage_id}'")
    current = stage.get("status")
    if current in {"SUCCEEDED", "FAILED", "SKIPPED"}:
        raise PipelineRunError(f"stage '{stage_id}' is already {current} and cannot be silently restarted")
    if status in {"SUCCEEDED", "FAILED"} and current != "RUNNING":
        raise PipelineRunError(f"stage '{stage_id}' cannot be marked {status} before it is RUNNING")
    now = _now_iso()
    if status == "RUNNING":
        stage["started_at"] = stage.get("started_at") or now
    if status in {"SUCCEEDED", "SKIPPED", "FAILED"}:
        stage["completed_at"] = now
        if not stage.get("started_at"):
            stage["started_at"] = now
    stage.update({"status": status, "command_ref": command_ref or stage.get("command_ref"),
                  "function_ref": function_ref or stage.get("function_ref"),
                  "input_references": [*stage.get("input_references", []), *input_refs],
                  "output_references": [*stage.get("output_references", []), *output_refs],
                  "log_reference": log_ref or stage.get("log_reference"),
                  "error_category": error_category.upper() if error_category else stage.get("error_category"),
                  "error_summary": error_summary or stage.get("error_summary"),
                  "warnings": [*stage.get("warnings", []), *warning_values],
                  "metrics": {**(stage.get("metrics", {}) if isinstance(stage.get("metrics"), dict) else {}), **(metrics or {})},
                  "operator": operator.strip(), "notes": notes or stage.get("notes")})
    updated_run = dict(run)
    updated_run.update({"revision": run_revision + 1, "stages": stages})
    report = validate_pipeline_run(updated_run, job=job)
    if not report["valid"]:
        raise PipelineRunError("pipeline run validation failed", report["issues"])
    event = _append_run_event(job, events, "PIPELINE_RUN_STAGE_UPDATED", message=f"Stage {stage_id} marked {status}",
                              operator=operator.strip(), run_id=run_id,
                              metadata={"stage_id": stage_id, "stage_status": status},
                              artifacts=[str(run_path), *output_refs], source=source)
    new_events = [*events, event]
    updated_job = dict(job)
    updated_job.update({"revision": job_revision + 1, "updated_at": now, "event_count": len(new_events),
                        "latest_event": {"event_id": event["event_id"], "timestamp": event["timestamp"],
                                         "event_type": event["event_type"], "previous_state": job.get("current_state"),
                                         "new_state": job.get("current_state"), "message": event["message"]}})
    _write_run_and_job(run_path, updated_run, record_path, updated_job, events_path, new_events)
    return {"job": updated_job, "run": updated_run, "run_path": str(run_path)}


def finish_pipeline_run(job_id: str, run_id: str, *, status: str, operator: str, summary: str,
                        jobs_dir: str | Path | None = None, expected_job_revision: int | None = None,
                        expected_run_revision: int | None = None, failure_category: str | None = None,
                        failure_summary: str | None = None, partial_success_explanation: str | None = None,
                        source: str = "pilot_job.runs.finish") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    run_path = _pipeline_run_path(job_id, run_id, jobs_dir_path)
    run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
    job_revision = _normalized_revision(job)
    run_revision = _normalized_run_revision(run)
    if expected_job_revision is not None and expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no run finish recorded")
    if expected_run_revision is not None and expected_run_revision != run_revision:
        raise JobRevisionError(f"pipeline run '{run_id}' stale revision: expected {expected_run_revision}, current revision {run_revision}; no run finish recorded")
    if run.get("status") != "STARTED":
        raise PipelineRunError(f"pipeline run '{run_id}' must be STARTED before finish; current status is '{run.get('status')}'")
    if status not in PIPELINE_RUN_FINAL_STATUSES:
        raise PipelineRunError(f"run finish status '{status}' is not allowed; allowed statuses: {', '.join(sorted(PIPELINE_RUN_FINAL_STATUSES))}")
    if not operator or not operator.strip() or not summary or not summary.strip():
        raise PipelineRunError("operator and completion summary are required to finish a run")
    _validate_metadata({"operator": operator, "summary": summary, "failure_summary": failure_summary or "", "partial_success_explanation": partial_success_explanation or ""})
    counts = _stage_counts(run)
    if status == "SUCCEEDED" and counts.get("FAILED", 0):
        raise PipelineRunError("successful runs cannot contain failed stages")
    if status == "SUCCEEDED" and counts.get("SUCCEEDED", 0) == 0:
        raise PipelineRunError("successful runs require at least one succeeded stage")
    if status == "PARTIALLY_SUCCEEDED" and not partial_success_explanation:
        raise PipelineRunError("partial success requires partial_success_explanation")
    if status == "FAILED":
        if not failure_category or not failure_summary:
            raise PipelineRunError("failed runs require failure_category and failure_summary")
        if failure_category.upper() not in FAILURE_CATEGORIES:
            raise PipelineRunError(f"failure_category must be one of {', '.join(sorted(FAILURE_CATEGORIES))}")
    now = _now_iso()
    updated_run = dict(run)
    updated_run.update({"status": status, "completed_at": now, "revision": run_revision + 1,
                        "completion_summary": summary.strip(), "failure_category": failure_category.upper() if failure_category else None,
                        "failure_summary": failure_summary, "partial_success_explanation": partial_success_explanation})
    report = validate_pipeline_run(updated_run, job=job)
    if not report["valid"]:
        raise PipelineRunError("pipeline run validation failed", report["issues"])
    event = _append_run_event(job, events, "PIPELINE_RUN_FINISHED", message=f"Pipeline run {run_id} finished as {status}",
                              operator=operator.strip(), run_id=run_id, metadata={"run_status": status},
                              artifacts=[str(run_path)], source=source)
    new_events = [*events, event]
    updated_job = dict(job)
    updated_job.update({"revision": job_revision + 1, "updated_at": now, "event_count": len(new_events),
                        "latest_event": {"event_id": event["event_id"], "timestamp": event["timestamp"],
                                         "event_type": event["event_type"], "previous_state": job.get("current_state"),
                                         "new_state": job.get("current_state"), "message": event["message"]}})
    _write_run_and_job(run_path, updated_run, record_path, updated_job, events_path, new_events)
    return {"job": updated_job, "run": updated_run, "run_path": str(run_path)}


def list_pipeline_runs(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, _ = _load_job_and_events(job_id, jobs_dir_path)
    rows = []
    for run_id in _job_run_ids(job, jobs_dir_path):
        try:
            run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
        except PipelineRunError:
            continue
        rows.append({"run_id": run_id, "status": run.get("status", ""), "entry_point": run.get("entry_point", ""),
                     "revision": _normalized_run_revision(run), "created_at": run.get("created_at", ""),
                     "plan_id": run.get("plan_id"), "plan_revision": run.get("plan_revision"),
                     "plan_workflow": run.get("plan_workflow")})
    return rows


def show_pipeline_run(job_id: str, run_id: str, jobs_dir: str | Path | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
    return {"run_id": run.get("run_id", run_id), "job_id": run.get("job_id", job_id), "status": run.get("status", ""),
            "revision": _normalized_run_revision(run), "entry_point": run.get("entry_point", ""),
            "plan_id": run.get("plan_id"), "plan_revision": run.get("plan_revision"),
            "plan_workflow": run.get("plan_workflow"), "planned_stage_ids": run.get("planned_stage_ids", []),
            "manual_deviations": run.get("manual_deviations", []),
            "command_args": run.get("command_args", []), "dry_run": run.get("dry_run"),
            "repository_commit": run.get("repository_commit"), "repository_dirty": run.get("repository_dirty"),
            "started_at": run.get("started_at"), "completed_at": run.get("completed_at"),
            "stages": [{"stage_id": s.get("stage_id"), "status": s.get("status"), "started_at": s.get("started_at"),
                        "completed_at": s.get("completed_at"), "warning_count": len(s.get("warnings", [])) if isinstance(s.get("warnings"), list) else 0,
                        "output_count": len(s.get("output_references", [])) if isinstance(s.get("output_references"), list) else 0}
                       for s in run.get("stages", []) if isinstance(s, dict)]}


def _registered_output_manifest_ids_for_run(job_id: str, run_id: str, jobs_dir: Path) -> list[str]:
    _, _, job, _ = _load_job_and_events(job_id, jobs_dir)
    manifest_ids = []
    for manifest_id in _job_output_manifest_ids(job, jobs_dir):
        manifest = _read_output_manifest(job_id, manifest_id, jobs_dir)
        if manifest.get("run_id") == run_id:
            manifest_ids.append(manifest_id)
    return sorted(manifest_ids)


def pipeline_run_summary(job_id: str, run_id: str, jobs_dir: str | Path | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    run = _read_pipeline_run(job_id, run_id, jobs_dir_path)
    outputs: list[str] = []
    warning_count = 0
    failed_stage = None
    for stage in run.get("stages", []):
        if not isinstance(stage, dict):
            continue
        outputs.extend(stage.get("output_references", []) if isinstance(stage.get("output_references"), list) else [])
        warning_count += len(stage.get("warnings", [])) if isinstance(stage.get("warnings"), list) else 0
        if stage.get("status") == "FAILED" and failed_stage is None:
            failed_stage = stage.get("stage_id")
    duration = None
    if run.get("started_at") and run.get("completed_at"):
        try:
            duration = (_dt.datetime.fromisoformat(run["completed_at"]) - _dt.datetime.fromisoformat(run["started_at"])).total_seconds()
        except ValueError:
            duration = None
    provenance = run.get("provenance") if isinstance(run.get("provenance"), dict) else {}
    validation = provenance.get("validation") if isinstance(provenance.get("validation"), dict) else {}
    return {"run_id": run_id, "run_status": run.get("status", ""), "entry_point": run.get("entry_point", ""),
            "plan_id": run.get("plan_id"), "plan_revision": run.get("plan_revision"),
            "plan_workflow": run.get("plan_workflow"), "planned_stage_ids": run.get("planned_stage_ids", []),
            "recorded_stage_ids": [stage.get("stage_id") for stage in run.get("stages", []) if isinstance(stage, dict) and stage.get("status") != "NOT_STARTED"],
            "manual_deviations": run.get("manual_deviations", []),
            "repository_commit": run.get("repository_commit"), "source_id": run.get("source_id"),
            "started_at": run.get("started_at"), "completed_at": run.get("completed_at"), "duration_seconds": duration,
            "stage_counts_by_status": _stage_counts(run), "failed_stage": failed_stage, "warning_count": warning_count,
            "referenced_outputs": sorted(set(outputs)), "registered_output_manifest_ids": _registered_output_manifest_ids_for_run(job_id, run_id, jobs_dir_path),
            "job_revision": _normalized_revision(read_job(job_id, jobs_dir=jobs_dir_path)), "run_revision": _normalized_run_revision(run),
            "source_provenance_complete": bool(provenance.get("source_media") and validation.get("source_ready")),
            "configuration_provenance_complete": bool(provenance.get("project_configuration") and provenance.get("brand_profile") and provenance.get("export_profiles")),
            "eligible_for_output_registration": run.get("status") in {"SUCCEEDED", "PARTIALLY_SUCCEEDED"}}


# ── Output manifests ─────────────────────────────────────────────────────────

_OUTPUT_MANIFEST_KEYS = (
    "schema_version", "manifest_id", "job_id", "pilot_id", "project_id", "source_id",
    "created_at", "created_by", "source_clip_manifest_path", "revision", "run_id", "outputs",
)
_OUTPUT_KEYS = (
    "output_id", "output_type", "local_path", "filename", "export_profile", "platform",
    "operational_category", "editorial_labels", "clip_id", "start_time", "end_time",
    "duration", "caption_path", "thumbnail_path", "metadata_path", "checksum",
    "review_status", "include_in_delivery", "review_notes", "rejection_reason",
    "approval_metadata",
)


def _output_issue(path: str, code: str, message: str) -> dict:
    return {"path": path, "code": code, "message": message}


def _reject_output_unknown(data: dict, allowed: tuple[str, ...], path: str, issues: list[dict]) -> None:
    for key in data:
        if key.startswith("_"):
            continue
        if key not in allowed:
            issues.append(_output_issue(f"{path}.{key}", "UNKNOWN_KEY", "is not a recognized output-manifest key"))


def _output_secret_scan(data: dict, path: str, issues: list[dict]) -> None:
    _scan_secrets(data, path, issues)
    for key, value in data.items():
        child = f"{path}.{key}"
        if isinstance(value, str):
            if _BASE64_MEDIA_RE.fullmatch(value.strip()):
                issues.append(_output_issue(child, "EMBEDDED_MEDIA", "must not contain embedded base64 media or binary data"))
            if _URL_CREDENTIAL_RE.match(value.strip()):
                issues.append(_output_issue(child, "CREDENTIAL_URL", "must not contain credential-bearing URLs"))
        elif isinstance(value, dict):
            _output_secret_scan(value, child, issues)
        elif isinstance(value, list):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    _output_secret_scan(item, f"{child}[{index}]", issues)
                elif isinstance(item, str) and _BASE64_MEDIA_RE.fullmatch(item.strip()):
                    issues.append(_output_issue(f"{child}[{index}]", "EMBEDDED_MEDIA", "must not contain embedded base64 media"))


def _output_manifest_dir(job_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(job_id):
        raise JobPathError(f"invalid job identifier '{job_id}'")
    directory = Path(jobs_dir) / f"{job_id}.outputs"
    if not _within_dir(directory, Path(jobs_dir)):
        raise JobPathError(f"output manifest directory for '{job_id}' would escape '{jobs_dir}'")
    return directory


def _output_manifest_path(job_id: str, manifest_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(manifest_id):
        raise OutputManifestError(f"manifest_id '{manifest_id}' is invalid; use letters, digits, '_' or '-'")
    path = _output_manifest_dir(job_id, jobs_dir) / f"{manifest_id}.json"
    if not _within_dir(path, Path(jobs_dir)):
        raise JobPathError(f"output manifest path for '{job_id}/{manifest_id}' would escape '{jobs_dir}'")
    return path


def _resolve_local_output_path(raw: str, issues: list[dict], field_path: str) -> Path | None:
    if not isinstance(raw, str) or not raw.strip():
        issues.append(_output_issue(field_path, "MISSING_PATH", "expected a non-empty local path"))
        return None
    value = raw.strip()
    if _URL_RE.match(value):
        issues.append(_output_issue(field_path, "URL_NOT_ALLOWED", "network URLs are not accepted for pilot outputs"))
        return None
    candidate = Path(value)
    if not candidate.is_absolute() and ".." in candidate.parts:
        issues.append(_output_issue(field_path, "PATH_TRAVERSAL", "relative paths must not contain '..' traversal"))
        return None
    return candidate.resolve() if candidate.is_absolute() else (ROOT / candidate).resolve()


def _validate_output_path(raw: object, output_type: str, issues: list[dict], field_path: str,
                          *, checksum: str | None = None, allow_missing: bool = False) -> Path | None:
    resolved = _resolve_local_output_path(raw, issues, field_path) if isinstance(raw, str) else None
    if resolved is None:
        if not isinstance(raw, str):
            issues.append(_output_issue(field_path, "BAD_TYPE", "expected a string path"))
        return None
    if not resolved.exists():
        if not allow_missing:
            issues.append(_output_issue(field_path, "OUTPUT_MISSING", f"path not found: {resolved}"))
        return resolved
    if resolved.is_dir():
        if output_type not in DIRECTORY_OUTPUT_TYPES:
            issues.append(_output_issue(field_path, "OUTPUT_IS_DIRECTORY", f"{output_type} requires a file, not a directory"))
        return resolved
    if not os.access(resolved, os.R_OK):
        issues.append(_output_issue(field_path, "OUTPUT_UNREADABLE", "path is not readable"))
    if resolved.stat().st_size == 0:
        issues.append(_output_issue(field_path, "OUTPUT_EMPTY", "file is empty"))
    allowed = OUTPUT_FILE_EXTENSIONS.get(output_type, frozenset())
    if resolved.suffix.lower() not in allowed:
        issues.append(_output_issue(field_path, "UNSUPPORTED_EXTENSION",
                                    f"extension '{resolved.suffix.lower() or '(none)'}' is not valid for {output_type}"))
    if checksum:
        expected = checksum.removeprefix("sha256:")
        actual = _sha256(resolved)
        if actual.lower() != expected.lower():
            issues.append(_output_issue(field_path, "CHECKSUM_MISMATCH", "provided checksum does not match the file"))
    return resolved


def _known_platforms(profile: str = "football") -> set[str]:
    values = set()
    for platform in select_platforms(profile):
        values.add(platform.lower())
    values.update({"tiktok", "reels", "shorts"})
    return values


def validate_output_manifest(data: object, *, job: dict | None = None) -> dict:
    """Validate a pilot output manifest. Read-only; performs no network calls,
    no file mutation, no media processing, and no directory creation."""
    issues: list[dict] = []
    if not isinstance(data, dict):
        issues.append(_output_issue("manifest", "BAD_TYPE", "root must be an object"))
        return {"valid": False, "issues": issues, "validation_codes": ["BAD_TYPE"]}

    _output_secret_scan(data, "manifest", issues)
    _reject_output_unknown(data, _OUTPUT_MANIFEST_KEYS, "manifest", issues)
    if data.get("schema_version") != OUTPUT_MANIFEST_SCHEMA_VERSION:
        issues.append(_output_issue("manifest.schema_version", "BAD_SCHEMA_VERSION", f"expected {OUTPUT_MANIFEST_SCHEMA_VERSION}"))

    for key in ("manifest_id", "job_id", "pilot_id", "project_id", "source_id", "created_at", "created_by"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issues.append(_output_issue(f"manifest.{key}", "MISSING_KEY", "expected a non-empty string"))
    if isinstance(data.get("manifest_id"), str) and not _is_valid_id(data["manifest_id"]):
        issues.append(_output_issue("manifest.manifest_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
    if job is not None and data.get("job_id") != job.get("job_id"):
        issues.append(_output_issue("manifest.job_id", "JOB_MISMATCH", f"does not match target job '{job.get('job_id')}'"))
    if isinstance(data.get("revision"), bool) or not isinstance(data.get("revision"), int) or data.get("revision") < 0:
        issues.append(_output_issue("manifest.revision", "BAD_TYPE", "expected a non-negative integer"))
    if "source_clip_manifest_path" in data and data.get("source_clip_manifest_path"):
        _validate_output_path(data["source_clip_manifest_path"], "CLIP_MANIFEST", issues, "manifest.source_clip_manifest_path")

    outputs = data.get("outputs")
    if not isinstance(outputs, list) or not outputs:
        issues.append(_output_issue("manifest.outputs", "MISSING_KEY", "expected a non-empty output list"))
        outputs = []

    seen_ids: set[str] = set()
    profile = data.get("project_id") if isinstance(data.get("project_id"), str) and data.get("project_id") else "football"
    try:
        platforms = _known_platforms(profile)
        categories = {c.lower() for c in resolve_operational_categories(profile)}
    except ConfigurationError as exc:
        issues.append(_output_issue("manifest.project_id", "CONFIG_UNKNOWN_PROJECT", str(exc)))
        platforms = set()
        categories = set()

    for index, output in enumerate(outputs):
        path = f"manifest.outputs[{index}]"
        if not isinstance(output, dict):
            issues.append(_output_issue(path, "BAD_TYPE", "expected an object"))
            continue
        _reject_output_unknown(output, _OUTPUT_KEYS, path, issues)
        output_id = output.get("output_id")
        if not _is_valid_id(output_id):
            issues.append(_output_issue(f"{path}.output_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
        elif output_id in seen_ids:
            issues.append(_output_issue(f"{path}.output_id", "DUPLICATE_OUTPUT_ID", "output IDs must be unique"))
        else:
            seen_ids.add(output_id)

        output_type = output.get("output_type")
        if output_type not in OUTPUT_TYPES:
            issues.append(_output_issue(f"{path}.output_type", "UNKNOWN_OUTPUT_TYPE", f"expected one of {', '.join(sorted(OUTPUT_TYPES))}"))
            output_type = "OTHER"
        status = output.get("review_status")
        if status not in OUTPUT_REVIEW_STATUSES:
            issues.append(_output_issue(f"{path}.review_status", "UNKNOWN_REVIEW_STATUS",
                                        f"expected one of {', '.join(sorted(OUTPUT_REVIEW_STATUSES))}"))
        if not isinstance(output.get("include_in_delivery"), bool):
            issues.append(_output_issue(f"{path}.include_in_delivery", "BAD_TYPE", "expected a boolean"))
        if not isinstance(output.get("filename"), str) or not output.get("filename", "").strip():
            issues.append(_output_issue(f"{path}.filename", "MISSING_KEY", "expected a non-empty filename"))
        checksum = output.get("checksum")
        if checksum is not None and (not isinstance(checksum, str) or not _CHECKSUM_RE.fullmatch(checksum)):
            issues.append(_output_issue(f"{path}.checksum", "BAD_CHECKSUM", "expected SHA-256 hex checksum"))
            checksum = None
        resolved = _validate_output_path(output.get("local_path"), output_type, issues, f"{path}.local_path", checksum=checksum)
        if resolved is not None and isinstance(output.get("filename"), str) and output["filename"].strip():
            if resolved.name != output["filename"].strip() and resolved.exists() and not resolved.is_dir():
                issues.append(_output_issue(f"{path}.filename", "FILENAME_MISMATCH", "filename must match local_path basename"))

        export_profile = output.get("export_profile")
        if not isinstance(export_profile, str) or not export_profile.strip():
            issues.append(_output_issue(f"{path}.export_profile", "MISSING_KEY", "expected a profile identifier"))
        else:
            try:
                resolve_export_profile(export_profile)
            except ConfigurationError as exc:
                issues.append(_output_issue(f"{path}.export_profile", C_CONFIG_UNKNOWN_EXPORT, str(exc)))
        platform = output.get("platform")
        if not isinstance(platform, str) or platform.lower() not in platforms:
            issues.append(_output_issue(f"{path}.platform", "UNKNOWN_PLATFORM", "platform is not configured"))
        category = output.get("operational_category")
        if not isinstance(category, str) or category.lower() not in categories:
            issues.append(_output_issue(f"{path}.operational_category", "UNKNOWN_OPERATIONAL_CATEGORY", "category is not configured"))
        if "editorial_labels" in output and output["editorial_labels"] is not None:
            if not isinstance(output["editorial_labels"], list) or not all(isinstance(v, str) and v.strip() for v in output["editorial_labels"]):
                issues.append(_output_issue(f"{path}.editorial_labels", "BAD_TYPE", "expected a list of strings"))
        for numeric in ("start_time", "end_time", "duration"):
            if numeric in output and output[numeric] is not None and (isinstance(output[numeric], bool) or not isinstance(output[numeric], (int, float, str))):
                issues.append(_output_issue(f"{path}.{numeric}", "BAD_TYPE", "expected timestamp string or number"))
        for ref_key, ref_type in (("caption_path", "CAPTION"), ("thumbnail_path", "THUMBNAIL"), ("metadata_path", "METADATA")):
            if output.get(ref_key):
                _validate_output_path(output[ref_key], ref_type, issues, f"{path}.{ref_key}")
        if output.get("approval_metadata") is not None and not isinstance(output["approval_metadata"], dict):
            issues.append(_output_issue(f"{path}.approval_metadata", "BAD_TYPE", "expected an object"))

    return {
        "valid": not issues,
        "issues": issues,
        "validation_codes": [issue["code"] for issue in issues] or [C_INTAKE_OK],
    }


def _read_output_manifest(job_id: str, manifest_id: str, jobs_dir: Path) -> dict:
    path = _output_manifest_path(job_id, manifest_id, jobs_dir)
    if not path.exists():
        raise OutputManifestError(f"output manifest '{manifest_id}' not found for job '{job_id}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise OutputManifestError(f"output manifest '{manifest_id}' root must be an object")
    return data


def _job_output_manifest_ids(job: dict, jobs_dir: Path) -> list[str]:
    values = job.get("output_manifests")
    if isinstance(values, list):
        return [v for v in values if isinstance(v, str)]
    directory = _output_manifest_dir(job.get("job_id", ""), jobs_dir)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json"))


def _validate_manifest_run_link(job_id: str, manifest_data: dict, jobs_dir: Path) -> None:
    run_id = manifest_data.get("run_id")
    if run_id is None or run_id == "":
        return
    if not isinstance(run_id, str) or not _is_valid_id(run_id):
        raise OutputManifestError("manifest.run_id must be a valid run identifier")
    run = _read_pipeline_run(job_id, run_id, jobs_dir)
    if run.get("job_id") != job_id:
        raise OutputManifestError(f"pipeline run '{run_id}' does not belong to job '{job_id}'")
    if run.get("status") not in {"SUCCEEDED", "PARTIALLY_SUCCEEDED"}:
        raise OutputManifestError(
            f"pipeline run '{run_id}' must be SUCCEEDED or PARTIALLY_SUCCEEDED before linking outputs; current status is '{run.get('status')}'"
        )


def register_output_manifest(job_id: str, manifest_data: object, *, jobs_dir: str | Path | None = None,
                             expected_revision: int | None = None, operator: str | None = None,
                             source: str = "pilot_job.outputs.register") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    current = job.get("current_state", "")
    if current not in OUTPUT_REGISTRATION_STATES:
        raise OutputManifestError(
            f"job '{job_id}' in state '{current}' cannot register outputs; allowed states: {', '.join(sorted(OUTPUT_REGISTRATION_STATES))}"
        )
    revision = _normalized_revision(job)
    if expected_revision is not None and expected_revision != revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_revision}, current revision {revision}; no output manifest registered")
    if not isinstance(manifest_data, dict):
        raise OutputManifestError("output manifest root must be an object")
    report = validate_output_manifest(manifest_data, job=job)
    if not report["valid"]:
        raise OutputManifestError("output manifest validation failed", report["issues"])
    _validate_manifest_run_link(job_id, manifest_data, jobs_dir_path)
    manifest_id = manifest_data["manifest_id"]
    manifest_path = _output_manifest_path(job_id, manifest_id, jobs_dir_path)
    if manifest_path.exists():
        raise OutputManifestError(f"output manifest '{manifest_id}' already exists for job '{job_id}'")

    sequence = _next_event_sequence(events)
    event = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _event_id(job_id, sequence, "OUTPUT_REGISTERED"),
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": _now_iso(),
        "event_type": "OUTPUT_REGISTERED",
        "previous_state": current,
        "new_state": current,
        "operator": operator,
        "message": f"Registered output manifest {manifest_id}",
        "metadata": {"manifest_id": manifest_id, "output_count": len(manifest_data.get("outputs", []))},
        "source": source,
        "related_codes": report["validation_codes"],
        "artifact_references": [str(manifest_path)],
    }
    new_events = [*events, event]
    updated_job = dict(job)
    manifest_ids = _job_output_manifest_ids(job, jobs_dir_path)
    manifest_ids.append(manifest_id)
    updated_job.update({
        "revision": revision + 1,
        "updated_at": _now_iso(),
        "event_count": len(new_events),
        "output_manifests": sorted(set(manifest_ids)),
        "latest_event": {
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "event_type": event["event_type"],
            "previous_state": current,
            "new_state": current,
            "message": event["message"],
        },
    })
    manifest_to_store = dict(manifest_data)
    manifest_to_store["revision"] = int(manifest_to_store.get("revision", 0))
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(manifest_path, manifest_to_store)
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return {"job": updated_job, "manifest": manifest_to_store, "path": str(manifest_path)}


def list_output_manifests(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, _events = _load_job_and_events(job_id, jobs_dir_path)
    rows = []
    for manifest_id in _job_output_manifest_ids(job, jobs_dir_path):
        try:
            manifest = _read_output_manifest(job_id, manifest_id, jobs_dir_path)
        except OutputManifestError:
            continue
        rows.append({
            "manifest_id": manifest_id,
            "revision": manifest.get("revision", 0),
            "run_id": manifest.get("run_id"),
            "output_count": len(manifest.get("outputs", [])) if isinstance(manifest.get("outputs"), list) else 0,
            "created_at": manifest.get("created_at", ""),
        })
    return rows


def show_output_manifest(job_id: str, manifest_id: str, jobs_dir: str | Path | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    manifest = _read_output_manifest(job_id, manifest_id, jobs_dir_path)
    return {
        "manifest_id": manifest.get("manifest_id", manifest_id),
        "job_id": manifest.get("job_id", job_id),
        "revision": manifest.get("revision", 0),
        "run_id": manifest.get("run_id"),
        "output_count": len(manifest.get("outputs", [])) if isinstance(manifest.get("outputs"), list) else 0,
        "outputs": [
            {
                "output_id": output.get("output_id", ""),
                "output_type": output.get("output_type", ""),
                "filename": output.get("filename", ""),
                "review_status": output.get("review_status", ""),
                "include_in_delivery": output.get("include_in_delivery", False),
                "platform": output.get("platform", ""),
                "export_profile": output.get("export_profile", ""),
                "operational_category": output.get("operational_category", ""),
            }
            for output in manifest.get("outputs", []) if isinstance(output, dict)
        ],
    }


def review_output(job_id: str, manifest_id: str, output_id: str, *, status: str, operator: str,
                  reason: str, include_in_delivery: bool | None = None, jobs_dir: str | Path | None = None,
                  expected_job_revision: int | None = None, expected_manifest_revision: int | None = None,
                  source: str = "pilot_job.outputs.review") -> dict:
    if status not in OUTPUT_REVIEW_STATUSES:
        raise OutputManifestError(f"review status '{status}' is not recognized; known statuses: {', '.join(sorted(OUTPUT_REVIEW_STATUSES))}")
    if not operator or not operator.strip():
        raise OutputManifestError("operator is required for output review")
    if not reason or not reason.strip():
        raise OutputManifestError("reason is required for output review")
    _validate_metadata({"operator": operator, "reason": reason})
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    job_revision = _normalized_revision(job)
    if expected_job_revision is not None and expected_job_revision != job_revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_job_revision}, current revision {job_revision}; no output review recorded")
    manifest_path = _output_manifest_path(job_id, manifest_id, jobs_dir_path)
    manifest = _read_output_manifest(job_id, manifest_id, jobs_dir_path)
    manifest_revision = manifest.get("revision", 0)
    if isinstance(manifest_revision, bool) or not isinstance(manifest_revision, int) or manifest_revision < 0:
        manifest_revision = 0
    if expected_manifest_revision is not None and expected_manifest_revision != manifest_revision:
        raise JobRevisionError(
            f"output manifest '{manifest_id}' stale revision: expected {expected_manifest_revision}, current revision {manifest_revision}; no output review recorded"
        )
    outputs = manifest.get("outputs") if isinstance(manifest.get("outputs"), list) else []
    target = None
    for output in outputs:
        if isinstance(output, dict) and output.get("output_id") == output_id:
            target = output
            break
    if target is None:
        raise OutputManifestError(f"output '{output_id}' not found in manifest '{manifest_id}'")

    new_outputs = []
    now = _now_iso()
    for output in outputs:
        if not isinstance(output, dict) or output.get("output_id") != output_id:
            new_outputs.append(output)
            continue
        updated = dict(output)
        updated["review_status"] = status
        updated["review_notes"] = reason.strip()
        if status == "APPROVED":
            updated["include_in_delivery"] = bool(include_in_delivery)
            updated["rejection_reason"] = None
            updated["approval_metadata"] = {
                "approved_by": operator.strip(),
                "approval_timestamp": now,
                "approval_statement": reason.strip(),
                "include_in_delivery": bool(include_in_delivery),
            }
        elif status in {"REJECTED", "CHANGES_REQUESTED", "EXCLUDED"}:
            updated["include_in_delivery"] = False
            updated["rejection_reason"] = reason.strip()
            updated["approval_metadata"] = None
        elif status == "PENDING":
            updated["include_in_delivery"] = False
            updated["rejection_reason"] = None
            updated["approval_metadata"] = None
        new_outputs.append(updated)

    updated_manifest = dict(manifest)
    updated_manifest["outputs"] = new_outputs
    updated_manifest["revision"] = manifest_revision + 1
    report = validate_output_manifest(updated_manifest, job=job)
    if not report["valid"]:
        raise OutputManifestError("output manifest validation failed after review", report["issues"])

    current = job.get("current_state", "")
    sequence = _next_event_sequence(events)
    event = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _event_id(job_id, sequence, "OUTPUT_REVIEWED"),
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": now,
        "event_type": "OUTPUT_REVIEWED",
        "previous_state": current,
        "new_state": current,
        "operator": operator.strip(),
        "message": f"Output {output_id} marked {status}",
        "metadata": {"manifest_id": manifest_id, "output_id": output_id, "review_status": status},
        "source": source,
        "related_codes": report["validation_codes"],
        "artifact_references": [str(manifest_path)],
    }
    new_events = [*events, event]
    updated_job = dict(job)
    updated_job.update({
        "revision": job_revision + 1,
        "updated_at": now,
        "event_count": len(new_events),
        "latest_event": {
            "event_id": event["event_id"],
            "timestamp": event["timestamp"],
            "event_type": event["event_type"],
            "previous_state": current,
            "new_state": current,
            "message": event["message"],
        },
    })
    _atomic_write_json(manifest_path, updated_manifest)
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return {"job": updated_job, "manifest": updated_manifest}


def output_summary(job_id: str, jobs_dir: str | Path | None = None, *, intake_root: str | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, _events = _load_job_and_events(job_id, jobs_dir_path)
    manifest_ids = _job_output_manifest_ids(job, jobs_dir_path)
    statuses = {status: 0 for status in sorted(OUTPUT_REVIEW_STATUSES)}
    total = video_count = included = missing = invalid = 0
    platforms: set[str] = set()
    profiles: set[str] = set()
    categories: set[str] = set()
    manifest_revisions: dict[str, int] = {}
    included_outputs: list[dict] = []
    issues: list[dict] = []

    for manifest_id in manifest_ids:
        try:
            manifest = _read_output_manifest(job_id, manifest_id, jobs_dir_path)
        except OutputManifestError as exc:
            invalid += 1
            issues.append(_output_issue(f"manifests.{manifest_id}", "MANIFEST_MISSING", str(exc)))
            continue
        manifest_revisions[manifest_id] = manifest.get("revision", 0) if isinstance(manifest.get("revision"), int) else 0
        report = validate_output_manifest(manifest, job=job)
        for issue in report["issues"]:
            if issue["code"] == "OUTPUT_MISSING":
                missing += 1
            else:
                invalid += 1
            issues.append(issue)
        for output in manifest.get("outputs", []):
            if not isinstance(output, dict):
                continue
            total += 1
            if output.get("output_type") == "VIDEO_CLIP":
                video_count += 1
            status = output.get("review_status", "")
            if status in statuses:
                statuses[status] += 1
            if output.get("include_in_delivery") is True:
                included += 1
                included_outputs.append(output)
            if isinstance(output.get("platform"), str):
                platforms.add(output["platform"])
            if isinstance(output.get("export_profile"), str):
                profiles.add(output["export_profile"])
            if isinstance(output.get("operational_category"), str):
                categories.add(output["operational_category"])

    rights_ok = False
    human_review_recorded = False
    rights_codes: list[str] = []
    try:
        report = _require_intake_readiness(job, "DELIVERY_READY", intake_root=intake_root)
        rights_ok = report["rights_cleared"]
    except JobRecordError as exc:
        rights_codes = [str(exc)]
    human_review_recorded = bool(included_outputs) and all(
        output.get("review_status") == "APPROVED" and isinstance(output.get("approval_metadata"), dict)
        for output in included_outputs
    )
    review_complete = bool(manifest_ids) and included > 0 and human_review_recorded and missing == 0 and invalid == 0 and rights_ok
    eligible_approved = review_complete
    eligible_delivery_ready = review_complete
    return {
        "job_id": job_id,
        "job_revision": _normalized_revision(job),
        "current_state": job.get("current_state", ""),
        "manifest_count": len(manifest_ids),
        "manifest_revisions": manifest_revisions,
        "total_outputs": total,
        "video_count": video_count,
        "counts_by_review_status": statuses,
        "delivery_included_count": included,
        "approved_delivery_included_count": sum(1 for output in included_outputs if output.get("review_status") == "APPROVED"),
        "missing_file_count": missing,
        "invalid_reference_count": invalid,
        "platforms": sorted(platforms),
        "export_profiles": sorted(profiles),
        "operational_categories": sorted(categories),
        "rights_valid": rights_ok,
        "human_review_recorded": human_review_recorded,
        "review_complete": review_complete,
        "eligible_for_approved": eligible_approved,
        "eligible_for_delivery_ready": eligible_delivery_ready,
        "issues": issues,
        "rights_issues": rights_codes,
    }


# ── Delivery packages ────────────────────────────────────────────────────────

_DELIVERY_PACKAGE_KEYS = (
    "schema_version", "package_id", "job_id", "pilot_id", "project_id", "source_id",
    "created_at", "created_by", "job_revision_used", "package_revision",
    "delivery_method", "delivery_destination", "package_label", "internal_notes",
    "rights_verification_timestamp", "rights_status_at_generation",
    "approval_verification_timestamp", "represented_run_ids", "deliverables", "summary",
)
_DELIVERABLE_KEYS = (
    "deliverable_id", "output_manifest_id", "run_id", "output_id", "output_type", "local_path",
    "filename", "platform", "export_profile", "operational_category",
    "editorial_labels", "clip_id", "duration", "caption_path", "thumbnail_path",
    "metadata_path", "checksum", "approval_reviewer", "approval_timestamp",
    "approval_statement", "delivery_sequence", "client_label", "client_note",
)


def _delivery_package_dir(job_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(job_id):
        raise JobPathError(f"invalid job identifier '{job_id}'")
    directory = Path(jobs_dir) / f"{job_id}.delivery"
    if not _within_dir(directory, Path(jobs_dir)):
        raise JobPathError(f"delivery package directory for '{job_id}' would escape '{jobs_dir}'")
    return directory


def _delivery_package_path(job_id: str, package_id: str, jobs_dir: Path) -> Path:
    if not _is_valid_id(package_id):
        raise DeliveryPackageError(f"package_id '{package_id}' is invalid; use letters, digits, '_' or '-'")
    path = _delivery_package_dir(job_id, jobs_dir) / f"{package_id}.json"
    if not _within_dir(path, Path(jobs_dir)):
        raise JobPathError(f"delivery package path for '{job_id}/{package_id}' would escape '{jobs_dir}'")
    return path


def _delivery_checklist_path(job_id: str, package_id: str, jobs_dir: Path) -> Path:
    return _delivery_package_dir(job_id, jobs_dir) / f"{package_id}.checklist.txt"


def _delivery_confirmation_path(job_id: str, package_id: str, jobs_dir: Path) -> Path:
    return _delivery_package_dir(job_id, jobs_dir) / f"{package_id}.confirmation.json"


def _validate_delivery_destination(destination: object, issues: list[dict], path: str) -> str | None:
    if not isinstance(destination, str) or not destination.strip():
        issues.append(_output_issue(path, "MISSING_KEY", "expected a non-empty delivery destination"))
        return None
    value = destination.strip()
    if _URL_CREDENTIAL_RE.match(value):
        issues.append(_output_issue(path, "CREDENTIAL_URL", "delivery destination must not contain embedded credentials"))
    if _URL_RE.match(value):
        issues.append(_output_issue(path, "URL_NOT_ALLOWED", "record a local path or human-readable shared-folder description, not a URL"))
    if _SECRET_VALUE_RE.search(value) or _BASE64_MEDIA_RE.fullmatch(value):
        issues.append(_output_issue(path, "SECRET_VALUE", "delivery destination must not contain secrets or embedded data"))
    if ".." in Path(value).parts:
        issues.append(_output_issue(path, "PATH_TRAVERSAL", "delivery destination must not contain '..' traversal"))
    return value


def _rights_snapshot(job: dict, *, intake_root: str | None = None) -> tuple[dict, dict]:
    intake = _load_stored_intake(job)
    report = validate_intake(intake, intake_root=intake_root, check_source=False, check_rights=True)
    rights = intake.get("rights") if isinstance(intake.get("rights"), dict) else {}
    uses = rights.get("permitted_uses") if isinstance(rights.get("permitted_uses"), list) else []
    return {
        "status": rights.get("status", ""),
        "expiration_date": rights.get("expiration_date"),
        "publishing_permitted": any(str(u).lower() in {"publish", "public_distribution"} for u in uses),
        "distribution_limitations": rights.get("distribution_limitations", []),
        "rights_valid": report["rights_cleared"],
        "validation_codes": report["validation_codes"],
    }, report


def _approved_included_outputs(job_id: str, jobs_dir: Path) -> list[dict]:
    _, _, job, _ = _load_job_and_events(job_id, jobs_dir)
    rows: list[dict] = []
    for manifest_id in _job_output_manifest_ids(job, jobs_dir):
        manifest = _read_output_manifest(job_id, manifest_id, jobs_dir)
        manifest_revision = manifest.get("revision", 0) if isinstance(manifest.get("revision"), int) else 0
        manifest_run_id = manifest.get("run_id") if isinstance(manifest.get("run_id"), str) else None
        for output in manifest.get("outputs", []):
            if not isinstance(output, dict):
                continue
            if output.get("review_status") == "APPROVED" and output.get("include_in_delivery") is True:
                rows.append({"manifest_id": manifest_id, "manifest_revision": manifest_revision,
                             "manifest_run_id": manifest_run_id, "output": output})
    return rows


def _package_summary(deliverables: list[dict], *, missing_file_count: int, checksum_verified_count: int,
                     rights_valid: bool, human_review_complete: bool) -> dict:
    counts_by_type: dict[str, int] = {}
    counts_by_platform: dict[str, int] = {}
    counts_by_profile: dict[str, int] = {}
    counts_by_category: dict[str, int] = {}
    total_duration = 0.0
    has_duration = False
    for item in deliverables:
        for key, target in (("output_type", counts_by_type), ("platform", counts_by_platform),
                            ("export_profile", counts_by_profile), ("operational_category", counts_by_category)):
            value = item.get(key)
            if isinstance(value, str) and value:
                target[value] = target.get(value, 0) + 1
        duration = item.get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            total_duration += float(duration)
            has_duration = True
    return {
        "total_deliverable_count": len(deliverables),
        "counts_by_output_type": counts_by_type,
        "counts_by_platform": counts_by_platform,
        "counts_by_export_profile": counts_by_profile,
        "counts_by_operational_category": counts_by_category,
        "total_video_duration": total_duration if has_duration else None,
        "missing_file_count": missing_file_count,
        "checksum_verified_count": checksum_verified_count,
        "rights_valid": rights_valid,
        "human_review_complete": human_review_complete,
        "delivery_ready": bool(deliverables) and missing_file_count == 0 and rights_valid and human_review_complete,
    }


def generate_delivery_package(job_id: str, *, package_id: str, operator: str, delivery_method: str,
                              delivery_destination: str, jobs_dir: str | Path | None = None,
                              expected_revision: int | None = None, package_label: str | None = None,
                              internal_notes: str | None = None, intake_root: str | None = None,
                              source: str = "pilot_job.delivery.generate") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    current = job.get("current_state", "")
    if current not in DELIVERY_PACKAGE_STATES:
        raise DeliveryPackageError(f"job '{job_id}' in state '{current}' cannot generate a delivery package; allowed states: {', '.join(sorted(DELIVERY_PACKAGE_STATES))}")
    revision = _normalized_revision(job)
    if expected_revision is not None and expected_revision != revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_revision}, current revision {revision}; no delivery package generated")
    if not _is_valid_id(package_id):
        raise DeliveryPackageError(f"package_id '{package_id}' is invalid; use letters, digits, '_' or '-'")
    if not operator or not operator.strip():
        raise DeliveryPackageError("operator is required for delivery package generation")
    _validate_metadata({"operator": operator, "delivery_method": delivery_method, "delivery_destination": delivery_destination,
                        "package_label": package_label or "", "internal_notes": internal_notes or ""})
    issues: list[dict] = []
    destination = _validate_delivery_destination(delivery_destination, issues, "delivery.destination")
    if delivery_method not in DELIVERY_METHODS:
        issues.append(_output_issue("delivery.method", "BAD_TYPE", f"expected one of {', '.join(sorted(DELIVERY_METHODS))}"))
    if issues:
        raise DeliveryPackageError("delivery package validation failed", issues)
    package_path = _delivery_package_path(job_id, package_id, jobs_dir_path)
    checklist_path = _delivery_checklist_path(job_id, package_id, jobs_dir_path)
    if package_path.exists() or checklist_path.exists():
        raise DeliveryPackageError(f"delivery package '{package_id}' already exists for job '{job_id}'")

    summary = output_summary(job_id, jobs_dir=jobs_dir_path, intake_root=intake_root)
    if not summary["review_complete"]:
        raise DeliveryPackageError(f"job '{job_id}' is not ready for delivery packaging", summary.get("issues", []))
    rights, rights_report = _rights_snapshot(job, intake_root=intake_root)
    if not rights["rights_valid"]:
        raise DeliveryPackageError(f"job '{job_id}' rights are not valid for delivery package generation")
    rows = _approved_included_outputs(job_id, jobs_dir_path)
    if not rows:
        raise DeliveryPackageError(f"job '{job_id}' has no approved delivery-included outputs")

    deliverables: list[dict] = []
    missing = 0
    checksum_verified = 0
    for sequence, row in enumerate(rows, start=1):
        output = row["output"]
        path_issues: list[dict] = []
        _validate_output_path(output.get("local_path"), output.get("output_type", "OTHER"), path_issues, f"deliverables[{sequence}].local_path", checksum=output.get("checksum"))
        if path_issues:
            missing += sum(1 for issue in path_issues if issue["code"] == "OUTPUT_MISSING")
            raise DeliveryPackageError("included output path failed validation", path_issues)
        if output.get("checksum"):
            checksum_verified += 1
        approval = output.get("approval_metadata") if isinstance(output.get("approval_metadata"), dict) else {}
        deliverables.append({
            "deliverable_id": f"{package_id}_{sequence:03d}",
            "output_manifest_id": row["manifest_id"],
            "run_id": row.get("manifest_run_id"),
            "output_id": output.get("output_id", ""),
            "output_type": output.get("output_type", ""),
            "local_path": output.get("local_path", ""),
            "filename": output.get("filename", ""),
            "platform": output.get("platform", ""),
            "export_profile": output.get("export_profile", ""),
            "operational_category": output.get("operational_category", ""),
            "editorial_labels": list(output.get("editorial_labels", [])) if isinstance(output.get("editorial_labels"), list) else [],
            "clip_id": output.get("clip_id"),
            "duration": output.get("duration"),
            "caption_path": output.get("caption_path"),
            "thumbnail_path": output.get("thumbnail_path"),
            "metadata_path": output.get("metadata_path"),
            "checksum": output.get("checksum"),
            "approval_reviewer": approval.get("approved_by", ""),
            "approval_timestamp": approval.get("approval_timestamp", ""),
            "approval_statement": approval.get("approval_statement", ""),
            "delivery_sequence": sequence,
            "client_label": None,
            "client_note": None,
        })

    now = _now_iso()
    package = {
        "schema_version": DELIVERY_PACKAGE_SCHEMA_VERSION,
        "package_id": package_id,
        "job_id": job_id,
        "pilot_id": job.get("pilot_id", ""),
        "project_id": job.get("project_id", ""),
        "source_id": job.get("source_id", ""),
        "created_at": now,
        "created_by": operator.strip(),
        "job_revision_used": revision,
        "package_revision": 0,
        "delivery_method": delivery_method,
        "delivery_destination": destination,
        "package_label": package_label,
        "internal_notes": internal_notes,
        "rights_verification_timestamp": now,
        "rights_status_at_generation": rights["status"],
        "approval_verification_timestamp": now,
        "represented_run_ids": sorted({d["run_id"] for d in deliverables if isinstance(d.get("run_id"), str) and d.get("run_id")}),
        "deliverables": deliverables,
        "summary": _package_summary(deliverables, missing_file_count=0, checksum_verified_count=checksum_verified,
                                    rights_valid=rights["rights_valid"], human_review_complete=summary["human_review_recorded"]),
    }
    report = validate_delivery_package(package, job=job, jobs_dir=jobs_dir_path, intake_root=intake_root)
    if not report["valid"]:
        raise DeliveryPackageError("delivery package validation failed", report["issues"])
    checklist = render_delivery_checklist(package, job=job, rights=rights, output_summary=summary)

    sequence = _next_event_sequence(events)
    event = {
        "event_schema_version": EVENT_SCHEMA_VERSION,
        "event_id": _event_id(job_id, sequence, "DELIVERY_PACKAGE_GENERATED"),
        "job_id": job_id,
        "sequence": sequence,
        "timestamp": now,
        "event_type": "DELIVERY_PACKAGE_GENERATED",
        "previous_state": current,
        "new_state": current,
        "operator": operator.strip(),
        "message": f"Generated delivery package {package_id}",
        "metadata": {"package_id": package_id, "deliverable_count": len(deliverables)},
        "source": source,
        "related_codes": rights_report["validation_codes"],
        "artifact_references": [str(package_path), str(checklist_path)],
    }
    new_events = [*events, event]
    package_ids = [v for v in job.get("delivery_packages", []) if isinstance(v, str)] if isinstance(job.get("delivery_packages"), list) else []
    package_ids.append(package_id)
    updated_job = dict(job)
    updated_job.update({
        "revision": revision + 1,
        "updated_at": now,
        "event_count": len(new_events),
        "delivery_packages": sorted(set(package_ids)),
        "active_delivery_package_id": package_id,
        "latest_event": {"event_id": event["event_id"], "timestamp": now, "event_type": event["event_type"],
                         "previous_state": current, "new_state": current, "message": event["message"]},
    })
    package_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(package_path, package)
    _atomic_write_text(checklist_path, checklist)
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return {"job": updated_job, "package": package, "package_path": str(package_path), "checklist_path": str(checklist_path)}


def _read_delivery_package(job_id: str, package_id: str, jobs_dir: Path) -> dict:
    path = _delivery_package_path(job_id, package_id, jobs_dir)
    if not path.exists():
        raise DeliveryPackageError(f"delivery package '{package_id}' not found for job '{job_id}'")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise DeliveryPackageError(f"delivery package '{package_id}' root must be an object")
    return data


def _job_delivery_package_ids(job: dict, jobs_dir: Path) -> list[str]:
    values = job.get("delivery_packages")
    if isinstance(values, list):
        return [v for v in values if isinstance(v, str)]
    directory = _delivery_package_dir(job.get("job_id", ""), jobs_dir)
    if not directory.exists():
        return []
    return sorted(path.stem for path in directory.glob("*.json") if not path.name.endswith(".confirmation.json"))


def validate_delivery_package(data: object, *, job: dict | None = None, jobs_dir: str | Path | None = None,
                              intake_root: str | None = None) -> dict:
    issues: list[dict] = []
    if not isinstance(data, dict):
        issues.append(_output_issue("package", "BAD_TYPE", "root must be an object"))
        return {"valid": False, "issues": issues, "validation_codes": ["BAD_TYPE"]}
    _output_secret_scan(data, "package", issues)
    _reject_output_unknown(data, _DELIVERY_PACKAGE_KEYS, "package", issues)
    if data.get("schema_version") != DELIVERY_PACKAGE_SCHEMA_VERSION:
        issues.append(_output_issue("package.schema_version", "BAD_SCHEMA_VERSION", f"expected {DELIVERY_PACKAGE_SCHEMA_VERSION}"))
    package_id = data.get("package_id")
    if not _is_valid_id(package_id):
        issues.append(_output_issue("package.package_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
    for key in ("job_id", "pilot_id", "project_id", "source_id", "created_at", "created_by", "delivery_method", "delivery_destination"):
        if not isinstance(data.get(key), str) or not data.get(key, "").strip():
            issues.append(_output_issue(f"package.{key}", "MISSING_KEY", "expected a non-empty string"))
    if isinstance(data.get("delivery_method"), str) and data["delivery_method"] not in DELIVERY_METHODS:
        issues.append(_output_issue("package.delivery_method", "BAD_TYPE", f"expected one of {', '.join(sorted(DELIVERY_METHODS))}"))
    _validate_delivery_destination(data.get("delivery_destination"), issues, "package.delivery_destination")
    if isinstance(data.get("job_revision_used"), bool) or not isinstance(data.get("job_revision_used"), int):
        issues.append(_output_issue("package.job_revision_used", "BAD_TYPE", "expected an integer"))
    if data.get("package_revision") != 0:
        issues.append(_output_issue("package.package_revision", "BAD_TYPE", "expected package revision 0"))
    if job is not None and data.get("job_id") != job.get("job_id"):
        issues.append(_output_issue("package.job_id", "JOB_MISMATCH", f"does not match target job '{job.get('job_id')}'"))
    represented = data.get("represented_run_ids")
    if represented is None:
        represented = []
    if not isinstance(represented, list) or any(not isinstance(v, str) or not _is_valid_id(v) for v in represented):
        issues.append(_output_issue("package.represented_run_ids", "BAD_TYPE", "expected a list of valid run identifiers"))
        represented = []

    deliverables = data.get("deliverables")
    if not isinstance(deliverables, list) or not deliverables:
        issues.append(_output_issue("package.deliverables", "MISSING_KEY", "expected a non-empty deliverable list"))
        deliverables = []
    seen_ids: set[str] = set()
    seen_sequences: set[int] = set()
    deliverable_run_ids: set[str] = set()
    total_duration = 0.0
    has_duration = False
    checksum_verified = 0
    job_outputs: dict[tuple[str, str], dict] = {}
    if job is not None:
        jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
        for row in _approved_included_outputs(job.get("job_id", ""), jobs_dir_path):
            job_outputs[(row["manifest_id"], row["output"].get("output_id", ""))] = row["output"]
    for index, item in enumerate(deliverables):
        path = f"package.deliverables[{index}]"
        if not isinstance(item, dict):
            issues.append(_output_issue(path, "BAD_TYPE", "expected an object"))
            continue
        _reject_output_unknown(item, _DELIVERABLE_KEYS, path, issues)
        deliverable_id = item.get("deliverable_id")
        if not _is_valid_id(deliverable_id):
            issues.append(_output_issue(f"{path}.deliverable_id", "BAD_ID", "expected letters, digits, '_' or '-'"))
        elif deliverable_id in seen_ids:
            issues.append(_output_issue(f"{path}.deliverable_id", "DUPLICATE_DELIVERABLE_ID", "deliverable IDs must be unique"))
        else:
            seen_ids.add(deliverable_id)
        sequence = item.get("delivery_sequence")
        if isinstance(sequence, bool) or not isinstance(sequence, int) or sequence <= 0:
            issues.append(_output_issue(f"{path}.delivery_sequence", "BAD_SEQUENCE", "expected a positive integer sequence"))
        elif sequence in seen_sequences:
            issues.append(_output_issue(f"{path}.delivery_sequence", "DUPLICATE_SEQUENCE", "delivery sequence values must be unique"))
        else:
            seen_sequences.add(sequence)
        if item.get("run_id") is not None:
            if not isinstance(item.get("run_id"), str) or not _is_valid_id(item.get("run_id")):
                issues.append(_output_issue(f"{path}.run_id", "BAD_ID", "expected a valid run identifier or null"))
            else:
                deliverable_run_ids.add(item["run_id"])
        output_type = item.get("output_type") if item.get("output_type") in OUTPUT_TYPES else "OTHER"
        if item.get("output_type") not in OUTPUT_TYPES:
            issues.append(_output_issue(f"{path}.output_type", "UNKNOWN_OUTPUT_TYPE", "output type is not supported"))
        checksum = item.get("checksum") if isinstance(item.get("checksum"), str) else None
        if checksum:
            checksum_verified += 1
        _validate_output_path(item.get("local_path"), output_type, issues, f"{path}.local_path", checksum=checksum)
        for ref_key, ref_type in (("caption_path", "CAPTION"), ("thumbnail_path", "THUMBNAIL"), ("metadata_path", "METADATA")):
            if item.get(ref_key):
                _validate_output_path(item[ref_key], ref_type, issues, f"{path}.{ref_key}")
        try:
            resolve_export_profile(item.get("export_profile", ""))
        except ConfigurationError as exc:
            issues.append(_output_issue(f"{path}.export_profile", C_CONFIG_UNKNOWN_EXPORT, str(exc)))
        if isinstance(item.get("platform"), str) and item["platform"].lower() not in _known_platforms(data.get("project_id", "football")):
            issues.append(_output_issue(f"{path}.platform", "UNKNOWN_PLATFORM", "platform is not configured"))
        duration = item.get("duration")
        if isinstance(duration, (int, float)) and not isinstance(duration, bool):
            total_duration += float(duration)
            has_duration = True
        if job_outputs:
            original = job_outputs.get((item.get("output_manifest_id"), item.get("output_id")))
            if not original:
                issues.append(_output_issue(f"{path}.output_id", "OUTPUT_NOT_APPROVED", "referenced output is not currently approved and included"))
            else:
                approval = original.get("approval_metadata") if isinstance(original.get("approval_metadata"), dict) else {}
                if item.get("approval_reviewer") != approval.get("approved_by") or item.get("approval_timestamp") != approval.get("approval_timestamp") or item.get("approval_statement") != approval.get("approval_statement"):
                    issues.append(_output_issue(f"{path}.approval_metadata", "APPROVAL_MISMATCH", "approval metadata no longer matches the source output manifest"))
    if seen_sequences and sorted(seen_sequences) != list(range(1, len(seen_sequences) + 1)):
        issues.append(_output_issue("package.deliverables", "SEQUENCE_ORDER", "delivery sequences must be contiguous starting at 1"))
    if sorted(deliverable_run_ids) != sorted(represented):
        issues.append(_output_issue("package.represented_run_ids", "RUN_ID_MISMATCH", "does not match deliverable run IDs"))

    summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
    if not summary:
        issues.append(_output_issue("package.summary", "MISSING_KEY", "summary is required"))
    elif summary.get("total_deliverable_count") != len(deliverables):
        issues.append(_output_issue("package.summary.total_deliverable_count", "COUNT_MISMATCH", "does not match deliverable count"))
    if has_duration and summary.get("total_video_duration") is not None and abs(float(summary.get("total_video_duration")) - total_duration) > 0.001:
        issues.append(_output_issue("package.summary.total_video_duration", "DURATION_MISMATCH", "does not match deliverable durations"))
    if summary and summary.get("checksum_verified_count") != checksum_verified:
        issues.append(_output_issue("package.summary.checksum_verified_count", "COUNT_MISMATCH", "does not match checksum-bearing deliverables"))
    if job is not None:
        try:
            rights, _ = _rights_snapshot(job, intake_root=intake_root)
            if not rights["rights_valid"] or data.get("rights_status_at_generation") != rights["status"]:
                issues.append(_output_issue("package.rights_status_at_generation", "RIGHTS_NOT_CURRENT", "rights status is not current or not valid"))
        except JobRecordError as exc:
            issues.append(_output_issue("package.rights_status_at_generation", "RIGHTS_NOT_CURRENT", str(exc)))
    return {"valid": not issues, "issues": issues, "validation_codes": [issue["code"] for issue in issues] or [C_INTAKE_OK]}


def render_delivery_checklist(package: dict, *, job: dict, rights: dict, output_summary: dict) -> str:
    reviewers = sorted({d.get("approval_reviewer", "") for d in package.get("deliverables", []) if d.get("approval_reviewer")})
    lines = [
        "Pilot Delivery Handoff Checklist",
        "================================",
        "",
        "Job Verification",
        f"- Job ID: {package.get('job_id')}",
        f"- Pilot ID: {package.get('pilot_id')}",
        f"- Project ID: {package.get('project_id')}",
        f"- Current revision at generation: {job.get('revision')}",
        f"- Current state at generation: {job.get('current_state')}",
        f"- Package ID: {package.get('package_id')}",
        "",
        "Rights Verification",
        f"- Rights status: {rights.get('status')}",
        f"- Rights checked: {package.get('rights_verification_timestamp')}",
        f"- Expiration date: {rights.get('expiration_date') or 'none recorded'}",
        f"- Publishing permitted: {'yes' if rights.get('publishing_permitted') else 'no'}",
        f"- Distribution limitations: {', '.join(rights.get('distribution_limitations') or []) or 'none recorded'}",
        "",
        "Review Verification",
        f"- Human review complete: {'yes' if output_summary.get('human_review_recorded') else 'no'}",
        f"- Reviewers: {', '.join(reviewers) or 'none'}",
        f"- Approved deliverable count: {package.get('summary', {}).get('total_deliverable_count')}",
        f"- Excluded/rejected count: {output_summary.get('counts_by_review_status', {}).get('EXCLUDED', 0) + output_summary.get('counts_by_review_status', {}).get('REJECTED', 0)}",
        f"- Output paths revalidated: yes",
        f"- Missing-file count: {package.get('summary', {}).get('missing_file_count')}",
        f"- Checksum-verified count: {package.get('summary', {}).get('checksum_verified_count')}",
        "",
        "Brand and Export Verification",
        f"- Platforms: {', '.join(package.get('summary', {}).get('counts_by_platform', {}).keys())}",
        f"- Export profiles: {', '.join(package.get('summary', {}).get('counts_by_export_profile', {}).keys())}",
        f"- Operational categories: {', '.join(package.get('summary', {}).get('counts_by_operational_category', {}).keys())}",
        "- Expected naming: use listed filenames exactly; do not rename files during delivery.",
        "",
        "Delivery Verification",
        f"- Delivery method: {package.get('delivery_method')}",
        f"- Delivery destination: {package.get('delivery_destination')}",
        f"- Deliverable count: {package.get('summary', {}).get('total_deliverable_count')}",
        "- File list:",
    ]
    for deliverable in package.get("deliverables", []):
        lines.append(f"  {deliverable.get('delivery_sequence')}. {deliverable.get('filename')} :: {deliverable.get('local_path')}")
        if deliverable.get("caption_path"):
            lines.append(f"     caption: {deliverable.get('caption_path')}")
        if deliverable.get("thumbnail_path"):
            lines.append(f"     thumbnail: {deliverable.get('thumbnail_path')}")
    lines.extend([
        "",
        "Manual actions still required:",
        "- No files have been copied by this command.",
        "- No files have been uploaded by this command.",
        "- No files have been sent by this command.",
        "- No publishing has occurred.",
        "- The operator must manually complete delivery through the agreed destination.",
        "",
        "Operator confirmation fields:",
        "- Delivery completed by: __________________",
        "- Delivery completed at: __________________",
        "- Client acknowledgment/reference: __________________",
        "- Notes: __________________",
        "",
    ])
    return "\n".join(lines)


def list_delivery_packages(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, _ = _load_job_and_events(job_id, jobs_dir_path)
    rows = []
    for package_id in _job_delivery_package_ids(job, jobs_dir_path):
        try:
            package = _read_delivery_package(job_id, package_id, jobs_dir_path)
        except DeliveryPackageError:
            continue
        rows.append({"package_id": package_id, "package_revision": package.get("package_revision", 0),
                     "deliverable_count": len(package.get("deliverables", [])),
                     "represented_run_ids": package.get("represented_run_ids", []),
                     "created_at": package.get("created_at", "")})
    return rows


def show_delivery_package(job_id: str, package_id: str, jobs_dir: str | Path | None = None) -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    package = _read_delivery_package(job_id, package_id, jobs_dir_path)
    return {"package_id": package.get("package_id", package_id), "job_id": package.get("job_id", job_id),
            "package_revision": package.get("package_revision", 0), "delivery_method": package.get("delivery_method", ""),
            "delivery_destination": package.get("delivery_destination", ""),
            "deliverable_count": len(package.get("deliverables", [])), "represented_run_ids": package.get("represented_run_ids", []),
            "summary": package.get("summary", {}),
            "deliverables": [{"deliverable_id": d.get("deliverable_id"), "filename": d.get("filename"),
                              "platform": d.get("platform"), "output_type": d.get("output_type"), "run_id": d.get("run_id"),
                              "delivery_sequence": d.get("delivery_sequence")} for d in package.get("deliverables", []) if isinstance(d, dict)]}


def read_delivery_checklist(job_id: str, package_id: str, jobs_dir: str | Path | None = None) -> str:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    path = _delivery_checklist_path(job_id, package_id, jobs_dir_path)
    if not path.exists():
        raise DeliveryPackageError(f"delivery checklist for package '{package_id}' not found for job '{job_id}'")
    return path.read_text(encoding="utf-8")


def _require_delivery_package_ready(job: dict, expected_count: int, target: str, *, jobs_dir: str | Path | None,
                                    intake_root: str | None, package_id: str | None = None) -> dict:
    job_id = job.get("job_id", "")
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    selected = package_id or job.get("active_delivery_package_id")
    if not isinstance(selected, str) or not selected:
        raise JobTransitionError(f"job '{job_id}' cannot transition to '{target}': no active delivery package is linked")
    package = _read_delivery_package(job_id, selected, jobs_dir_path)
    report = validate_delivery_package(package, job=job, jobs_dir=jobs_dir_path, intake_root=intake_root)
    if not report["valid"]:
        codes = ", ".join(issue["code"] for issue in report["issues"][:5])
        raise JobTransitionError(f"job '{job_id}' cannot transition to '{target}': delivery package '{selected}' is invalid; {codes}")
    count = len(package.get("deliverables", []))
    if count != expected_count:
        raise JobTransitionError(f"job '{job_id}' cannot transition to '{target}': count {expected_count} does not match delivery package count {count}")
    return package


def _require_delivery_confirmation_ready(job: dict, expected_count: int, target: str, *, jobs_dir: str | Path | None,
                                         intake_root: str | None, package_id: str | None = None) -> dict:
    job_id = job.get("job_id", "")
    package = _require_delivery_package_ready(job, expected_count, target, jobs_dir=jobs_dir, intake_root=intake_root,
                                              package_id=package_id)
    selected = package["package_id"]
    confirmation = job.get("delivery_confirmation") if isinstance(job.get("delivery_confirmation"), dict) else {}
    confirmed_package = confirmation.get("package_id")
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    confirmation_path = _delivery_confirmation_path(job_id, selected, jobs_dir_path)
    if confirmed_package != selected or not confirmation_path.exists():
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': delivery package '{selected}' has no recorded confirmation"
        )
    try:
        record = json.loads(confirmation_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': delivery confirmation for package '{selected}' is unreadable"
        ) from exc
    if not isinstance(record, dict) or record.get("schema_version") != DELIVERY_CONFIRMATION_SCHEMA_VERSION:
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': delivery confirmation for package '{selected}' is invalid"
        )
    if record.get("package_id") != selected or record.get("delivered_item_count") != expected_count:
        raise JobTransitionError(
            f"job '{job_id}' cannot transition to '{target}': delivery confirmation does not match package/count"
        )
    return package


def confirm_delivery(job_id: str, package_id: str, *, operator: str, confirmation: str, delivered_count: int,
                     jobs_dir: str | Path | None = None, expected_revision: int | None = None,
                     client_acknowledgment: str | None = None, notes: str | None = None,
                     intake_root: str | None = None, source: str = "pilot_job.delivery.confirm") -> dict:
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    record_path, events_path, job, events = _load_job_and_events(job_id, jobs_dir_path)
    current = job.get("current_state", "")
    if current != "DELIVERY_READY":
        raise DeliveryPackageError(f"job '{job_id}' must be DELIVERY_READY before delivery confirmation; current state is '{current}'")
    revision = _normalized_revision(job)
    if expected_revision is not None and expected_revision != revision:
        raise JobRevisionError(f"job '{job_id}' stale revision: expected {expected_revision}, current revision {revision}; no delivery confirmation recorded")
    if not operator or not operator.strip() or not confirmation or not confirmation.strip():
        raise DeliveryPackageError("operator and confirmation are required for delivery confirmation")
    if isinstance(delivered_count, bool) or not isinstance(delivered_count, int) or delivered_count <= 0:
        raise DeliveryPackageError("delivered_count must be a positive integer")
    _validate_metadata({"operator": operator, "confirmation": confirmation, "client_acknowledgment": client_acknowledgment or "", "notes": notes or ""})
    package = _require_delivery_package_ready(job, delivered_count, "DELIVERED", jobs_dir=jobs_dir_path, intake_root=intake_root, package_id=package_id)
    if package_id in job.get("delivered_package_ids", []):
        raise DeliveryPackageError(f"delivery package '{package_id}' has already been used for a delivery event")
    confirmation_path = _delivery_confirmation_path(job_id, package_id, jobs_dir_path)
    if confirmation_path.exists():
        raise DeliveryPackageError(f"delivery confirmation already exists for package '{package_id}'")
    now = _now_iso()
    record = {"schema_version": DELIVERY_CONFIRMATION_SCHEMA_VERSION, "job_id": job_id, "package_id": package_id,
              "operator": operator.strip(), "delivery_timestamp": now, "delivery_method": package.get("delivery_method"),
              "delivery_destination": package.get("delivery_destination"), "delivered_item_count": delivered_count,
              "confirmation_statement": confirmation.strip(), "client_acknowledgment_reference": client_acknowledgment,
              "notes": notes, "job_revision": revision, "package_revision": package.get("package_revision", 0)}
    sequence = _next_event_sequence(events)
    event = {"event_schema_version": EVENT_SCHEMA_VERSION, "event_id": _event_id(job_id, sequence, "DELIVERY_CONFIRMED"),
             "job_id": job_id, "sequence": sequence, "timestamp": now, "event_type": "DELIVERY_CONFIRMED",
             "previous_state": current, "new_state": current, "operator": operator.strip(),
             "message": confirmation.strip(), "metadata": {"package_id": package_id, "delivered_item_count": delivered_count,
                                                               "delivery_confirmation_path": str(confirmation_path)},
             "source": source, "related_codes": [], "artifact_references": [str(_delivery_package_path(job_id, package_id, jobs_dir_path)), str(confirmation_path)]}
    new_events = [*events, event]
    delivered_ids = [v for v in job.get("delivered_package_ids", []) if isinstance(v, str)] if isinstance(job.get("delivered_package_ids"), list) else []
    delivered_ids.append(package_id)
    updated_job = dict(job)
    updated_job.update({"current_state": current, "revision": revision + 1, "updated_at": now, "event_count": len(new_events),
                        "delivered_package_ids": sorted(set(delivered_ids)), "delivery_confirmation": {"package_id": package_id, "path": str(confirmation_path), "timestamp": now},
                        "latest_event": {"event_id": event["event_id"], "timestamp": now, "event_type": event["event_type"],
                                         "previous_state": current, "new_state": current, "message": event["message"]}})
    confirmation_path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write_json(confirmation_path, record)
    _atomic_write_json(events_path, new_events)
    _atomic_write_json(record_path, updated_job)
    return {"job": updated_job, "confirmation": record, "confirmation_path": str(confirmation_path)}


def _latest_pipeline_run_for_job(job: dict, jobs_dir: Path) -> dict | None:
    latest: dict | None = None
    for run_id in _job_run_ids(job, jobs_dir):
        try:
            run = _read_pipeline_run(job.get("job_id", ""), run_id, jobs_dir)
        except PipelineRunError:
            continue
        if latest is None or str(run.get("created_at", "")) >= str(latest.get("created_at", "")):
            latest = run
    return latest


def _delivery_readiness_for_job(job: dict, jobs_dir: Path, *, intake_root: str | None = None) -> dict:
    package_ids = _job_delivery_package_ids(job, jobs_dir)
    active_id = job.get("active_delivery_package_id") if isinstance(job.get("active_delivery_package_id"), str) else None
    active_valid = False
    delivery_ready = False
    represented_run_ids: list[str] = []
    issues: list[str] = []
    if active_id:
        try:
            package = _read_delivery_package(job.get("job_id", ""), active_id, jobs_dir)
            report = validate_delivery_package(package, job=job, jobs_dir=jobs_dir, intake_root=intake_root)
            active_valid = report["valid"]
            delivery_ready = bool(package.get("summary", {}).get("delivery_ready")) and active_valid
            represented_run_ids = [v for v in package.get("represented_run_ids", []) if isinstance(v, str)]
            issues = [issue["code"] for issue in report.get("issues", [])[:5]]
        except JobRecordError as exc:
            issues = [str(exc)]
    return {"package_count": len(package_ids), "active_package_id": active_id, "active_package_valid": active_valid,
            "delivery_ready": delivery_ready, "represented_run_ids": represented_run_ids, "issues": issues}


def pilot_readiness_report(job_id: str | None = None, *, jobs_dir: str | Path | None = None,
                           intake_root: str | None = None) -> dict:
    """Return a read-only operational readiness report for one or all jobs.

    This composes existing intake, run, output, and delivery records. It never
    writes records, executes commands, processes media, or creates directories.
    """
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    job_ids = [job_id] if job_id else [row["job_id"] for row in list_jobs(jobs_dir=jobs_dir_path)]
    rows: list[dict] = []
    for current_job_id in job_ids:
        _, _, job, _events = _load_job_and_events(current_job_id, jobs_dir_path)
        blockers: list[str] = []
        intake_status = {"structurally_valid": False, "source_ready": False, "rights_cleared": False,
                         "config_references_valid": False, "execution_ready": False, "issues": []}
        try:
            intake = _load_stored_intake(job)
            intake_report = validate_intake(intake, intake_root=intake_root, check_source=True, check_rights=True)
            intake_status = {"structurally_valid": intake_report["structurally_valid"],
                             "source_ready": intake_report["source_ready"],
                             "rights_cleared": intake_report["rights_cleared"],
                             "config_references_valid": intake_report["config_references_valid"],
                             "execution_ready": intake_report["execution_ready"],
                             "issues": intake_report["validation_codes"]}
            if not intake_report["rights_cleared"]:
                blockers.append("rights_not_cleared")
            if not intake_report["source_ready"]:
                blockers.append("source_not_ready")
            if not intake_report["config_references_valid"]:
                blockers.append("configuration_invalid")
        except JobRecordError as exc:
            intake_status["issues"] = [str(exc)]
            blockers.append("intake_unavailable")

        latest_run = _latest_pipeline_run_for_job(job, jobs_dir_path)
        latest_run_summary = None
        if latest_run:
            latest_run_summary = {"run_id": latest_run.get("run_id"), "status": latest_run.get("status"),
                                  "revision": _normalized_run_revision(latest_run), "entry_point": latest_run.get("entry_point"),
                                  "started_at": latest_run.get("started_at"), "completed_at": latest_run.get("completed_at")}
            if latest_run.get("status") in {"FAILED", "ABORTED"}:
                blockers.append(f"latest_run_{str(latest_run.get('status')).lower()}")

        try:
            outputs = output_summary(current_job_id, jobs_dir=jobs_dir_path, intake_root=intake_root)
        except JobRecordError as exc:
            outputs = {"manifest_count": 0, "review_complete": False, "eligible_for_approved": False,
                       "eligible_for_delivery_ready": False, "approved_delivery_included_count": 0,
                       "missing_file_count": 0, "invalid_reference_count": 0, "issues": [], "rights_issues": [str(exc)]}
        if outputs.get("manifest_count", 0) == 0 and job.get("current_state") in {"RUNNING", "REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY"}:
            blockers.append("no_output_manifests")
        if outputs.get("missing_file_count", 0):
            blockers.append("output_files_missing")
        if outputs.get("invalid_reference_count", 0):
            blockers.append("output_references_invalid")
        if job.get("current_state") in {"REVIEW_REQUIRED", "APPROVED", "DELIVERY_READY"} and not outputs.get("review_complete"):
            blockers.append("output_review_incomplete")

        delivery = _delivery_readiness_for_job(job, jobs_dir_path, intake_root=intake_root)
        if job.get("current_state") == "APPROVED" and not delivery["active_package_valid"]:
            blockers.append("delivery_package_missing_or_invalid")
        if job.get("current_state") == "DELIVERY_READY" and not job.get("delivery_confirmation"):
            blockers.append("delivery_confirmation_missing")

        rows.append({"job_id": current_job_id, "state": job.get("current_state", ""), "revision": _normalized_revision(job),
                     "allowed_next_states": allowed_next_states(job.get("current_state", "")),
                     "intake": intake_status, "latest_run": latest_run_summary,
                     "outputs": {"manifest_count": outputs.get("manifest_count", 0),
                                 "review_complete": outputs.get("review_complete", False),
                                 "approved_delivery_included_count": outputs.get("approved_delivery_included_count", 0),
                                 "missing_file_count": outputs.get("missing_file_count", 0),
                                 "invalid_reference_count": outputs.get("invalid_reference_count", 0),
                                 "eligible_for_approved": outputs.get("eligible_for_approved", False),
                                 "eligible_for_delivery_ready": outputs.get("eligible_for_delivery_ready", False)},
                     "delivery": delivery, "blockers": sorted(set(blockers))})
    return {"generated_at": _now_iso(), "job_count": len(rows), "jobs": rows}


def list_jobs(jobs_dir: str | Path | None = None) -> list[dict]:
    """List job records in the configured job-record root (read-only)."""
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    if not jobs_dir_path.exists():
        return []
    rows: list[dict] = []
    for path in sorted(jobs_dir_path.glob("*.json")):
        if path.name.endswith(".events.json"):
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows.append({
            "job_id": data.get("job_id", path.stem),
            "current_state": data.get("current_state", ""),
        })
    return rows


def show_job(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read-only, privacy-safe job summary for ``show``. Never exposes the
    intake's personal or confirmation fields."""
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else default_jobs_dir()
    _, _, job, events = _load_job_and_events(job_id, jobs_dir_path)
    latest = events[-1] if events else {}
    return {
        "job_id": job.get("job_id", job_id),
        "current_state": job.get("current_state", ""),
        "revision": _normalized_revision(job),
        "allowed_next_states": allowed_next_states(job.get("current_state", "")),
        "pilot_id": job.get("pilot_id", ""),
        "source_id": job.get("source_id", ""),
        "project_id": job.get("project_id", ""),
        "created_at": job.get("created_at", ""),
        "updated_at": job.get("updated_at", ""),
        "expected_output_root": job.get("expected_output_root", ""),
        "readiness_summary": job.get("readiness_summary", {}),
        "event_count": len(events),
        "latest_event_summary": latest.get("message", "") if isinstance(latest, dict) else "",
        "events": [
            {
                "sequence": event.get("sequence", index),
                "event_id": event.get("event_id", ""),
                "timestamp": event.get("timestamp", ""),
                "event_type": event.get("event_type", ""),
                "previous_state": event.get("previous_state"),
                "new_state": event.get("new_state"),
                "operator": event.get("operator"),
                "message": event.get("message", ""),
                "source": event.get("source", ""),
            }
            for index, event in enumerate(events, start=1)
        ],
    }
