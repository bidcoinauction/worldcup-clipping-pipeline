"""Channel / platform preset model and registry."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .edit_brief import FORMAT_TREATMENTS
from .rendering import RENDER_MODES

PLATFORMS = frozenset({"TIKTOK", "INSTAGRAM_REELS", "YOUTUBE_SHORTS", "X", "GENERIC_VERTICAL", "GENERIC_HORIZONTAL"})


@dataclass(frozen=True)
class ChannelPreset:
    preset_id: str
    name: str
    platform: str
    channel_name: str
    aspect_ratio: str
    width: int
    height: int
    fps: float
    max_duration_seconds: int | None = None
    render_profile: str = "REFERENCE"
    format_treatment: str = "SHORT"
    caption_style: str = ""
    safe_area_profile: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.platform not in PLATFORMS:
            raise ValueError(f"invalid platform: {self.platform}")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("preset dimensions must be positive")
        if self.fps <= 0:
            raise ValueError("preset fps must be positive")
        if self.render_profile not in RENDER_MODES:
            raise ValueError(f"invalid render profile: {self.render_profile}")
        if self.format_treatment not in FORMAT_TREATMENTS:
            raise ValueError(f"invalid format treatment: {self.format_treatment}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "preset_id": self.preset_id,
            "name": self.name,
            "platform": self.platform,
            "channel_name": self.channel_name,
            "aspect_ratio": self.aspect_ratio,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "max_duration_seconds": self.max_duration_seconds,
            "render_profile": self.render_profile,
            "format_treatment": self.format_treatment,
            "caption_style": self.caption_style,
            "safe_area_profile": self.safe_area_profile,
            "created_at": self.created_at,
            "metadata": dict(self.metadata),
        }


CHANNEL_PRESET_REGISTRY: dict[str, ChannelPreset] = {
    "tiktok_vertical": ChannelPreset(
        preset_id="tiktok_vertical",
        name="TikTok Vertical",
        platform="TIKTOK",
        channel_name="Football Aura",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        fps=30.0,
        max_duration_seconds=60,
        render_profile="REFERENCE",
        format_treatment="SHORT",
        caption_style="short_caption",
        safe_area_profile="vertical_safe",
    ),
    "instagram_reels": ChannelPreset(
        preset_id="instagram_reels",
        name="Instagram Reels",
        platform="INSTAGRAM_REELS",
        channel_name="Football Aura",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        fps=30.0,
        max_duration_seconds=90,
        render_profile="REFERENCE",
        format_treatment="SHORT",
        caption_style="reels_caption",
        safe_area_profile="vertical_safe",
    ),
    "youtube_shorts": ChannelPreset(
        preset_id="youtube_shorts",
        name="YouTube Shorts",
        platform="YOUTUBE_SHORTS",
        channel_name="Football Stories",
        aspect_ratio="9:16",
        width=1080,
        height=1920,
        fps=30.0,
        max_duration_seconds=60,
        render_profile="REFERENCE",
        format_treatment="SHORT",
        caption_style="shorts_caption",
        safe_area_profile="vertical_safe",
    ),
    "x_horizontal": ChannelPreset(
        preset_id="x_horizontal",
        name="X Horizontal",
        platform="X",
        channel_name="Football Clips",
        aspect_ratio="16:9",
        width=1280,
        height=720,
        fps=30.0,
        max_duration_seconds=120,
        render_profile="REFERENCE",
        format_treatment="MEDIUM",
        caption_style="x_caption",
        safe_area_profile="horizontal_safe",
    ),
    "review_proxy": ChannelPreset(
        preset_id="review_proxy",
        name="Review Proxy",
        platform="GENERIC_HORIZONTAL",
        channel_name="Internal Review",
        aspect_ratio="16:9",
        width=1280,
        height=720,
        fps=30.0,
        max_duration_seconds=None,
        render_profile="REFERENCE",
        format_treatment="MEDIUM",
        caption_style="",
        safe_area_profile="horizontal_safe",
    ),
}


def list_channel_presets() -> list[ChannelPreset]:
    return [preset for preset in CHANNEL_PRESET_REGISTRY.values()]


def get_channel_preset(preset_id: str) -> ChannelPreset | None:
    return CHANNEL_PRESET_REGISTRY.get(preset_id)


def resolve_channel_preset(preset_id: str) -> ChannelPreset:
    preset = get_channel_preset(preset_id)
    if preset is None:
        raise ValueError(f"unknown channel preset: {preset_id}")
    return preset


def validate_preset_compatibility(preset: ChannelPreset, *, format_treatment: str, render_profile: str) -> None:
    if format_treatment not in FORMAT_TREATMENTS:
        raise ValueError(f"invalid format treatment: {format_treatment}")
    if render_profile not in RENDER_MODES:
        raise ValueError(f"invalid render profile: {render_profile}")
    if preset.format_treatment != format_treatment:
        raise ValueError(
            f"preset '{preset.preset_id}' requires format '{preset.format_treatment}' but '{format_treatment}' was requested"
        )
    if preset.render_profile != render_profile:
        raise ValueError(
            f"preset '{preset.preset_id}' requires render profile '{preset.render_profile}' but '{render_profile}' was requested"
        )