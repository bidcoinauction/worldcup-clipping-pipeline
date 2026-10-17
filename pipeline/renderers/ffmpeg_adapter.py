"""FFmpeg renderer adapter for simple deterministic EditPlans."""

from __future__ import annotations

from pathlib import Path

from pipeline.edit_plan_models import EditPlan


class FFmpegRendererAdapter:
    renderer_id = "FFMPEG"

    def can_render(self, edit_plan: EditPlan) -> tuple[bool, str]:
        if edit_plan.renderer != self.renderer_id:
            return False, "EditPlan selected a different renderer."
        if edit_plan.metadata.get("motion_graphics_used"):
            return False, "FFmpeg adapter only accepts simple deterministic plans in this slice."
        return True, "FFmpeg can render simple EditPlans via the existing EDL renderer."

    def prepare(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict:
        ok, reason = self.can_render(edit_plan)
        return {"ok": ok, "renderer": self.renderer_id, "reason": reason, "edit_plan_id": edit_plan.edit_plan_id}

    def render_or_export(self, edit_plan: EditPlan, *, output_dir: str | Path | None = None) -> dict:
        ok, reason = self.can_render(edit_plan)
        if not ok:
            return {"ok": False, "status": "FAILED", "renderer": self.renderer_id, "error": reason}
        return {
            "ok": True,
            "status": "READY",
            "renderer": self.renderer_id,
            "edit_plan_id": edit_plan.edit_plan_id,
            "handoff": False,
            "message": "Use existing render_edl/generate_canonical_render for FFmpeg execution.",
        }

    def health(self) -> dict:
        return {"renderer": self.renderer_id, "ready": True, "mode": "existing_ffmpeg_boundary"}
