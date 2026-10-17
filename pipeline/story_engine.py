"""Story Engine — service boundary.

Transforms detected moments into suggested VIDEO STORIES. Each story
reasons across multiple moments and proposes coherent narratives rather
than ranking individual clips.

No CLI invocation. No shell execution. No secrets exposure.
"""

from __future__ import annotations

import json
import os
import traceback
from pathlib import Path

# ── Archetype taxonomy ──────────────────────────────────────────────────────

ARCHETYPES = {
    "COMEBACK",
    "COLLAPSE",
    "DOMINATION",
    "RIVALRY",
    "CHAOS",
    "CLUTCH",
    "UPSET",
    "SURVIVAL",
    "INDIVIDUAL_PERFORMANCE",
    "CONTROVERSY",
}

# ── Narrative roles ─────────────────────────────────────────────────────────

NARRATIVE_ROLES = {"HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"}

# ── Recommended formats ─────────────────────────────────────────────────────

RECOMMENDED_FORMATS = {"SHORT", "MEDIUM", "LONG"}

# ── Story state constants ───────────────────────────────────────────────────

STORY_STATES = {"WAITING", "RUNNING", "COMPLETE", "NEEDS ATTENTION", "FAILED"}

STAGE_BUILDING = "BUILDING_STORIES"

# ── Internal helpers ────────────────────────────────────────────────────────


def _artifacts_dir(jobs_dir: Path) -> Path:
    d = jobs_dir / "STORIES"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _story_path(job_id: str, jobs_dir: Path) -> Path:
    return _artifacts_dir(jobs_dir) / f"{job_id}_stories.json"


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
    # Strip potential API keys, tokens, URLs with auth
    import re
    msg = re.sub(r"sk-[A-Za-z0-9_-]{20,}", "[REDACTED]", msg)
    msg = re.sub(r"token[=:]\s*\S+", "token=[REDACTED]", msg, flags=re.IGNORECASE)
    msg = re.sub(r"https?://[^\s]+@[^\s]+", "[REDACTED_URL]", msg)
    if len(msg) > 500:
        msg = msg[:500] + "..."
    return msg or "Story generation failed. Check the project details for more information."


# ── Story state I/O ─────────────────────────────────────────────────────────


def update_story_state(job_id: str, *, status: str, stage: str = "",
                       error: str = "", jobs_dir: str | Path | None = None,
                       **extra) -> dict:
    """Update the story generation state on a job record."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")

    now = _now_iso()
    job["story_status"] = status
    if stage:
        job["story_stage"] = stage
    if status == "RUNNING":
        job["story_started_at"] = now
        job.pop("story_completed_at", None)
    if status in ("COMPLETE", "FAILED", "NEEDS ATTENTION"):
        job["story_completed_at"] = now
    if error:
        job["story_error"] = error
    else:
        job.pop("story_error", None)
    for key, value in extra.items():
        job[key] = value

    _write_job_record(job_id, jobs_dir_path, job)
    return job


def read_story_state(job_id: str, jobs_dir: str | Path | None = None) -> dict:
    """Read the story generation state from a job record (read-only)."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        raise ValueError(f"job '{job_id}' not found")
    return {
        "job_id": job_id,
        "story_status": job.get("story_status", ""),
        "story_stage": job.get("story_stage", ""),
        "story_started_at": job.get("story_started_at", ""),
        "story_completed_at": job.get("story_completed_at", ""),
        "story_error": job.get("story_error", ""),
        "story_count": job.get("story_count", 0),
        "story_artifact_path": job.get("story_artifact_path", ""),
    }


# ── Story suggestions I/O ──────────────────────────────────────────────────


