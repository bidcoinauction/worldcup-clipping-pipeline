"""Phase 7 — Mac-native media ingestion.

An *additive* orchestration layer that turns one authorized local media file
into one project (managed pilot job record) and one verified, FFmpeg-backed
processed artifact, then emits a small versioned integration contract that
Stadium Signal Lite can consume.

This module sits *on top of* existing Clipper services and never replaces them:

* source registration + rights gate + read-only source validation come from
  :mod:`pipeline.pilot` (``validate_intake`` / ``create_job``);
* the timeline plan comes from :mod:`pipeline.edl` (``write_edl``);
* the genuine processing operation is the reference renderer in
  :mod:`pipeline.rendering` (``render_edl``), which shells out to FFmpeg.

Nothing here touches the World Cup reference deployment: callers point the
runtime at an explicit job root (``data/pilot/`` by default, gitignored, or a
test ``tmp_path``), and the built-in test project is the registered,
non-production ``basketball_sandbox`` profile.

Local-first by design: by default there is no network access, no worker
process, no OORT, and no publishing. The only subprocesses are ``ffprobe``
(read-only metadata) and ``ffmpeg`` (the render), both invoked with structured
arguments.

Phase 7.1 adds an *opt-in* OORT Storage upload (see
:mod:`pipeline.integration_service`): when explicitly requested it uploads the
verified artifact and re-verifies it, recording an honest ``storage`` block in
the contract. Local artifacts are always preserved, and the run degrades
gracefully when OORT is not configured.

Public surface:

* :func:`build_test_intake` — construct an authorized local-source intake.
* :func:`probe_media` / :func:`sha256_file` — read-only media facts.
* :func:`register_local_source` — validate + create the project (job record).
* :func:`write_reference_edl` — write the smallest deterministic timeline plan.
* :func:`render_reference` — run the genuine FFmpeg reference render.
* :func:`verify_artifact` — existence, readable duration, provenance link.
* :func:`upload_artifact_to_storage` — opt-in OORT upload + verification.
* :func:`build_integration_contract` — the versioned Lite handoff envelope.
* :func:`validate_integration_contract` — schema check for the consumer.
* :func:`run_local_ingest` — the full one-source → one-project → one-artifact run.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import shutil
import subprocess
from pathlib import Path

from . import edl as _edl
from . import integration_service as _integration
from . import pilot as _pilot
from . import rendering as _rendering

# ── Contract identity ────────────────────────────────────────────────────────

LOCAL_INGEST_SCHEMA = "stadium_signal.local_ingest/v1"
LOCAL_INGEST_CONTRACT_VERSION = 1
LOCAL_INGEST_PRODUCER = "worldcup-clipping-pipeline"
LOCAL_INGEST_COMPONENT = "pipeline.local_ingest"

# The built-in, explicitly non-production project used for local ingestion
# tests and the Stadium Signal Lite handoff. See docs/REFERENCE_DEPLOYMENT.md.
DEFAULT_TEST_PROJECT = "basketball_sandbox"
DEFAULT_TEST_BRAND = "basketball_example"
DEFAULT_TEST_EDITORIAL = "basketball"
DEFAULT_TEST_REFERENCE_DEPLOYMENT = "stadium_signal_lite_local"

DEFAULT_OPERATION = "reference_render"
DEFAULT_RENDER_MODE = "REFERENCE"
DEFAULT_STORY_ID = "ss_lite_story"
DEFAULT_FORMAT = "SHORT"

# Phase 7.1 — optional OORT storage upload (see pipeline.integration_service).
DEFAULT_STORAGE_OBJECT_PREFIX = "stadium-signal/local-ingest"

# Stage status vocabulary for the integration contract's honest stage report.
STAGE_REAL = "real"
STAGE_MOCKED = "mocked"
STAGE_UNAVAILABLE = "unavailable"


class LocalIngestError(Exception):
    """Raised for expected local-ingestion failures."""


class LocalIngestNotReadyError(LocalIngestError):
    """Raised when an intake does not pass the execution-ready gate."""

    def __init__(self, report: dict) -> None:
        self.report = report
        issues = report.get("issues") or []
        detail = "; ".join(f"{i.get('path')}: {i.get('message')}" for i in issues) or "intake not execution-ready"
        super().__init__(detail)


# ── Read-only media facts ────────────────────────────────────────────────────


def sha256_file(path: str | Path) -> str:
    """Streaming SHA-256 of a file. Used for source/artifact provenance."""
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def probe_media(path: str | Path) -> dict:
    """Read-only ``ffprobe`` metadata for a local media file.

    Raises :class:`LocalIngestError` when ffprobe is unavailable or the file
    cannot be probed. Never modifies the file and makes no network requests.
    """
    target = Path(path)
    if not shutil.which("ffprobe"):
        raise LocalIngestError("ffprobe is not available on PATH; media probing is required for ingestion")
    if not target.exists() or not target.is_file():
        raise LocalIngestError(f"media file not found: {target}")

    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(target)],
        capture_output=True, text=True, shell=False,
    )
    if result.returncode != 0:
        raise LocalIngestError(f"ffprobe failed for '{target}': {result.stderr.strip()[:300]}")

    data = json.loads(result.stdout)
    fmt = data.get("format", {})
    streams = data.get("streams", [])
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)

    duration = fmt.get("duration")
    duration_seconds = float(duration) if duration not in (None, "") else None

    frame_rate = None
    if video and video.get("avg_frame_rate") and video["avg_frame_rate"] != "0/0":
        num, _, den = video["avg_frame_rate"].partition("/")
        try:
            frame_rate = round(float(num) / float(den), 3) if float(den) else None
        except (ValueError, ZeroDivisionError):
            frame_rate = None

    return {
        "path": str(target),
        "size_bytes": target.stat().st_size,
        "duration_seconds": duration_seconds,
        "format_name": fmt.get("format_name"),
        "video_codec": video.get("codec_name") if video else None,
        "audio_codec": audio.get("codec_name") if audio else None,
        "width": video.get("width") if video else None,
        "height": video.get("height") if video else None,
        "frame_rate": frame_rate,
        "has_audio": audio is not None,
    }


# ── Intake construction ──────────────────────────────────────────────────────


def build_test_intake(
    source_path: str | Path,
    *,
    pilot_id: str,
    source_id: str,
    reference_deployment: str = DEFAULT_TEST_REFERENCE_DEPLOYMENT,
    project: str = DEFAULT_TEST_PROJECT,
    brand: str = DEFAULT_TEST_BRAND,
    editorial_taxonomy: str = DEFAULT_TEST_EDITORIAL,
    export_profiles: list[str] | None = None,
    event_name: str | None = None,
    event_date: str | None = None,
    duration_seconds: float | None = None,
    checksum: str | None = None,
    operator_notes: str = "",
) -> dict:
    """Construct an execution-ready intake for a locally owned test source.

    Rights are marked ``CONFIRMED`` with an explicit statement that the source
    is a locally generated, operator-owned synthetic asset — not third-party
    media. The default project/brand/editorial references are the registered,
    non-production sandbox profile so the World Cup reference deployment is
    never selected.
    """
    resolved = Path(source_path)
    media = {
        "source_id": source_id,
        "local_file_path": str(resolved),
        "original_filename": resolved.name,
        "media_type": "video",
        "duration_seconds": duration_seconds,
        "checksum": checksum,
        "supplied_by_client": False,
        "source_validation_completed": False,
    }
    if event_name:
        media["match_or_event_name"] = event_name
    if event_date:
        media["event_date"] = event_date

    rights = {
        "status": "CONFIRMED",
        "confirmation_statement": (
            "Operator confirms this is a locally generated synthetic test asset owned by the "
            "operator, cleared for clip, store, review, and delivery."
        ),
        "confirmed_by": "Stadium Signal Lite local test harness",
        "confirmation_date": _dt.date.today().isoformat(),
        "permitted_uses": ["clip", "store", "review", "delivery"],
        "distribution_limitations": [],
        "expiration_date": None,
        "source_of_permission": "self-owned synthetic asset",
        "notes": "Non-production test intake. Contains no client data.",
    }

    return {
        "intake_version": _pilot.INTAKE_SCHEMA_VERSION,
        "pilot": {
            "pilot_id": pilot_id,
            "project": project,
            "reference_deployment": reference_deployment,
            "requested_clip_count": {"min": 1, "max": 1},
            "operator_notes": operator_notes or "Local Stadium Signal Lite ingestion test project.",
        },
        "media": media,
        "rights": rights,
        "configuration": {
            "project": project,
            "brand": brand,
            "editorial_taxonomy": editorial_taxonomy,
            "operational_taxonomy": editorial_taxonomy,
            "detection_template": "prompt",
            "export_profiles": list(export_profiles or ["source"]),
            "delivery_destination": "EXPORTS",
        },
        "review_and_delivery": {
            "human_review_required": True,
            "approval_method": "manual operator review (local)",
            "delivery_method": "local_directory",
            "delivery_directory": "EXPORTS",
            "expected_deliverables": ["rough cut"],
            "publishing_included": False,
        },
    }


# ── Source registration ──────────────────────────────────────────────────────


def register_local_source(
    intake: dict,
    *,
    intake_path: str | Path,
    jobs_dir: str | Path | None = None,
    intake_root: str | None = None,
    operator: str | None = None,
) -> dict:
    """Validate an intake and create the project (managed pilot job record).

    Fails closed when the intake is not execution-ready (structural,
    configuration, source, and rights checks must all pass). Read-only with
    respect to the media; the only write is the job record inside *jobs_dir*.
    """
    report = _pilot.validate_intake(intake, intake_root=intake_root)
    if not report["execution_ready"]:
        raise LocalIngestNotReadyError(report)

    job = _pilot.create_job(
        intake,
        intake_path=intake_path,
        jobs_dir=jobs_dir,
        operator=operator,
        source="local_ingest.register",
        intake_root=intake_root,
    )

    media = intake.get("media", {})
    source_path = media.get("local_file_path")
    source_facts = probe_media(source_path)
    return {
        "job": job,
        "job_id": job["job_id"],
        "project_id": job.get("project_id"),
        "reference_deployment": (intake.get("pilot") or {}).get("reference_deployment"),
        "intake_manifest_path": job.get("intake_manifest_path"),
        "current_state": job.get("current_state"),
        "report": report,
        "source": {
            "source_id": media.get("source_id"),
            "local_file_path": source_path,
            "sha256": sha256_file(source_path),
            **{k: source_facts[k] for k in (
                "size_bytes", "duration_seconds", "video_codec", "audio_codec",
                "width", "height", "frame_rate", "has_audio",
            )},
        },
    }


# ── EDL + render ─────────────────────────────────────────────────────────────


def write_reference_edl(
    job_id: str,
    *,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    source_start: float = 0.0,
    source_end: float = 2.0,
    jobs_dir: str | Path | None = None,
) -> dict:
    """Write the smallest deterministic EDL: one segment, one straight cut.

    This is a genuine timeline plan (Clipper's own EDL schema), not a fake.
    """
    start = float(source_start)
    end = float(source_end)
    if end <= start:
        raise LocalIngestError(f"invalid render window: source_end ({end}) must be greater than source_start ({start})")

    edl = {
        "job_id": job_id,
        "story_id": story_id,
        "format": fmt,
        "segments": [
            {
                "segment_id": "seg_000",
                "order": 0,
                "source_start": round(start, 3),
                "source_end": round(end, 3),
                "transition_intent": "HARD_CUT",
                "audio_strategy": "ORIGINAL",
            }
        ],
        "segment_count": 1,
        "timeline_duration": round(end - start, 3),
    }
    path = _edl.write_edl(edl, jobs_dir=jobs_dir)
    return {"edl": edl, "edl_path": str(path)}


def render_reference(
    job_id: str,
    *,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    jobs_dir: str | Path | None = None,
    timeout: int = 300,
    dry_run: bool = False,
) -> dict:
    """Run the genuine FFmpeg-backed reference render for an existing EDL."""
    return _rendering.render_edl(
        job_id, story_id, fmt,
        jobs_dir=jobs_dir, timeout=timeout, dry_run=dry_run, mode=DEFAULT_RENDER_MODE,
    )


def read_render_state(
    job_id: str,
    *,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    jobs_dir: str | Path | None = None,
) -> dict | None:
    return _rendering.read_render_state(job_id, story_id, fmt, jobs_dir=jobs_dir, mode=DEFAULT_RENDER_MODE)


# ── Verification ─────────────────────────────────────────────────────────────


def verify_artifact(
    job_id: str,
    *,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    jobs_dir: str | Path | None = None,
    expected_source_sha256: str | None = None,
    tolerance_seconds: float = 1.0,
) -> dict:
    """Verify the rendered artifact and its provenance back to the source.

    Checks: the render completed, the artifact exists and is non-empty, its
    duration is readable via ffprobe and matches the EDL timeline, and — when
    given — the original source still hashes to the recorded SHA-256, proving
    the artifact's lineage is intact.
    """
    checks: dict[str, bool] = {}
    state = read_render_state(job_id, story_id=story_id, fmt=fmt, jobs_dir=jobs_dir)
    checks["render_state_present"] = state is not None
    checks["render_complete"] = bool(state and state.get("render_status") == "COMPLETE")

    artifact_path = Path(state["output"]) if state and state.get("output") else None
    checks["artifact_exists"] = bool(artifact_path and artifact_path.exists())
    checks["artifact_non_empty"] = bool(artifact_path and artifact_path.exists() and artifact_path.stat().st_size > 0)

    artifact_facts: dict | None = None
    checks["artifact_duration_readable"] = False
    checks["artifact_duration_matches_timeline"] = False
    if artifact_path and artifact_path.exists():
        artifact_facts = probe_media(artifact_path)
        checks["artifact_duration_readable"] = bool(artifact_facts.get("duration_seconds"))
        timeline = state.get("duration") if state else None
        if artifact_facts.get("duration_seconds") and isinstance(timeline, (int, float)) and timeline:
            checks["artifact_duration_matches_timeline"] = (
                abs(float(artifact_facts["duration_seconds"]) - float(timeline)) <= tolerance_seconds
            )

    checks["source_provenance_linked"] = True
    if expected_source_sha256:
        job = _pilot.read_job(job_id, jobs_dir=jobs_dir)
        intake_path = job.get("intake_manifest_path")
        source_sha = None
        if intake_path and Path(intake_path).exists():
            intake = json.loads(Path(intake_path).read_text(encoding="utf-8"))
            src = (intake.get("media") or {}).get("local_file_path")
            if src and Path(src).exists():
                source_sha = sha256_file(src)
        checks["source_provenance_linked"] = source_sha is not None and source_sha == expected_source_sha256

    verified = all(checks.values())
    return {
        "verified": verified,
        "checks": checks,
        "artifact": (
            {
                "path": str(artifact_path),
                "filename": artifact_path.name,
                "size_bytes": artifact_path.stat().st_size,
                "sha256": sha256_file(artifact_path),
                "duration_seconds": (artifact_facts or {}).get("duration_seconds"),
            }
            if artifact_path and artifact_path.exists()
            else None
        ),
    }


# ── Stage report ─────────────────────────────────────────────────────────────


def stage_report(*, rendered: bool, storage: dict | None = None) -> list[dict]:
    """Honest report of which pipeline stages were real, mocked, or unavailable.

    The optional *storage* block (Phase 7.1) drives the OORT upload stage so the
    report reflects what actually happened rather than what is merely possible.
    """
    storage_status = (storage or {}).get("status")
    if storage_status == _integration.STORAGE_STATUS_UPLOADED:
        oort_status = STAGE_REAL
        oort_detail = "OORTStorageAdapter.upload_file + verify (real HTTPS to the OORT S3 gateway)"
    elif storage_status == _integration.STORAGE_STATUS_FAILED:
        oort_status = STAGE_UNAVAILABLE
        oort_detail = "OORT upload attempted but failed; local-only artifacts preserved"
    elif storage_status == _integration.STORAGE_STATUS_UNAVAILABLE:
        oort_status = STAGE_UNAVAILABLE
        oort_detail = "OORT upload requested but unavailable (no credentials/config); local-only run preserved"
    else:
        oort_status = STAGE_UNAVAILABLE
        oort_detail = "no storage upload requested; local-only integration by design"

    return [
        {"stage": "source_registration", "status": STAGE_REAL,
         "detail": "pipeline.pilot.validate_intake + create_job (structural, config, source, rights)"},
        {"stage": "source_probe", "status": STAGE_REAL, "detail": "ffprobe -show_format -show_streams (read-only)"},
        {"stage": "rights_gate", "status": STAGE_REAL, "detail": "pipeline.pilot CONFIRMED + unexpired rights gate"},
        {"stage": "edl_build", "status": STAGE_REAL, "detail": "pipeline.edl.write_edl (single straight-cut segment)"},
        {"stage": "ffmpeg_reference_render", "status": STAGE_REAL if rendered else STAGE_UNAVAILABLE,
         "detail": "pipeline.rendering.render_edl, mode=REFERENCE (real ffmpeg)"},
        {"stage": "artifact_provenance", "status": STAGE_REAL, "detail": "sha256 source + artifact, duration re-probe"},
        {"stage": "transcription", "status": STAGE_UNAVAILABLE,
         "detail": "requires a faster-whisper model; not run for ingestion"},
        {"stage": "moment_detection", "status": STAGE_UNAVAILABLE,
         "detail": "requires an LLM/Ollama detection provider; not run for ingestion"},
        {"stage": "editorial_render", "status": STAGE_UNAVAILABLE,
         "detail": "mode=EDITORIAL not selected; only the reference render is exercised"},
        {"stage": "delivery", "status": STAGE_MOCKED,
         "detail": "delivery metadata recorded only; no publish to a downstream channel"},
        {"stage": "network_worker_oort", "status": STAGE_UNAVAILABLE,
         "detail": "no separate worker process; OORT upload is adapter-direct when enabled"},
        {"stage": "oort_storage_upload", "status": oort_status, "detail": oort_detail},
    ]


def empty_storage_block() -> dict:
    """Stable-shape ``storage`` block for runs that did not upload anything."""
    return {
        "enabled": False,
        "provider": None,
        "status": _integration.STORAGE_STATUS_SKIPPED,
        "endpoint": None,
        "region": None,
        "bucket": None,
        "object_key": None,
        "object_uri": None,
        "etag": None,
        "sha256": None,
        "size_bytes": None,
        "storage_class": None,
        "uploaded_at": None,
        "verified": False,
        "read_back_verified": False,
        "checks": {},
        "error": None,
    }


def upload_artifact_to_storage(
    artifact: dict,
    *,
    job_id: str,
    adapter: "_integration.OORTAdapter",
    object_prefix: str = DEFAULT_STORAGE_OBJECT_PREFIX,
    storage_class: str | None = None,
    read_back: bool = True,
    scratch_dir: str | Path | None = None,
    project: str | None = None,
    source_sha256: str | None = None,
) -> dict:
    """Upload a verified artifact to OORT and return a ``storage`` block.

    Raises :class:`pipeline.integration_service.StorageIntegrationError` on any
    failure; the caller decides whether to fail the run or degrade gracefully.
    Local files are never modified or removed here.
    """
    artifact_path = artifact.get("path")
    if not artifact_path:
        block = empty_storage_block()
        block["status"] = _integration.STORAGE_STATUS_SKIPPED
        block["error"] = "no artifact to upload"
        return block

    filename = artifact.get("filename") or Path(artifact_path).name
    object_key = f"{object_prefix.strip('/')}/{job_id}/{filename}"
    result = adapter.upload_file(
        artifact_path,
        object_key,
        storage_class=storage_class,
        metadata={
            "job-id": job_id,
            "project": project or "",
            "source-sha256": source_sha256 or "",
        },
    )
    adapter.verify_upload(
        result,
        expected_sha256=artifact.get("sha256"),
        expected_size=artifact.get("size_bytes"),
        read_back=read_back,
        scratch_dir=scratch_dir,
    )
    block = result.to_block()
    block["enabled"] = True
    return block


def _resolve_storage_upload(
    *,
    upload: bool,
    dry_run: bool,
    verification: dict,
    job_id: str,
    adapter: "_integration.OORTAdapter | None",
    bucket: str | None,
    object_prefix: str,
    storage_class: str | None,
    require_storage: bool,
    read_back: bool,
    scratch_dir: str | Path | None,
    project: str | None,
    source_sha256: str | None = None,
) -> dict:
    """Select/validate a storage adapter and perform the optional upload."""
    block = empty_storage_block()
    if not upload:
        return block
    if dry_run:
        block["error"] = "dry run: storage upload skipped"
        return block

    resolved = adapter or _integration.build_storage_adapter()
    if resolved is None:
        block["status"] = _integration.STORAGE_STATUS_UNAVAILABLE
        block["error"] = (
            "OORT storage not configured; set OORT_ACCESS_KEY_ID and OORT_SECRET_ACCESS_KEY "
            "(and OORT_BUCKET) to enable uploads"
        )
        if require_storage:
            raise LocalIngestError(f"storage upload required but unavailable: {block['error']}")
        return block

    if bucket and bucket != resolved.config.bucket:
        resolved = resolved.with_bucket(bucket)

    try:
        return upload_artifact_to_storage(
            verification.get("artifact") or {},
            job_id=job_id,
            adapter=resolved,
            object_prefix=object_prefix,
            storage_class=storage_class,
            read_back=read_back,
            scratch_dir=scratch_dir,
            project=project,
            source_sha256=source_sha256,
        )
    except _integration.StorageIntegrationError as exc:
        block = empty_storage_block()
        block["status"] = _integration.STORAGE_STATUS_FAILED
        block["provider"] = _integration.OORT_STORAGE_PROVIDER
        block["endpoint"] = resolved.config.endpoint
        block["region"] = resolved.config.region
        block["bucket"] = resolved.config.bucket
        block["error"] = str(exc)
        if require_storage:
            raise LocalIngestError(f"storage upload required but failed: {exc}") from exc
        return block


# ── Integration contract ─────────────────────────────────────────────────────


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def build_integration_contract(
    *,
    registration: dict,
    edl_result: dict,
    render_result: dict,
    verification: dict,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    operation: str = DEFAULT_OPERATION,
    jobs_dir: str | Path | None = None,
    storage: dict | None = None,
) -> dict:
    """Assemble the versioned Stadium Signal Lite integration contract."""
    job = registration["job"]
    source = registration["source"]
    artifact = verification.get("artifact") or {}
    jobs_root = Path(jobs_dir) if jobs_dir is not None else _pilot.default_jobs_dir()
    storage_block = storage if storage is not None else empty_storage_block()

    ffmpeg_argv: list[str] = []
    source_path = source.get("local_file_path")
    artifact_path = artifact.get("path")
    if source_path and artifact_path:
        try:
            ffmpeg_argv = _rendering.build_ffmpeg_args(edl_result["edl"], source_path, artifact_path)
        except Exception:
            ffmpeg_argv = []

    render_state_path = _rendering._render_state_path(
        job.get("job_id", "unknown"), story_id, fmt, jobs_root, DEFAULT_RENDER_MODE,
    )

    return {
        "contract_schema": LOCAL_INGEST_SCHEMA,
        "contract_version": LOCAL_INGEST_CONTRACT_VERSION,
        "generated_at": _now_iso(),
        "producer": {"application": LOCAL_INGEST_PRODUCER, "component": LOCAL_INGEST_COMPONENT},
        "project": {
            "job_id": job.get("job_id"),
            "project_id": job.get("project_id"),
            "reference_deployment": registration.get("reference_deployment"),
            "current_state": job.get("current_state"),
            "intake_manifest_path": job.get("intake_manifest_path"),
        },
        "source": {
            "source_id": source.get("source_id"),
            "local_file_path": source.get("local_file_path"),
            "original_filename": Path(source.get("local_file_path", "")).name,
            "sha256": source.get("sha256"),
            "duration_seconds": source.get("duration_seconds"),
            "width": source.get("width"),
            "height": source.get("height"),
            "video_codec": source.get("video_codec"),
            "audio_codec": source.get("audio_codec"),
            "has_audio": source.get("has_audio"),
        },
        "processing": {
            "operation": operation,
            "render_mode": render_result.get("mode", DEFAULT_RENDER_MODE),
            "status": render_result.get("status"),
            "story_id": story_id,
            "format": fmt,
            "segment_count": edl_result["edl"].get("segment_count"),
            "timeline_duration_seconds": edl_result["edl"].get("timeline_duration"),
            "dry_run": bool(render_result.get("dry_run")),
            "edl_path": edl_result.get("edl_path"),
            "render_state_path": str(render_state_path),
            "ffmpeg_argv": ffmpeg_argv,
            "applied_features": render_result.get("applied_features", []),
            "deferred_features": render_result.get("deferred_features", []),
        },
        "artifact": {
            "path": artifact.get("path"),
            "filename": artifact.get("filename"),
            "sha256": artifact.get("sha256"),
            "size_bytes": artifact.get("size_bytes"),
            "duration_seconds": artifact.get("duration_seconds"),
        },
        "provenance": {
            "links_to_source": bool(verification.get("checks", {}).get("source_provenance_linked")),
            "source_sha256": source.get("sha256"),
            "artifact_sha256": artifact.get("sha256"),
            "job_id": job.get("job_id"),
            "intake_manifest_path": job.get("intake_manifest_path"),
            "edl_path": edl_result.get("edl_path"),
            "storage_object_uri": storage_block.get("object_uri"),
            "storage_object_sha256": storage_block.get("sha256"),
        },
        "rights": {
            "status": registration.get("report", {}).get("rights_status"),
            "cleared": registration.get("report", {}).get("rights_cleared"),
        },
        "verification": {
            "verified": verification.get("verified"),
            "checks": verification.get("checks", {}),
        },
        "storage": storage_block,
        "stages": stage_report(rendered=render_result.get("status") == "COMPLETE", storage=storage_block),
    }


_REQUIRED_CONTRACT_KEYS = (
    "contract_schema", "contract_version", "producer", "project", "source",
    "processing", "artifact", "provenance", "rights", "verification", "stages",
)


def validate_integration_contract(data: object) -> list[str]:
    """Validate a contract envelope. Returns a list of error strings (empty = ok)."""
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["contract root must be an object"]
    for key in _REQUIRED_CONTRACT_KEYS:
        if key not in data:
            errors.append(f"missing required key: {key}")
    if data.get("contract_schema") != LOCAL_INGEST_SCHEMA:
        errors.append(f"unsupported contract_schema: {data.get('contract_schema')!r}")
    if data.get("contract_version") != LOCAL_INGEST_CONTRACT_VERSION:
        errors.append(f"unsupported contract_version: {data.get('contract_version')!r}")
    if errors:
        return errors
    if not isinstance(data["source"], dict) or "sha256" not in data["source"]:
        errors.append("source.sha256 is required")
    processing = data.get("processing") or {}
    render_complete = isinstance(processing, dict) and processing.get("status") == "COMPLETE"
    dry_run = bool(isinstance(processing, dict) and processing.get("dry_run"))
    if not isinstance(data["artifact"], dict) or (render_complete and not dry_run and not data["artifact"].get("path")):
        errors.append("artifact.path is required for a completed (non-dry-run) render")
    if not isinstance(data["provenance"], dict) or data["provenance"].get("job_id") is None:
        errors.append("provenance.job_id is required")
    stages = data.get("stages")
    if not isinstance(stages, list) or not stages:
        errors.append("stages must be a non-empty list")
    else:
        allowed = {STAGE_REAL, STAGE_MOCKED, STAGE_UNAVAILABLE}
        for i, stage in enumerate(stages):
            if not isinstance(stage, dict) or stage.get("status") not in allowed:
                errors.append(f"stages[{i}].status must be one of {sorted(allowed)}")
    return errors


# ── Full orchestration ───────────────────────────────────────────────────────


def run_local_ingest(
    source_path: str | Path,
    *,
    pilot_id: str,
    source_id: str | None = None,
    jobs_dir: str | Path | None = None,
    intake_path: str | Path | None = None,
    intake_root: str | None = None,
    operator: str | None = None,
    project: str = DEFAULT_TEST_PROJECT,
    brand: str = DEFAULT_TEST_BRAND,
    editorial_taxonomy: str = DEFAULT_TEST_EDITORIAL,
    reference_deployment: str = DEFAULT_TEST_REFERENCE_DEPLOYMENT,
    story_id: str = DEFAULT_STORY_ID,
    fmt: str = DEFAULT_FORMAT,
    source_start: float = 0.0,
    source_end: float = 2.0,
    dry_run: bool = False,
    upload: bool = False,
    storage_adapter: "_integration.OORTAdapter | None" = None,
    storage_bucket: str | None = None,
    storage_object_prefix: str = DEFAULT_STORAGE_OBJECT_PREFIX,
    storage_class: str | None = None,
    storage_read_back: bool = True,
    require_storage: bool = False,
) -> dict:
    """One authorized local video → one project → one verified artifact.

    Deterministic ordering: register (validate + job) → EDL → render → verify →
    (optional OORT upload) → contract. When ``upload`` is set the artifact is
    pushed to OORT Storage and verified; the local artifact is always preserved
    even if the upload fails. Returns the full run result including the contract.
    """
    resolved_source = Path(source_path)
    if not resolved_source.exists():
        raise LocalIngestError(f"source file not found: {resolved_source}")

    sid = source_id or resolved_source.stem
    media = probe_media(resolved_source)
    intake = build_test_intake(
        resolved_source,
        pilot_id=pilot_id,
        source_id=sid,
        reference_deployment=reference_deployment,
        project=project,
        brand=brand,
        editorial_taxonomy=editorial_taxonomy,
        duration_seconds=media.get("duration_seconds"),
    )

    jobs_root = Path(jobs_dir) if jobs_dir is not None else _pilot.default_jobs_dir()
    if intake_path is None:
        intake_path = jobs_root / "intakes" / f"{pilot_id}.json"
    intake_file = Path(intake_path)
    intake_file.parent.mkdir(parents=True, exist_ok=True)
    intake_file.write_text(json.dumps(intake, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    registration = register_local_source(
        intake, intake_path=intake_file, jobs_dir=jobs_root, intake_root=intake_root, operator=operator,
    )
    job_id = registration["job_id"]

    edl_result = write_reference_edl(
        job_id, story_id=story_id, fmt=fmt,
        source_start=source_start, source_end=source_end, jobs_dir=jobs_root,
    )
    render_result = render_reference(
        job_id, story_id=story_id, fmt=fmt, jobs_dir=jobs_root, dry_run=dry_run,
    )
    verification = (
        verify_artifact(
            job_id, story_id=story_id, fmt=fmt, jobs_dir=jobs_root,
            expected_source_sha256=registration["source"]["sha256"],
        )
        if not dry_run else {"verified": False, "checks": {}, "artifact": None}
    )
    storage_block = _resolve_storage_upload(
        upload=upload,
        dry_run=dry_run,
        verification=verification,
        job_id=job_id,
        adapter=storage_adapter,
        bucket=storage_bucket,
        object_prefix=storage_object_prefix,
        storage_class=storage_class,
        require_storage=require_storage,
        read_back=storage_read_back,
        scratch_dir=None,
        project=project,
        source_sha256=registration["source"]["sha256"],
    )
    contract = build_integration_contract(
        registration=registration, edl_result=edl_result,
        render_result=render_result, verification=verification,
        story_id=story_id, fmt=fmt, jobs_dir=jobs_root, storage=storage_block,
    )
    return {
        "job_id": job_id,
        "registration": registration,
        "edl": edl_result,
        "render": render_result,
        "verification": verification,
        "storage": storage_block,
        "contract": contract,
    }
