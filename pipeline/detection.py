"""Detection service boundary.

Orchestrates the full analysis workflow for a project: prompt generation,
API call to the configured provider, and clip-manifest construction. CLI
scripts and the Operator Console call these functions rather than
reimplementing the detection flow.

No CLI invocation. No shell execution. No secrets exposure.
"""

from __future__ import annotations

import json
import logging
import os
import importlib.util
import shutil
import time as _time
from pathlib import Path

from .clip_manifest import build_clip_manifest, write_clip_manifest
from .config import get_default_clip_mode, get_model, get_provider
from .config_errors import ConfigurationError
from .configurator import (
    default_project_profile,
    registered_project_profiles,
    resolve_project_profile,
)
from .prompt_generation import build_prompt

logger = logging.getLogger("pipeline.detection")

DETECTION_CATEGORIES = {"EMOTION", "AURA", "CHAOS", "AMERICA"}
DETECTION_CLIP_FIELDS = {
    "clip_id",
    "category",
    "start_time",
    "end_time",
    "virality_score",
    "retention_reason",
    "share_reason",
    "hook_text",
    "caption",
    "editorial_thesis",
    "emotional_angle",
    "legacy_value",
    "thumbnail_idea",
    "manual_scrub_note",
    "platform_notes",
}
DETECTION_JSON_SCHEMA = {
    "type": "array",
    "minItems": 3,
    "maxItems": 5,
    "items": {
        "type": "object",
        "required": sorted(DETECTION_CLIP_FIELDS),
        "properties": {
            "clip_id": {"type": "string"},
            "category": {"type": "string", "enum": sorted(DETECTION_CATEGORIES)},
            "start_time": {"type": "string", "pattern": "^[0-9]+$"},
            "end_time": {"type": "string", "pattern": "^[0-9]+$"},
            "virality_score": {"type": "integer", "minimum": 1, "maximum": 10},
            "retention_reason": {"type": "string"},
            "share_reason": {"type": "string"},
            "hook_text": {"type": "string"},
            "caption": {"type": "string"},
            "editorial_thesis": {"type": "string"},
            "emotional_angle": {"type": "string"},
            "legacy_value": {"type": "integer", "minimum": 1, "maximum": 10},
            "thumbnail_idea": {"type": "string"},
            "manual_scrub_note": {"type": "string"},
            "platform_notes": {
                "type": "object",
                "required": ["reels", "shorts", "tiktok"],
                "properties": {
                    "tiktok": {"type": "string"},
                    "reels": {"type": "string"},
                    "shorts": {"type": "string"},
                },
                "additionalProperties": False,
            },
        },
        "additionalProperties": False,
    },
}

# ── Analysis stages (human-facing) ──────────────────────────────────────────

STAGE_PREPARING = "PREPARING_SOURCE"
STAGE_TRANSCRIBING = "TRANSCRIBING"
STAGE_UNDERSTANDING = "UNDERSTANDING_GAME"
STAGE_FINDING = "FINDING_MOMENTS"
STAGE_PREPARING_RESULTS = "PREPARING_RESULTS"

STAGES = [STAGE_PREPARING, STAGE_TRANSCRIBING, STAGE_UNDERSTANDING, STAGE_FINDING, STAGE_PREPARING_RESULTS]

ANALYSIS_STATES = {"WAITING", "RUNNING", "COMPLETE", "NEEDS ATTENTION", "FAILED"}

INTERRUPTED_MESSAGE = "Previous analysis was interrupted before completion."


# ── Profile capabilities ────────────────────────────────────────────────────


def profile_capabilities(profile: str) -> dict:
    """Return capability flags for a registered profile.

    Returns a dict with at least ``analysis_supported``.
    Unknown profiles raise :class:`ConfigurationError`.
    """
    data = resolve_project_profile(profile)
    return {
        "profile": profile,
        "analysis_supported": data.get("analysis_supported", False),
    }


def analysis_supported(profile: str) -> bool:
    """Return True if the given profile supports analysis."""
    return profile_capabilities(profile)["analysis_supported"]


# ── Analysis status I/O ────────────────────────────────────────────────────


def _analysis_artifacts_dir(jobs_dir: Path) -> Path:
    d = jobs_dir / "ANALYSIS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _analysis_path(job_id: str, jobs_dir: Path) -> Path:
    return _analysis_artifacts_dir(jobs_dir) / f"{job_id}_analysis.json"