def read_story_suggestions(job_id: str, jobs_dir: str | Path | None = None) -> list[dict]:
    """Read story suggestions for a project (read-only)."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _story_path(job_id, jobs_dir_path)
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict) and "stories" in data:
        return data["stories"]
    return []


def write_story_suggestions(job_id: str, stories: list[dict],
                            jobs_dir: str | Path | None = None) -> Path:
    """Write story suggestions artifact."""
    from .pilot import default_jobs_dir as _default_jobs_dir
    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()
    path = _story_path(job_id, jobs_dir_path)
    artifact = {
        "job_id": job_id,
        "story_count": len(stories),
        "stories": stories,
    }
    path.write_text(json.dumps(artifact, indent=2) + "\n", encoding="utf-8")
    return path


# ── Validation ──────────────────────────────────────────────────────────────


def validate_story(story: dict, valid_moment_ids: set[str]) -> list[str]:
    """Validate a single story dict. Returns list of error strings (empty = valid)."""
    errors: list[str] = []

    if not story.get("story_id"):
        errors.append("story_id is required")
    if not story.get("title"):
        errors.append("title is required")

    archetype = story.get("archetype", "")
    if archetype not in ARCHETYPES:
        errors.append(f"unsupported archetype: {archetype!r} (must be one of: {', '.join(sorted(ARCHETYPES))})")

    moment_ids = story.get("moment_ids", [])
    if not moment_ids:
        errors.append("story must reference at least one moment")
    else:
        for mid in moment_ids:
            if mid not in valid_moment_ids:
                errors.append(f"moment_id {mid!r} not found in detected moments")

    # Validate narrative roles reference valid moments
    narrative_roles = story.get("narrative_roles", {})
    if isinstance(narrative_roles, dict):
        for role, mapped_ids in narrative_roles.items():
            if role not in NARRATIVE_ROLES:
                errors.append(f"unsupported narrative role: {role!r}")
            if isinstance(mapped_ids, list):
                for mid in mapped_ids:
                    if mid not in valid_moment_ids:
                        errors.append(f"narrative role {role} references unknown moment_id {mid!r}")

    # Validate recommended formats
    formats = story.get("recommended_formats", [])
    for fmt in formats:
        if fmt not in RECOMMENDED_FORMATS:
            errors.append(f"unsupported recommended format: {fmt!r}")

    # Validate emotional arc entries are strings
    arc = story.get("emotional_arc", [])
    if arc and not all(isinstance(e, str) for e in arc):
        errors.append("emotional_arc entries must be strings")

    return errors


def validate_stories(stories: list[dict], valid_moment_ids: set[str]) -> list[str]:
    """Validate a list of stories. Returns list of error strings."""
    errors: list[str] = []
    if not stories:
        errors.append("at least one story is required")
        return errors
    if len(stories) > 5:
        errors.append(f"too many stories: {len(stories)} (max 5)")
    seen_ids: set[str] = set()
    for i, story in enumerate(stories):
        prefix = f"story[{i}]"
        story_errors = validate_story(story, valid_moment_ids)
        sid = story.get("story_id", "")
        if sid in seen_ids:
            story_errors.append(f"duplicate story_id: {sid!r}")
        seen_ids.add(sid)
        for err in story_errors:
            errors.append(f"{prefix}: {err}")
    return errors


# ── Prompt construction ─────────────────────────────────────────────────────


def _build_story_prompt(moments: list[dict], match_name: str,
                        profile: str) -> str:
    """Build the LLM prompt for story generation."""
    moment_lines = []
    for m in moments:
        mid = m.get("clip_id", "")
        cat = m.get("category", "")
        start = m.get("start_time", "")
        end = m.get("end_time", "")
        score = m.get("virality_score", "")
        caption = m.get("caption", "")
        hook = m.get("hook_text", "")
        retention = m.get("retention_reason", "")
        share = m.get("share_reason", "")
        moment_lines.append(
            f"- [{mid}] {cat} | {start}s-{end}s | score={score} | {caption}"
            + (f" | hook: {hook}" if hook else "")
            + (f" | retention: {retention}" if retention else "")
            + (f" | share: {share}" if share else "")
        )

    moments_block = "\n".join(moment_lines)

    archetypes_list = ", ".join(sorted(ARCHETYPES))
    roles_list = ", ".join(sorted(NARRATIVE_ROLES))
    formats_list = ", ".join(sorted(RECOMMENDED_FORMATS))

    return f"""You are a sports story editor. Given detected moments from a match, propose 1-5 coherent VIDEO STORIES.

A moment is NOT a story. A story reasons across multiple moments and tells a complete narrative.

MATCH: {match_name}

DETECTED MOMENTS:
{moments_block}

ARCHETYPES (use exactly one per story):
{archetypes_list}

NARRATIVE ROLES (map moments to these roles in each story):
{roles_list}

RECOMMENDED FORMATS:
{formats_list}

RULES:
- Each story must reference 1-5 moment_ids from the list above.
- Do NOT invent moment_ids that are not in the list.
- Each story must have a unique story_id and title.
- The archetype must be one of the listed archetypes.
- recommended_formats must use only the listed format values.
- emotional_arc should describe the narrative shape as a list of emotional labels (e.g. ["DESPAIR", "BELIEF", "CHAOS", "SURVIVAL"]).
- narrative_roles should map each role to a list of moment_ids (only roles that apply).
- Do NOT make up events. Only use the detected moments.
- Return a JSON array of stories. No markdown. No explanation.

SCHEMA for each story:
{{
  "story_id": "unique_slug",
  "title": "Story Title",
  "archetype": "ARCHETYPE",
  "summary": "1-2 sentence summary",
  "hook": "Why this story matters",
  "moment_ids": ["001", "002"],
  "estimated_duration": 45,
  "emotional_arc": ["EMOTION1", "EMOTION2"],
  "narrative_roles": {{"HOOK": ["001"], "SETUP": ["002"], "ESCALATION": ["003"], "CLIMAX": ["004"], "AFTERMATH": ["005"]}},
  "recommended_formats": ["SHORT", "MEDIUM"],
  "why_this_story": "Why this story is compelling"
}}

