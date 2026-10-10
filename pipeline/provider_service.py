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

    def __init__(self, message: str = "Story generation is unavailable. Configure a provider, then retry.", *, code: str = "STORY_PROVIDER_UNAVAILABLE", status: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status or {}


class EditProviderUnavailable(Exception):
    """Raised before edit brief generation when the selected provider is not ready."""

    def __init__(self, message: str = "Edit generation is unavailable. Configure a provider, then retry.", *, code: str = "EDIT_PROVIDER_UNAVAILABLE", status: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.status = status or {}


def _ollama_base_url() -> str:
    url = os.environ.get("OLLAMA_URL", "http://localhost:11434/api/generate")
    if url.endswith("/api/generate"):
        return url[: -len("/api/generate")]
    if url.endswith("/"):
        return url[:-1]
    return url


def _ollama_ready(model: str | None = None, *, allow_model_fallback: bool = False) -> dict[str, Any]:
    import requests

    base = _ollama_base_url()
    expected_model = model or detection_model()
    try:
        resp = requests.get(f"{base}/api/tags", timeout=2)
        resp.raise_for_status()
        models = [m.get("name", "") for m in resp.json().get("models", [])]
        selected = _select_ollama_model(expected_model, models, allow_fallback=allow_model_fallback)
        model_available = selected is not None
        if not model_available:
            return {"ready": False, "status": "MODEL_UNAVAILABLE", "message": f"Ollama is reachable but model '{expected_model}' is not pulled.", "models": models, "model": expected_model}
        return {"ready": True, "status": "READY", "message": "Ollama is ready.", "models": models, "model": selected}
    except Exception as exc:  # noqa: BLE001
        return {"ready": False, "status": "UNAVAILABLE", "message": "Ollama is not reachable. Start the local Ollama service.", "error": str(exc)}


def _select_ollama_model(preferred: str | None, models: list[str], *, allow_fallback: bool = False) -> str | None:
    if preferred and any(preferred == name or preferred in name or name in preferred for name in models):
        return next(name for name in models if preferred == name or preferred in name or name in preferred)
    if preferred and not allow_fallback:
        return None
    for candidate in ("llama3.1", "qwen2.5:3b"):
        match = next((name for name in models if candidate == name or candidate in name or name in candidate), None)
        if match:
            return match
    return models[0] if models else None


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


def _explicit_provider(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip().lower() if value and value.strip() else None


def story_provider() -> str:
    """Resolved Story provider. Explicit STORY_PROVIDER wins; otherwise local-only fallback."""
    status = story_provider_status()
    return str(status.get("configured_provider") or "")


def edit_provider() -> str:
    """Resolved Edit Brief provider. Explicit EDIT_PROVIDER wins; otherwise Story/local fallback."""
    status = edit_provider_status()
    return str(status.get("configured_provider") or "")


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


def stage_provider_status(provider_name: str, model: str | None = None, *, explicit: bool = True, stage: str = "story") -> dict[str, Any]:
    """Readiness for a stage configured with the given provider name."""
    configured = provider_name
    if configured == "ollama":
        status = _ollama_ready(model=model)
        return {
            "configured_provider": "ollama",
            "model": status.get("model") or model or os.environ.get("OLLAMA_MODEL", "llama3.1"),
            "ready": status["ready"],
            "status": status.get("status") or ("READY" if status["ready"] else "UNAVAILABLE"),
            "message": status["message"],
            "explicit": explicit,
            "stage": stage,
            "providers": {"ollama": status},
        }
    if configured == "openai":
        status = _openai_ready()
        return {
            "configured_provider": "openai",
            "ready": status["ready"],
            "status": "READY" if status["ready"] else "NOT_CONFIGURED",
            "message": status["message"],
            "explicit": explicit,
            "stage": stage,
            "providers": {"openai": status},
        }
    return {
        "configured_provider": configured,
        "ready": False,
        "status": "MISCONFIGURED",
        "message": f"Unknown provider: {configured}",
        "explicit": explicit,
        "stage": stage,
        "providers": {},
    }


def _local_fallback_status(*, stage: str, model: str) -> dict[str, Any]:
    allow_fallback = stage in {"story", "edit"} and not (os.environ.get("OLLAMA_MODEL") or os.environ.get("OLLAMA_STORY_MODEL") or os.environ.get("OLLAMA_EDIT_MODEL"))
    ollama = _ollama_ready(model=model, allow_model_fallback=allow_fallback)
    if ollama["ready"]:
        return {
            "configured_provider": "ollama",
            "model": ollama.get("model") or model,
            "ready": True,
            "status": "READY",
            "message": "Ollama is ready.",
            "explicit": False,
            "stage": stage,
            "providers": {"ollama": ollama},
        }
    return {
        "configured_provider": None,
        "model": model,
        "ready": False,
        "status": ollama.get("status") if ollama.get("status") == "MODEL_UNAVAILABLE" else "UNAVAILABLE",
        "message": "Story generation isn't available on this system." if stage == "story" else "Edit generation isn't available on this system.",
        "explicit": False,
        "stage": stage,
        "providers": {"ollama": ollama},
    }


def story_provider_status() -> dict[str, Any]:
    """Canonical Story generation readiness with explicit-provider precedence."""
    explicit = _explicit_provider("STORY_PROVIDER")
    if explicit:
        return stage_provider_status(explicit, model=story_model(), explicit=True, stage="story")
    return _local_fallback_status(stage="story", model=story_model())


def edit_provider_status() -> dict[str, Any]:
    """Canonical Edit Brief readiness with explicit-provider precedence."""
    explicit = _explicit_provider("EDIT_PROVIDER")
    if explicit:
        return stage_provider_status(explicit, model=edit_model(), explicit=True, stage="edit")
    story_explicit = _explicit_provider("STORY_PROVIDER")
    if story_explicit:
        return stage_provider_status(story_explicit, model=edit_model(), explicit=False, stage="edit")
    return _local_fallback_status(stage="edit", model=edit_model())


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
            status.get("message") or "Story generation isn't available on this system.",
            code=str(status.get("status") or "UNAVAILABLE"),
            status=status,
        )


def require_edit_provider() -> None:
    status = edit_provider_status()
    if not status["ready"]:
        raise EditProviderUnavailable(
            status.get("message") or "Edit generation isn't available on this system.",
            code=str(status.get("status") or "UNAVAILABLE"),
            status=status,
        )
