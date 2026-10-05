"""Structured core + integration system health."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path
from typing import Any

from . import runtime_db
from .integration_service import integration_health_report
from .provider_service import detection_provider, detection_provider_status, edit_provider, edit_provider_status, story_provider, story_provider_status
from .safety import safe_operator_message
from .version import application_version


def _check(check_id: str, status: str, operator_message: str, *, technical_detail: str = "",
           recommended_action: str = "") -> dict[str, Any]:
    return {
        "check_id": check_id,
        "status": status,
        "operator_message": operator_message,
        "technical_detail": technical_detail,
        "recommended_action": recommended_action,
    }


def _ffmpeg_available(name: str) -> bool:
    return shutil.which(name) is not None


def _whisper_importable() -> bool:
    try:
        import faster_whisper  # noqa: F401
        return True
    except ImportError:
        return False


def _openai_configured() -> bool:
    import os
    return bool(os.environ.get("OPENAI_API_KEY"))


def _runtime_writable(db_path: Path) -> bool:
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".clipper_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def core_health_report(db_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Run fast deterministic core checks (no external API calls)."""
    db_path_obj = Path(db_path) if db_path is not None else runtime_db.default_runtime_db_path()
    checks: list[dict[str, Any]] = []

    checks.append(_check(
        "python_version", "PASS" if sys.version_info >= (3, 11) else "WARN",
        f"Python {sys.version.split()[0]}",
        technical_detail=sys.version,
        recommended_action="Python 3.11 or 3.12 is recommended.",
    ))

    try:
        runtime_db.initialize(db_path_obj)
        version = runtime_db.current_schema_version(db_path_obj)
        checks.append(_check(
            "runtime_db", "PASS" if runtime_db.schema_is_current(db_path_obj) else "WARN",
            f"Runtime DB ready (schema v{version})",
            technical_detail=str(db_path_obj),
        ))
    except Exception as exc:  # noqa: BLE001
        checks.append(_check(
            "runtime_db", "FAIL",
            safe_operator_message(str(exc), "Runtime database could not be initialized."),
            technical_detail=str(db_path_obj),
            recommended_action="Run: python scripts/doctor.py and review the runtime DB error.",
        ))

    if _runtime_writable(db_path_obj):
        checks.append(_check("storage", "PASS", "Runtime directory is writable.", technical_detail=str(db_path_obj.parent)))
    else:
        checks.append(_check(
            "storage", "FAIL", "Runtime directory is not writable.",
            technical_detail=str(db_path_obj.parent),
            recommended_action="Fix permissions on the runtime data directory.",
        ))

    ffmpeg = _ffmpeg_available("ffmpeg")
    ffprobe = _ffmpeg_available("ffprobe")
    checks.append(_check("ffmpeg", "PASS" if ffmpeg else "WARN",
                         "FFmpeg available." if ffmpeg else "FFmpeg not found on PATH.",
                         recommended_action="Install FFmpeg for transcription audio and rendering." if not ffmpeg else ""))
    checks.append(_check("ffprobe", "PASS" if ffprobe else "WARN",
                         "FFprobe available." if ffprobe else "FFprobe not found on PATH.",
                         recommended_action="Install FFprobe for duration/media inspection." if not ffprobe else ""))

    if _whisper_importable():
        checks.append(_check("whisper", "PASS", "faster-whisper is importable."))
    else:
        checks.append(_check("whisper", "WARN", "faster-whisper is not installed.",
                             recommended_action="Install faster-whisper for local transcription."))

    needs_openai = (
        detection_provider() == "openai"
        or story_provider() == "openai"
        or edit_provider() == "openai"
    )
    if _openai_configured():
        checks.append(_check("openai", "PASS", "OpenAI API key is configured."))
    elif needs_openai:
        checks.append(_check("openai", "WARN", "OpenAI API key is not configured.",
                             recommended_action="Set OPENAI_API_KEY for the OpenAI-backed stage(s)."))
    else:
        checks.append(_check("openai", "PASS",
                             "OpenAI not configured (all model stages are fully local via Ollama)."))

    detection = detection_provider_status()
    if detection["ready"]:
        model = detection.get("model") or ""
        detail = f"Detection ready via {detection['configured_provider']}."
        if model:
            detail = f"Detection ready via {detection['configured_provider']} / {model}."
        checks.append(_check("detection_provider", "PASS", detail))
    else:
        checks.append(_check(
            "detection_provider", "WARN",
            "Detection is unavailable. No model provider is currently ready.",
            recommended_action="Start Ollama or configure OpenAI, then retry analysis.",
        ))

    story = story_provider_status()
    if story["ready"]:
        model = story.get("model") or ""
        detail = f"Story ready via {story['configured_provider']}."
        if model:
            detail = f"Story ready via {story['configured_provider']} / {model}."
        checks.append(_check("story_provider", "PASS", detail))
    else:
        checks.append(_check("story_provider", "WARN",
                             story["message"],
                             recommended_action="Start Ollama or configure OpenAI, then retry story generation."))

    edit = edit_provider_status()
    if edit["ready"]:
        model = edit.get("model") or ""
        detail = f"Edit ready via {edit['configured_provider']}."
        if model:
            detail = f"Edit ready via {edit['configured_provider']} / {model}."
        checks.append(_check("edit_provider", "PASS", detail))
    else:
        checks.append(_check("edit_provider", "WARN",
                             edit["message"],
                             recommended_action="Start Ollama or configure OpenAI, then retry edit brief generation."))

    return checks


def provider_report() -> dict[str, Any]:
    return {
        "detection": detection_provider_status(),
        "story": story_provider_status(),
        "edit": edit_provider_status(),
    }


def full_health_report(db_path: str | Path | None = None) -> dict[str, Any]:
    return {
        "version": application_version(),
        "core": core_health_report(db_path),
        "providers": provider_report(),
        "integrations": integration_health_report(),
    }


def doctor_ok(report: dict[str, Any], *, strict: bool = False) -> bool:
    """Decide whether the report is healthy."""
    fail = "FAIL"
    fail_statuses = {fail}
    if strict:
        fail_statuses.add("WARN")
    for check in report["core"]:
        if check["status"] in fail_statuses:
            return False
    return True