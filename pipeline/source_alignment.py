"""Bounded source alignment for research-first workflows.

This module keeps reusable historical research separate from source-specific
observations. V2 is deliberately conservative: clock math can make an event
AVAILABLE/ESTIMATED, but only bounded evidence or operator confirmation can
promote it to ALIGNED/VERIFIED.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import wave
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
import unicodedata

from .moment_models import Moment
from .research_service import event_search_window, seed_moments_from_research
from .source_clock import (
    FIRST_HALF,
    HIGH as CLOCK_HIGH,
    LOW as CLOCK_LOW,
    MEDIUM as CLOCK_MEDIUM,
    SECOND_HALF,
    SOURCE_CLOCK_VERSION,
    SourceClockSegment,
    estimate_source_time,
    event_match_seconds,
    event_position_provenance,
    improve_anchor_from_candidates,
    segments_from_payload,
    source_clock_payload,
)
from .runtime_service import (
    get_artifact,
    get_moment,
    get_project,
    list_project_artifacts,
    list_project_moments,
    list_research_events,
    register_artifact,
    upsert_moment,
    upsert_project,
    upsert_research_event,
)


ALIGNMENT_VERSION = "bounded_alignment_v2"
BOUNDED_TRANSCRIBER_VERSION = "bounded_transcriber_v1"
AVAILABLE = "AVAILABLE"
OUTSIDE_SOURCE = "OUTSIDE_SOURCE"
UNKNOWN = "UNKNOWN"
NOT_FOUND = "NOT_FOUND"
UNALIGNED = "UNALIGNED"
ESTIMATED = "ESTIMATED"
ALIGNED = "ALIGNED"
VERIFIED = "VERIFIED"
CONFIDENCE_LOW = "LOW"
CONFIDENCE_MEDIUM = "MEDIUM"
CONFIDENCE_HIGH = "HIGH"
SOURCE_ROLES = frozenset({"FIRST_HALF", "SECOND_HALF", "FULL_MATCH", "PARTIAL", "UNKNOWN"})


def _alignment_rank(payload: dict[str, Any] | None) -> tuple[int, int]:
    if not payload:
        return (0, 0)
    availability_rank = {OUTSIDE_SOURCE: 1, UNKNOWN: 2, AVAILABLE: 3}.get(payload.get("availability_status"), 0)
    alignment_rank = {UNALIGNED: 0, ESTIMATED: 1, ALIGNED: 2, VERIFIED: 3}.get(payload.get("alignment_status"), 0)
    return (availability_rank, alignment_rank)


def _best_source_availability(source_availabilities: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if not source_availabilities:
        return {}
    return max(source_availabilities.values(), key=_alignment_rank)


def source_roles(metadata: dict[str, Any] | None) -> set[str]:
    raw = (metadata or {}).get("source_roles") or (metadata or {}).get("source_role") or ["UNKNOWN"]
    if isinstance(raw, str):
        raw = [raw]
    roles = {str(role).strip().upper() for role in raw if str(role).strip().upper() in SOURCE_ROLES}
    return roles or {"UNKNOWN"}


def source_segments_for_roles(roles: set[str], duration: float | None) -> list[SourceClockSegment]:
    if "SECOND_HALF" in roles and "FIRST_HALF" not in roles and "FULL_MATCH" not in roles:
        return [SourceClockSegment(segment_type=SECOND_HALF, match_clock_start_seconds=45 * 60.0, source_time_start=0.0, source_time_end=duration, confidence=CLOCK_LOW, method="source_role_observation")]
    return [SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0.0, source_time_start=0.0, source_time_end=duration, confidence=CLOCK_LOW, method="source_start_heuristic")]


@dataclass(frozen=True)
class SourceAnchor:
    anchor_type: str
    media_time_seconds: float | None
    match_time_seconds: float = 0.0
    confidence: str = CONFIDENCE_LOW
    validation_method: str = "source_heuristic"
    evidence: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


@dataclass(frozen=True)
class SourceCoverageObservation:
    source_artifact_id: str
    source_duration_seconds: float | None
    partial_source: bool
    estimated_match_coverage_start_minute: float | None
    estimated_match_coverage_end_minute: float | None
    has_prematch_material: bool | None = None
    has_halftime_break: bool | None = None
    reaches_regulation_end: bool | None = None
    has_extra_time: bool | None = None
    confidence: str = CONFIDENCE_LOW
    validation_method: str = "source_heuristic"
    anchor: SourceAnchor | None = None
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_clock: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = dict(self.__dict__)
        data["anchor"] = self.anchor.to_dict() if self.anchor else None
        data["evidence"] = list(self.evidence)
        data["source_clock"] = dict(self.source_clock)
        return data


@dataclass(frozen=True)
class AlignmentResult:
    research_event_id: str
    availability_status: str
    alignment_status: str
    estimated_source_time: float | None
    search_window_start: float | None
    search_window_end: float | None
    refined_source_time: float | None
    clip_window_start: float | None
    clip_window_end: float | None
    confidence: str
    validation_method: str
    evidence: list[dict[str, Any]] = field(default_factory=list)
    source_observations: dict[str, Any] = field(default_factory=dict)
    needs_operator_confirmation: bool = False
    bounded_transcript_duration_seconds: float = 0.0
    elapsed_seconds: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


def is_usable_source_moment(moment: Moment | dict[str, Any]) -> bool:
    metadata = moment.metadata if isinstance(moment, Moment) else (moment.get("metadata") or {})
    return metadata.get("availability_status") == AVAILABLE and metadata.get("alignment_status") in {ALIGNED, VERIFIED}


def _ffprobe_duration(path: Path) -> float | None:
    if not shutil.which("ffprobe") or not path.exists():
        return None
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True,
            text=True,
            check=True,
            timeout=20,
        )
        return float(result.stdout.strip())
    except Exception:
        return None


def _cache_root() -> Path:
    return Path("data") / "pilot" / "alignment_cache"


def _audio_cache_root() -> Path:
    return Path("data") / "pilot" / "bounded_audio"


def _normalize_text(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(ch for ch in normalized if not unicodedata.combining(ch))
    return " ".join("".join(ch.lower() if ch.isalnum() else " " for ch in ascii_text).split())


def _cache_key(source_artifact_id: str, event_id: str, start: float | None, end: float | None, anchor: SourceAnchor | None) -> str:
    raw = json.dumps({"version": ALIGNMENT_VERSION, "source": source_artifact_id, "event": event_id, "start": start, "end": end, "anchor": anchor.to_dict() if anchor else None}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _transcript_cache_key(source_artifact_id: str, start: float, end: float, *, model: str, language: str | None) -> str:
    raw = json.dumps({"version": BOUNDED_TRANSCRIBER_VERSION, "source": source_artifact_id, "start": round(start, 3), "end": round(end, 3), "model": model, "language": language or "auto"}, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:20]


def _adaptive_search_windows(estimated: float | None, source_duration: float | None = None) -> list[dict[str, float]]:
    if estimated is None:
        return []
    rows = []
    for label, radius in (("initial", 45.0), ("expanded", 60.0), ("final", 90.0)):
        start = max(0.0, float(estimated) - radius)
        end = float(estimated) + radius
        if source_duration is not None:
            end = min(float(source_duration), end)
        if end > start:
            rows.append({"stage": label, "start": start, "end": end, "radius_seconds": radius})
    return rows


def _event_terms(event) -> list[str]:
    terms = [event.team or "", event.headline or "", event.sport_event_type or "", event.universal_event_type or ""]
    for participant in event.participants:
        if isinstance(participant, dict) and participant.get("name"):
            terms.append(str(participant["name"]))
    extras = []
    if event.universal_event_type == "SCORE":
        extras.extend(["goal", "scores", "scored", "one nil", "two nil", "three nil"])
    if "penalty" in (event.sport_event_type or "").lower() or "penalty" in (event.headline or "").lower():
        extras.append("penalty")
    terms.extend(extras)
    cleaned: list[str] = []
    for term in terms:
        for part in str(term).replace("Á", "A").replace("á", "a").split():
            if len(part) >= 4:
                cleaned.append(part.lower())
    return sorted(set(cleaned))


def _event_signal_terms(event) -> dict[str, list[str]]:
    players: list[str] = []
    for participant in event.participants:
        if isinstance(participant, dict) and participant.get("name"):
            name = _normalize_text(str(participant["name"]))
            parts = [part for part in name.split() if len(part) >= 4]
            players.extend([name, *parts])
    team = [_normalize_text(event.team or "")] if event.team else []
    event_terms = []
    text = _normalize_text(f"{event.sport_event_type} {event.headline} {event.universal_event_type}")
    if event.universal_event_type == "SCORE" or "goal" in text:
        event_terms.extend(["goal", "scores", "scored", "lead", "ahead", "puts them ahead", "takes the lead", "equalizes", "finish", "finishes", "strikes", "one nil", "one-nil", "1 0", "two nil", "three nil"])
    if event.universal_event_type == "CARD" or "red card" in text:
        event_terms.extend(["red card", "sent off", "sending off", "second yellow", "dismissed", "booking", "down to ten"])
    if "penalty" in text:
        event_terms.extend(["penalty", "spot kick", "from the spot", "penalty kick"])
    return {
        "player": sorted({term for term in players if term}),
        "team": sorted({term for term in team if term}),
        "event_type": sorted({term for term in event_terms if term}),
    }


def _term_in_text(term: str, text: str) -> bool:
    norm = _normalize_text(term)
    return bool(norm and norm in text)


def _match_transcript_evidence(event, segments: list[dict[str, Any]]) -> dict[str, Any]:
    terms = _event_signal_terms(event)
    matched: dict[str, list[dict[str, Any]]] = {"player": [], "team": [], "event_type": []}
    for segment in segments:
        text = _normalize_text(str(segment.get("text") or ""))
        for category, category_terms in terms.items():
            for term in category_terms:
                if _term_in_text(term, text):
                    matched[category].append({"term": term, "segment_start": segment.get("start"), "segment_end": segment.get("end"), "text": segment.get("text", "")})
    player = bool(matched["player"])
    event_type = bool(matched["event_type"])
    team = bool(matched["team"])
    categories = [name for name, rows in matched.items() if rows]
    if player and event_type and team:
        confidence = CONFIDENCE_HIGH
        signals = ["PLAYER_NAME_MATCH", "EVENT_TYPE_MATCH", "TEAM_MATCH", "MULTIPLE_TERM_CLUSTER"]
    elif player and event_type:
        confidence = CONFIDENCE_MEDIUM
        signals = ["PLAYER_NAME_MATCH", "EVENT_TYPE_MATCH"]
    elif player:
        confidence = CONFIDENCE_LOW
        signals = ["PLAYER_NAME_MATCH"]
    elif event_type and team:
        confidence = CONFIDENCE_LOW
        signals = ["EVENT_TYPE_MATCH", "TEAM_MATCH"]
    else:
        confidence = CONFIDENCE_LOW
        signals = []
    matched_starts = [float(row["segment_start"]) for rows in matched.values() for row in rows if row.get("segment_start") is not None]
    refined = min(matched_starts) if matched_starts else None
    return {"terms": terms, "matched": matched, "categories": categories, "signals": signals, "confidence": confidence, "refined_source_time": refined}


def _audio_energy_signal(audio_path: Path, *, window_start: float) -> dict[str, Any]:
    try:
        with wave.open(str(audio_path), "rb") as wf:
            frames = wf.readframes(wf.getnframes())
            sample_width = wf.getsampwidth()
            rate = wf.getframerate() or 16000
        if sample_width != 2 or not frames:
            return {"method": "pcm16_rms", "available": False}
        import array
        samples = array.array("h")
        samples.frombytes(frames)
        chunk = max(1, rate)
        peaks: list[tuple[float, float]] = []
        for index in range(0, len(samples), chunk):
            part = samples[index:index + chunk]
            if not part:
                continue
            rms = (sum(float(x) * float(x) for x in part) / len(part)) ** 0.5
            peaks.append((window_start + (index / rate), rms))
        if not peaks:
            return {"method": "pcm16_rms", "available": False}
        avg = sum(v for _t, v in peaks) / len(peaks)
        peak_t, peak_v = max(peaks, key=lambda row: row[1])
        return {"method": "pcm16_rms", "available": True, "peak_timestamp": peak_t, "relative_spike_strength": (peak_v / avg) if avg else 0.0}
    except Exception as exc:
        return {"method": "pcm16_rms", "available": False, "error": str(exc)}


@dataclass(frozen=True)
class BoundedTranscriptResult:
    ok: bool
    text: str = ""
    segments: list[dict[str, Any]] = field(default_factory=list)
    provider: str = "faster-whisper"
    model: str = ""
    language: str | None = None
    window_start: float = 0.0
    window_end: float = 0.0
    duration_seconds: float = 0.0
    audio_path: str = ""
    cache_path: str = ""
    cached: bool = False
    extraction_seconds: float = 0.0
    model_load_seconds: float = 0.0
    transcription_seconds: float = 0.0
    audio_energy: dict[str, Any] = field(default_factory=dict)
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dict(self.__dict__)


class BoundedTranscriptionService:
    def __init__(self, *, model: str | None = None, language: str | None = None):
        self.model = model or os.getenv("BOUNDED_WHISPER_MODEL") or os.getenv("DEFAULT_WHISPER_MODEL") or "base"
        self.language = language or os.getenv("BOUNDED_TRANSCRIBE_LANGUAGE") or None

    def _paths(self, source_artifact_id: str, start: float, end: float) -> tuple[str, Path, Path]:
        key = _transcript_cache_key(source_artifact_id, start, end, model=self.model, language=self.language)
        return key, _audio_cache_root() / f"{key}.wav", _cache_root() / f"{key}.bounded_transcript.json"

    def ffmpeg_extract_command(self, source_path: str | Path, audio_path: str | Path, *, start: float, end: float) -> list[str]:
        duration = max(0.0, float(end) - float(start))
        return ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{float(start):.3f}", "-t", f"{duration:.3f}", "-i", str(source_path), "-vn", "-ac", "1", "-ar", "16000", "-acodec", "pcm_s16le", str(audio_path)]

    def transcribe_window(self, *, project_id: str, source_artifact_id: str, source_path: str | Path, start: float, end: float) -> BoundedTranscriptResult:
        if end <= start:
            return BoundedTranscriptResult(ok=False, window_start=start, window_end=end, error="empty window")
        key, audio_path, cache_path = self._paths(source_artifact_id, start, end)
        if cache_path.exists():
            data = json.loads(cache_path.read_text(encoding="utf-8"))
            allowed = BoundedTranscriptResult.__dataclass_fields__.keys()
            return BoundedTranscriptResult(**{k: v for k, v in {**data, "cached": True, "cache_path": str(cache_path)}.items() if k in allowed})
        audio_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        if not shutil.which("ffmpeg"):
            return BoundedTranscriptResult(ok=False, model=self.model, window_start=start, window_end=end, duration_seconds=end - start, error="ffmpeg unavailable")
        extract_started = time.perf_counter()
        cmd = self.ffmpeg_extract_command(source_path, audio_path, start=start, end=end)
        try:
            subprocess.run(cmd, capture_output=True, text=True, check=True, timeout=max(30, int(end - start) + 30))
            extraction_seconds = time.perf_counter() - extract_started
        except Exception as exc:
            return BoundedTranscriptResult(ok=False, model=self.model, window_start=start, window_end=end, duration_seconds=end - start, audio_path=str(audio_path), error=f"ffmpeg bounded extraction failed: {exc}")
        energy = _audio_energy_signal(audio_path, window_start=start)
        transcribe_started = time.perf_counter()
        try:
            from .whisper_transcriber import last_model_load_seconds, transcribe as whisper_transcribe
            text, relative_segments = whisper_transcribe(audio_path, self.model, fast=True)
            transcription_seconds = time.perf_counter() - transcribe_started
            model_load_seconds = last_model_load_seconds()
            segments = [{**seg, "start": float(start) + float(seg.get("start") or 0.0), "end": float(start) + float(seg.get("end") or 0.0)} for seg in relative_segments]
            result = BoundedTranscriptResult(ok=True, text=text, segments=segments, model=self.model, language=self.language, window_start=start, window_end=end, duration_seconds=end - start, audio_path=str(audio_path), cache_path=str(cache_path), extraction_seconds=extraction_seconds, model_load_seconds=model_load_seconds, transcription_seconds=transcription_seconds, audio_energy=energy)
        except BaseException as exc:  # faster-whisper raises SystemExit when missing.
            result = BoundedTranscriptResult(ok=False, model=self.model, language=self.language, window_start=start, window_end=end, duration_seconds=end - start, audio_path=str(audio_path), cache_path=str(cache_path), extraction_seconds=extraction_seconds, transcription_seconds=time.perf_counter() - transcribe_started, audio_energy=energy, error=str(exc))
        cache_path.write_text(json.dumps({**result.to_dict(), "created_at": datetime.now(timezone.utc).isoformat(), "source_artifact_id": source_artifact_id, "cache_key": key}, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        register_artifact(project_id=project_id, artifact_type="bounded_transcript", path=cache_path, metadata={"cache_key": key, "source_artifact_id": source_artifact_id, "window_start": start, "window_end": end, "provider": result.provider, "model": result.model, "ok": result.ok})
        return result

    def candidate_windows(self, *, source_path: str | Path, estimated: float, broad_start: float, broad_end: float, source_duration: float | None = None, max_candidates: int = 3, window_seconds: float = 36.0) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        broad_start = max(0.0, broad_start)
        broad_end = max(broad_start, broad_end)
        if source_duration is not None:
            broad_end = min(float(source_duration), broad_end)
        centers = [float(estimated)]
        energy = {"available": False, "peaks": []}
        # Cheap audio-first scan: extract the broad window once, compute RMS peak.
        try:
            key = hashlib.sha1(f"energy|{source_path}|{broad_start:.3f}|{broad_end:.3f}".encode("utf-8")).hexdigest()[:20]
            broad_audio = _audio_cache_root() / f"{key}.energy.wav"
            broad_audio.parent.mkdir(parents=True, exist_ok=True)
            if shutil.which("ffmpeg"):
                subprocess.run(self.ffmpeg_extract_command(source_path, broad_audio, start=broad_start, end=broad_end), capture_output=True, text=True, check=True, timeout=max(30, int(broad_end - broad_start) + 30))
                peak = _audio_energy_signal(broad_audio, window_start=broad_start)
                if peak.get("available") and peak.get("peak_timestamp") is not None:
                    centers.append(float(peak["peak_timestamp"]))
                    energy = {**peak, "peaks": [peak]}
        except Exception as exc:
            energy = {"available": False, "error": str(exc), "peaks": []}
        windows: list[dict[str, Any]] = []
        half = window_seconds / 2.0
        for center in centers:
            start = max(broad_start, center - half)
            end = min(broad_end, center + half)
            if source_duration is not None:
                end = min(float(source_duration), end)
            if end <= start:
                continue
            if any(not (end <= row["start"] or start >= row["end"]) for row in windows):
                continue
            windows.append({"start": start, "end": end, "center": center})
            if len(windows) >= max_candidates:
                break
        return windows, energy

    def transcribe_candidates(self, *, project_id: str, source_artifact_id: str, source_path: str | Path, windows: list[dict[str, Any]]) -> dict[str, Any]:
        started = time.perf_counter()
        results = []
        text_parts = []
        segments = []
        for window in windows:
            result = self.transcribe_window(project_id=project_id, source_artifact_id=source_artifact_id, source_path=source_path, start=float(window["start"]), end=float(window["end"]))
            data = result.to_dict()
            results.append(data)
            if result.text:
                text_parts.append(result.text)
            segments.extend(result.segments)
        return {
            "ok": any(row.get("ok") for row in results),
            "text": " ".join(text_parts),
            "segments": sorted(segments, key=lambda row: float(row.get("start") or 0.0)),
            "candidate_windows": windows,
            "results": results,
            "duration_seconds": sum(float(row.get("duration_seconds") or 0.0) for row in results),
            "extraction_seconds": sum(float(row.get("extraction_seconds") or 0.0) for row in results),
            "model_load_seconds": sum(float(row.get("model_load_seconds") or 0.0) for row in results),
            "transcription_seconds": sum(float(row.get("transcription_seconds") or 0.0) for row in results),
            "cached": all(bool(row.get("cached")) for row in results) if results else False,
            "elapsed_seconds": time.perf_counter() - started,
        }


class BoundedSourceAlignmentService:
    def _default_source_clock(self, artifact, duration_f: float | None) -> dict[str, Any]:
        existing = (artifact.metadata or {}).get("source_clock") if artifact is not None else None
        if existing:
            return existing
        segments = source_segments_for_roles(source_roles(getattr(artifact, "metadata", {}) if artifact is not None else {}), duration_f)
        review_segment = segments[0].segment_type if segments else FIRST_HALF
        return source_clock_payload(segments, status="UNCALIBRATED", review={"segment_type": review_segment, "cursor_seconds": 0.0, "status": "PENDING"})

    def _initial_review_cursor(self, clock: dict[str, Any], *, source_duration: float | None = None) -> float:
        review = clock.get("review") or {}
        if review.get("cursor_seconds") is not None:
            return float(review.get("cursor_seconds") or 0.0)
        candidates = [row for row in clock.get("anchor_candidates") or [] if row.get("candidate_source_time_start") is not None]
        if candidates:
            return self._clamp_source_time(float(candidates[0]["candidate_source_time_start"]), source_duration)
        sampled_ends = [float(row.get("end") or 0.0) for row in review.get("sampled_windows") or [] if row.get("end") is not None]
        if sampled_ends:
            return self._clamp_source_time(max(sampled_ends), source_duration)
        return self._clamp_source_time(180.0, source_duration)

    def _clamp_source_time(self, value: float, source_duration: float | None) -> float:
        value = max(0.0, float(value))
        if source_duration is not None:
            value = min(float(source_duration), value)
        return value

    def inspect_source_coverage(self, project_id: str, source_artifact_id: str) -> SourceCoverageObservation:
        artifact = get_artifact(source_artifact_id)
        if artifact is None:
            return SourceCoverageObservation(source_artifact_id=source_artifact_id, source_duration_seconds=None, partial_source=True, estimated_match_coverage_start_minute=None, estimated_match_coverage_end_minute=None, evidence=[{"type": "missing_source_artifact"}])
        path = Path(artifact.path)
        duration = artifact.metadata.get("duration_seconds") or artifact.metadata.get("duration") or _ffprobe_duration(path)
        try:
            duration_f = float(duration) if duration is not None else None
        except (TypeError, ValueError):
            duration_f = None
        name = path.name.lower()
        roles = source_roles(artifact.metadata)
        partial = "PARTIAL" in roles or any(token in name for token in ("part", "1st", "2nd", "first_half", "first-half", "second_half", "second-half"))
        anchor = SourceAnchor(
            anchor_type="KICKOFF",
            media_time_seconds=0.0 if duration_f is not None else None,
            confidence=CONFIDENCE_LOW,
            validation_method="source_filename_duration_heuristic",
            evidence=[{"type": "source_duration", "value": duration_f}, {"type": "filename", "value": path.name}],
        )
        coverage_start = 45.0 if "SECOND_HALF" in roles and "FULL_MATCH" not in roles and "FIRST_HALF" not in roles else 0.0
        coverage_end = (coverage_start + duration_f / 60.0) if duration_f is not None else None
        source_clock = self._default_source_clock(artifact, duration_f)
        observation = SourceCoverageObservation(
            source_artifact_id=source_artifact_id,
            source_duration_seconds=duration_f,
            partial_source=partial,
            estimated_match_coverage_start_minute=coverage_start if duration_f is not None else None,
            estimated_match_coverage_end_minute=coverage_end,
            has_prematch_material=None,
            has_halftime_break=None,
            reaches_regulation_end=(coverage_end is not None and coverage_end >= 90.0),
            has_extra_time=(coverage_end is not None and coverage_end >= 105.0),
            confidence=CONFIDENCE_LOW,
            validation_method="source_filename_duration_heuristic",
            anchor=anchor,
            evidence=[{"type": "ffprobe_duration", "value": duration_f}, {"type": "filename", "value": path.name}],
            source_clock=source_clock,
        )
        metadata = {**artifact.metadata, "duration_seconds": duration_f, "source_clock": source_clock, "source_observations": observation.to_dict()}
        register_artifact(project_id=project_id, artifact_type=artifact.artifact_type, path=artifact.path, mime_type=artifact.mime_type, status=artifact.status, parent_artifact_id=artifact.parent_artifact_id, artifact_id=artifact.artifact_id, metadata=metadata)
        return observation

    def discover_first_half_anchor(self, project_id: str, source_artifact_id: str, *, max_search_seconds: float = 720.0) -> dict[str, Any]:
        started = time.perf_counter()
        source = get_artifact(source_artifact_id)
        if source is None:
            return {"ok": False, "status": "MISSING_SOURCE", "elapsed_seconds": time.perf_counter() - started}
        duration = source.metadata.get("duration_seconds") or source.metadata.get("duration") or _ffprobe_duration(Path(source.path))
        duration_f = float(duration) if duration is not None else None
        search_end = min(max_search_seconds, duration_f) if duration_f is not None else max_search_seconds
        service = BoundedTranscriptionService()
        centers = [30.0, 90.0, 180.0, 300.0, 480.0, 660.0]
        windows = []
        for center in centers:
            if center >= search_end:
                continue
            start = max(0.0, center - 18.0)
            end = min(search_end, center + 18.0)
            windows.append({"start": start, "end": end, "center": center})
        transcript = service.transcribe_candidates(project_id=project_id, source_artifact_id=source_artifact_id, source_path=source.path, windows=windows)
        kickoff_terms = ["kickoff", "kick off", "underway", "we are off", "we re off", "we're underway", "and we're underway", "opening whistle"]
        candidates = []
        for segment in transcript.get("segments") or []:
            text = _normalize_text(str(segment.get("text") or ""))
            matched = [term for term in kickoff_terms if _normalize_text(term) in text]
            if matched:
                candidates.append({"segment_type": FIRST_HALF, "candidate_source_time_start": float(segment.get("start") or 0.0), "matched_terms": matched, "text": segment.get("text", ""), "confidence": CLOCK_MEDIUM, "method": "early_bounded_kickoff_commentary"})
        selected = candidates[0] if candidates else None
        existing_clock = source.metadata.get("source_clock") or self._default_source_clock(source, duration_f)
        existing_candidates = list(existing_clock.get("anchor_candidates") or [])
        status = "NEEDS_OPERATOR" if selected is None else "READY"
        segments = segments_from_payload(existing_clock)
        if selected:
            first = SourceClockSegment(segment_type=FIRST_HALF, match_clock_start_seconds=0.0, source_time_start=float(selected["candidate_source_time_start"]), source_time_end=duration_f, confidence=CLOCK_MEDIUM, method="early_bounded_kickoff_commentary", evidence=[selected])
            segments = [row for row in segments if row.segment_type != FIRST_HALF] + [first]
        cursor = float(selected["candidate_source_time_start"]) if selected else (max((row["end"] for row in windows), default=180.0))
        cursor = self._clamp_source_time(cursor, duration_f)
        review = {"segment_type": FIRST_HALF, "cursor_seconds": cursor, "status": "PENDING", "sampled_windows": windows}
        payload = source_clock_payload(segments, candidates=[*existing_candidates, *candidates], status=status, review=review)
        register_artifact(project_id=project_id, artifact_type=source.artifact_type, path=source.path, mime_type=source.mime_type, status=source.status, parent_artifact_id=source.parent_artifact_id, artifact_id=source.artifact_id, metadata={**source.metadata, "duration_seconds": duration_f, "source_clock": payload})
        return {"ok": True, "status": status, "source_clock": payload, "opening_description": transcript.get("text", "")[:500], "candidate_windows": windows, "candidates": candidates, "selected_anchor": selected, "timing": {"transcribed_duration_seconds": transcript.get("duration_seconds", 0.0), "extraction_seconds": transcript.get("extraction_seconds", 0.0), "model_load_seconds": transcript.get("model_load_seconds", 0.0), "transcription_seconds": transcript.get("transcription_seconds", 0.0), "elapsed_seconds": time.perf_counter() - started}}

    def confirm_source_anchor(self, project_id: str, source_artifact_id: str, *, segment_type: str = FIRST_HALF, source_time_start: float | None = None, action: str = "confirm", shift_seconds: float = 15.0, recheck: bool = True) -> dict[str, Any]:
        source = get_artifact(source_artifact_id)
        if source is None:
            return {"ok": False, "error": "source artifact not found"}
        duration = source.metadata.get("duration_seconds") or source.metadata.get("duration")
        duration_f = float(duration) if duration is not None else None
        clock = source.metadata.get("source_clock") or self._default_source_clock(source, duration_f)
        segments = segments_from_payload(clock)
        current = next((row for row in segments if row.segment_type == segment_type), None)
        review = dict(clock.get("review") or {})
        base = float(source_time_start if source_time_start is not None else review.get("cursor_seconds") if review.get("cursor_seconds") is not None else (current.source_time_start if current else self._initial_review_cursor(clock, source_duration=duration_f)))
        if action == "earlier":
            base = self._clamp_source_time(base - shift_seconds, duration_f)
        elif action == "later":
            base = self._clamp_source_time(base + shift_seconds, duration_f)
        review = {**review, "segment_type": segment_type, "cursor_seconds": base, "status": "CONFIRMED" if action == "confirm" else "PENDING"}
        if action != "confirm":
            payload = source_clock_payload(segments, candidates=clock.get("anchor_candidates") or [], status=clock.get("status") or "NEEDS_OPERATOR", review=review)
            register_artifact(project_id=project_id, artifact_type=source.artifact_type, path=source.path, mime_type=source.mime_type, status=source.status, parent_artifact_id=source.parent_artifact_id, artifact_id=source.artifact_id, metadata={**source.metadata, "source_clock": payload})
            return {"ok": True, "source_clock": payload, "cursor_seconds": base, "recalculated_count": 0, "rechecked_count": 0}
        confirmed = SourceClockSegment(segment_type=segment_type, match_clock_start_seconds=0.0 if segment_type == FIRST_HALF else 45 * 60.0, source_time_start=base, source_time_end=current.source_time_end if current else duration_f, confidence=CLOCK_HIGH, method="operator_confirmation", evidence=[{"type": "operator_confirmation", "action": action, "cursor_seconds": base}], confirmed_by_operator=True, updated_at=datetime.now(timezone.utc).isoformat())
        payload = source_clock_payload([row for row in segments if row.segment_type != segment_type] + [confirmed], candidates=clock.get("anchor_candidates") or [], status="VERIFIED", review=review)
        register_artifact(project_id=project_id, artifact_type=source.artifact_type, path=source.path, mime_type=source.mime_type, status=source.status, parent_artifact_id=source.parent_artifact_id, artifact_id=source.artifact_id, metadata={**source.metadata, "source_clock": payload})
        recalculated = self.recalculate_estimated_moments(project_id, source_artifact_id, payload)
        rechecked = self.recheck_estimated_events(project_id, source_artifact_id, payload) if recheck else {"rechecked_count": 0, "results": []}
        return {"ok": True, "source_clock": payload, "segment": confirmed.to_dict(), "cursor_seconds": base, "recalculated_count": recalculated.get("recalculated_count", 0), "rechecked_count": rechecked.get("rechecked_count", 0), "recheck_results": rechecked.get("results", [])}

    def _propose_anchor_from_alignment(self, project_id: str, source_artifact_id: str, event, result: AlignmentResult) -> None:
        if result.alignment_status != ALIGNED or result.refined_source_time is None or result.confidence not in {CONFIDENCE_MEDIUM, CONFIDENCE_HIGH}:
            return
        source = get_artifact(source_artifact_id)
        if source is None:
            return
        match_seconds = event_match_seconds(event)
        segment_type = FIRST_HALF if match_seconds < 45 * 60 else SECOND_HALF
        segment_start = 0.0 if segment_type == FIRST_HALF else 45 * 60.0
        candidate = {"type": "aligned_event_anchor_candidate", "research_event_id": event.event_id, "segment_type": segment_type, "candidate_source_time_start": float(result.refined_source_time) - (match_seconds - segment_start), "event_position": event_position_provenance(event), "confidence": result.confidence, "method": result.validation_method}
        clock = source.metadata.get("source_clock") or self._default_source_clock(source, source.metadata.get("duration_seconds"))
        candidates = [*(clock.get("anchor_candidates") or []), candidate]
        segments = segments_from_payload(clock)
        improved = improve_anchor_from_candidates(candidates)
        if improved:
            segments = [row for row in segments if row.segment_type != improved.segment_type] + [improved]
        payload = source_clock_payload(segments, candidates=candidates, status="READY" if improved else clock.get("status", "READY"), review=clock.get("review") or {})
        register_artifact(project_id=project_id, artifact_type=source.artifact_type, path=source.path, mime_type=source.mime_type, status=source.status, parent_artifact_id=source.parent_artifact_id, artifact_id=source.artifact_id, metadata={**source.metadata, "source_clock": payload})

    def recalculate_estimated_moments(self, project_id: str, source_artifact_id: str, source_clock: dict[str, Any]) -> dict[str, Any]:
        moved = 0
        segments = segments_from_payload(source_clock)
        for moment in list_project_moments(project_id):
            if moment.source_artifact_id and moment.source_artifact_id != source_artifact_id:
                continue
            if moment.metadata.get("alignment_status") != ESTIMATED or moment.metadata.get("availability_status") != AVAILABLE:
                continue
            match_seconds = moment.metadata.get("estimated_match_seconds")
            if match_seconds is None:
                minute = moment.metadata.get("match_minute")
                if minute is None:
                    continue
                match_seconds = float(minute) * 60.0
            estimated, segment = estimate_source_time(float(match_seconds), segments)
            if estimated is None:
                continue
            start = max(0.0, estimated - 12.0)
            end = estimated + 18.0
            previous_estimate = moment.metadata.get("estimated_media_time")
            if previous_estimate is not None and abs(float(previous_estimate) - float(estimated)) < 0.001:
                continue
            metadata = {**moment.metadata, "estimated_media_time": estimated, "refined_media_time": None, "search_window": {"start": max(0.0, estimated - 90.0), "end": estimated + 90.0}, "source_clock_recalculation": {"version": SOURCE_CLOCK_VERSION, "segment_type": segment.segment_type if segment else None, "method": segment.method if segment else None, "previous_estimated_media_time": previous_estimate}, "evidence": {**(moment.metadata.get("evidence") or {}), "bounded_transcript": False}, "bounded_transcript_duration_seconds": 0.0}
            upsert_moment(replace(moment, source_artifact_id=source_artifact_id, start_seconds=start, peak_seconds=estimated, end_seconds=end, metadata=metadata))
            moved += 1
        return {"ok": True, "recalculated_count": moved}

    def recheck_estimated_events(self, project_id: str, source_artifact_id: str, source_clock: dict[str, Any]) -> dict[str, Any]:
        from pipeline.runtime_service import get_research_event
        coverage = self.inspect_source_coverage(project_id, source_artifact_id)
        coverage = replace(coverage, source_clock=source_clock)
        results = []
        for moment in list_project_moments(project_id):
            meta = moment.metadata or {}
            if meta.get("alignment_status") != ESTIMATED or meta.get("availability_status") != AVAILABLE:
                continue
            match_seconds = float(meta.get("estimated_match_seconds") or -1)
            if match_seconds < 0:
                continue
            event_id = meta.get("research_event_id")
            event = get_research_event(event_id) if event_id else None
            if event is None:
                continue
            result = self.align_event(project_id, source_artifact_id, event, coverage=coverage)
            self.persist_alignment(project_id, source_artifact_id, event, result)
            results.append(result.to_dict())
        return {"ok": True, "rechecked_count": len(results), "results": results}

    def _bounded_transcript(self, project_id: str, source_artifact_id: str, event, start: float | None, end: float | None, anchor: SourceAnchor | None, *, estimated: float | None = None, source_duration: float | None = None) -> dict[str, Any]:
        if start is None or end is None or end <= start:
            return {"text": "", "duration_seconds": 0.0, "cached": False, "path": "", "method": "not_invoked_no_window"}
        source = get_artifact(source_artifact_id)
        if source is not None:
            service = BoundedTranscriptionService()
            if estimated is not None:
                windows, energy_scan = service.candidate_windows(source_path=source.path, estimated=estimated, broad_start=start, broad_end=end, source_duration=source_duration)
                bounded_multi = service.transcribe_candidates(project_id=project_id, source_artifact_id=source_artifact_id, source_path=source.path, windows=windows)
                if bounded_multi.get("ok"):
                    return {**bounded_multi, "path": "", "method": "bounded_faster_whisper_candidates", "audio_energy": energy_scan}
            bounded = service.transcribe_window(project_id=project_id, source_artifact_id=source_artifact_id, source_path=source.path, start=start, end=min(end, start + 45.0))
            if bounded.ok:
                return {**bounded.to_dict(), "path": bounded.cache_path, "method": "bounded_faster_whisper"}
        # Fallback: reuse existing transcript artifacts only if they already exist;
        # never create a full-match transcript from this bounded alignment path.
        key = _cache_key(source_artifact_id, event.event_id, start, end, anchor)
        path = _cache_root() / f"{key}.json"
        if path.exists():
            return {**json.loads(path.read_text(encoding="utf-8")), "cached": True, "path": str(path)}
        text = ""
        segments: list[dict[str, Any]] = []
        method = "no_bounded_transcriber_configured"
        for artifact in list_project_artifacts(project_id, artifact_type="transcript"):
            transcript_path = Path(artifact.path)
            if transcript_path.exists():
                try:
                    raw = transcript_path.read_text(encoding="utf-8", errors="ignore")
                    text = raw[:20000]
                    segments = [{"start": start, "end": end, "text": text}]
                    method = "existing_transcript_window_reuse"
                except Exception:
                    text = ""
                break
        payload = {"alignment_version": ALIGNMENT_VERSION, "source_artifact_id": source_artifact_id, "research_event_id": event.event_id, "window": {"start": start, "end": end}, "text": text, "segments": segments, "duration_seconds": float(end - start), "method": method, "cached": False, "audio_energy": {}}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        register_artifact(project_id=project_id, artifact_type="bounded_transcript", path=path, metadata={"alignment_cache_key": key, "research_event_id": event.event_id, "source_artifact_id": source_artifact_id, "window_start": start, "window_end": end, "method": method})
        return {**payload, "path": str(path)}

    def align_event(self, project_id: str, source_artifact_id: str, event, coverage: SourceCoverageObservation | None = None) -> AlignmentResult:
        started = time.perf_counter()
        coverage = coverage or self.inspect_source_coverage(project_id, source_artifact_id)
        anchor = coverage.anchor
        source_duration = coverage.source_duration_seconds
        match_seconds = event_match_seconds(event)
        roles = source_roles((get_artifact(source_artifact_id).metadata if get_artifact(source_artifact_id) else {}) or {})
        if "SECOND_HALF" in roles and "FULL_MATCH" not in roles and "FIRST_HALF" not in roles and match_seconds < 45 * 60:
            return AlignmentResult(event.event_id, OUTSIDE_SOURCE, UNALIGNED, None, None, None, None, None, None, CONFIDENCE_HIGH, "source_role_outside_match_segment", evidence=[{"type": "source_role_outside_match_segment", "source_roles": sorted(roles), "event_position": event_position_provenance(event)}], source_observations=coverage.to_dict(), elapsed_seconds=time.perf_counter() - started)
        clock_segments = segments_from_payload(coverage.source_clock)
        estimated, clock_segment = estimate_source_time(match_seconds, clock_segments)
        if estimated is None:
            estimated = None if anchor is None or anchor.media_time_seconds is None else float(anchor.media_time_seconds) + match_seconds - float(anchor.match_time_seconds or 0)
            clock_segment = None
        adaptive_windows = _adaptive_search_windows(estimated, source_duration)
        if adaptive_windows:
            broad = adaptive_windows[-1]
            search_start = broad["start"]
            search_end = broad["end"]
        else:
            before, after = event_search_window(event.universal_event_type)
            search_start = max(0.0, estimated + before) if estimated is not None else None
            search_end = max(0.0, estimated + after) if estimated is not None else None
        if source_duration is not None and estimated is not None and estimated > source_duration:
            return AlignmentResult(event.event_id, OUTSIDE_SOURCE, UNALIGNED, None, None, None, None, None, None, CONFIDENCE_HIGH, "source_coverage_duration", evidence=[{"type": "clock_estimate_outside_source", "estimated_source_time": estimated, "source_duration_seconds": source_duration}], source_observations=coverage.to_dict(), elapsed_seconds=time.perf_counter() - started)
        transcript = self._bounded_transcript(project_id, source_artifact_id, event, search_start, search_end, anchor, estimated=estimated, source_duration=source_duration)
        segments = list(transcript.get("segments") or [])
        text = transcript.get("text") or ""
        if not segments and text:
            segments = [{"start": search_start, "end": search_end, "text": text}]
        transcript_evidence = _match_transcript_evidence(event, segments)
        matched = [row["term"] for rows in (transcript_evidence.get("matched") or {}).values() for row in rows]
        audio_energy = transcript.get("audio_energy") or {}
        energy_support = bool(audio_energy.get("available") and transcript_evidence.get("refined_source_time") is not None and abs(float(audio_energy.get("peak_timestamp") or 0.0) - float(transcript_evidence["refined_source_time"])) <= 20.0)
        evidence = [
            {"type": "clock_estimate", "estimated_source_time": estimated, "anchor_confidence": anchor.confidence if anchor else None, "source_clock_version": SOURCE_CLOCK_VERSION, "source_clock_segment": clock_segment.to_dict() if clock_segment else None, "event_position": event_position_provenance(event)},
            {"type": "bounded_transcript", "matched_terms": matched, "signals": transcript_evidence.get("signals", []), "method": transcript.get("method"), "cached": transcript.get("cached", False), "path": transcript.get("path", ""), "adaptive_search_windows": adaptive_windows, "candidate_windows": transcript.get("candidate_windows", []), "transcribed_duration_seconds": transcript.get("duration_seconds", 0.0), "extraction_seconds": transcript.get("extraction_seconds", 0.0), "model_load_seconds": transcript.get("model_load_seconds", 0.0), "transcription_seconds": transcript.get("transcription_seconds", 0.0), "error": transcript.get("error", "")},
            {"type": "audio_energy", **audio_energy, "supporting_signal": energy_support},
        ]
        if estimated is None:
            status = UNALIGNED
            availability = UNKNOWN
            confidence = CONFIDENCE_LOW
            validation = "no_source_anchor"
            refined = None
        elif transcript_evidence.get("signals") and transcript_evidence.get("confidence") in {CONFIDENCE_MEDIUM, CONFIDENCE_HIGH}:
            status = ALIGNED
            availability = AVAILABLE
            confidence = transcript_evidence.get("confidence")
            if confidence == CONFIDENCE_MEDIUM and energy_support:
                confidence = CONFIDENCE_HIGH
            validation = "bounded_transcript_term_match"
            refined = transcript_evidence.get("refined_source_time") or estimated
        else:
            status = ESTIMATED
            availability = AVAILABLE
            confidence = CONFIDENCE_LOW
            validation = "source_clock_estimate"
            refined = None
        clip_start = max(0.0, (refined if refined is not None else estimated or 0.0) - 12.0) if availability == AVAILABLE else None
        clip_end = (refined if refined is not None else estimated or 0.0) + 18.0 if availability == AVAILABLE else None
        result = AlignmentResult(event.event_id, availability, status, estimated, search_start, search_end, refined, clip_start, clip_end, confidence, validation, evidence=evidence, source_observations=coverage.to_dict(), needs_operator_confirmation=status == ESTIMATED, bounded_transcript_duration_seconds=float(transcript.get("duration_seconds") or 0.0), elapsed_seconds=time.perf_counter() - started)
        self._propose_anchor_from_alignment(project_id, source_artifact_id, event, result)
        return result

    def persist_alignment(self, project_id: str, source_artifact_id: str, event, result: AlignmentResult) -> None:
        source_availability = {
            "source_artifact_id": source_artifact_id,
            "availability_status": result.availability_status,
            "alignment_status": result.alignment_status,
            "estimated_media_time": result.estimated_source_time,
            "refined_media_time": result.refined_source_time,
            "search_window_start": result.search_window_start,
            "search_window_end": result.search_window_end,
            "clip_window_start": result.clip_window_start,
            "clip_window_end": result.clip_window_end,
            "confidence": result.confidence,
            "validation_method": result.validation_method,
            "evidence": result.evidence,
            "source_observations": result.source_observations,
            "bounded_transcript_duration_seconds": result.bounded_transcript_duration_seconds,
            "alignment_version": ALIGNMENT_VERSION,
            "source_clock_version": SOURCE_CLOCK_VERSION,
        }
        source_availabilities = dict((event.metadata or {}).get("source_availabilities") or {})
        source_availabilities[source_artifact_id] = source_availability
        best_availability = _best_source_availability(source_availabilities)
        metadata = {**event.metadata, "source_availabilities": source_availabilities, "source_availability": best_availability}
        upsert_research_event(replace(event, metadata=metadata))
        for moment in list_project_moments(project_id):
            if moment.metadata.get("research_event_id") != event.event_id:
                continue
            existing_source_availabilities = dict((moment.metadata or {}).get("source_availabilities") or {})
            existing_source_availabilities[source_artifact_id] = source_availability
            best_for_moment = _best_source_availability(existing_source_availabilities)
            if best_for_moment.get("source_artifact_id") != source_artifact_id:
                moment_metadata = {**moment.metadata, "source_availabilities": existing_source_availabilities, "source_availability": best_for_moment, "availability_status": best_for_moment.get("availability_status"), "alignment_status": best_for_moment.get("alignment_status"), "alignment_confidence": best_for_moment.get("confidence"), "needs_operator_confirmation": best_for_moment.get("alignment_status") == ESTIMATED}
                upsert_moment(replace(moment, metadata=moment_metadata))
                continue
            base_time = result.refined_source_time if result.refined_source_time is not None else result.estimated_source_time
            start = result.clip_window_start if result.clip_window_start is not None else moment.start_seconds
            end = result.clip_window_end if result.clip_window_end is not None else moment.end_seconds
            peak = base_time if base_time is not None else moment.peak_seconds
            clock_evidence = next((e for e in result.evidence if e.get("type") == "clock_estimate"), {})
            moment_metadata = {**moment.metadata, **source_availability, "source_availabilities": existing_source_availabilities, "source_availability": best_for_moment, "availability_status": result.availability_status, "alignment_status": result.alignment_status, "alignment_confidence": result.confidence, "needs_operator_confirmation": result.needs_operator_confirmation, "search_window": {"start": result.search_window_start, "end": result.search_window_end}, "estimated_match_seconds": (clock_evidence.get("event_position") or {}).get("estimated_match_seconds"), "event_position": (clock_evidence.get("event_position") or {}).get("display"), "source_clock_segment": clock_evidence.get("source_clock_segment"), "evidence": {**(moment.metadata.get("evidence") or {}), "bounded_transcript": bool([e for e in result.evidence if e.get("type") == "bounded_transcript" and e.get("matched_terms")])}}
            upsert_moment(replace(moment, source_artifact_id=source_artifact_id if result.availability_status == AVAILABLE else moment.source_artifact_id, start_seconds=float(start or 0.0), peak_seconds=float(peak or 0.0), end_seconds=float(end or 0.0), metadata=moment_metadata))

    def align_research(self, project_id: str, source_artifact_id: str, research_id: str) -> dict[str, Any]:
        total_started = time.perf_counter()
        coverage = self.inspect_source_coverage(project_id, source_artifact_id)
        # Ensure research moments exist before alignment updates them.
        seed_moments_from_research(research_id, source_duration_seconds=coverage.source_duration_seconds)
        results = []
        for event in list_research_events(research_id):
            result = self.align_event(project_id, source_artifact_id, event, coverage=coverage)
            self.persist_alignment(project_id, source_artifact_id, event, result)
            results.append(result.to_dict())
        usable = [m for m in list_project_moments(project_id) if is_usable_source_moment(m)]
        return {"ok": True, "alignment_version": ALIGNMENT_VERSION, "coverage": coverage.to_dict(), "results": results, "usable_moment_count": len(usable), "full_match_transcription_used": False, "full_match_detection_used": False, "elapsed_seconds": time.perf_counter() - total_started}

    def align_research_across_sources(self, project_id: str, research_id: str, source_artifact_ids: list[str] | None = None) -> dict[str, Any]:
        total_started = time.perf_counter()
        sources = source_artifact_ids or [artifact.artifact_id for artifact in list_project_artifacts(project_id, artifact_type="source_media")]
        coverages = {source_id: self.inspect_source_coverage(project_id, source_id) for source_id in sources}
        if sources:
            seed_moments_from_research(research_id, source_duration_seconds=coverages[sources[0]].source_duration_seconds)
        results: list[dict[str, Any]] = []
        from pipeline.runtime_service import get_research_event
        for event in list_research_events(research_id):
            for source_id in sources:
                current_event = get_research_event(event.event_id) or event
                result = self.align_event(project_id, source_id, current_event, coverage=coverages[source_id])
                self.persist_alignment(project_id, source_id, current_event, result)
                results.append({"source_artifact_id": source_id, **result.to_dict()})
        usable = [m for m in list_project_moments(project_id) if is_usable_source_moment(m)]
        return {"ok": True, "alignment_version": ALIGNMENT_VERSION, "coverages": {source_id: coverage.to_dict() for source_id, coverage in coverages.items()}, "results": results, "usable_moment_count": len(usable), "source_artifact_ids": sources, "full_match_transcription_used": False, "full_match_detection_used": False, "elapsed_seconds": time.perf_counter() - total_started}

    def confirm_moment_alignment(self, project_id: str, moment_id: str, *, action: str = "confirm", shift_seconds: float = 5.0) -> dict[str, Any]:
        moment = get_moment(moment_id)
        if moment is None or moment.project_id != project_id:
            raise ValueError("moment not found")
        peak = float(moment.peak_seconds or 0.0)
        action_norm = action.lower().strip()
        if action_norm == "earlier":
            peak = max(0.0, peak - shift_seconds)
            status = ESTIMATED
        elif action_norm == "later":
            peak = peak + shift_seconds
            status = ESTIMATED
        elif action_norm == "not_found":
            metadata = {**moment.metadata, "availability_status": NOT_FOUND, "alignment_status": UNALIGNED, "validation_method": "operator_not_found", "needs_operator_confirmation": False}
            upsert_moment(replace(moment, metadata=metadata))
            return {"ok": True, "moment_id": moment_id, "source_artifact_id": moment.source_artifact_id, "availability_status": NOT_FOUND, "alignment_status": UNALIGNED, "cursor_seconds": peak, "refined_media_time": peak}
        else:
            status = VERIFIED
        metadata = {**moment.metadata, "availability_status": AVAILABLE, "alignment_status": status, "alignment_confidence": CONFIDENCE_HIGH if status == VERIFIED else CONFIDENCE_LOW, "validation_method": "operator_confirmation" if status == VERIFIED else "operator_preview_shift", "needs_operator_confirmation": status != VERIFIED, "refined_media_time": peak, "review_cursor_seconds": peak}
        upsert_moment(replace(moment, start_seconds=max(0.0, peak - 12.0), peak_seconds=peak, end_seconds=peak + 18.0, metadata=metadata))
        return {"ok": True, "moment_id": moment_id, "source_artifact_id": moment.source_artifact_id, "availability_status": AVAILABLE, "alignment_status": status, "cursor_seconds": peak, "refined_media_time": peak, "clip_window_start": max(0.0, peak - 12.0), "clip_window_end": peak + 18.0}
