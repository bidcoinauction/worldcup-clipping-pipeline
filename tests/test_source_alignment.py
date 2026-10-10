from __future__ import annotations

from pathlib import Path

from pipeline.moment_models import Moment
from pipeline.research_models import ResearchEvent
from pipeline.research_service import persist_research_fixture
from pipeline.runtime_service import get_artifact, get_research_event, list_project_moments, list_project_research, register_artifact, upsert_moment, upsert_project, upsert_research_event
from pipeline.source_alignment import (
    ALIGNED,
    AVAILABLE,
    ESTIMATED,
    OUTSIDE_SOURCE,
    VERIFIED,
    BoundedTranscriptionService,
    BoundedSourceAlignmentService,
    SourceAnchor,
    SourceCoverageObservation,
    is_usable_source_moment,
)
from pipeline.source_clock import FIRST_HALF, SECOND_HALF, SourceClockSegment, estimate_source_time, event_position_provenance, source_clock_payload


def _disable_real_media_tools(monkeypatch):
    monkeypatch.setattr("pipeline.source_alignment.shutil.which", lambda name: None)


def _event(project_id="p1", minute=34, event_id="event_34"):
    return ResearchEvent(
        event_id=event_id,
        research_id="research_1",
        project_id=project_id,
        match_minute=minute,
        universal_event_type="SCORE",
        sport_event_type="penalty goal",
        team="Argentina",
        participants=[{"name": "Lionel Messi"}],
        headline="Messi penalty",
        confidence=0.9,
    )


def _project(project_id="p1"):
    return upsert_project(project_id=project_id, job_id=project_id, profile="football", sport="football", display_name=project_id, status="READY")


def _coverage(source_artifact_id="art_source", duration=3600.0):
    return SourceCoverageObservation(
        source_artifact_id=source_artifact_id,
        source_duration_seconds=duration,
        partial_source=False,
        estimated_match_coverage_start_minute=0.0,
        estimated_match_coverage_end_minute=duration / 60.0,
        anchor=SourceAnchor(anchor_type="KICKOFF", media_time_seconds=0.0),
    )


def _clocked_coverage(source_artifact_id="art_source", duration=7200.0):
    return SourceCoverageObservation(
        source_artifact_id=source_artifact_id,
        source_duration_seconds=duration,
        partial_source=False,
        estimated_match_coverage_start_minute=0.0,
        estimated_match_coverage_end_minute=duration / 60.0,
        anchor=SourceAnchor(anchor_type="KICKOFF", media_time_seconds=0.0),
        source_clock=source_clock_payload([
            SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0.0, source_time_start=412.4, confidence="MEDIUM", method="test"),
            SourceClockSegment(segment_type=SECOND_HALF, match_clock_start_seconds=45 * 60.0, source_time_start=3488.2, confidence="MEDIUM", method="test"),
        ]),
    )


def test_source_coverage_observation_and_anchor(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "match_1st.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3332.8})

    observation = BoundedSourceAlignmentService().inspect_source_coverage("p1", artifact.artifact_id)

    assert observation.anchor.anchor_type == "KICKOFF"
    assert observation.anchor.media_time_seconds == 0.0
    assert observation.partial_source is True
    assert observation.source_duration_seconds == 3332.8


def test_estimated_source_position_and_cache_reuse(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3600})
    event = _event()

    service = BoundedSourceAlignmentService()
    first = service.align_event("p1", artifact.artifact_id, event, coverage=_coverage(artifact.artifact_id, 3600))
    second = service.align_event("p1", artifact.artifact_id, event, coverage=_coverage(artifact.artifact_id, 3600))

    assert first.availability_status == AVAILABLE
    assert first.alignment_status == ESTIMATED
    assert first.estimated_source_time == 34 * 60
    assert first.confidence == "LOW"
    assert second.evidence[1]["cached"] is True