Return a JSON array of 1-5 stories."""


# ── LLM call ────────────────────────────────────────────────────────────────


def _run_story_llm(prompt_text: str, *, provider: str | None = None,
                   model: str | None = None) -> list[dict]:
    """Call the LLM for story generation. Returns parsed stories.

    Provider selection: explicit *provider*, else ``STORY_PROVIDER`` (default
    ``openai``). Ollama uses the shared helper; the output contract is identical.
    """
    from .provider_service import ollama_generate, story_model, story_provider
    selected_provider = (provider or story_provider()).strip().lower()

    if selected_provider == "openai":
        return _run_openai_story(prompt_text, model=model)
    if selected_provider == "ollama":
        content = ollama_generate(
            prompt_text,
            model=model or story_model(),
            system_prompt="You are a sports story editor. Return only valid JSON.",
            json_mode=True,
        )
        return _parse_stories_json(content)
    raise ValueError(f"unsupported story generation provider: {selected_provider!r}")


def _run_openai_story(prompt_text: str, *, model: str | None = None) -> list[dict]:
    """Call OpenAI for story generation."""
    from .api import make_openai_client
    client = make_openai_client()
    selected_model = model or "gpt-4o"
    response = client.chat.completions.create(
        model=selected_model,
        messages=[
            {"role": "system", "content": "You are a sports story editor. Return only valid JSON."},
            {"role": "user", "content": prompt_text},
        ],
        temperature=0.7,
        max_tokens=4000,
    )
    content = response.choices[0].message.content or "[]"
    return _parse_stories_json(content)


def _parse_stories_json(raw: str) -> list[dict]:
    """Parse stories from LLM response, handling markdown wrapping."""
    text = raw.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [l for l in lines if not l.strip().startswith("```")]
        text = "\n".join(lines)
    parsed = json.loads(text)
    if isinstance(parsed, dict) and "stories" in parsed:
        return parsed["stories"]
    if isinstance(parsed, list):
        return parsed
    raise ValueError("LLM response is neither a JSON array nor an object with 'stories'")


# ── Main service interface ──────────────────────────────────────────────────


def generate_story_suggestions(
    job_id: str,
    *,
    jobs_dir: str | Path | None = None,
    provider: str | None = None,
    model: str | None = None,
    dry_run: bool = False,
) -> dict:
    """Generate story suggestions from detected moments.

    This is the primary entry point for story generation. It:
    1. Reads detected moments
    2. Builds a story prompt
    3. Calls the LLM
    4. Validates output
    5. Writes the story artifact
    6. Updates job record

    Returns: ``{"ok": bool, "status": str, "story_count": int, ...}``
    """
    from .pilot import default_jobs_dir as _default_jobs_dir
    from .detection import read_moments as _read_moments, read_analysis_state

    jobs_dir_path = Path(jobs_dir) if jobs_dir is not None else _default_jobs_dir()

    # 1. Validate job exists
    job = _read_job_record(job_id, jobs_dir_path)
    if not job:
        return {"ok": False, "error": f"job '{job_id}' not found", "status": "FAILED"}

    # 2. Check analysis is complete
    analysis = read_analysis_state(job_id, jobs_dir=jobs_dir_path)
    if analysis.get("analysis_status") != "COMPLETE":
        return {
            "ok": False,
            "error": "Analysis must be complete before generating stories",
            "status": "NEEDS ATTENTION",
        }

    # 3. Read moments
    moments = _read_moments(job_id, jobs_dir=jobs_dir_path)
    if not moments:
        return {
            "ok": False,
            "error": "No detected moments found. Run analysis first.",
            "status": "NEEDS ATTENTION",
        }

    # 4. Update state to RUNNING
    update_story_state(job_id, status="RUNNING", stage=STAGE_BUILDING, jobs_dir=jobs_dir_path)

    try:
        # 5. Build prompt
        match_name = job.get("match_name", job.get("pilot_id", job_id))
        project_id = job.get("project_id", "football")
        prompt_text = _build_story_prompt(moments, match_name, project_id)

        if dry_run:
            update_story_state(job_id, status="COMPLETE", jobs_dir=jobs_dir_path,
                               story_count=0, story_artifact_path="")
            return {"ok": True, "status": "COMPLETE", "story_count": 0, "dry_run": True}

        # 6. Call LLM
        stories = _run_story_llm(prompt_text, provider=provider, model=model)

        # 7. Validate
        moment_ids = {m.get("clip_id", "") for m in moments}
        validation_errors = validate_stories(stories, moment_ids)
        if validation_errors:
            error_msg = "; ".join(validation_errors[:5])
            update_story_state(
                job_id, status="FAILED",
                error=f"Story validation failed: {error_msg}",
                jobs_dir=jobs_dir_path,
            )
            return {"ok": False, "error": f"Validation failed: {error_msg}", "status": "FAILED"}

        # 8. Write artifact
        artifact_path = write_story_suggestions(job_id, stories, jobs_dir=jobs_dir_path)

        # 9. Update job record
        update_story_state(
            job_id, status="COMPLETE",
            story_count=len(stories),
            story_artifact_path=str(artifact_path),
            jobs_dir=jobs_dir_path,
        )

        return {
            "ok": True,
            "status": "COMPLETE",
            "story_count": len(stories),
            "artifact_path": str(artifact_path),
        }

    except Exception as exc:
        error_msg = _safe_error_message(exc)
        update_story_state(
            job_id, status="FAILED",
            error=error_msg,
            jobs_dir=jobs_dir_path,
        )
        return {"ok": False, "error": error_msg, "status": "FAILED"}
