from __future__ import annotations

from pipeline.composition_service import football_default_composition, ffmpeg_video_filter_for_mode, handoff_composition_contract


def test_football_default_composition_mapping():
    assert football_default_composition("TACTICAL_SHIFT") == "WIDE_PLAY"
    assert football_default_composition("DEFENSIVE_PLAY") == "WIDE_PLAY"
    assert football_default_composition("CARD") == "PUNCH_IN"
    assert football_default_composition("CROWD_REACTION") == "REACTION"
    assert football_default_composition("SCORE", narrative_role="SETUP") == "WIDE_PLAY"


def test_wide_play_filter_preserves_aspect_with_foreground_and_background():
    filt = ffmpeg_video_filter_for_mode("WIDE_PLAY", input_label="in0", output_label="out0")
    assert "force_original_aspect_ratio=decrease" in filt
    assert "boxblur" in filt
    assert "overlay=(W-w)/2:(H-h)/2" in filt
    assert "scale=1080:-2" in filt


def test_handoff_composition_contract():
    contract = handoff_composition_contract("WIDE_PLAY")
    assert contract["composition_mode"] == "WIDE_PLAY"
    assert contract["foreground_fit"] == "contain_width_no_stretch"
    assert contract["background_treatment"] == "blurred_dimmed_fill"