def test_bounded_audio_extraction_command_is_windowed():
    cmd = BoundedTranscriptionService(model="tiny").ffmpeg_extract_command("source.mp4", "clip.wav", start=120.5, end=150.5)
    assert "-ss" in cmd and cmd[cmd.index("-ss") + 1] == "120.500"
    assert "-t" in cmd and cmd[cmd.index("-t") + 1] == "30.000"
    assert "-i" in cmd and cmd[cmd.index("-i") + 1] == "source.mp4"
    assert "clip.wav" == cmd[-1]


def test_bounded_transcriber_cache_reuse(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source)

    def fake_run(cmd, **kwargs):
        Path(cmd[-1]).parent.mkdir(parents=True, exist_ok=True)
        import wave
        with wave.open(cmd[-1], "wb") as wf:
            wf.setnchannels(1); wf.setsampwidth(2); wf.setframerate(16000); wf.writeframes(b"\0\0" * 16000)
        class R: pass
        return R()

    monkeypatch.setattr("pipeline.source_alignment.subprocess.run", fake_run)
    monkeypatch.setattr("pipeline.source_alignment.shutil.which", lambda name: "ffmpeg")
    monkeypatch.setattr("pipeline.whisper_transcriber.transcribe", lambda path, model, **kwargs: ("Messi penalty goal Argentina", [{"start": 2.0, "end": 4.0, "text": "Messi penalty goal Argentina"}]))
    service = BoundedTranscriptionService(model="tiny")
    first = service.transcribe_window(project_id="p1", source_artifact_id=artifact.artifact_id, source_path=source, start=10, end=20)
    second = service.transcribe_window(project_id="p1", source_artifact_id=artifact.artifact_id, source_path=source, start=10, end=20)
    assert first.ok is True
    assert second.cached is True
    assert second.segments[0]["start"] == 12.0


def test_outside_source_event_skips_alignment(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "part1.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 5400})
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(minute=119, event_id="event_119"), coverage=_coverage(artifact.artifact_id, 5400))

    assert result.availability_status == OUTSIDE_SOURCE
    assert result.refined_source_time is None


def test_bounded_transcript_promotes_estimated_to_aligned(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("[2030.0s - 2050.0s] Messi penalty goal for Argentina\n", encoding="utf-8")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3600})
    register_artifact(project_id="p1", artifact_type="transcript", path=transcript)

    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 3600))

    assert result.availability_status == AVAILABLE
    assert result.alignment_status == ALIGNED
    assert result.confidence == "HIGH"
    assert result.validation_method == "bounded_transcript_term_match"


def test_player_name_only_remains_estimated(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("[2030.0s - 2050.0s] Alvarez on the ball for Argentina\n", encoding="utf-8")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3600})
    register_artifact(project_id="p1", artifact_type="transcript", path=transcript)
    event = ResearchEvent(**{**_event(event_id="alvarez", minute=39).to_dict(), "participants": [{"name": "Julián Álvarez"}], "sport_event_type": "goal", "headline": "Álvarez goal"})

    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, event, coverage=_coverage(artifact.artifact_id, 3600))

    assert result.alignment_status == ESTIMATED
    assert result.confidence == "LOW"


def test_team_only_and_generic_event_terms_remain_estimated(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    transcript = tmp_path / "transcript.txt"
    transcript.write_text("[2030.0s - 2050.0s] Argentina attacking, goal kick follows\n", encoding="utf-8")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3600})
    register_artifact(project_id="p1", artifact_type="transcript", path=transcript)

    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 3600))

    assert result.alignment_status == ESTIMATED


def test_candidate_windows_are_capped_and_deduped(tmp_path, monkeypatch):
    monkeypatch.setattr("pipeline.source_alignment.shutil.which", lambda name: None)
    service = BoundedTranscriptionService(model="tiny")
    windows, energy = service.candidate_windows(source_path="source.mp4", estimated=100.0, broad_start=0.0, broad_end=200.0, max_candidates=3, window_seconds=36.0)
    assert len(windows) == 1
    assert windows[0]["start"] == 82.0
    assert windows[0]["end"] == 118.0


def test_clock_only_does_not_promote_to_aligned_when_transcript_empty(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project()
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3600})
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 3600))
    assert result.availability_status == AVAILABLE
    assert result.alignment_status == ESTIMATED


