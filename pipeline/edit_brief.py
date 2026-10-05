"""Edit Intelligence — service boundary.

Transforms a Story into an Edit Brief: a structured editorial sequence
that describes HOW a story should unfold and feel. Edit Briefs sit
between Stories (WHAT we tell) and the future EDL/Renderer (HOW we
physically assemble it).

No CLI invocation. No shell execution. No secrets exposure.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

# ── Controlled vocabularies ─────────────────────────────────────────────────

PACING_VALUES = {"SLOW", "BUILDING", "MEDIUM", "FAST", "PEAK", "RELEASE"}

INTENSITY_VALUES = {"LOW", "MEDIUM", "HIGH", "PEAK", "RELEASE"}

TRANSITION_INTENT_VALUES = {
    "HARD_CUT", "REACTION_CUT", "CROWD_CUT", "COMMENTARY_CARRY",
    "AUDIO_BRIDGE", "SCOREBOARD_FLASH", "FREEZE_PUSH", "REPLAY_ECHO",
    "FLASH_CUT", "WHIP_PAN", "AUDIO_DROP", "FADE",
}

AUDIO_STRATEGY_VALUES = {
    "ORIGINAL", "CROWD_FOCUS", "COMMENTARY_FOCUS", "CROWD_AND_COMMENTARY",
    "MUSIC_BUILD", "MUSIC_PEAK", "AUDIO_DROP", "SILENCE", "IMPACT", "AMBIENT",
}

TEXT_INTENT_VALUES = {"NONE", "HOOK_TEXT", "SCORE_CONTEXT", "TIME_CONTEXT", "PLAYER_CONTEXT"}

FORMAT_TREATMENTS = {"SHORT", "MEDIUM", "LONG"}

BEAT_ROLES = {"HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"}

# ── Format treatment guidance ────────────────────────────────────────────────

FORMAT_GUIDANCE = {
    "SHORT": "strongest possible hook, compressed setup, fast escalation, minimal context, decisive payoff",
    "MEDIUM": "stronger atmosphere, more context, reactions and breathing room, clearer escalation curve, fuller aftermath",
    "LONG": "narrative chapters, multiple pressure/release cycles, greater contextual setup, more atmosphere, extended aftermath",
}

# ── Internal helpers ────────────────────────────────────────────────────────


def _artifacts_dir(jobs_dir: Path) -> Path:
    d = jobs_dir / "EDIT_BRIEFS"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _brief_path(job_id: str, story_id: str, fmt: str, jobs_dir: Path) -> Path:
    return _artifacts_dir(jobs_dir) / f"{job_id}_{story_id}_{fmt}.json"


def _read_job_record(job_id: str, jobs_dir: Path) -> dict:
    record_path = jobs_dir / f"{job_id}.json"
    if not record_path.exists():
        return {}
    return json.loads(record_path.read_text(encoding="utf-8"))


def _write_job_record(job_id: str, jobs_dir: Path, data: dict) -> None:
    record_path = jobs_dir / f"{job_id}.json"
    record_path.parent.mkdir(parents=True, exist_ok=True)
    import tempfile
    fd, tmp_name = tempfile.mkstemp(dir=str(record_path.parent), prefix=record_path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(data, indent=2, sort_keys=True) + "\n")
        os.replace(tmp_name, record_path)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def _now_iso() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def _safe_error_message(exc: Exception) -> str:
    """Produce a credential-safe error message."""
    msg = str(exc)
    import re
    msg = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED]", msg)
    msg = re.sub(r"token[=:]\s*\S+", "token=[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"https?://[^\s]+@[^\s]+", "[REDACTED_URL]", msg)
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg or "Edit brief generation failed."


# ── Brief state I/O ─────────────────────────────────────────────────────────


def update_brief_state(job_id: str, *, story_id: str, status: str,
                       error: str = "", jobs_dir: str | Path | None = None,
                       **extra) -> dict:
    """Update the edit-brief generation state on a job record."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")

    now = _now_iso()
    key = f"brief_status_{story_id}"
    job[key] = status
    if status == "RUNNING" and not job.get(f"brief_started_{story_id}"):
        job[f"brief_started_{story_id}"] = now
    if status in ("COMPLETE", "FAILED", "NEEDS ATTENTION"):
        job[f"brief_completed_{story_id}"] = now
    if error:
        job[f"brief_error_{story_id}"] = error
    else:
        job.pop(f"brief_error_{story_id}", None)
    for field, value in extra.items():
        job[f"brief_{field}_{story_id}"] = value

    _write_job_record(job_id, jobs_dir_path, job)
    return job