def _moments_path(job_id: str, jobs_dir: Path) -> Path:
    return _analysis_artifacts_dir(jobs_dir) / f"{job_id}_moments.json"


def _read_job_record(job_id: str, jobs_dir: Path) -> dict:
    record_path = jobs_dir / f"{job_id}.json"
    if not record_path.exists():
        return {}
    return json.loads(record_path.read_text(encoding="utf-8"))


def _write_job_record(job_id: str, jobs_dir: Path, data: dict) -> None:
    record_path = jobs_dir / f"{job_id}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=str(record_path.parent), prefix=record_path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, record_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def update_analysis_state(job_id: str, *, status: str, stage: str = "",
                          error: str = "", jobs_dir: str | Path | None = None,
                          **extra) -> dict:
    """Update the analysis state on a job record and return the updated job.

    This is the single write point for analysis state. It never runs
    detection, prompts, or network calls.
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")

    now = _now_iso()
    job["analysis_status"] = status
    job["analysis_stage"] = stage
    if status == "RUNNING" and not job.get("analysis_started_at"):
        job["analysis_started_at"] = now
    if status in ("COMPLETE", "FAILED", "NEEDS ATTENTION"):
        job["analysis_completed_at"] = now
    if error:
        job["analysis_error"] = error
    else:
        job.pop("analysis_error", None)
    for key, value in extra.items():
        job[key] = value

    _write_job_record(job_id, jobs_dir_path, job)
    return job


def read_analysis_state(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read the analysis state from a job record (read-only)."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")
    return {
        "job_id": job_id,
        "analysis_status": job.get("analysis_status", ""),
        "analysis_stage": job.get("analysis_stage", ""),
        "analysis_started_at": job.get("analysis_started_at", ""),
        "analysis_completed_at": job.get("analysis_completed_at", ""),
        "analysis_error": job.get("analysis_error", ""),
        "analysis_moments_path": job.get("analysis_moments_path", ""),
        "analysis_manifest_path": job.get("analysis_manifest_path", ""),
        "analysis_manifest_count": job.get("analysis_manifest_count", 0),
        "transcription_reused": job.get("transcription_reused", None),
        "transcription_reference": job.get("transcription_reference", ""),
        "transcription_status": job.get("transcription_status", ""),
        "transcription_error": job.get("transcription_error", ""),
    }


# ── In-flight analysis registry ──────────────────────────────────────────────
#
# Analysis runs in-process. Tracking live job ids keeps restart recovery from
# touching an analysis that is genuinely running in this process, even if the
# console later becomes multi-threaded.

_ACTIVE_ANALYSES: set[str] = set()


def _is_analysis_active(job_id: str) -> bool:
    return job_id in _ACTIVE_ANALYSES


def recover_interrupted_analysis(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Mark orphaned in-process RUNNING analysis as failed on operator load.

    Analysis currently runs in the console process. There is no durable worker
    that can resume after a process restart, so a persisted RUNNING state with
    no live in-process run is an interrupted run, not real work. Never
    auto-retries; the operator decides to retry.
    """
    state = read_analysis_state(job_id, jobs_dir=jobs_dir)
    if state.get("analysis_status") != "RUNNING" or _is_analysis_active(job_id):
        return state

    extra = {}
    if state.get("analysis_stage") == STAGE_TRANSCRIBING:
        extra = {
            "transcription_status": "FAILED",
            "transcription_error": INTERRUPTED_MESSAGE,
        }
    update_analysis_state(
        job_id,
        status="FAILED",
        stage="",
        error=INTERRUPTED_MESSAGE,
        jobs_dir=jobs_dir,
        **extra,
    )
    return read_analysis_state(job_id, jobs_dir=jobs_dir)


# ── Moments I/O ──────────────────────────────────────────────────────────────