def test_operator_confirmation_verifies_moment(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    moment = upsert_moment(Moment(
        moment_id="m1", project_id="p1", source_artifact_id=None, sport="football", universal_event_type="SCORE", sport_event_type="goal",
        start_seconds=100, peak_seconds=112, end_seconds=130, metadata={"availability_status": AVAILABLE, "alignment_status": ESTIMATED},
    ))

    result = BoundedSourceAlignmentService().confirm_moment_alignment("p1", moment.moment_id, action="confirm")

    assert result["alignment_status"] == VERIFIED


def test_usable_source_moment_helper_requires_aligned_or_verified():
    assert not is_usable_source_moment({"metadata": {"availability_status": AVAILABLE, "alignment_status": ESTIMATED}})
    assert is_usable_source_moment({"metadata": {"availability_status": AVAILABLE, "alignment_status": ALIGNED}})
    assert is_usable_source_moment({"metadata": {"availability_status": AVAILABLE, "alignment_status": VERIFIED}})
    assert not is_usable_source_moment({"metadata": {"availability_status": OUTSIDE_SOURCE, "alignment_status": VERIFIED}})


def test_alignment_interface_accepts_sport_agnostic_event_position(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    event = _event(minute=0, event_id="basketball_like")
    event = ResearchEvent(**{**event.to_dict(), "metadata": {"event_position": {"sport": "basketball", "period": "Q4", "clock": "01:24", "display_label": "Q4 01:24"}}})
    source = tmp_path / "source.mp4"
    source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 600})
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, event, coverage=_coverage(artifact.artifact_id, 600))
    assert result.research_event_id == "basketball_like"


def test_first_half_source_anchor_estimates_with_pregame_offset(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(minute=34), coverage=_clocked_coverage(artifact.artifact_id))
    assert result.estimated_source_time == 412.4 + 34 * 60
    assert result.evidence[0]["source_clock_segment"]["segment_type"] == FIRST_HALF


def test_second_half_anchor_is_independent_of_halftime_duration(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    event = _event(minute=50, event_id="second_half")
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, event, coverage=_clocked_coverage(artifact.artifact_id))
    assert result.estimated_source_time == 3488.2 + 5 * 60
    assert result.evidence[0]["source_clock_segment"]["segment_type"] == SECOND_HALF


def test_stoppage_position_provenance_is_preserved():
    event = ResearchEvent(**{**_event(minute=46).to_dict(), "metadata": {"event_position": "45+1"}})
    provenance = event_position_provenance(event)
    assert provenance["display"] == "45+1"
    assert provenance["estimated_match_seconds"] == 46 * 60


def test_ninety_plus_stoppage_position_provenance_is_preserved():
    event = ResearchEvent(**{**_event(minute=95).to_dict(), "metadata": {"event_position": "90+5"}})
    provenance = event_position_provenance(event)
    assert provenance["display"] == "90+5"
    assert provenance["estimated_match_seconds"] == 95 * 60


def test_operator_kickoff_confirmation_and_shift_controls(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService()
    later = service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=400.0, action="later", shift_seconds=10.0)
    assert later["cursor_seconds"] == 410.0
    confirmed = service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=410.0, action="confirm")
    assert confirmed["segment"]["confidence"] == "HIGH"
    assert confirmed["segment"]["confirmed_by_operator"] is True


def test_kickoff_review_cursor_moves_without_confirming_anchor(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 200})
    service = BoundedSourceAlignmentService()
    service.inspect_source_coverage("p1", artifact.artifact_id)
    later = service.confirm_source_anchor("p1", artifact.artifact_id, action="later", shift_seconds=15.0)
    assert later["cursor_seconds"] == 15.0
    stored = get_artifact(artifact.artifact_id).metadata["source_clock"]
    assert stored["segments"][0]["confidence"] == "LOW"
    assert stored["status"] == "UNCALIBRATED"


def test_kickoff_review_cursor_clamps_to_source_bounds(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 20})
    service = BoundedSourceAlignmentService()
    service.inspect_source_coverage("p1", artifact.artifact_id)
    assert service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=2, action="earlier")["cursor_seconds"] == 0.0
    assert service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=18, action="later")["cursor_seconds"] == 20.0


