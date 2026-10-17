"""Explicit renderer adapter registry."""

from __future__ import annotations

from .chatcut_adapter import ChatCutRendererAdapter
from .ffmpeg_adapter import FFmpegRendererAdapter


_ADAPTERS = {
    "FFMPEG": FFmpegRendererAdapter,
    "CHATCUT": ChatCutRendererAdapter,
}


def get_renderer_adapter(renderer: str):
    key = renderer.strip().upper()
    if key not in _ADAPTERS:
        raise ValueError(f"unknown renderer: {renderer}")
    return _ADAPTERS[key]()


def list_renderer_adapters() -> list[str]:
    return sorted(_ADAPTERS)
