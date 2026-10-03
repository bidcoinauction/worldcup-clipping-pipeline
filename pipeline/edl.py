"""EDL — Edit Decision List service boundary.

Converts an approved Edit Brief into an exact, machine-readable timeline
plan that a future renderer can execute. Deterministic construction from
Edit Brief + Moments + source metadata. No LLM for calculations.

No CLI invocation. No shell execution. No secrets exposure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .edit_brief import (
    PACING_VALUES,
    INTENSITY_VALUES,
    TRANSITION_INTENT_VALUES,
    AUDIO_STRATEGY_VALUES,
    TEXT_INTENT_VALUES,
    BEAT_ROLES,
)

# ── Internal helpers ────────────────────────────────────────────────────────


def _artifacts_dir(jobs_dir: Path) -> Path:
    d = jobs_dir / "EDL"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _edl_path(job_id: str, story_id: str, fmt: str, jobs_dir: Path) -> Path:
    return _artifacts_dir(jobs_dir) / f"{job_id}_{story_id}_{fmt}_edl.json"


def _read_job_record(job_id: str, jobs_dir: Path) -> dict:
    record_path = jobs_dir / f"{job_id}.json"
    if not record_path.exists():
        return {}
    return json.loads(record_path.read_text(encoding="utf-8"))


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_error_message(exc: Exception) -> str:
    """Produce a credential-safe error message."""
    msg = str(exc)
    import re
    msg = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED]", msg)
    msg = re.sub(r"token[=:]\s*\S+", "token=[REDACTED]", msg, flags=re.IGNORECASE)
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg or "EDL generation failed."


def _fmt_timestamp(seconds: float) -> str:
    """Format seconds as MM:SS.s for human display."""
    m = int(seconds) // 60
    s = seconds - m * 60
    return f"{m:02d}:{s:04.1f}"


# ── EDL state I/O ───────────────────────────────────────────────────────────


def update_edl_state(job_id: str, *, story_id: str, status: str,
                     error: str = "", jobs_dir: str | Path | None = None,
                     **extra) -> dict:
    """Update the EDL generation state on a job record."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")

    now = _now_iso()
    key = f"edl_status_{story_id}"
    job[key] = status
    if status == "RUNNING" and not job.get(f"edl_started_{story_id}"):
        job[f"edl_started_{story_id}"] = now
    if status in ("COMPLETE", "FAILED", "NEEDS ATTENTION"):
        job[f"edl_completed_{story_id}"] = now
    if error:
        job[f"edl_error_{story_id}"] = error
    else:
        job.pop(f"edl_error_{story_id}", None)
    for field, value in extra.items():
        job[f"edl_{field}_{story_id}"] = value

    from .edit_brief import _write_job_record
    _write_job_record(job_id, jobs_dir_path, job)
    return job


def read_edl_state(job_id: str, story_id: str,
                   jobs_dir: str | Path | None = None) -> dict:
    """Read the EDL generation state for a specific story."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")
    return {
        "job_id": job_id,
        "story_id": story_id,
        "edl_status": job.get(f"edl_status_{story_id}", ""),
        "edl_started_at": job.get(f"edl_started_{story_id}", ""),
        "edl_completed_at": job.get(f"edl_completed_{story_id}", ""),
        "edl_error": job.get(f"edl_error_{story_id}", ""),
    }


# ── EDL artifact I/O ───────────────────────────────────────────────────────


def write_edl(edl: dict, jobs_dir: str | Path | None = None) -> Path:
    """Write an EDL artifact."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job_id = edl.get("job_id", "unknown")
    story_id = edl.get("story_id", "unknown")
    fmt = edl.get("format", "SHORT")
    path = _edl_path(job_id, story_id, fmt, jobs_dir_path)
    path.write_text(json.dumps(edl, indent=2) + "\n", encoding="utf-8")
    return path