def test_confirm_kickoff_persists_verified_anchor_and_recalculates_estimates(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    upsert_moment(Moment(moment_id="est", project_id="p1", source_artifact_id=artifact.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2040, end_seconds=2, metadata={"availability_status": AVAILABLE, "alignment_status": ESTIMATED, "estimated_match_seconds": 34 * 60, "estimated_media_time": 2040}))
    result = BoundedSourceAlignmentService().confirm_source_anchor("p1", artifact.artifact_id, source_time_start=412.0, action="confirm", recheck=False)
    from pipeline.runtime_service import get_moment
    assert result["source_clock"]["status"] == "VERIFIED"
    assert result["segment"]["method"] == "operator_confirmation"
    assert result["segment"]["confirmed_by_operator"] is True
    assert get_moment("est").peak_seconds == 2452.0


def test_reconfirmation_updates_anchor_provenance(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService()
    service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=400.0, action="confirm", recheck=False)
    second = service.confirm_source_anchor("p1", artifact.artifact_id, source_time_start=420.0, action="confirm", recheck=False)
    assert second["segment"]["source_time_start"] == 420.0
    assert second["segment"]["method"] == "operator_confirmation"


def test_single_aligned_event_only_proposes_anchor_not_overwrite(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService()
    result = service.align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 7200))
    aligned = type(result)(**{**result.to_dict(), "alignment_status": "ALIGNED", "confidence": "HIGH", "refined_source_time": 2457.0})
    service._propose_anchor_from_alignment("p1", artifact.artifact_id, _event(), aligned)
    updated = BoundedSourceAlignmentService().inspect_source_coverage("p1", artifact.artifact_id).source_clock
    assert updated["segments"][0]["source_time_start"] == 0.0
    assert updated["anchor_candidates"]


def test_multiple_consistent_aligned_events_improve_anchor(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService()
    base = service.align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 7200))
    for minute, refined in [(34, 2455.0), (39, 2758.0)]:
        event = _event(minute=minute, event_id=f"e{minute}")
        aligned = type(base)(**{**base.to_dict(), "research_event_id": event.event_id, "alignment_status": "ALIGNED", "confidence": "HIGH", "refined_source_time": refined})
        service._propose_anchor_from_alignment("p1", artifact.artifact_id, event, aligned)
    updated = service.inspect_source_coverage("p1", artifact.artifact_id).source_clock
    assert updated["segments"][0]["method"] == "multiple_aligned_event_consistency"
    assert 414.0 <= updated["segments"][0]["source_time_start"] <= 419.0


