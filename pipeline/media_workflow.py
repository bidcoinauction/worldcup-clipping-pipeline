"""Phase 7.4 — end-to-end media workflow orchestrator.

One operator entry point that drives the existing Phase 7 services from an
authorized local media source to a verified local preview in Stadium Signal
Lite:

```text
authorized local / synthetic source
  -> register (pilot job)              [pipeline.local_ingest]
  -> EDL + reference render + verify   [pipeline.local_ingest / pipeline.rendering]
  -> optional OORT upload (verified)   [pipeline.integration_service]
  -> contract persisted                [pipeline.local_ingest]
  -> unified media catalog             [pipeline.media_catalog]
  -> Lite import + local playback      [stadium-signal-lite tools/verify_library.py]
  -> durable workflow status           [this module]
```

This module owns **orchestration only**. It reuses the existing services and
never re-implements registration, rendering, storage, catalog building, or
preview serving. It is local-first: no worker API, no database, and uploads
remain opt-in.

Reliability guarantees:

* **Idempotent reruns** — when the target contract already exists and its
  artifact + source verify, the expensive stages are skipped (``reuse``).
* **Resume after interruption** — ingest runs with ``resume=True`` so a prior
  job that never emitted a contract is recovered instead of raising.
* **Durable status** — every run writes a versioned status document.
* **Duplicate suppression** — the catalog dedupes by stable ``asset_id``.
* **Local preservation** — a storage failure never deletes the local artifact.

Public surface:

* :func:`run_media_workflow`
* :func:`verify_contract_artifact`
* :func:`default_contracts_dir` / :func:`default_catalog_path` /
  :func:`workflow_status_path`
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from . import local_ingest as _li
from . import media_catalog as _mc
from . import pilot as _pilot
from . import integration_service as _storage

MEDIA_WORKFLOW_SCHEMA = "stadium_signal.media_workflow/v1"
MEDIA_WORKFLOW_VERSION = 1
MEDIA_WORKFLOW_PRODUCER = "worldcup-clipping-pipeline"
MEDIA_WORKFLOW_COMPONENT = "pipeline.media_workflow"

LITE_ROOT_ENV = "STADIUM_SIGNAL_LITE_ROOT"
LITE_VERIFY_SCRIPT = Path("tools") / "verify_library.py"

STAGE_REAL = "real"
STAGE_UNAVAILABLE = "unavailable"
STAGE_FAILED = "failed"

_SAFE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


class MediaWorkflowError(Exception):
    """Raised for expected end-to-end workflow failures."""


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _resolve_jobs_dir(jobs_dir: str | Path | None) -> Path:
    return Path(jobs_dir) if jobs_dir is not None else _pilot.default_jobs_dir()


def _require_safe_id(value: object, *, name: str) -> str:
    if not isinstance(value, str) or not _SAFE_ID_RE.fullmatch(value):
        raise MediaWorkflowError(f"{name} must contain only letters, numbers, '_' or '-'")
    return value


def default_contracts_dir(jobs_dir: str | Path | None = None) -> Path:
    """Directory the catalog reads (``*.contract.json``) — ``<jobs>/contracts``."""
    return _resolve_jobs_dir(jobs_dir) / "contracts"


def default_catalog_path(jobs_dir: str | Path | None = None) -> Path:
    """Default catalog output — ``<jobs>/library.catalog.json``."""
    return _resolve_jobs_dir(jobs_dir) / "library.catalog.json"


def default_workflow_status_dir(jobs_dir: str | Path | None = None) -> Path:
    return _resolve_jobs_dir(jobs_dir) / "workflow"


def workflow_status_path(
    pilot_id: str,
    jobs_dir: str | Path | None = None,
    *,
    source_id: str,
) -> Path:
    """Durable per-pilot/source workflow status document."""
    _require_safe_id(pilot_id, name="pilot_id")
    _require_safe_id(source_id, name="source_id")
    return default_workflow_status_dir(jobs_dir) / f"{pilot_id}_{source_id}.workflow.json"


def _write_json_atomic(path: str | Path, data: dict) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(target.parent), prefix=target.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, str(target))
    finally:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
    return target


def _request_facts(
    source_path: Path,
    *,
    source_id: str | None,
    project: str,
    reference_deployment: str | None,
    story_id: str,
    fmt: str,
    source_start: float,
    source_end: float,
    upload: bool,
    require_storage: bool,
    storage_bucket: str | None,
    storage_object_prefix: str,
    storage_class: str | None,
    storage_read_back: bool,
    storage_adapter: object | None,
) -> dict:
    effective_endpoint = None
    effective_region = None
    effective_bucket = storage_bucket
    if upload:
        adapter = storage_adapter or _storage.build_storage_adapter()
        config = getattr(adapter, "config", None)
        if config is not None:
            effective_endpoint = getattr(config, "endpoint", None)
            effective_region = getattr(config, "region", None)
            effective_bucket = storage_bucket or getattr(config, "bucket", None)
    return {
        "source_filename": source_path.name,
        "source_id": source_id or source_path.stem,
        "project": project,
        "reference_deployment": reference_deployment,
        "story_id": story_id,
        "format": fmt,
        "source_start": source_start,
        "source_end": source_end,
        "upload": upload,
        "require_storage": require_storage,
        "storage_bucket": storage_bucket,
        "storage_object_prefix": storage_object_prefix,
        "storage_class": storage_class,
        "storage_read_back": storage_read_back,
        "storage_endpoint": effective_endpoint,
        "storage_region": effective_region,
        "storage_effective_bucket": effective_bucket,
    }


def _validate_request(
    source_path: Path,
    *,
    pilot_id: str,
    source_id: str | None,
    project: str,
    reference_deployment: str | None,
    intake_root: str | None,
    source_start: float,
    source_end: float,
    upload: bool,
    require_storage: bool,
) -> None:
    """Validate operator inputs without invoking ffprobe, FFmpeg, or storage."""
    _require_safe_id(pilot_id, name="pilot_id")
    resolved_source_id = source_id or source_path.stem
    _require_safe_id(resolved_source_id, name="source_id")
    if not source_path.is_file():
        raise MediaWorkflowError(f"source file not found: {source_path}")
    if not math.isfinite(source_start) or not math.isfinite(source_end):
        raise MediaWorkflowError("source range values must be finite")
    if source_start < 0 or source_end <= source_start:
        raise MediaWorkflowError("source range must satisfy 0 <= start < end")
    if require_storage and not upload:
        raise MediaWorkflowError("require_storage requires upload=True (CLI: --require-storage requires --upload)")

    intake = _li.build_test_intake(
        source_path,
        pilot_id=pilot_id,
        source_id=resolved_source_id,
        reference_deployment=reference_deployment,
        project=project,
        duration_seconds=None,
    )
    validation = _pilot.validate_intake(intake, intake_root=intake_root)
    if not validation.get("execution_ready"):
        issues = validation.get("issues") or []
        detail = "; ".join(
            f"{issue.get('path')}: {issue.get('message')}" for issue in issues[:5]
            if isinstance(issue, dict)
        ) or "intake is not execution-ready"
        raise MediaWorkflowError(f"workflow request failed intake validation: {detail}")

    try:
        facts = _li.probe_media(source_path)
    except _li.LocalIngestError as exc:
        raise MediaWorkflowError(f"source media probe failed: {exc}") from exc
    duration = facts.get("duration_seconds")
    if isinstance(duration, (int, float)) and source_end > float(duration) + 0.05:
        raise MediaWorkflowError(
            f"source range ends at {source_end}s but media duration is {duration}s"
        )


def _load_json_object(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _contract_matches_request(contract: dict, prior_status: dict | None, request: dict, source_path: Path) -> bool:
    """Require an exact Phase 7.4 request match before reusing a contract."""
    if prior_status is None or prior_status.get("request") != request:
        return False
    source = contract.get("source") or {}
    project = contract.get("project") or {}
    processing = contract.get("processing") or {}
    try:
        same_path = Path(str(source.get("local_file_path"))).expanduser().resolve() == source_path.expanduser().resolve()
    except (OSError, RuntimeError, TypeError):
        return False
    return bool(
        same_path
        and source.get("source_id") == request["source_id"]
        and project.get("project_id") == request["project"]
        and project.get("reference_deployment") == request["reference_deployment"]
        and processing.get("story_id") == request["story_id"]
        and processing.get("format") == request["format"]
    )


def verify_contract_artifact(contract: dict, *, jobs_dir: str | Path | None = None) -> dict:
    """Re-verify a persisted contract: artifact/source exist and checksums match.

    Read-only and FFmpeg-free; it compares SHA-256 digests rather than probing
    durations, so it is fast and safe to run on every rerun.
    """
    artifact = contract.get("artifact") or {}
    source = contract.get("source") or {}
    a_path = artifact.get("path")
    s_path = source.get("local_file_path")

    artifact_exists = bool(a_path) and Path(str(a_path)).exists() and Path(str(a_path)).stat().st_size > 0
    artifact_sha_ok = bool(artifact_exists and artifact.get("sha256")) and (
        _li.sha256_file(a_path) == artifact.get("sha256")
    )
    source_exists = bool(s_path) and Path(str(s_path)).exists()
    source_sha_ok = bool(source_exists and source.get("sha256")) and (
        _li.sha256_file(s_path) == source.get("sha256")
    )
    verified = bool(contract.get("verification", {}).get("verified") and artifact_sha_ok and source_sha_ok)
    return {
        "verified": verified,
        "artifact_exists": bool(artifact_exists),
        "artifact_sha256_matches": bool(artifact_sha_ok),
        "source_exists": bool(source_exists),
        "source_sha256_matches": bool(source_sha_ok),
    }


def build_catalog_from_runtime(
    *,
    contracts_dir: str | Path,
    sessions_dir: str | Path | None = None,
    jobs_dir: str | Path | None = None,
    reference_deployment: str | None = _mc.DEFAULT_REFERENCE_DEPLOYMENT,
    out_path: str | Path | None = None,
) -> dict:
    """Build (and optionally write) the unified catalog from runtime records."""
    assets = _mc.collect_assets(
        contracts_dir=contracts_dir,
        sessions_dir=sessions_dir,
        jobs_dir=jobs_dir,
    )
    catalog = _mc.build_catalog(assets, reference_deployment=reference_deployment)
    if out_path is not None:
        _mc.write_catalog(out_path, catalog)
    return catalog


def _resolve_lite_root(lite_root: str | Path | None) -> Path | None:
    raw = lite_root or os.environ.get(LITE_ROOT_ENV)
    if not raw:
        return None
    root = Path(raw).expanduser()
    return root


def verify_with_lite(
    *,
    catalog_path: str | Path,
    asset_id: str,
    lite_root: str | Path | None = None,
    lite_python: str | None = None,
    preview_roots: list[str] | None = None,
    preview_enabled: bool = False,
    timeout: float = 120.0,
) -> dict:
    """Run Stadium Signal Lite's verification tool against the built catalog.

    Executes ``tools/verify_library.py`` in the Lite repository as a subprocess
    (Lite owns its own import/preview policy). Returns a structured result;
    never raises for a failed check — the failure is reported in the payload.
    """
    root = _resolve_lite_root(lite_root)
    if root is None:
        return {
            "ran": False,
            "ok": False,
            "reason": f"lite_root_not_configured (set --lite-root or {LITE_ROOT_ENV})",
        }
    script = root / LITE_VERIFY_SCRIPT
    if not script.is_file():
        return {"ran": False, "ok": False, "reason": f"verify tool not found: {script}"}

    python = lite_python or sys.executable
    cmd = [
        python, str(script),
        "--catalog", str(catalog_path),
        "--expect-asset-id", asset_id,
        "--json",
    ]
    if preview_roots:
        cmd += ["--preview-roots", os.pathsep.join(str(p) for p in preview_roots)]
    if preview_enabled:
        cmd.append("--preview-enabled")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ran": True, "ok": False, "reason": f"verify tool failed to run: {exc}"}

    payload: dict | None = None
    for line in reversed((proc.stdout or "").splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                payload = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
    if payload is None:
        return {
            "ran": True,
            "ok": False,
            "reason": "verify tool produced no JSON report",
            "exit_code": proc.returncode,
            "stderr": (proc.stderr or "").strip()[:2000],
        }
    payload["ran"] = True
    payload["exit_code"] = proc.returncode
    payload["ok"] = bool(payload.get("ok") and proc.returncode == 0)
    return payload


def _stage(stage: str, status: str, detail: str) -> dict:
    return {"stage": stage, "status": status, "detail": detail}


def _persist_failure(
    *,
    pilot_id: str,
    source_path: Path,
    jobs_root: Path,
    contract_path: Path,
    catalog_path: Path,
    request: dict,
    stages: list[dict],
    stage: str,
    error: Exception,
    write_status: bool,
) -> None:
    if not write_status:
        return
    failed_stages = [*stages, _stage(stage, STAGE_FAILED, str(error))]
    report = _build_report(
        pilot_id=pilot_id,
        source_path=source_path,
        jobs_root=jobs_root,
        contract_path=contract_path,
        catalog_path=catalog_path,
        contract=None,
        reused=False,
        resumed=False,
        stages=failed_stages,
        catalog=None,
        library=None,
        storage=None,
        dry_run=False,
        request=request,
    )
    report["outcome"] = "failed"
    report["error"] = {"type": type(error).__name__, "message": str(error)}
    report["recommendations"] = ["Correct the failed stage and retry the same command; local media was not deleted."]
    try:
        _write_json_atomic(
            workflow_status_path(pilot_id, jobs_root, source_id=request["source_id"]),
            report,
        )
    except OSError:
        pass  # Do not replace the original workflow failure with a status-write error.


def run_media_workflow(
    source_path: str | Path,
    *,
    pilot_id: str,
    source_id: str | None = None,
    jobs_dir: str | Path | None = None,
    contracts_dir: str | Path | None = None,
    catalog_out: str | Path | None = None,
    sessions_dir: str | Path | None = None,
    reference_deployment: str | None = _mc.DEFAULT_REFERENCE_DEPLOYMENT,
    intake_root: str | None = None,
    operator: str | None = None,
    project: str = _li.DEFAULT_TEST_PROJECT,
    story_id: str = _li.DEFAULT_STORY_ID,
    fmt: str = _li.DEFAULT_FORMAT,
    source_start: float = 0.0,
    source_end: float = 2.0,
    dry_run: bool = False,
    reuse: bool = True,
    upload: bool = False,
    storage_adapter: "_li._integration.OORTAdapter | None" = None,
    storage_bucket: str | None = None,
    storage_object_prefix: str = _li.DEFAULT_STORAGE_OBJECT_PREFIX,
    storage_class: str | None = None,
    storage_read_back: bool = True,
    require_storage: bool = False,
    lite_root: str | Path | None = None,
    lite_python: str | None = None,
    preview_roots: list[str] | None = None,
    preview_enabled: bool = False,
    verify_library: bool = True,
    write_status: bool = True,
) -> dict:
    """Drive one source through the full Phase 7 workflow.

    Returns a versioned status/report dict (also written to disk unless
    ``write_status`` is false). Raises :class:`MediaWorkflowError` for expected
    failures with actionable guidance.
    """
    resolved_source = Path(source_path).expanduser()
    jobs_root = _resolve_jobs_dir(jobs_dir)
    c_dir = Path(contracts_dir) if contracts_dir else default_contracts_dir(jobs_root)
    catalog_path = Path(catalog_out) if catalog_out else default_catalog_path(jobs_root)
    _require_safe_id(pilot_id, name="pilot_id")
    resolved_source_id = source_id or resolved_source.stem
    _require_safe_id(resolved_source_id, name="source_id")
    runtime_id = f"{pilot_id}_{resolved_source_id}"
    contract_path = c_dir / f"{runtime_id}.contract.json"
    request = _request_facts(
        resolved_source,
        source_id=source_id,
        project=project,
        reference_deployment=reference_deployment,
        story_id=story_id,
        fmt=fmt,
        source_start=source_start,
        source_end=source_end,
        upload=upload,
        require_storage=require_storage,
        storage_bucket=storage_bucket,
        storage_object_prefix=storage_object_prefix,
        storage_class=storage_class,
        storage_read_back=storage_read_back,
        storage_adapter=storage_adapter,
    )

    stages: list[dict] = []
    ingest: dict | None = None
    reused = False

    try:
        _validate_request(
            resolved_source,
            pilot_id=pilot_id,
            source_id=source_id,
            project=project,
            reference_deployment=reference_deployment,
            intake_root=intake_root,
            source_start=source_start,
            source_end=source_end,
            upload=upload,
            require_storage=require_storage,
        )
    except MediaWorkflowError as exc:
        _persist_failure(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, request=request,
            stages=stages, stage="request_validation", error=exc, write_status=write_status,
        )
        raise

    existing = None
    prior_status = _load_json_object(
        workflow_status_path(pilot_id, jobs_root, source_id=resolved_source_id)
    )
    if reuse and contract_path.is_file():
        existing = _load_json_object(contract_path)
    try:
        if (
            existing is not None
            and not _li.validate_integration_contract(existing)
            and _contract_matches_request(existing, prior_status, request, resolved_source)
        ):
            check = verify_contract_artifact(existing, jobs_dir=jobs_root)
            storage = existing.get("storage") or {}
            storage_satisfied = storage.get("status") == "uploaded" and bool(storage.get("verified"))
            if check["verified"] and (not (upload or require_storage) or storage_satisfied):
                reused = True
                contract = existing
                stages.append(_stage("register", STAGE_REAL, f"reused verified contract {contract_path}"))
                stages.append(_stage("render", STAGE_REAL, "reused — artifact + source checksums verified"))
                stages.append(_stage("verify", STAGE_REAL, "reused — contract re-verified (sha256)"))
            else:
                stages.append(_stage("reuse", STAGE_UNAVAILABLE, f"existing contract not reusable: {check}"))
    except Exception as exc:
        _persist_failure(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, request=request,
            stages=stages, stage="reuse", error=exc, write_status=write_status,
        )
        raise

    if dry_run:
        stages.append(_stage("dry_run", STAGE_REAL, "validation/planning only; no FFmpeg, no contract persisted"))
        report = _build_report(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, contract=None,
            reused=False, resumed=False, stages=stages, catalog=None, library=None,
            storage=None, dry_run=True, request=request,
        )
        if write_status:
            _write_json_atomic(
                workflow_status_path(pilot_id, jobs_root, source_id=resolved_source_id),
                report,
            )
        return report

    if not reused:
        try:
            ingest = _li.run_local_ingest(
                resolved_source,
                pilot_id=pilot_id,
                source_id=source_id,
                jobs_dir=jobs_root,
                intake_root=intake_root,
                operator=operator,
                project=project,
                reference_deployment=reference_deployment,
                story_id=story_id,
                fmt=fmt,
                source_start=source_start,
                source_end=source_end,
                upload=upload,
                storage_adapter=storage_adapter,
                storage_bucket=storage_bucket,
                storage_object_prefix=storage_object_prefix,
                storage_class=storage_class,
                storage_read_back=storage_read_back,
                require_storage=require_storage,
                resume=True,
            )
        except _pilot.JobExistsError as exc:
            error = MediaWorkflowError(
                f"job already exists and the stored contract is missing or unverified: {exc}. "
                f"Retry the same request to resume, or use a new --pilot-id to reprocess."
            )
            _persist_failure(
                pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
                contract_path=contract_path, catalog_path=catalog_path, request=request,
                stages=stages, stage="ingest", error=error, write_status=write_status,
            )
            raise error from exc
        except Exception as exc:
            _persist_failure(
                pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
                contract_path=contract_path, catalog_path=catalog_path, request=request,
                stages=stages, stage="ingest", error=exc, write_status=write_status,
            )
            raise
        contract = ingest["contract"]
        stages.append(_stage("register", STAGE_REAL, f"pilot job {ingest['job_id']}"))
        stages.append(_stage(
            "render", STAGE_REAL,
            f"reference render {contract['processing']['status']} "
            f"(resumed={ingest.get('resumed', False)})",
        ))
        stages.append(_stage("verify", STAGE_REAL, f"artifact verified={contract['verification']['verified']}"))
        if not contract["verification"]["verified"]:
            error = MediaWorkflowError(
                "artifact verification failed — inspect the render state under "
                f"{jobs_root}/RENDERS and re-run with --no-reuse; the local source is untouched."
            )
            _persist_failure(
                pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
                contract_path=contract_path, catalog_path=catalog_path, request=request,
                stages=stages, stage="verify", error=error, write_status=write_status,
            )
            raise error
        c_dir.mkdir(parents=True, exist_ok=True)
        try:
            _write_json_atomic(contract_path, contract)
        except OSError as exc:
            _persist_failure(
                pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
                contract_path=contract_path, catalog_path=catalog_path, request=request,
                stages=stages, stage="contract", error=exc, write_status=write_status,
            )
            raise

    storage = contract.get("storage") or {}
    stages.append(_stage(
        "storage",
        STAGE_REAL if storage.get("status") == "uploaded" else STAGE_UNAVAILABLE,
        f"status={storage.get('status')} provider={storage.get('provider')} verified={storage.get('verified')}",
    ))

    asset = _mc.asset_from_contract(contract)
    if asset is None:
        error = MediaWorkflowError("persisted contract is not a valid catalog source")
        _persist_failure(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, request=request,
            stages=stages, stage="catalog", error=error, write_status=write_status,
        )
        raise error
    asset_id = asset["asset_id"]

    try:
        catalog = build_catalog_from_runtime(
            contracts_dir=c_dir,
            sessions_dir=sessions_dir,
            jobs_dir=jobs_root,
            reference_deployment=reference_deployment,
            out_path=catalog_path,
        )
    except Exception as exc:
        _persist_failure(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, request=request,
            stages=stages, stage="catalog", error=exc, write_status=write_status,
        )
        raise
    matching = [a for a in catalog["assets"] if a["asset_id"] == asset_id]
    duplicate_suppressed = len(matching) == 1
    stages.append(_stage(
        "catalog", STAGE_REAL,
        f"{catalog['asset_count']} asset(s); new asset {asset_id} "
        f"occurs {len(matching)}x (duplicate_suppressed={duplicate_suppressed})",
    ))
    if not duplicate_suppressed:
        error = MediaWorkflowError(
            f"catalog duplicate suppression failed for {asset_id}: found {len(matching)} entries"
        )
        _persist_failure(
            pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
            contract_path=contract_path, catalog_path=catalog_path, request=request,
            stages=stages, stage="catalog", error=error, write_status=write_status,
        )
        raise error

    library: dict | None = None
    if verify_library:
        roots = preview_roots or [str(jobs_root)]
        try:
            library = verify_with_lite(
                catalog_path=catalog_path,
                asset_id=asset_id,
                lite_root=lite_root,
                lite_python=lite_python,
                preview_roots=roots,
                preview_enabled=preview_enabled,
            )
        except Exception as exc:
            _persist_failure(
                pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
                contract_path=contract_path, catalog_path=catalog_path, request=request,
                stages=stages, stage="library_import", error=exc, write_status=write_status,
            )
            raise
        stages.append(_stage(
            "library_import",
            STAGE_REAL if library.get("asset_found") else STAGE_UNAVAILABLE,
            f"available={library.get('available')} asset_found={library.get('asset_found')} "
            f"leak_free={library.get('leak_free')}" if library.get("ran") else str(library.get("reason")),
        ))
        playback_ok = library.get("range_status") == 206
        stages.append(_stage(
            "local_playback",
            STAGE_REAL if playback_ok else STAGE_UNAVAILABLE,
            f"range_status={library.get('range_status')} bytes={library.get('range_bytes')}"
            if library.get("ran") else str(library.get("reason")),
        ))

    report = _build_report(
        pilot_id=pilot_id, source_path=resolved_source, jobs_root=jobs_root,
        contract_path=contract_path, catalog_path=catalog_path, contract=contract,
        reused=reused, resumed=bool(ingest and ingest.get("resumed")), stages=stages,
        catalog=catalog, library=library, storage=storage, dry_run=False,
        asset_id=asset_id, duplicate_suppressed=duplicate_suppressed, request=request,
    )
    if write_status:
        _write_json_atomic(
            workflow_status_path(pilot_id, jobs_root, source_id=resolved_source_id),
            report,
        )
    return report


def _build_report(
    *,
    pilot_id: str,
    source_path: Path,
    jobs_root: Path,
    contract_path: Path,
    catalog_path: Path,
    contract: dict | None,
    reused: bool,
    resumed: bool,
    stages: list[dict],
    catalog: dict | None,
    library: dict | None,
    storage: dict | None,
    dry_run: bool,
    request: dict,
    asset_id: str | None = None,
    duplicate_suppressed: bool | None = None,
) -> dict:
    artifact = (contract or {}).get("artifact") or {}
    storage = storage or ((contract or {}).get("storage") or {})
    verified = bool((contract or {}).get("verification", {}).get("verified"))

    recommendations: list[str] = []
    if library is not None and not library.get("ok"):
        if not library.get("ran"):
            recommendations.append(
                "Library verification was skipped: set --lite-root (or STADIUM_SIGNAL_LITE_ROOT) "
                "to the Stadium Signal Lite checkout to verify import + playback."
            )
        else:
            recommendations.append(
                "Library verification failed: confirm the catalog path, preview roots, and that "
                "the Lite checkout is on the Phase 7 branch."
            )
    if storage.get("status") == "failed":
        recommendations.append(
            "OORT upload failed; the local artifact is preserved. Re-run with --upload once "
            "OORT_* credentials/connectivity are restored."
        )
    if not verified and not dry_run:
        recommendations.append("Artifact not verified; inspect the render state and re-run with --no-reuse.")

    library_failed = bool(library is not None and library.get("ran") and not library.get("ok"))
    outcome = "planned" if dry_run else ("failed" if library_failed else "succeeded")
    error = (
        {"type": "LibraryVerificationError", "message": library.get("reason") or "Lite verification failed"}
        if library_failed else None
    )

    return {
        "workflow_schema": MEDIA_WORKFLOW_SCHEMA,
        "workflow_version": MEDIA_WORKFLOW_VERSION,
        "generated_at": _now_iso(),
        "producer": {"application": MEDIA_WORKFLOW_PRODUCER, "component": MEDIA_WORKFLOW_COMPONENT},
        "outcome": outcome,
        "error": error,
        "dry_run": dry_run,
        "pilot_id": pilot_id,
        "job_id": (contract or {}).get("provenance", {}).get("job_id") or ((contract or {}).get("project") or {}).get("job_id"),
        "project_id": ((contract or {}).get("project") or {}).get("project_id"),
        "reused": reused,
        "resumed": resumed,
        "request": request,
        "source": {
            "filename": source_path.name,
            "sha256": ((contract or {}).get("source") or {}).get("sha256"),
        },
        "contract": {"path": str(contract_path), "written": bool(contract) and not dry_run},
        "artifact": {
            "path": artifact.get("path"),
            "filename": artifact.get("filename"),
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "duration_seconds": artifact.get("duration_seconds"),
        },
        "verified": verified,
        "storage": {
            "status": storage.get("status"),
            "provider": storage.get("provider"),
            "verified": bool(storage.get("verified")),
            "error": storage.get("error"),
        },
        "catalog": None if catalog is None else {
            "path": str(catalog_path),
            "asset_count": catalog.get("asset_count"),
            "asset_id": asset_id,
            "duplicate_suppressed": duplicate_suppressed,
            "generated_at": catalog.get("generated_at"),
        },
        "library": None if library is None else {
            "ran": bool(library.get("ran")),
            "ok": bool(library.get("ok")),
            "available": library.get("available"),
            "asset_found": library.get("asset_found"),
            "preview_url": library.get("preview_url"),
            "preview_enabled": library.get("preview_enabled"),
            "range_status": library.get("range_status"),
            "range_bytes": library.get("range_bytes"),
            "range_valid": library.get("range_valid"),
            "leak_free": library.get("leak_free"),
            "arbitrary_blocked": library.get("arbitrary_blocked"),
            "unknown_asset_blocked": library.get("unknown_asset_blocked"),
            "reason": library.get("reason"),
        },
        "stages": stages,
        "recommendations": recommendations,
    }
