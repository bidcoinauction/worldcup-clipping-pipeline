"""Operator-safe error handling helpers."""

from __future__ import annotations

import re

_SECRET_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9_-]{20,}", re.IGNORECASE),
    re.compile(r"(?i)(api[_-]?key|token|secret|password|webhook|credential)[=:]\s*\S+"),
    re.compile(r"https?://[^\s]+@[^\s]+"),
)


def sanitize_text(text: str) -> str:
    """Redact obvious secret values from an operator-facing message."""
    if not text:
        return text
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub("[REDACTED]", text)
    return text


def safe_operator_message(message: str, default: str = "An unexpected error occurred.") -> str:
    cleaned = sanitize_text(str(message or "")).strip()
    if not cleaned:
        return default
    return cleaned[:500] + ("..." if len(cleaned) > 500 else "")