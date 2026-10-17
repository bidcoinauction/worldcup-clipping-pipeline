"""ChatCut handoff adapter.

No direct ChatCut API is assumed. This adapter exports a deterministic package
that a future direct API/SDK adapter can consume without changing EditPlan.
"""

from __future__ import annotations

from pathlib import Path

from pipeline.edit_plan_models import EditPlan
from pipeline.edit_handoff_service import prepare_chatcut_handoff_v1


class ChatCutRendererAdapter:
    renderer_id = "CHATCUT"

    def can_render(self, edit_plan: EditPlan) -> tuple[bool, str]:
        if edit_plan.renderer != self.renderer_id:
            return False, "EditPlan selected a different renderer."
        return True, "ChatCut handoff package can be prepared."

    def prepare(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict:
        return self.render_or_export(edit_plan, output_dir=output_dir)

    def render_or_export(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict:
        ok, reason = self.can_render(edit_plan)
        if not ok:
            return {"ok": False, "status": "FAILED", "renderer": self.renderer_id, "error": reason}
        return prepare_chatcut_handoff_v1(edit_plan, output_dir=output_dir)

    def health(self) -> dict:
        return {"renderer": self.renderer_id, "ready": True, "mode": "handoff_package"}
