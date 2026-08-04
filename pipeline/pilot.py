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
import re
import shutil
import subprocess
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
        _require_non_empty(metadata, "delivery_method", job_id, current, target)
        _require_non_empty(metadata, "delivery_destination", job_id, current, target)
        deliverable_count = _require_positive_int(metadata, "deliverable_count", job_id, current, target)
        if not artifacts:
            artifacts = [metadata["delivery_destination"]]
        report = _require_intake_readiness(job, target, intake_root=intake_root)
        _require_output_readiness(job_id, deliverable_count, target, intake_root=intake_root, jobs_dir=jobs_dir)
        return report, artifacts

    if target == "DELIVERED":
        _require_non_empty(metadata, "operator", job_id, current, target)
        _require_non_empty(metadata, "confirmation", job_id, current, target)
        _require_non_empty(metadata, "delivery_destination", job_id, current, target)
        delivered_count = _require_positive_int(metadata, "delivered_item_count", job_id, current, target)
        metadata["delivery_timestamp"] = _now_iso()
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


# ── Output manifests ─────────────────────────────────────────────────────────

_OUTPUT_MANIFEST_KEYS = (
    "schema_version", "manifest_id", "job_id", "pilot_id", "project_id", "source_id",
    "created_at", "created_by", "source_clip_manifest_path", "revision", "outputs",
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
