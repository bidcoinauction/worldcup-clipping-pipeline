"""Renderer adapter protocol for EditPlans."""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from pipeline.edit_plan_models import EditPlan


class RendererAdapter(Protocol):
    renderer_id: str

    def can_render(self, edit_plan: EditPlan) -> tuple[bool, str]: ...

    def prepare(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict: ...

    def render_or_export(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict: ...

    def health(self) -> dict: ...