def read_brief_state(job_id: str, story_id: str,
                     jobs_dir: str | Path | None = None) -> dict:
    """Read the edit-brief generation state for a specific story."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")
    return {
        "job_id": job_id,
        "story_id": story_id,
        "brief_status": job.get(f"brief_status_{story_id}", ""),
        "brief_started_at": job.get(f"brief_started_{story_id}", ""),
        "brief_completed_at": job.get(f"brief_completed_{story_id}", ""),
        "brief_error": job.get(f"brief_error_{story_id}", ""),
    }


# ── Edit brief I/O ─────────────────────────────────────────────────────────


def write_edit_brief(job_id: str, brief: dict,
                     jobs_dir: str | Path | None = None) -> Path:
    """Write an edit brief artifact."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    story_id = brief.get("story_id", "unknown")
    fmt = brief.get("format", "SHORT")
    path = _brief_path(job_id, story_id, fmt, jobs_dir_path)
    path.write_text(json.dumps(brief, indent=2) + "\n", encoding="utf-8")
    return path


def read_edit_brief(job_id: str, story_id: str, fmt: str,
                    jobs_dir: str | Path | None = None) -> dict | None:
    """Read an edit brief artifact. Returns None if not found."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _brief_path(job_id, story_id, fmt, jobs_dir_path)
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_edit_briefs(job_id: str, story_id: str | None = None,
                     jobs_dir: str | Path | None = None) -> list[dict]:
    """List edit briefs for a job, optionally filtered by story_id."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    artifacts_dir = _artifacts_dir(jobs_dir_path)
    results: list[dict] = []
    for p in sorted(artifacts_dir.glob(f"{job_id}_*_*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if story_id and data.get("story_id") != story_id:
                continue
            results.append(data)
        except (json.JSONDecodeError, KeyError):
            continue
    return results


# ── Validation ──────────────────────────────────────────────────────────────


def validate_edit_brief(brief: dict, valid_moment_ids: set[str],
                        story_recommended_formats: set[str] | None = None) -> list[str]:
    """Validate an edit brief. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not brief.get("job_id"):
        errors.append("job_id is required")
    if not brief.get("story_id"):
        errors.append("story_id is required")

    fmt = brief.get("format", "")
    if fmt not in FORMAT_TREATMENTS:
        errors.append(f"unsupported format: {fmt!r} (must be SHORT, MEDIUM, or LONG)")
    if story_recommended_formats is not None and fmt not in story_recommended_formats:
        errors.append(f"format {fmt!r} is not recommended by the story (recommended: {', '.join(sorted(story_recommended_formats))})")

    if not brief.get("editorial_intent"):
        errors.append("editorial_intent is required")

    # Validate emotional arc
    arc = brief.get("emotional_arc", [])
    if not arc:
        errors.append("emotional_arc must have at least one entry")

    # Validate beats
    beats = brief.get("beats", [])
    if not beats:
        errors.append("at least one beat is required")
    else:
        seen_beat_ids: set[str] = set()
        seen_moments: list[str] = []
        for i, beat in enumerate(beats):
            prefix = f"beat[{i}]"

            bid = beat.get("beat_id", "")
            if not bid:
                errors.append(f"{prefix}: beat_id is required")
            elif bid in seen_beat_ids:
                errors.append(f"{prefix}: duplicate beat_id {bid!r}")
            seen_beat_ids.add(bid)

            role = beat.get("role", "")
            if role not in BEAT_ROLES:
                errors.append(f"{prefix}: unsupported role {role!r}")

            mid = beat.get("moment_id", "")
            if not mid:
                errors.append(f"{prefix}: moment_id is required")
            elif mid not in valid_moment_ids:
                errors.append(f"{prefix}: moment_id {mid!r} not found in detected moments")
            seen_moments.append(mid)

            if not beat.get("direction"):
                errors.append(f"{prefix}: direction is required")

            pacing = beat.get("pacing", "")
            if pacing not in PACING_VALUES:
                errors.append(f"{prefix}: unsupported pacing {pacing!r}")

            intensity = beat.get("intensity", "")
            if intensity and intensity not in INTENSITY_VALUES:
                errors.append(f"{prefix}: unsupported intensity {intensity!r}")

            transition = beat.get("transition_intent", "")
            if transition and transition not in TRANSITION_INTENT_VALUES:
                errors.append(f"{prefix}: unsupported transition_intent {transition!r}")

            audio = beat.get("audio_strategy", "")
            if audio and audio not in AUDIO_STRATEGY_VALUES:
                errors.append(f"{prefix}: unsupported audio_strategy {audio!r}")

            text = beat.get("text_intent", "")
            if text and text not in TEXT_INTENT_VALUES:
                errors.append(f"{prefix}: unsupported text_intent {text!r}")

    return errors


# ── Prompt construction ─────────────────────────────────────────────────────


def _build_edit_brief_prompt(story: dict, moments: list[dict],
                             format_treatment: str) -> str:
    """Build the LLM prompt for edit brief generation."""
    moment_lines = []
    for m in moments:
        mid = m.get("clip_id", "")
        cat = m.get("category", "")
        start = m.get("start_time", "")
        end = m.get("end_time", "")
        caption = m.get("caption", "")
        hook = m.get("hook_text", "")
        moment_lines.append(f"- [{mid}] {cat} | {start}s-{end}s | {caption}" + (f" | hook: {hook}" if hook else ""))

    moments_block = "\n".join(moment_lines)

    story_moments = story.get("moment_ids", [])
    story_arc = story.get("emotional_arc", [])
    narrative_roles = story.get("narrative_roles", {})

    roles_str = json.dumps(narrative_roles, indent=2) if narrative_roles else "None specified"

    pacing_list = ", ".join(sorted(PACING_VALUES))
    intensity_list = ", ".join(sorted(INTENSITY_VALUES))
    transition_list = ", ".join(sorted(TRANSITION_INTENT_VALUES))
    audio_list = ", ".join(sorted(AUDIO_STRATEGY_VALUES))
    text_list = ", ".join(sorted(TEXT_INTENT_VALUES))

    guidance = FORMAT_GUIDANCE.get(format_treatment, "")

    return f"""You are a sports edit intelligence system. Given a Story and its detected Moments, generate an Edit Brief for the {format_treatment} treatment.

STORY:
Title: {story.get('title', '')}
Archetype: {story.get('archetype', '')}
Summary: {story.get('summary', '')}
Hook: {story.get('hook', '')}
Emotional arc: {', '.join(story_arc)}
Story moment_ids: {', '.join(story_moments)}
Narrative roles: {roles_str}

FORMAT: {format_treatment}
Treatment guidance: {guidance}

DETECTED MOMENTS:
{moments_block}

RULES:
- Generate beats that cover the narrative roles from the story.
- Beats may be in editorial (non-chronological) order. Start strong.
- A moment may appear in multiple beats when editorially justified (e.g., HOOK then CLIMAX).
- Each beat must reference a valid moment_id from the list above.
- Use the controlled vocabularies exactly. Do not invent new values.
- Return valid JSON only. No markdown. No explanation.

PACING values: {pacing_list}
INTENSITY values: {intensity_list}
TRANSITION INTENT values: {transition_list}
AUDIO STRATEGY values: {audio_list}
TEXT INTENT values: {text_list}

SCHEMA:
{{
  "editorial_intent": "one paragraph describing the editorial vision",
  "emotional_arc": ["EMOTION1", "EMOTION2", ...],
  "beats": [
    {{
      "beat_id": "beat_001",
      "role": "HOOK",
      "moment_id": "004",
      "direction": "what this beat should feel like and do",
      "pacing": "PEAK",
      "intensity": "PEAK",
      "audio_strategy": "COMMENTARY_FOCUS",
      "transition_intent": "AUDIO_DROP",
      "text_intent": "HOOK_TEXT"
    }}
  ]
}}

Return a JSON object with editorial_intent, emotional_arc, and beats."""


# ── LLM call ────────────────────────────────────────────────────────────────


def _run_brief_llm(prompt_text: str, *, provider: str | None = None,
                   model: str | None = None) -> dict:
    """Call the LLM for edit brief generation.

    Provider selection: explicit *provider*, else ``EDIT_PROVIDER`` (default =
    ``STORY_PROVIDER``, itself defaulting to ``openai``). Ollama uses the shared
    helper; the output contract is identical.
    """
    from .provider_service import edit_model, edit_provider, ollama_generate
    selected_provider = (provider or edit_provider()).strip().lower()

    if selected_provider == "openai":
        return _run_openai_brief(prompt_text, model=model)
    if selected_provider == "ollama":
        content = ollama_generate(
            prompt_text,
            model=model or edit_model(),
            system_prompt="You are a sports edit intelligence system. Return only valid JSON.",
            json_mode=True,
        )
        return _parse_brief_json(content)
    raise ValueError(f"unsupported edit brief provider: {selected_provider!r}")


def _run_openai_brief(prompt_text: str, *, model: str | None = None) -> dict:
    """Call OpenAI for edit brief generation."""
    from .api import make_openai_client
    client = make_openai_client()
    selected_model = model or "gpt-4o"
    response = client.chat.completions.create(
        model=selected_model,
        messages=[
            {"role": "system", "content": "You are a sports edit intelligence system. Return only valid JSON."},
            {"role": "user", "content": prompt_text},
        ],
        temperature=0.7,
        max_tokens=3000,
    )
    content = response.choices[0].message.content or "{}"
    return _parse_brief_json(content)


def _parse_brief_json(raw: str) -> dict:
    """Parse brief from LLM response, handling markdown wrapping."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    parsed = json.loads(text)
    if isinstance(parsed, dict):
        return parsed
    raise ValueError("LLM response is not a JSON object")


# ── Main service interface ──────────────────────────────────────────────────


def generate_edit_brief(
    job_id: str,
    story_id: str,
    format_treatment: str,
    *,
    jobs_dir: str | Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate an edit brief from a story.

    This is the primary entry point. It:
    1. Reads the story and moments
    2. Validates format is recommended
    3. Builds the edit brief prompt
    4. Calls the LLM
    5. Validates the output
    6. Writes the artifact
    7. Updates job record

    Returns: ``{"ok": bool, "status": str, ...}``
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    from .detection import read_moments as _read_moments
    from .story_engine import read_story_suggestions, ARCHETYPES

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()

    # 1. Validate job exists
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        return {"ok": False, "error": f"job '{job_id}' not found", "status": "FAILED"}

    # 2. Read story
    stories = read_story_suggestions(job_id, jobs_dir=jobs_dir_path)
    story = next((s for s in stories if s.get("story_id") == story_id), None)
    if story is None:
        return {"ok": False, "error": f"story '{story_id}' not found", "status": "FAILED"}

    # 3. Validate format
    if format_treatment not in FORMAT_TREATMENTS:
        return {
            "ok": False,
            "error": f"unsupported format: {format_treatment!r}",
            "status": "FAILED",
        }

    recommended = set(story.get("recommended_formats", []))
    if recommended and format_treatment not in recommended:
        return {
            "ok": False,
            "error": f"format {format_treatment!r} is not recommended by this story (recommended: {', '.join(sorted(recommended))})",
            "status": "FAILED",
        }

    # 4. Read moments
    moments = _read_moments(job_id, jobs_dir=jobs_dir_path)
    if not moments:
        return {
            "ok": False,
            "error": "No detected moments found.",
            "status": "FAILED",
        }

    # 5. Update state
    update_brief_state(job_id, story_id=story_id, status="RUNNING", jobs_dir=jobs_dir_path)

    try:
        if dry_run:
            update_brief_state(job_id, story_id=story_id, status="COMPLETE", jobs_dir=jobs_dir_path)
            return {"ok": True, "status": "COMPLETE", "dry_run": True}

        # 6. Build prompt and call LLM
        prompt_text = _build_edit_brief_prompt(story, moments, format_treatment)
        raw_brief = _run_brief_llm(prompt_text, provider=provider, model=model)

        # 7. Assemble full brief artifact
        brief = {
            "job_id": job_id,
            "story_id": story_id,
            "format": format_treatment,
            "target_duration": story.get("estimated_duration", 0),
            "story_archetype": story.get("archetype", ""),
            "editorial_intent": raw_brief.get("editorial_intent", ""),
            "emotional_arc": raw_brief.get("emotional_arc", []),
            "beats": raw_brief.get("beats", []),
        }

        # 8. Validate
        moment_ids = {m.get("clip_id", "") for m in moments}
        validation_errors = validate_edit_brief(brief, moment_ids, recommended or None)
        if validation_errors:
            error_msg = "; ".join(validation_errors[:5])
            update_brief_state(
                job_id, story_id=story_id, status="FAILED",
                error=f"Brief validation failed: {error_msg}",
                jobs_dir=jobs_dir_path,
            )
            return {"ok": False, "error": f"Validation failed: {error_msg}", "status": "FAILED"}

        # 9. Write artifact
        artifact_path = write_edit_brief(job_id, brief, jobs_dir=jobs_dir_path)

        # 10. Update state
        update_brief_state(
            job_id, story_id=story_id, status="COMPLETE",
            brief_path=str(artifact_path),
            jobs_dir=jobs_dir_path,
        )

        return {
            "ok": True,
            "status": "COMPLETE",
            "brief": brief,
            "artifact_path": str(artifact_path),
        }

    except Exception as exc:
        error_msg = _safe_error_message(exc)
        update_brief_state(
            job_id, story_id=story_id, status="FAILED",
            error=error_msg,
            jobs_dir=jobs_dir_path,
        )
        return {"ok": False, "error": error_msg, "status": "FAILED"}
