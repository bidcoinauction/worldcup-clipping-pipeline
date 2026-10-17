"""Phase 7.3 — unified media catalog exporter.

Builds a **read-only**, versioned media catalog
(``stadium_signal.media_catalog/v1``) that unifies the Clipper engine's
runtime records — completed live recordings, processed artifacts, and reference
renders — into one stable list that Stadium Signal Lite can import and list.

Design constraints:

* **Read-only.** Nothing here writes to the archives it reads; the only write is
  the catalog file the caller asks for.
* **Runtime records, not filesystem scraping.** Assets are derived from the
  authoritative records the engine already writes: Phase 7/7.2 integration
  contracts (``*.contract.json``), live-capture session manifests
  (``*.manifest.json``), and managed pilot job records.
* **Stable asset IDs.** ``asset_id`` is a deterministic hash of the asset kind
  plus its provenance (job/session id and artifact/source checksum), so an
  unchanged asset keeps its ID across exports.
* **Local-first, secret-free.** No public URLs. The private source file path is
  never emitted; only its checksum and media facts are. OORT references stay
  private ``s3://`` values.

Public surface:

* :func:`asset_from_contract` / :func:`asset_from_session_manifest` /
  :func:`asset_from_job`.
* :func:`collect_assets` — load runtime records and build assets.
* :func:`build_catalog` — assemble + dedupe + validate a catalog envelope.
* :func:`validate_catalog` — structural/version/uniqueness checks.
* :func:`write_catalog` — atomic write.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
import re

# ── Identity ─────────────────────────────────────────────────────────────────

MEDIA_CATALOG_SCHEMA = "stadium_signal.media_catalog/v1"
MEDIA_CATALOG_VERSION = 1
MEDIA_CATALOG_PRODUCER = "worldcup-clipping-pipeline"
MEDIA_CATALOG_COMPONENT = "pipeline.media_catalog"
DEFAULT_REFERENCE_DEPLOYMENT = "stadium_signal_lite_local"

ASSET_TYPE_ARTIFACT = "processed_artifact"
ASSET_TYPE_RECORDING = "live_recording"
ASSET_TYPE_SOURCE = "registered_source"
ASSET_TYPES = (ASSET_TYPE_ARTIFACT, ASSET_TYPE_RECORDING, ASSET_TYPE_SOURCE)

# Playback visibility classes (task: local-only / private-remote / public).
VISIBILITY_LOCAL_ONLY = "local_only"
VISIBILITY_PRIVATE_REMOTE = "private_remote"
VISIBILITY_PUBLIC = "public"
VISIBILITIES = (VISIBILITY_LOCAL_ONLY, VISIBILITY_PRIVATE_REMOTE, VISIBILITY_PUBLIC)

# Processing states surfaced to the library UI.
PROCESSING_COMPLETE = "complete"
PROCESSING_IN_PROGRESS = "in_progress"
PROCESSING_FAILED = "failed"
PROCESSING_STATES = (PROCESSING_COMPLETE, PROCESSING_IN_PROGRESS, PROCESSING_FAILED)

_VIDEO_EXTENSIONS = {".mp4", ".m4v", ".mov", ".mkv", ".webm", ".ts", ".m2ts", ".avi"}
_AUDIO_EXTENSIONS = {".m4a", ".mp3", ".aac", ".wav", ".flac", ".ogg"}

_SPORT_HINTS = {
    "basketball": "basketball",
    "football": "football",
    "soccer": "football",
    "baseball": "baseball",
    "hockey": "hockey",
    "american_football": "american_football",
    "nfl": "american_football",
}

_PUBLIC_PATH_RE = re.compile(
    r"(?:^|[\s:=])/(?:Users|private|tmp|var|home|Volumes)/"
    r"|(?:^|[\s:=])[A-Za-z]:[\\/]"
)


class MediaCatalogError(Exception):
    """Raised for expected media-catalog failures."""


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


# ── Small derivations ────────────────────────────────────────────────────────


def stable_asset_id(asset_type: str, *parts: object) -> str:
    """Deterministic, stable asset id.

    Depends only on the asset kind and its provenance, so re-exporting an
    unchanged asset yields the same id.
    """
    material = "|".join(str(p) for p in parts if p not in (None, ""))
    digest = hashlib.sha256(f"{asset_type}|{material}".encode("utf-8")).hexdigest()[:24]
    return f"clipper:{asset_type}:{digest}"


def infer_container(path: str | None) -> str | None:
    if not path:
        return None
    suffix = Path(str(path)).suffix.lower().lstrip(".")
    return suffix or None


def media_kind_for(container: str | None) -> str:
    if not container:
        return "unknown"
    ext = f".{container}"
    if ext in _VIDEO_EXTENSIONS:
        return "video"
    if ext in _AUDIO_EXTENSIONS:
        return "audio"
    return "unknown"


def infer_sport(*hints: object) -> str | None:
    for hint in hints:
        if not hint:
            continue
        text = str(hint).strip().lower().replace("-", "_")
        for token, sport in _SPORT_HINTS.items():
            if token in text:
                return sport
    return None


def _norm(path: str | Path | None) -> str | None:
    if not path:
        return None
    return str(Path(str(path)).expanduser().absolute())


def _storage_summary(storage: dict | None) -> dict:
    storage = storage or {}
    status = storage.get("status") or "skipped"
    verified = bool(storage.get("verified"))
    return {
        "status": status,
        "provider": storage.get("provider"),
        "bucket": storage.get("bucket"),
        "region": storage.get("region"),
        "object_uri": storage.get("object_uri"),
        "sha256": storage.get("sha256"),
        "verified": verified,
    }


def _visibility_for(storage: dict) -> str:
    if storage.get("status") == "uploaded" and storage.get("verified"):
        return VISIBILITY_PRIVATE_REMOTE
    return VISIBILITY_LOCAL_ONLY


# ── Asset builders ───────────────────────────────────────────────────────────


def asset_from_contract(contract: dict) -> dict | None:
    """Build a catalog asset from a ``local_ingest`` contract. ``None`` if invalid."""
    from . import local_ingest as _li

    if _li.validate_integration_contract(contract):
        return None

    project = contract.get("project") or {}
    source = contract.get("source") or {}
    artifact = contract.get("artifact") or {}
    provenance = contract.get("provenance") or {}
    processing = contract.get("processing") or {}
    rights = contract.get("rights") or {}
    verification = contract.get("verification") or {}
    storage = _storage_summary(contract.get("storage"))

    artifact_path = artifact.get("path")
    container = infer_container(artifact_path)
    job_id = project.get("job_id") or provenance.get("job_id")
    artifact_sha = artifact.get("sha256") or provenance.get("artifact_sha256")
    source_sha = source.get("sha256") or provenance.get("source_sha256")

    render_status = processing.get("status")
    if render_status == "COMPLETE" and verification.get("verified"):
        state = PROCESSING_COMPLETE
    elif render_status == "FAILED":
        state = PROCESSING_FAILED
    elif render_status == "COMPLETE":
        state = PROCESSING_IN_PROGRESS
    else:
        state = PROCESSING_IN_PROGRESS

    filename = artifact.get("filename") or (Path(artifact_path).name if artifact_path else None)
    title = filename or source.get("original_filename") or job_id or "Untitled asset"

    return {
        "asset_id": stable_asset_id(ASSET_TYPE_ARTIFACT, job_id, artifact_sha or source_sha),
        "asset_type": ASSET_TYPE_ARTIFACT,
        "title": title,
        "sport": infer_sport(project.get("project_id"), project.get("reference_deployment")),
        "event_name": None,
        "event_date": None,
        "duration_seconds": artifact.get("duration_seconds") or source.get("duration_seconds"),
        "created_at": contract.get("generated_at"),
        "project": {
            "job_id": job_id,
            "project_id": project.get("project_id"),
            "reference_deployment": project.get("reference_deployment"),
            "current_state": project.get("current_state"),
        },
        "source": {
            "source_id": source.get("source_id"),
            "sha256": source_sha,
            "duration_seconds": source.get("duration_seconds"),
            "width": source.get("width"),
            "height": source.get("height"),
        },
        "artifact": {
            "path": _norm(artifact_path),
            "filename": filename,
            "sha256": artifact_sha,
            "size_bytes": artifact.get("size_bytes"),
            "duration_seconds": artifact.get("duration_seconds"),
        },
        "storage": storage,
        "processing": {
            "state": state,
            "operation": processing.get("operation"),
            "render_mode": processing.get("render_mode"),
            "status": render_status,
            "verified": bool(verification.get("verified")),
        },
        "playback": {
            "visibility": _visibility_for(storage),
            "previewable": bool(artifact_path),
            "media_kind": media_kind_for(container),
            "container": container,
        },
        "provenance": {
            "links_to_source": bool(provenance.get("links_to_source")),
            "source_sha256": source_sha,
            "artifact_sha256": artifact_sha,
            "job_id": job_id,
        },
        "rights": {"status": rights.get("status"), "cleared": rights.get("cleared")},
    }


def asset_from_session_manifest(manifest: dict) -> dict | None:
    """Build a catalog asset from a live-capture session manifest.

    A session that produced a recorded file becomes a ``live_recording`` asset;
    a failed session is still surfaced so the library can show a failed state.
    A session with neither is skipped.
    """
    if not isinstance(manifest, dict):
        return None
    source = manifest.get("source") or {}
    recording = manifest.get("recording")
    session_state = manifest.get("state")
    match_id = manifest.get("match_id")
    session_id = manifest.get("session_id") or match_id

    if recording:
        artifact_path = recording.get("path")
        container = infer_container(artifact_path)
        artifact_sha = recording.get("sha256")
        state = PROCESSING_COMPLETE if session_state in ("assembled", "registered") else PROCESSING_IN_PROGRESS
        title = f"{match_id or 'recording'} (live recording)"
    elif session_state == "failed":
        artifact_path, container, artifact_sha = None, None, None
        state = PROCESSING_FAILED
        title = f"{match_id or session_id or 'recording'} (live recording)"
    else:
        return None

    return {
        "asset_id": stable_asset_id(ASSET_TYPE_RECORDING, session_id, artifact_sha or session_id),
        "asset_type": ASSET_TYPE_RECORDING,
        "title": title,
        "sport": infer_sport(match_id),
        "event_name": None,
        "event_date": None,
        "duration_seconds": (recording or {}).get("duration_seconds"),
        "created_at": manifest.get("started_at"),
        "project": {
            "job_id": None,
            "project_id": None,
            "reference_deployment": None,
            "current_state": session_state,
        },
        "source": {
            "source_id": None,
            "sha256": None,
            "duration_seconds": None,
            "width": None,
            "height": None,
        },
        "artifact": {
            "path": _norm(artifact_path),
            "filename": (recording or {}).get("filename"),
            "sha256": artifact_sha,
            "size_bytes": (recording or {}).get("size_bytes"),
            "duration_seconds": (recording or {}).get("duration_seconds"),
        },
        "storage": _storage_summary(None),
        "processing": {
            "state": state,
            "operation": "live_capture_assembly",
            "render_mode": None,
            "status": session_state,
            "verified": bool(artifact_sha),
        },
        "playback": {
            "visibility": VISIBILITY_LOCAL_ONLY,
            "previewable": bool(artifact_path),
            "media_kind": media_kind_for(container),
            "container": container,
        },
        "provenance": {
            "links_to_source": bool(artifact_sha),
            "source_sha256": None,
            "artifact_sha256": artifact_sha,
            "job_id": None,
        },
        "rights": {"status": None, "cleared": None},
    }


def asset_from_job(job: dict) -> dict | None:
    """Build a minimal ``registered_source`` asset from a pilot job record."""
    if not isinstance(job, dict) or not job.get("job_id"):
        return None
    job_id = job["job_id"]
    state = job.get("current_state") or ""
    processing = PROCESSING_COMPLETE if state in ("DELIVERED", "COMPLETE") else PROCESSING_IN_PROGRESS
    readiness = job.get("readiness_summary") or {}
    return {
        "asset_id": stable_asset_id(ASSET_TYPE_SOURCE, job_id),
        "asset_type": ASSET_TYPE_SOURCE,
        "title": f"{job_id} (registered source)",
        "sport": infer_sport(job.get("project_id")),
        "event_name": None,
        "event_date": None,
        "duration_seconds": None,
        "created_at": job.get("created_at"),
        "project": {
            "job_id": job_id,
            "project_id": job.get("project_id"),
            "reference_deployment": job.get("reference_deployment"),
            "current_state": state,
        },
        "source": {"source_id": job.get("source_id") or None, "sha256": None, "duration_seconds": None, "width": None, "height": None},
        "artifact": {"path": None, "filename": None, "sha256": None, "size_bytes": None, "duration_seconds": None},
        "storage": _storage_summary(None),
        "processing": {
            "state": processing,
            "operation": "source_registration",
            "render_mode": None,
            "status": state,
            "verified": False,
        },
        "playback": {
            "visibility": VISIBILITY_LOCAL_ONLY,
            "previewable": False,
            "media_kind": "unknown",
            "container": None,
        },
        "provenance": {"links_to_source": False, "source_sha256": None, "artifact_sha256": None, "job_id": job_id},
        "rights": {"status": readiness.get("rights_status"), "cleared": readiness.get("rights_cleared")},
    }


# ── Record loading ───────────────────────────────────────────────────────────


def _load_contracts(contracts, contracts_dir):
    found = list(contracts or [])
    if contracts_dir:
        directory = Path(contracts_dir)
        if directory.is_dir():
            for path in sorted(directory.glob("*.contract.json")):
                try:
                    found.append(json.loads(path.read_text(encoding="utf-8")))
                except (json.JSONDecodeError, OSError):
                    continue
    assets = []
    for contract in found:
        asset = asset_from_contract(contract)
        if asset is not None:
            assets.append(asset)
    return assets


def _load_sessions(sessions, sessions_dir):
    from . import live_capture as _lc

    found = list(sessions or [])
    if sessions_dir:
        directory = Path(sessions_dir)
        if directory.is_dir():
            for path in sorted(directory.glob("*.manifest.json")):
                manifest = _lc.load_manifest(path)
                if manifest is not None:
                    found.append(manifest)
    assets = []
    for manifest in found:
        asset = asset_from_session_manifest(manifest)
        if asset is not None:
            assets.append(asset)
    return assets


def _load_jobs(jobs, jobs_dir):
    found = list(jobs or [])
    if jobs_dir:
        from . import pilot as _pilot

        for entry in _pilot.list_jobs(jobs_dir):
            job_id = entry.get("job_id") if isinstance(entry, dict) else None
            if not job_id:
                continue
            # ``list_jobs`` returns a summary; read the full record so the
            # catalog carries project/rights/created_at facts when available.
            try:
                found.append(_pilot.read_job(job_id, jobs_dir=jobs_dir))
            except _pilot.JobRecordError:
                continue
    assets = []
    for job in found:
        asset = asset_from_job(job)
        if asset is not None:
            assets.append(asset)
    return assets


def collect_assets(
    *,
    contracts=None,
    contracts_dir=None,
    sessions=None,
    sessions_dir=None,
    jobs=None,
    jobs_dir=None,
) -> list[dict]:
    """Load runtime records and return deduplicated assets (by ``asset_id``)."""
    assets = [
        *_load_contracts(contracts, contracts_dir),
        *_load_sessions(sessions, sessions_dir),
        *_load_jobs(jobs, jobs_dir),
    ]
    by_id: dict[str, dict] = {}
    for asset in assets:
        by_id.setdefault(asset["asset_id"], asset)
    return sorted(by_id.values(), key=lambda a: (a.get("asset_type", ""), a.get("asset_id", "")))


# ── Catalog ──────────────────────────────────────────────────────────────────


def build_catalog(
    assets: list[dict],
    *,
    reference_deployment: str = DEFAULT_REFERENCE_DEPLOYMENT,
    generated_at: str | None = None,
) -> dict:
    by_id: dict[str, dict] = {}
    for asset in assets:
        by_id.setdefault(asset["asset_id"], asset)
    ordered = sorted(by_id.values(), key=lambda a: (a.get("asset_type", ""), a.get("asset_id", "")))
    catalog = {
        "catalog_schema": MEDIA_CATALOG_SCHEMA,
        "catalog_version": MEDIA_CATALOG_VERSION,
        "generated_at": generated_at or _now_iso(),
        "producer": {"application": MEDIA_CATALOG_PRODUCER, "component": MEDIA_CATALOG_COMPONENT},
        "reference_deployment": reference_deployment,
        "asset_count": len(ordered),
        "assets": ordered,
    }
    errors = validate_catalog(catalog)
    if errors:
        raise MediaCatalogError("built catalog is invalid: " + "; ".join(errors))
    return catalog


def _public_scalar(value: object):
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if "://" in candidate or _PUBLIC_PATH_RE.search(candidate):
        return None
    return value


def _public_filename(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    # Keep a display basename without publishing the producer's directory.
    filename = value.replace("\\", "/").rsplit("/", 1)[-1]
    return _public_scalar(filename)


def build_public_catalog(catalog: dict) -> dict:
    """Project a catalog for explicit public metadata publication.

    This is metadata-only: local paths, private storage references, and
    playback sources are intentionally removed. Playback authorization remains
    a separate future concern and is never implied by this catalog.
    """
    errors = validate_catalog(catalog)
    if errors:
        raise MediaCatalogError("cannot publish an invalid catalog: " + "; ".join(errors))

    public_assets = []
    for asset in catalog["assets"]:
        project = asset.get("project") or {}
        source = asset.get("source") or {}
        artifact = asset.get("artifact") or {}
        storage = asset.get("storage") or {}
        processing = asset.get("processing") or {}
        playback = asset.get("playback") or {}
        provenance = asset.get("provenance") or {}
        rights = asset.get("rights") or {}
        public_assets.append({
            "asset_id": _public_scalar(asset.get("asset_id")),
            "asset_type": _public_scalar(asset.get("asset_type")),
            "title": _public_scalar(asset.get("title")) or "Untitled asset",
            "sport": _public_scalar(asset.get("sport")),
            "event_name": _public_scalar(asset.get("event_name")),
            "event_date": _public_scalar(asset.get("event_date")),
            "duration_seconds": asset.get("duration_seconds"),
            "created_at": _public_scalar(asset.get("created_at")),
            "project": {
                "job_id": _public_scalar(project.get("job_id")),
                "project_id": _public_scalar(project.get("project_id")),
                "reference_deployment": _public_scalar(project.get("reference_deployment")),
                "current_state": _public_scalar(project.get("current_state")),
            },
            "source": {
                "source_id": _public_scalar(source.get("source_id")),
                "sha256": _public_scalar(source.get("sha256")),
                "duration_seconds": source.get("duration_seconds"),
                "width": source.get("width"),
                "height": source.get("height"),
            },
            "artifact": {
                "path": None,
                "filename": _public_filename(artifact.get("filename")),
                "sha256": _public_scalar(artifact.get("sha256")),
                "size_bytes": artifact.get("size_bytes"),
                "duration_seconds": artifact.get("duration_seconds"),
            },
            "storage": {
                "status": _public_scalar(storage.get("status")),
                "provider": _public_scalar(storage.get("provider")),
                "sha256": _public_scalar(storage.get("sha256")),
                "verified": bool(storage.get("verified")),
            },
            "processing": {
                "state": _public_scalar(processing.get("state")),
                "operation": _public_scalar(processing.get("operation")),
                "render_mode": _public_scalar(processing.get("render_mode")),
                "status": _public_scalar(processing.get("status")),
                "verified": bool(processing.get("verified")),
            },
            "playback": {
                "visibility": _public_scalar(playback.get("visibility")),
                "previewable": False,
                "media_kind": _public_scalar(playback.get("media_kind")),
                "container": _public_scalar(playback.get("container")),
            },
            "provenance": {
                "links_to_source": bool(provenance.get("links_to_source")),
                "source_sha256": _public_scalar(provenance.get("source_sha256")),
                "artifact_sha256": _public_scalar(provenance.get("artifact_sha256")),
                "job_id": _public_scalar(provenance.get("job_id")),
            },
            "rights": {
                "status": _public_scalar(rights.get("status")),
                "cleared": rights.get("cleared") if isinstance(rights.get("cleared"), bool) else None,
            },
        })

    public = {
        "catalog_schema": catalog["catalog_schema"],
        "catalog_version": catalog["catalog_version"],
        "generated_at": _public_scalar(catalog.get("generated_at")),
        "producer": catalog.get("producer"),
        "reference_deployment": _public_scalar(catalog.get("reference_deployment")),
        "publication": {
            "visibility": "public_metadata",
            "playback_authorized": False,
            "source": "explicit_operator_export",
        },
        "asset_count": len(public_assets),
        "assets": public_assets,
    }
    errors = validate_catalog(public)
    if errors:
        raise MediaCatalogError("public catalog is invalid: " + "; ".join(errors))
    return public


_REQUIRED_TOP = (
    "catalog_schema", "catalog_version", "generated_at", "producer",
    "reference_deployment", "asset_count", "assets",
)
_REQUIRED_ASSET = (
    "asset_id", "asset_type", "title", "project", "source", "artifact",
    "storage", "processing", "playback", "provenance", "rights",
)


def validate_catalog(data: object) -> list[str]:
    """Validate a catalog envelope. Returns a list of error strings (empty = ok)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["catalog root must be an object"]
    for key in _REQUIRED_TOP:
        if key not in data:
            errors.append(f"missing required key: {key}")
    if data.get("catalog_schema") != MEDIA_CATALOG_SCHEMA:
        errors.append(f"unsupported catalog_schema: {data.get('catalog_schema')!r}")
    if data.get("catalog_version") != MEDIA_CATALOG_VERSION:
        errors.append(f"unsupported catalog_version: {data.get('catalog_version')!r}")
    if errors:
        return errors

    assets = data.get("assets")
    if not isinstance(assets, list):
        return ["assets must be a list"]
    if data.get("asset_count") != len(assets):
        errors.append(f"asset_count ({data.get('asset_count')}) does not match assets ({len(assets)})")

    seen: set[str] = set()
    for i, asset in enumerate(assets):
        prefix = f"assets[{i}]"
        if not isinstance(asset, dict):
            errors.append(f"{prefix} must be an object")
            continue
        for key in _REQUIRED_ASSET:
            if key not in asset:
                errors.append(f"{prefix}.{key} is required")
        asset_id = asset.get("asset_id")
        if not isinstance(asset_id, str) or not asset_id:
            errors.append(f"{prefix}.asset_id must be a non-empty string")
        elif asset_id in seen:
            errors.append(f"{prefix}.asset_id is a duplicate: {asset_id}")
        else:
            seen.add(asset_id)
        if asset.get("asset_type") not in ASSET_TYPES:
            errors.append(f"{prefix}.asset_type must be one of {list(ASSET_TYPES)}")
        playback = asset.get("playback")
        if not isinstance(playback, dict) or playback.get("visibility") not in VISIBILITIES:
            errors.append(f"{prefix}.playback.visibility must be one of {list(VISIBILITIES)}")
        processing = asset.get("processing")
        if not isinstance(processing, dict) or processing.get("state") not in PROCESSING_STATES:
            errors.append(f"{prefix}.processing.state must be one of {list(PROCESSING_STATES)}")
        source = asset.get("source")
        if isinstance(source, dict) and source.get("local_file_path"):
            errors.append(f"{prefix}.source must not expose local_file_path (private source path)")
    return errors


def write_catalog(path: str | Path, catalog: dict) -> Path:
    """Atomically write a catalog (tmp + ``os.replace``)."""
    errors = validate_catalog(catalog)
    if errors:
        raise MediaCatalogError("refusing to write an invalid catalog: " + "; ".join(errors))
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(catalog, indent=2))
        os.replace(tmp_name, str(target))
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return target


def write_public_catalog(path: str | Path, catalog: dict) -> Path:
    """Write an explicit public metadata projection atomically."""
    return write_catalog(path, build_public_catalog(catalog))