def test_inconsistent_anchor_candidates_stay_uncertain(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService(); base = service.align_event("p1", artifact.artifact_id, _event(), coverage=_coverage(artifact.artifact_id, 7200))
    for minute, refined in [(34, 2455.0), (39, 2850.0)]:
        event = _event(minute=minute, event_id=f"bad{minute}")
        aligned = type(base)(**{**base.to_dict(), "research_event_id": event.event_id, "alignment_status": "ALIGNED", "confidence": "HIGH", "refined_source_time": refined})
        service._propose_anchor_from_alignment("p1", artifact.artifact_id, event, aligned)
    updated = service.inspect_source_coverage("p1", artifact.artifact_id).source_clock
    assert updated["segments"][0]["method"] == "source_start_heuristic"


def test_recalculate_estimated_moments_but_not_aligned_or_verified(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "source.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 7200})
    for status in [ESTIMATED, ALIGNED, VERIFIED, OUTSIDE_SOURCE, "NOT_FOUND"]:
        availability = AVAILABLE if status in {ESTIMATED, ALIGNED, VERIFIED} else status
        alignment = status if status in {ESTIMATED, ALIGNED, VERIFIED} else "UNALIGNED"
        upsert_moment(Moment(moment_id=status, project_id="p1", source_artifact_id=artifact.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2, end_seconds=3, metadata={"availability_status": availability, "alignment_status": alignment, "estimated_match_seconds": 34 * 60, "estimated_media_time": 2}))
    clock = source_clock_payload([SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0.0, source_time_start=412.0, confidence="HIGH")])
    BoundedSourceAlignmentService().recalculate_estimated_moments("p1", artifact.artifact_id, clock)
    from pipeline.runtime_service import get_moment
    assert get_moment(ESTIMATED).peak_seconds == 2452.0
    assert get_moment(ALIGNED).peak_seconds == 2
    assert get_moment(VERIFIED).peak_seconds == 2
    assert get_moment(OUTSIDE_SOURCE).peak_seconds == 2
    assert get_moment("NOT_FOUND").peak_seconds == 2
    assert get_moment(ESTIMATED).metadata["evidence"]["bounded_transcript"] is False


def test_outside_source_remains_outside_with_calibrated_clock(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "part.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 5400})
    result = BoundedSourceAlignmentService().align_event("p1", artifact.artifact_id, _event(minute=119), coverage=_clocked_coverage(artifact.artifact_id, duration=5400))
    assert result.availability_status == OUTSIDE_SOURCE


def test_source_clock_is_source_specific(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    a = tmp_path / "a.mp4"; b = tmp_path / "b.mp4"; a.write_bytes(b"media"); b.write_bytes(b"media")
    art_a = register_artifact(project_id="p1", artifact_type="source_media", path=a, metadata={"duration_seconds": 7200})
    art_b = register_artifact(project_id="p1", artifact_type="source_media", path=b, metadata={"duration_seconds": 7200})
    service = BoundedSourceAlignmentService()
    service.confirm_source_anchor("p1", art_a.artifact_id, source_time_start=500.0, action="confirm")
    assert service.inspect_source_coverage("p1", art_a.artifact_id).source_clock["segments"][0]["source_time_start"] == 500.0
    assert service.inspect_source_coverage("p1", art_b.artifact_id).source_clock["segments"][0]["source_time_start"] == 0.0


def test_source_clock_recalculation_does_not_move_other_source_moments(tmp_path, monkeypatch):
    from pipeline.runtime_service import upsert_project, register_artifact, upsert_moment, get_moment
    from pipeline.moment_models import Moment
    upsert_project(project_id="p_multi_clock", job_id="p_multi_clock", profile="football", sport="football", display_name="Multi Clock", status="READY")
    art_a = register_artifact(project_id="p_multi_clock", artifact_type="source_media", path=tmp_path / "first.mp4", metadata={"duration_seconds": 5000})
    art_b = register_artifact(project_id="p_multi_clock", artifact_type="source_media", path=tmp_path / "second.mp4", metadata={"duration_seconds": 5000})
    upsert_moment(Moment(moment_id="m_first", project_id="p_multi_clock", source_artifact_id=art_a.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=2040, end_seconds=2, metadata={"availability_status": AVAILABLE, "alignment_status": ESTIMATED, "match_minute": 34}))
    upsert_moment(Moment(moment_id="m_second", project_id="p_multi_clock", source_artifact_id=art_b.artifact_id, sport="football", universal_event_type="SCORE", sport_event_type="goal", start_seconds=1, peak_seconds=1440, end_seconds=2, metadata={"availability_status": AVAILABLE, "alignment_status": ESTIMATED, "match_minute": 69}))
    clock = source_clock_payload([SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0, source_time_start=180, confidence="HIGH", method="operator_confirmation")])

    result = BoundedSourceAlignmentService().recalculate_estimated_moments("p_multi_clock", art_a.artifact_id, clock)

    assert result["recalculated_count"] == 1
    assert get_moment("m_first").source_artifact_id == art_a.artifact_id
    assert get_moment("m_second").source_artifact_id == art_b.artifact_id
    assert get_moment("m_second").peak_seconds == 1440


def test_source_clock_estimate_helper_uses_half_segments():
    first = SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0, source_time_start=400)
    second = SourceClockSegment(segment_type=SECOND_HALF, match_clock_start_seconds=2700, source_time_start=3500)
    assert estimate_source_time(30 * 60, [first, second])[0] == 2200
    assert estimate_source_time(50 * 60, [first, second])[0] == 3800


def test_multi_source_research_routes_events_and_preserves_single_match_research(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.chdir(tmp_path)
    _project("arg_cro")
    first = tmp_path / "2022ArgCro_1st_3.mp4"; second = tmp_path / "2022ArgCro_2nd.mp4"
    first.write_bytes(b"media"); second.write_bytes(b"media")
    source1 = register_artifact(project_id="arg_cro", artifact_type="source_media", path=first, metadata={"duration_seconds": 3000.0, "source_roles": ["FIRST_HALF", "PARTIAL"]})
    source2 = register_artifact(project_id="arg_cro", artifact_type="source_media", path=second, metadata={"duration_seconds": 3000.0, "source_roles": ["SECOND_HALF"]})
    research, events = persist_research_fixture("arg_cro", {
        "sport": "football", "competition": "2022 FIFA World Cup", "date": "2022-12-13", "home_team": "Argentina", "away_team": "Croatia", "season": "2022", "stage": "Semifinal",
        "events": [
            {"match_minute": 34, "universal_event_type": "SCORE", "sport_event_type": "penalty goal", "team": "Argentina", "participants": [{"name": "Lionel Messi"}], "headline": "Messi opens the scoring"},
            {"match_minute": 39, "universal_event_type": "SCORE", "sport_event_type": "goal", "team": "Argentina", "participants": [{"name": "Julian Alvarez"}], "headline": "Alvarez breaks through"},
            {"match_minute": 69, "universal_event_type": "SCORE", "sport_event_type": "goal", "team": "Argentina", "participants": [{"name": "Julian Alvarez"}], "headline": "Alvarez finishes it"},
        ],
    }, source_artifact_id=source1.artifact_id)

    result = BoundedSourceAlignmentService().align_research_across_sources("arg_cro", research.research_id)

    assert result["ok"] is True
    assert len(list_project_research("arg_cro")) == 1
    by_minute = {event.match_minute: get_research_event(event.event_id).metadata["source_availabilities"] for event in events}
    assert by_minute[34][source1.artifact_id]["availability_status"] == AVAILABLE
    assert by_minute[34][source2.artifact_id]["availability_status"] == OUTSIDE_SOURCE
    assert by_minute[39][source1.artifact_id]["availability_status"] == AVAILABLE
    assert by_minute[39][source2.artifact_id]["availability_status"] == OUTSIDE_SOURCE
    assert by_minute[69][source1.artifact_id]["availability_status"] == OUTSIDE_SOURCE
    assert by_minute[69][source2.artifact_id]["availability_status"] == AVAILABLE
    moments = sorted(list_project_moments("arg_cro"), key=lambda m: m.metadata.get("match_minute"))
    assert [m.metadata.get("match_minute") for m in moments] == [34, 39, 69]
    assert moments[0].source_artifact_id == source1.artifact_id
    assert moments[1].source_artifact_id == source1.artifact_id
    assert moments[2].source_artifact_id == source2.artifact_id
    assert moments[2].metadata["source_availabilities"][source1.artifact_id]["availability_status"] == OUTSIDE_SOURCE


def test_second_half_source_clock_is_independent_for_second_half_role(tmp_path, monkeypatch):
    _disable_real_media_tools(monkeypatch)
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    _project()
    source = tmp_path / "second.mp4"; source.write_bytes(b"media")
    artifact = register_artifact(project_id="p1", artifact_type="source_media", path=source, metadata={"duration_seconds": 3000.0, "source_roles": ["SECOND_HALF"]})
    service = BoundedSourceAlignmentService()
    coverage = service.inspect_source_coverage("p1", artifact.artifact_id)
    segment = coverage.source_clock["segments"][0]
    assert segment["segment_type"] == SECOND_HALF
    assert segment["match_clock_start_seconds"] == 45 * 60
    confirmed = service.confirm_source_anchor("p1", artifact.artifact_id, segment_type=SECOND_HALF, source_time_start=125.0, action="confirm", recheck=False)
    assert confirmed["segment"]["match_clock_start_seconds"] == 45 * 60
    assert confirmed["segment"]["source_time_start"] == 125.0
