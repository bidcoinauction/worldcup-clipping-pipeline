"""Football-aware composition intent helpers."""

from __future__ import annotations

from typing import Any


COMPOSITION_MODES = {"WIDE_PLAY", "TRACKED_VERTICAL", "PUNCH_IN", "REACTION"}


def normalize_composition_mode(value: object | None) -> str:
    text = str(value or "").strip().upper()
    return text if text in COMPOSITION_MODES else "TRACKED_VERTICAL"


def football_default_composition(universal_event_type: str, *, sport_event_type: str = "", narrative_role: str = "") -> str:
    event = str(universal_event_type or "").upper()
    sport = str(sport_event_type or "").lower()
    role = str(narrative_role or "").upper()
    if event in {"TACTICAL_SHIFT", "DEFENSIVE_PLAY", "ATTEMPT", "SAVE"}:
        return "WIDE_PLAY"
    if event == "CONFRONTATION":
        return "WIDE_PLAY"
    if event == "FOUL":
        return "PUNCH_IN" if "aftermath" in sport else "WIDE_PLAY"
    if event == "CARD":
        return "PUNCH_IN"
    if event == "CROWD_REACTION":
        return "REACTION"
    if event == "CELEBRATION":
        return "PUNCH_IN"
    if event == "SCORE":
        if role in {"SETUP", "BUILD", "ESCALATION"} or "buildup" in sport:
            return "WIDE_PLAY"
        if role in {"AFTERMATH", "FINISH"} or "celebration" in sport:
            return "PUNCH_IN"
        return "WIDE_PLAY"
    return "TRACKED_VERTICAL"


def composition_for_instruction(instruction, beat=None, moment=None) -> str:
    explicit = (instruction.metadata or {}).get("composition_mode") or (instruction.crop or {}).get("composition_mode")
    if explicit:
        return normalize_composition_mode(explicit)
    if beat and (beat.metadata or {}).get("composition_mode"):
        return normalize_composition_mode((beat.metadata or {}).get("composition_mode"))
    if moment:
        return football_default_composition(moment.universal_event_type, sport_event_type=moment.sport_event_type, narrative_role=beat.narrative_role if beat else "")
    return "TRACKED_VERTICAL"


def human_composition_label(mode: str) -> str:
    return {
        "WIDE_PLAY": "Play View",
        "TRACKED_VERTICAL": "Player Focus",
        "PUNCH_IN": "Close-Up",
        "REACTION": "Reaction",
    }.get(normalize_composition_mode(mode), "Player Focus")


def handoff_composition_contract(mode: str) -> dict[str, Any]:
    mode = normalize_composition_mode(mode)
    if mode == "WIDE_PLAY":
        return {
            "composition_mode": mode,
            "safe_area_intent": "preserve_play_context",
            "foreground_fit": "contain_width_no_stretch",
            "background_treatment": "blurred_dimmed_fill",
        }
    if mode == "PUNCH_IN":
        return {"composition_mode": mode, "safe_area_intent": "subject_close_up", "foreground_fit": "center_crop_when_safe", "background_treatment": "none"}
    if mode == "REACTION":
        return {"composition_mode": mode, "safe_area_intent": "reaction_subject", "foreground_fit": "center_crop_when_safe", "background_treatment": "none"}
    return {"composition_mode": "TRACKED_VERTICAL", "safe_area_intent": "center_action_safe", "foreground_fit": "vertical_crop", "background_treatment": "none"}


def ffmpeg_video_filter_for_mode(mode: str, *, input_label: str, output_label: str) -> str:
    mode = normalize_composition_mode(mode)
    if mode == "WIDE_PLAY":
        return (
            f"[{input_label}]split=2[fgsrc{output_label}][bgsrc{output_label}];"
            f"[bgsrc{output_label}]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,setsar=1,boxblur=24:2,eq=brightness=-0.12:saturation=0.85[bg{output_label}];"
            f"[fgsrc{output_label}]scale=1080:-2:force_original_aspect_ratio=decrease,setsar=1[fg{output_label}];"
            f"[bg{output_label}][fg{output_label}]overlay=(W-w)/2:(H-h)/2,setsar=1[{output_label}]"
        )
    if mode in {"PUNCH_IN", "REACTION"}:
        return f"[{input_label}]crop='min(iw,ih*9/16)':ih,scale=1080:1920,setsar=1[{output_label}]"
    return f"[{input_label}]crop='min(iw,ih*9/16)':ih,scale=1080:1920,setsar=1[{output_label}]"
