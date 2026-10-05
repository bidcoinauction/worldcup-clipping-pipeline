"""Detection / story provider readiness.

Readiness is checked without live paid API calls. Provider setup remains
operator-controlled; this only reports availability so operators are not shown
raw connection-refused text as the primary message.
"""

from __future__ import annotations

import os
from typing import Any


class DetectionProviderUnavailable(Exception):
    """Raised before detection when no supported provider is ready."""

    def __init__(self, message: str = "Detection is unavailable. No model provider is currently ready.") -> None:
        super().__init__(message)


class StoryProviderUnavailable(Exception):
    """Raised before story generation when the selected provider is not ready."""

    def __init__(self, message: str = "Story generation is unavailable. Configure a provider, then retry.") -> None:
        super().__init__(message)


class EditProviderUnavailable(Exception):
    """Raised before edit brief generation when the selected provider is not ready."""

    def __init__(self, message: str = "Edit generation is unavailable. Configure a provider, then retry.") -> None:
        super().__init__(message)


def _ollama_base_url() -> str:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
    if url.endswith("/api/generate"):
        return url[: -len("/api/generate")]
    if url.endswith("/"):
        return url[:-1]
    return url


def _ollama_ready(model: str | None = None) -> dict[str, Any]:
    import requests

    base = _ollama_base_url()
    expected_model = model or detection_model()
    try:
        resp = requests.get(f"{base}/api/tags", timeout=2)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        model_available = any(expected_model in name or name in expected_model for name in models)
        if not model_available:
            return {"ready": False, "message": f"Ollama is reachable but model '{expected_model}' is not pulled."}
        return {"ready": True, "message": "Ollama is ready."}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "message": "Ollama is not reachable. Start the local Ollama service."}


def _openai_ready() -> dict[str, Any]:
    if not os.environ.get("OPENAI_API_KEY"):
        return {"ready": False, "message": "Set OPENAI_API_KEY to enable OpenAI-backed stages."}
    try:
        import openai  # noqa: F401
        return {"ready": True, "message": "OpenAI is configured."}
    except ImportError:
        return {"ready": False, "message": "The openai package is not installed."}


def detection_model() -> str:
    """Canonical Detection model.

    The stage-specific configured detection model from ``pipeline_config.json``
    (``models.detection``) is the single source of truth for Detection so
    provider readiness (doctor/system) and the actual detection request agree.
    """
    from .config import get_model
    return get_model("detection")


def detection_provider_status() -> dict[str, Any]:
    """Structured detection provider readiness (no live paid calls)."""
    from .config import get_provider

    configured = get_provider("detection")
    model = detection_model()
    ollama = _ollama_ready()
    openai = _openai_ready()
    providers = {"ollama": ollama, "openai": openai}

    ready_via = None
    for name, status in providers.items():
        if status["ready"]:
            ready_via = name
            break

    configured_ready = providers.get(configured, {}).get("ready", False)
    return {
        "configured_provider": configured,
        "providers": providers,
        "configured_ready": configured_ready,
        "ready": configured_ready,
        "ready_via": ready_via if ready_via else None,
        "model": model if configured == "ollama" else None,
    }


def detection_available() -> tuple[bool, str, dict[str, Any]]:
    """Return (available, provider_or_empty, status)."""
    status = detection_provider_status()
    if status["ready"]:
        return True, status["configured_provider"], status
    return False, "", status


def detection_provider() -> str:
    """Configured Detection provider (from config/pipeline_config.json)."""
    from .config import get_provider
    return get_provider("detection")


def story_provider() -> str:
    """Selected Story generation provider (STORY_PROVIDER, default openai)."""
    return os.environ.get("STORY_PROVIDER", "openai").strip().lower()


def edit_provider() -> str:
    """Selected Edit Brief provider (EDIT_PROVIDER, default = STORY_PROVIDER)."""
    return os.environ.get("EDIT_PROVIDER", story_provider()).strip().lower()


def story_model() -> str:
    return os.environ.get("OLLAMA_STORY_MODEL") or os.environ.get("OLLAMA_MODEL", "llama3.1")


def edit_model() -> str:
    return os.environ.get("OLLAMA_EDIT_MODEL") or os.environ.get("OLLAMA_MODEL", "llama3.1")


def ollama_generate(prompt: str, model: str | None = None, system_prompt: str | None = None,
                    json_mode: bool = False, temperature: float = 0.7) -> str:
    """Call Ollama and return the raw response text."""
    import requests

    selected_model = model or os.environ.get("OLLAMA_MODEL", "llama3.1")
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
    payload: dict[str, Any] = {
        "model": selected_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": temperature},
    }
    if system_prompt:
        payload["system"] = system_prompt
    if json_mode:
        payload["format"] = "json"
    resp = requests.post(url, json=payload, timeout=600)
    resp.raise_for_status()
    return resp.json()["response"]


def stage_provider_status(provider_name: str, model: str | None = None) -> dict[str, Any]:
    """Readiness for a stage configured with the given provider name."""
    configured = provider_name
    if configured == "ollama":
        status = _ollama_ready(model=model)
        return {
            "configured_provider": "ollama",
            "model": model or os.environ.get("OLLAMA_MODEL", "llama3.1"),
            "ready": status["ready"],
            "message": status["message"],
            "providers": {"ollama": status},
        }
    if configured == "openai":
        status = _openai_ready()
        return {
            "configured_provider": "openai",
            "ready": status["ready"],
            "message": status["message"],
            "providers": {"openai": status},
        }
    return {
        "configured_provider": configured,
        "ready": False,
        "message": f"Unknown provider: {configured}",
        "providers": {},
    }


def story_provider_status() -> dict[str, Any]:
    """Story generation readiness (selected provider)."""
    return stage_provider_status(story_provider(), model=story_model())


def edit_provider_status() -> dict[str, Any]:
    """Edit Brief generation readiness (selected provider)."""
    return stage_provider_status(edit_provider(), model=edit_model())


def require_detection_provider() -> None:
    available, provider, _status = detection_available()
    if not available:
        raise DetectionProviderUnavailable(
            "Detection is unavailable. No model provider is currently ready. Start Ollama or configure OpenAI, then retry."
        )


def require_story_provider() -> None:
    status = story_provider_status()
    if not status["ready"]:
        raise StoryProviderUnavailable(
            "Story generation is unavailable. Start Ollama or configure OpenAI, then retry."
        )


def require_edit_provider() -> None:
    status = edit_provider_status()
    if not status["ready"]:
        raise EditProviderUnavailable(
            "Edit generation is unavailable. Start Ollama or configure OpenAI, then retry."
        )