def read_edl(job_id: str, story_id: str, fmt: str,
             jobs_dir: str | Path | None = None) -> dict | None:
    """Read an EDL artifact. Returns None if not found."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _edl_path(job_id, story_id, fmt, jobs_dir_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_edls(job_id: str, story_id: str | None = None,
              jobs_dir: str | Path | None = None) -> list[dict]:
    """List EDLs for a job, optionally filtered by story_id."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    artifacts_dir = _artifacts_dir(jobs_dir_path)
    results: list[dict] = []
    for p in sorted(artifacts_dir.glob(f"{job_id}_*_*_edl.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if story_id and data.get("story_id") != story_id:
                continue
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return results


# ── Timeline construction ───────────────────────────────────────────────────


def _resolve_moment_by_id(moments: list[dict], moment_id: str) -> dict | None:
    """Find a moment by clip_id."""
    for m in moments:
        if m.get("clip_id") == moment_id:
            return m
    return None


def _parse_time(val) -> float:
    """Parse a time value (already numeric or string) to float seconds."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        val = val.strip()
        if not val:
            return 0.0
        return float(val)
    return 0.0


def _format_duration_for_beat(moment: dict, beat: dict, format_treatment: str) -> tuple[float, float]:
    """Deterministically select source window for a beat.

    Returns (source_start, source_end) in seconds.

    Conservative policy: use the full moment window. For SHORT treatment,
    trim up to 20% from the end if the moment is long, preserving the start.
    """
    src_start = _parse_time(moment.get("start_time", 0))
    src_end = _parse_time(moment.get("end_time", 0))

    if src_end <= src_start:
        src_end = src_start + 5.0  # fallback: 5 seconds

    duration = src_end - src_start

    if format_treatment == "SHORT" and duration >= 15.0:
        # Trim up to 20% from the end for SHORT, keep at least 60%
        trim = min(duration * 0.2, duration * 0.4)
        src_end = src_end - trim

    return src_start, src_end


def build_edl_from_brief(
    job_id: str,
    brief: dict,
    moments: list[dict],
    *,
    source_duration: float | None = None,
) -> dict:
    """Deterministically construct an EDL from an Edit Brief and Moments.

    This is a pure function: same inputs always produce the same output.
    No LLM calls. No network requests.
    """
    beats = brief.get("beats", [])
    if not beats:
        raise ValueError("Edit Brief has no beats")

    fmt = brief.get("format", "SHORT")
    story_id = brief.get("story_id", "")
    brief_id = f"{story_id}_{fmt}"

    segments: list[dict] = []
    timeline_cursor = 0.0

    for i, beat in enumerate(beats):
        moment_id = beat.get("moment_id", "")
        moment = _resolve_moment_by_id(moments, moment_id)
        if moment is None:
            raise ValueError(f"beat[{i}] references unknown moment_id {moment_id!r}")

        src_start, src_end = _format_duration_for_beat(moment, beat, fmt)
        segment_duration = src_end - src_start

        if segment_duration <= 0:
            raise ValueError(f"beat[{i}] produces zero/negative duration ({segment_duration:.1f}s)")

        seg_id = f"seg_{i + 1:03d}"
        tl_start = timeline_cursor
        tl_end = timeline_cursor + segment_duration

        segments.append({
            "segment_id": seg_id,
            "timeline_start": round(tl_start, 3),
            "timeline_end": round(tl_end, 3),
            "source_start": round(src_start, 3),
            "source_end": round(src_end, 3),
            "moment_id": moment_id,
            "story_role": beat.get("role", ""),
            "editorial_direction": beat.get("direction", ""),
            "pacing": beat.get("pacing", ""),
            "intensity": beat.get("intensity", ""),
            "audio_strategy": beat.get("audio_strategy", ""),
            "transition_in": "HARD_CUT" if i == 0 else beat.get("transition_intent", "HARD_CUT"),
            "transition_out": beat.get("transition_intent", ""),
            "text_intent": beat.get("text_intent", ""),
        })

        timeline_cursor = tl_end

    total_duration = round(timeline_cursor, 3)

    return {
        "job_id": job_id,
        "story_id": story_id,
        "brief_id": brief_id,
        "format": fmt,
        "source": brief.get("job_id", job_id),
        "timeline_duration": total_duration,
        "segment_count": len(segments),
        "segments": segments,
        "source_duration": source_duration,
        "editorial_intent": brief.get("editorial_intent", ""),
    }


# ── Validation ──────────────────────────────────────────────────────────────


def validate_edl(edl: dict, valid_moment_ids: set[str],
                 source_duration: float | None = None) -> list[str]:
    """Validate an EDL. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not edl.get("job_id"):
        errors.append("job_id is required")
    if not edl.get("story_id"):
        errors.append("story_id is required")
    if not edl.get("brief_id"):
        errors.append("brief_id is required")

    fmt = edl.get("format", "")
    if fmt not in ("SHORT", "MEDIUM", "LONG"):
        errors.append(f"unsupported format: {fmt!r}")

    segments = edl.get("segments", [])
    if not segments:
        errors.append("at least one segment is required")
        return errors

    seen_ids: set[str] = set()
    prev_timeline_end: float | None = None

    for i, seg in enumerate(segments):
        prefix = f"segment[{i}]"

        sid = seg.get("segment_id", "")
        if not sid:
            errors.append(f"{prefix}: segment_id is required")
        elif sid in seen_ids:
            errors.append(f"{prefix}: duplicate segment_id {sid!r}")
        seen_ids.add(sid)

        mid = seg.get("moment_id", "")
        if not mid:
            errors.append(f"{prefix}: moment_id is required")
        elif mid not in valid_moment_ids:
            errors.append(f"{prefix}: moment_id {mid!r} not found in detected moments")

        # Source bounds
        src_start = seg.get("source_start")
        src_end = seg.get("source_end")
        if src_start is None or src_end is None:
            errors.append(f"{prefix}: source_start and source_end are required")
        else:
            if src_end <= src_start:
                errors.append(f"{prefix}: source_end ({src_end}) must be > source_start ({src_start})")
            if source_duration is not None:
                if src_start < 0:
                    errors.append(f"{prefix}: source_start ({src_start}) must be >= 0")
                if src_end > source_duration:
                    errors.append(f"{prefix}: source_end ({src_end}) exceeds source duration ({source_duration})")

        # Timeline bounds
        tl_start = seg.get("timeline_start")
        tl_end = seg.get("timeline_end")
        if tl_start is None or tl_end is None:
            errors.append(f"{prefix}: timeline_start and timeline_end are required")
        else:
            if tl_end <= tl_start:
                errors.append(f"{prefix}: timeline_end ({tl_end}) must be > timeline_start ({tl_start})")
            # Check contiguous timeline
            if prev_timeline_end is not None:
                if abs(tl_start - prev_timeline_end) > 0.001:
                    errors.append(f"{prefix}: timeline gap or overlap at {tl_start} (expected ~{prev_timeline_end})")
            prev_timeline_end = tl_end

        # Validate controlled vocabularies
        role = seg.get("story_role", "")
        if role and role not in BEAT_ROLES:
            errors.append(f"{prefix}: unsupported story_role {role!r}")

        pacing = seg.get("pacing", "")
        if pacing and pacing not in PACING_VALUES:
            errors.append(f"{prefix}: unsupported pacing {pacing!r}")

        intensity = seg.get("intensity", "")
        if intensity and intensity not in INTENSITY_VALUES:
            errors.append(f"{prefix}: unsupported intensity {intensity!r}")

        t_in = seg.get("transition_in", "")
        if t_in and t_in not in TRANSITION_INTENT_VALUES:
            errors.append(f"{prefix}: unsupported transition_in {t_in!r}")

        t_out = seg.get("transition_out", "")
        if t_out and t_out not in TRANSITION_INTENT_VALUES:
            errors.append(f"{prefix}: unsupported transition_out {t_out!r}")

        audio = seg.get("audio_strategy", "")
        if audio and audio not in AUDIO_STRATEGY_VALUES:
            errors.append(f"{prefix}: unsupported audio_strategy {audio!r}")

        text = seg.get("text_intent", "")
        if text and text not in TEXT_INTENT_VALUES:
            errors.append(f"{prefix}: unsupported text_intent {text!r}")

    return errors


# ── Main service interface ──────────────────────────────────────────────────


def build_edl(
    job_id: str,
    story_id: str,
    fmt: str,
    *,
    jobs_dir: str | Path | None = None,
    source_duration: float | None = None,
    dry_run: bool = False,
) -> dict:
    """Build an EDL from an approved Edit Brief.

    Deterministic: same inputs always produce the same output.
    No LLM calls.

    Returns: ``{"ok": bool, "status": str, "edl": dict | None, ...}``
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    from .detection import read_moments as _read_moments
    from .edit_brief import read_edit_brief

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()

    # 1. Validate job exists
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        return {"ok": False, "error": f"job '{job_id}' not found", "status": "FAILED"}

    # 2. Read Edit Brief
    brief = read_edit_brief(job_id, story_id, fmt, jobs_dir=jobs_dir_path)
    if brief is None:
        return {"ok": False, "error": f"Edit Brief not found for story '{story_id}' format '{fmt}'", "status": "FAILED"}

    # 3. Read Moments
    moments = _read_moments(job_id, jobs_dir=jobs_dir_path)
    if not moments:
        return {"ok": False, "error": "No detected moments found.", "status": "FAILED"}

    # 4. Update state
    update_edl_state(job_id, story_id=story_id, status="RUNNING", jobs_dir=jobs_dir_path)

    try:
        if dry_run:
            update_edl_state(job_id, story_id=story_id, status="COMPLETE", jobs_dir=jobs_dir_path)
            return {"ok": True, "status": "COMPLETE", "dry_run": True}

        # 5. Build EDL deterministically
        edl = build_edl_from_brief(job_id, brief, moments, source_duration=source_duration)

        # 6. Validate
        moment_ids = {m.get("clip_id", "") for m in moments}
        validation_errors = validate_edl(edl, moment_ids, source_duration=source_duration)
        if validation_errors:
            error_msg = "; ".join(validation_errors[:5])
            update_edl_state(
                job_id, story_id=story_id, status="FAILED",
                error=f"EDL validation failed: {error_msg}",
                jobs_dir=jobs_dir_path,
            )
            return {"ok": False, "error": f"Validation failed: {error_msg}", "status": "FAILED"}

        # 7. Write artifact
        from .edit_brief import write_edit_brief  # noqa: we use write_edl
        artifact_path = write_edl(edl, jobs_dir=jobs_dir_path)

        # 8. Update state
        update_edl_state(
            job_id, story_id=story_id, status="COMPLETE",
            edl_path=str(artifact_path),
            edl_segment_count=edl["segment_count"],
            edl_duration=edl["timeline_duration"],
            jobs_dir=jobs_dir_path,
        )

        return {
            "ok": True,
            "status": "COMPLETE",
            "edl": edl,
            "artifact_path": str(artifact_path),
        }

    except Exception as exc:
        error_msg = _safe_error_message(exc)
        update_edl_state(
            job_id, story_id=story_id, status="FAILED",
            error=error_msg,
            jobs_dir=jobs_dir_path,
        )
        return {"ok": False, "error": error_msg, "status": "FAILED"}