def read_moments(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Read detected moments for a project (read-only).

    Returns a list of moment dicts with: category, start_time, end_time,
    virality_score, caption, status, etc.
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _moments_path(job_id, jobs_dir_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "clips" in data:
        return data["clips"]
    return []


def _write_moments(job_id: str, jobs_dir: Path, moments: list[dict]) -> Path:
    path = _moments_path(job_id, jobs_dir)
    path.write_text(json.dumps(moments, indent=2) + "\n", encoding="utf-8")
    return path


# ── Detection runner ─────────────────────────────────────────────────────────


def run_detection_call(prompt_text: str, *, provider: str | None = None,
                       model: str | None = None, dry_run: bool = False) -> list[dict]:
    """Run the detection API call and return parsed clips.

    This is a pure function: it takes prompt text and returns parsed JSON.
    It does not write files, construct paths, or depend on CLI invocation.

    Raises on API failure or unparseable response.
    """
    selected_provider = provider or get_provider("detection")

    if dry_run:
        return []

    if selected_provider == "openai":
        return _run_openai(prompt_text, model=model)
    elif selected_provider == "ollama":
        return _run_ollama(prompt_text, model=model)
    else:
        raise ConfigurationError(f"unknown detection provider '{selected_provider}'")


def _run_openai(prompt_text: str, *, model: str | None = None) -> list[dict]:
    from .api import make_openai_client
    selected_model = model or os.getenv("DEFAULT_OPENAI_MODEL") or get_model("detection")
    client = make_openai_client()
    response = client.responses.create(
        model=selected_model,
        input=prompt_text,
        temperature=0.2,
    )
    raw = response.output_text
    return _parse_clips_json(raw)


def _run_ollama(prompt_text: str, *, model: str | None = None) -> list[dict]:
    import requests as _requests
    from .config import load_config
    from .provider_service import detection_model as _detection_model
    selected_model = model or _detection_model()
    url = os.getenv("OLLAMA_URL", "http://localhost:11434/api/generate")
    request_options = {"temperature": 0.2}
    payload = {
        "model": selected_model,
        "prompt": prompt_text,
        "stream": False,
        "format": DETECTION_JSON_SCHEMA,
        "options": request_options,
    }
    timeout = load_config()["providers"]["timeout"]
    started = _time.monotonic()

    def _log(status: str, error: str = "") -> None:
        logger.info(
            "detection_request stage=detection provider=ollama model=%s prompt_chars=%d "
            "options=%s timeout=%ss status=%s elapsed=%.2fs%s",
            selected_model,
            len(prompt_text),
            sorted(request_options.keys()),
            timeout,
            status,
            _time.monotonic() - started,
            f" error={error}" if error else "",
        )

    try:
        resp = _requests.post(url, json=payload, timeout=timeout)
        _log(str(resp.status_code))
        resp.raise_for_status()
        raw = resp.json()["response"]
        return _parse_clips_json(raw, strict=True)
    except _requests.HTTPError as exc:
        body_error = _response_error_body(exc)
        safe_error = _safe_error_message(body_error)
        _log("HTTP_ERROR", safe_error)
        raise _OllamaDetectionError(safe_error, status_code=getattr(exc.response, "status_code", None)) from exc
    except Exception as exc:  # noqa: BLE001
        _log("ERROR", _safe_error_message(str(exc)))
        raise


class _OllamaDetectionError(RuntimeError):
    """Raised when the Ollama detection call fails.

    Carries the safe model-level error text from the Ollama response body so
    the operator sees the actual cause (e.g. a ggml device-lost) instead of
    only the HTTP status line. Never includes request bodies or secrets.
    """

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        self.status_code = status_code
        prefix = f"Ollama detection failed (HTTP {status_code}): " if status_code else "Ollama detection failed: "
        super().__init__(f"{prefix}{message}")


def _response_error_body(exc: Exception) -> str:
    """Extract the safe ``error`` field from an Ollama HTTP error response.

    Ollama returns ``{"error": "..."}`` bodies on 4xx/5xx; surfacing that text
    makes detection failures diagnosable without exposing raw response bodies.
    """
    response = getattr(exc, "response", None)
    body = getattr(response, "text", "")
    if not isinstance(body, str) or not body:
        return str(exc)
    try:
        parsed = json.loads(body)
    except (json.JSONDecodeError, TypeError):
        return str(exc)
    if isinstance(parsed, dict) and isinstance(parsed.get("error"), str):
        return parsed["error"]
    return str(exc)


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    if not stripped.startswith("```"):
        return text
    first_newline = stripped.find("\n")
    if first_newline < 0:
        return text
    body = stripped[first_newline + 1:]
    if body.rstrip().endswith("```"):
        return body.rstrip()[:-3].strip()
    return text


def _find_complete_json_value(text: str) -> str | None:
    opening = {"[": "]", "{": "}"}
    candidates: list[str] = []
    start = 0
    while start < len(text):
        char = text[start]
        if char not in opening:
            start += 1
            continue
        stack = [opening[char]]
        in_string = False
        escaped = False
        for index in range(start + 1, len(text)):
            current = text[index]
            if in_string:
                if escaped:
                    escaped = False
                elif current == "\\":
                    escaped = True
                elif current == '"':
                    in_string = False
                continue
            if current == '"':
                in_string = True
            elif current in opening:
                stack.append(opening[current])
            elif stack and current == stack[-1]:
                stack.pop()
                if not stack:
                    candidate = text[start:index + 1]
                    try:
                        json.loads(candidate)
                    except json.JSONDecodeError:
                        break
                    candidates.append(candidate)
                    start = index
                    break
        if len(candidates) > 1:
            return None
        start += 1
    return candidates[0] if len(candidates) == 1 else None


def _extract_json_text(raw: str) -> str:
    text = _strip_markdown_fence(raw).strip()
    try:
        json.loads(text)
        return text
    except json.JSONDecodeError:
        extracted = _find_complete_json_value(text)
        if extracted is not None:
            return extracted
    raise ValueError("detection response is not valid JSON")


def _parse_clips_json(raw: str, *, strict: bool = False) -> list[dict]:
    try:
        parsed = json.loads(_extract_json_text(raw))
    except json.JSONDecodeError as exc:
        raise ValueError("detection response is not valid JSON") from exc
    if isinstance(parsed, list):
        clips = parsed
    elif isinstance(parsed, dict) and "clips" in parsed:
        clips = parsed["clips"]
    else:
        raise ValueError("detection response is not a JSON array of clips")
    if not isinstance(clips, list) or not all(isinstance(item, dict) for item in clips):
        raise ValueError("detection response is not a JSON array of clips")
    if strict:
        _validate_detection_clips(clips)
    return clips


def _validate_detection_clips(clips: list[dict]) -> None:
    if not 3 <= len(clips) <= 5:
        raise ValueError("detection response must contain 3-5 clips")
    for index, clip in enumerate(clips, start=1):
        missing = DETECTION_CLIP_FIELDS - set(clip)
        if missing:
            raise ValueError(f"detection clip {index} missing required fields: {', '.join(sorted(missing))}")
        extra = set(clip) - DETECTION_CLIP_FIELDS
        if extra:
            raise ValueError(f"detection clip {index} has unsupported fields: {', '.join(sorted(extra))}")
        if clip.get("category") not in DETECTION_CATEGORIES:
            raise ValueError(f"detection clip {index} has invalid category")
        for field in ("start_time", "end_time"):
            value = clip.get(field)
            if not isinstance(value, str) or not value.isdigit():
                raise ValueError(f"detection clip {index} has invalid {field}")
        if int(clip["end_time"]) <= int(clip["start_time"]):
            raise ValueError(f"detection clip {index} has non-positive duration")
        for field in ("virality_score", "legacy_value"):
            value = clip.get(field)
            if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 10:
                raise ValueError(f"detection clip {index} has invalid {field}")
        notes = clip.get("platform_notes")
        if not isinstance(notes, dict) or set(notes) != {"tiktok", "reels", "shorts"}:
            raise ValueError(f"detection clip {index} has invalid platform_notes")
        string_fields = DETECTION_CLIP_FIELDS - {"virality_score", "legacy_value", "platform_notes"}
        if any(not isinstance(clip.get(field), str) or not clip.get(field).strip() for field in string_fields):
            raise ValueError(f"detection clip {index} has empty required text")


# ── Full analysis orchestrator ──────────────────────────────────────────────


def analyze_project(job_id: str, *, jobs_dir: str | Path | None = None,
                    provider: str | None = None, model: str | None = None,
                    dry_run: bool = False) -> dict:
    """Run the full analysis workflow for a project.

    This is the main entry point for Analyze Game. It:
    1. Reads the job record
    2. Validates the profile supports analysis
    3. Updates analysis state to RUNNING
    4. Generates the prompt
    5. Runs the detection API call
    6. Builds the clip manifest / moments
    7. Persists results
    8. Returns the analysis result

    On any failure, updates the job record with error state and re-raises.

    Delegates to prompt_generation and clip_manifest services. Never shells
    into CLI scripts.
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    from .pilot import read_job as _read_job

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()

    # 1. Read the job
    try:
        job = _read_job(job_id, jobs_dir=jobs_dir_path)
    except Exception as exc:
        raise ValueError(f"job '{job_id}' not found: {exc}") from exc

    project_id = job.get("project_id", "football")
    source_id = job.get("source_id", "")
    pilot_id = job.get("pilot_id", "")
    intake_path = job.get("intake_manifest_path", "")

    # 2. Validate profile
    if not analysis_supported(project_id):
        update_analysis_state(
            job_id, status="NEEDS ATTENTION",
            stage="", error=f"Analysis support for {project_id.title()} is not available yet.",
            jobs_dir=jobs_dir_path,
        )
        return {
            "ok": False,
            "error": f"Analysis support for {project_id.title()} is not available yet.",
            "status": "NEEDS ATTENTION",
        }

    try:
        preflight = _analysis_preflight(job, jobs_dir_path)
        if not preflight["ok"]:
            update_analysis_state(
                job_id,
                status="NEEDS ATTENTION",
                stage="",
                error=preflight["error"],
                jobs_dir=jobs_dir_path,
                **preflight.get("extra", {}),
            )
            return {"ok": False, "error": preflight["error"], "status": "NEEDS ATTENTION"}

        _ACTIVE_ANALYSES.add(job_id)
        # 3. Update state to RUNNING only after preflight succeeds.
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_PREPARING, jobs_dir=jobs_dir_path)

        source_file = preflight["source_file"]
        match_name = preflight["match_name"]

        # 5. Transcribe (check for existing transcript first, auto-transcribe if missing)
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_TRANSCRIBING, jobs_dir=jobs_dir_path)
        transcript = _ensure_transcription(source_file, match_name, project_id, jobs_dir_path, job_id)

        # 6. Generate prompt
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_UNDERSTANDING, jobs_dir=jobs_dir_path)
        prompt_result = build_prompt(
            transcript=transcript,
            match_name=match_name,
            profile=project_id,
        )

        # 7. Run detection
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_FINDING, jobs_dir=jobs_dir_path)
        clips = run_detection_call(
            prompt_result["prompt"],
            provider=provider,
            model=model,
            dry_run=dry_run,
        )

        # 8. Build clip manifest / moments
        update_analysis_state(job_id, status="RUNNING", stage=STAGE_PREPARING_RESULTS, jobs_dir=jobs_dir_path)
        league = resolve_project_profile(project_id).get("league", "WORLD_CUP")
        source_video_name = Path(source_file).name

        manifest_result = build_clip_manifest(
            clips,
            league=league,
            match_name=match_name,
            source_video=source_video_name,
            profile=project_id,
        )

        # 9. Persist results
        moments_data = manifest_result["rows"]
        _write_moments(job_id, jobs_dir_path, moments_data)

        manifest_path = jobs_dir_path / "ANALYSIS" / f"{job_id}_manifest.csv"
        import csv as _csv
        with manifest_path.open("w", newline="", encoding="utf-8") as handle:
            writer = _csv.DictWriter(handle, fieldnames=manifest_result["fieldnames"])
            writer.writeheader()
            writer.writerows(moments_data)

        # 10. Update job record with final state
        job = update_analysis_state(
            job_id, status="COMPLETE", stage="",
            jobs_dir=jobs_dir_path,
            analysis_moments_path=str(_moments_path(job_id, jobs_dir_path)),
            analysis_manifest_path=str(manifest_path),
            analysis_manifest_count=len(moments_data),
        )

        return {
            "ok": True,
            "status": "COMPLETE",
            "moments_count": len(moments_data),
            "manifest_path": str(manifest_path),
            "profile": project_id,
            "match_name": match_name,
        }

    except Exception as exc:
        safe_error = _safe_error_message(exc)
        state = read_analysis_state(job_id, jobs_dir=jobs_dir_path)
        extra = {}
        if state.get("analysis_stage") == STAGE_TRANSCRIBING:
            extra = {"transcription_status": "FAILED", "transcription_error": safe_error}
        update_analysis_state(
            job_id, status="FAILED", stage="",
            error=safe_error,
            jobs_dir=jobs_dir_path,
            **extra,
        )
        return {
            "ok": False,
            "error": safe_error,
            "status": "FAILED",
        }
    finally:
        _ACTIVE_ANALYSES.discard(job_id)


def _analysis_preflight(job: dict, jobs_dir: Path) -> dict:
    """Validate local analysis prerequisites before entering RUNNING."""
    from .pilot import validate_intake
    from .transcription import find_existing_transcript
    from .utils import ROOT

    job_id = job.get("job_id", "")
    intake_path = job.get("intake_manifest_path", "")
    if not intake_path or not Path(intake_path).exists():
        return {"ok": False, "error": "Intake manifest not found; cannot determine source media."}

    try:
        intake_data = json.loads(Path(intake_path).read_text(encoding="utf-8"))
    except Exception:
        return {"ok": False, "error": "Intake manifest could not be read."}

    report = validate_intake(intake_data, check_source=True, check_rights=True)
    if not report.get("execution_ready"):
        codes = ", ".join(report.get("validation_codes", [])[:5])
        return {"ok": False, "error": f"Project is not execution-ready for analysis. Validation codes: {codes}"}

    media = intake_data.get("media", {}) if isinstance(intake_data.get("media"), dict) else {}
    source_file = media.get("local_file_path", "")
    source_path = Path(source_file) if isinstance(source_file, str) else Path("")
    if not source_file or not source_path.exists() or not source_path.is_file() or not os.access(source_path, os.R_OK):
        return {"ok": False, "error": "Source media is missing or unreadable."}

    project_id = job.get("project_id", "football")
    league = resolve_project_profile(project_id).get("league", "WORLD_CUP")
    existing = find_existing_transcript(source_file, ROOT, league)
    match_name = media.get("match_or_event_name", f"{job.get('pilot_id', '')} {job.get('source_id', '')}")
    if existing is not None:
        return {"ok": True, "source_file": source_file, "match_name": match_name, "transcript_reuse": True}

    if not shutil.which("ffmpeg"):
        return {
            "ok": False,
            "error": "FFmpeg is required for transcription but was not found on PATH.",
            "extra": {"transcription_status": "FAILED", "transcription_error": "FFmpeg is required for transcription but was not found on PATH."},
        }

    provider = get_provider("transcription")
    if provider == "faster-whisper" and importlib.util.find_spec("faster_whisper") is None:
        message = "Missing dependency. Run: pip install faster-whisper"
        return {
            "ok": False,
            "error": message,
            "extra": {"transcription_status": "FAILED", "transcription_error": message},
        }

    return {"ok": True, "source_file": source_file, "match_name": match_name, "transcript_reuse": False}


def _safe_error_message(exc: Exception) -> str:
    """Convert an exception to a safe human-readable message.

    Never exposes secrets, environment values, credential-bearing URLs,
    or giant stack traces.
    """
    msg = str(exc)
    # Truncate very long messages
    if len(msg) > 500:
        msg = msg[:500] + "..."
    # Filter out lines that look like secrets or env values
    safe_parts = []
    for line in msg.split("\n"):
        line = line.strip()
        if not line:
            continue
        lower = line.lower()
        if any(kw in lower for kw in ("key", "token", "secret", "password", "credential", "api_key")):
            continue
        if "http" in lower and ("@" in line or "token" in lower):
            continue
        safe_parts.append(line)
    return " ".join(safe_parts) if safe_parts else "Analysis failed. Check the project details for more information."


def _ensure_transcription(source_file: str, match_name: str, profile: str,
                          jobs_dir: Path, job_id: str) -> Path:
    """Ensure a transcript exists for the source file.

    Checks for existing transcript first (reuse). If not found, automatically
    transcribes the source video. Updates analysis state throughout.
    Returns the transcript path.

    Raises on transcription failure (after updating job state).
    """
    from .utils import ROOT, slugify
    from .transcription import find_existing_transcript, transcribe_source

    league = resolve_project_profile(profile).get("league", "WORLD_CUP")

    # Check for existing valid transcript
    existing = find_existing_transcript(source_file, ROOT, league)
    if existing is not None:
        update_analysis_state(
            job_id, status="RUNNING", stage=STAGE_TRANSCRIBING,
            jobs_dir=jobs_dir,
            transcription_reused=True,
            transcription_status="READY",
            transcription_reference=str(existing),
            transcription_error="",
        )
        return existing

    # No valid transcript — auto-transcribe
    update_analysis_state(
        job_id, status="RUNNING", stage=STAGE_TRANSCRIBING,
        jobs_dir=jobs_dir,
        transcription_reused=False,
        transcription_status="RUNNING",
        transcription_error="",
    )

    result = transcribe_source(
        source_file,
        league=league,
        root=ROOT,
    )

    if not result["ok"]:
        raise RuntimeError(f"Transcription failed: {result}")

    transcript_path = Path(result["transcript_path"])
    if not transcript_path.exists():
        raise RuntimeError(f"Transcription completed but transcript not found at {transcript_path}")

    update_analysis_state(
        job_id, status="RUNNING", stage=STAGE_TRANSCRIBING,
        jobs_dir=jobs_dir,
        transcription_status="READY",
        transcription_reference=str(transcript_path),
        transcription_error="",
    )
    return transcript_path
