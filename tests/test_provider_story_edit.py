"""Provider-completion tests: Ollama for Story + Edit Brief while preserving OpenAI.

Covers sections 15-20 of the RC1 provider-completion task:
- OpenAI regression for Story and Edit Brief
- Ollama Story / Edit generation with realistic structured output
- Mixed provider selection (STORY_PROVIDER / EDIT_PROVIDER defaults)
- Provider readiness per stage (Ollama ready / model missing / OpenAI no key)
- Malformed Ollama output -> safe failure with preserved prior stage
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from pipeline import edit_brief, provider_service, story_engine
from pipeline.edit_brief import _parse_brief_json, _run_brief_llm
from pipeline.story_engine import _parse_stories_json, _run_story_llm
from tests.test_pilot_intake import build_intake


# ── Fixtures / helpers ───────────────────────────────────────────────────────


@pytest.fixture
def jobs_root(tmp_path: Path, monkeypatch) -> Path:
    root = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(root))
    return root


@pytest.fixture
def media_file(tmp_path: Path) -> Path:
    path = tmp_path / "source.mp4"
    path.write_bytes(b"provider story edit bytes" * 100)
    return path


SAMPLE_MOMENTS = [
    {
        "clip_id": "001",
        "category": "EMOTION",
        "start_time": "5",
        "end_time": "20",
        "virality_score": 9,
        "caption": "Header pulls one back",
        "hook_text": "The captain refuses to die",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "002",
        "category": "CHAOS",
        "start_time": "25",
        "end_time": "35",
        "virality_score": 7,
        "caption": "The momentum shifts",
        "hook_text": "Chaos in the box",
        "status": "needs_visual_scrub",
    },
    {
        "clip_id": "003",
        "category": "MOMENTUM",
        "start_time": "40",
        "end_time": "55",
        "virality_score": 8,
        "caption": "Screamer hits the top corner",
        "hook_text": "A goal from nowhere",
        "status": "needs_visual_scrub",
    },
]

OLLAMA_STORIES_JSON = json.dumps([
    {
        "story_id": "impossible_comeback",
        "title": "The Impossible Comeback",
        "archetype": "COMEBACK",
        "summary": "Trailed then the captain changed everything.",
        "hook": "One of the greatest comebacks in football history",
        "moment_ids": ["001", "002", "003"],
        "estimated_duration": 48,
        "emotional_arc": ["DESPAIR", "BELIEF", "RELEASE"],
        "narrative_roles": {"HOOK": ["001"], "CLIMAX": ["003"], "AFTERMATH": ["003"]},
        "recommended_formats": ["SHORT", "MEDIUM"],
        "why_this_story": "Classic underdog narrative",
    }
])

OLLAMA_BRIEF_JSON = json.dumps({
    "editorial_intent": "Capture the emotional swing of the comeback.",
    "emotional_arc": ["DESPAIR", "BELIEF", "RELEASE"],
    "beats": [
        {"beat_id": "b1", "moment_id": "001", "role": "HOOK", "direction": "Open on despair, then plant belief.",
         "pacing": "SLOW", "intensity": "HIGH"},
        {"beat_id": "b2", "moment_id": "003", "role": "CLIMAX", "direction": "Cut on impact, hold the release.",
         "pacing": "FAST", "intensity": "PEAK"},
    ],
})


def _create_project(media_file: Path, jobs_root: Path) -> dict:
    from pipeline.operator_console import create_project
    intake = build_intake(str(media_file), overrides={
        "pilot": {"pilot_id": "provider_test", "project": "football"},
        "media": {"source_id": "provider_source", "match_or_event_name": "Provider Match"},
        "configuration": {"project": "football"},
    })
    intake_path = media_file.parent / "football_intake.json"
    intake_path.write_text(json.dumps(intake, indent=2), encoding="utf-8")
    return create_project(intake, intake_path=intake_path, operator="test", jobs_dir=jobs_root)


def _setup_completed(media_file: Path, jobs_root: Path) -> dict:
    from pipeline.detection import _write_moments, update_analysis_state
    job = _create_project(media_file, jobs_root)
    job_id = job["job_id"]
    _write_moments(job_id, jobs_root, SAMPLE_MOMENTS)
    update_analysis_state(job_id, status="COMPLETE", stage="PREPARING_RESULTS",
                          jobs_dir=jobs_root, analysis_manifest_count=len(SAMPLE_MOMENTS))
    return job


def _patch_ollama_generate(monkeypatch, raw: str):
    monkeypatch.setattr(provider_service, "ollama_generate", lambda *a, **k: raw)


# ── Provider selection ───────────────────────────────────────────────────────


def test_provider_selection_defaults(monkeypatch):
    monkeypatch.delenv("STORY_PROVIDER", raising=False)
    monkeypatch.delenv("EDIT_PROVIDER", raising=False)
    assert provider_service.story_provider() == "openai"
    assert provider_service.edit_provider() == "openai"


def test_edit_provider_defaults_to_story_provider(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    monkeypatch.delenv("EDIT_PROVIDER", raising=False)
    assert provider_service.edit_provider() == "ollama"


def test_provider_selection_allows_mixed(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    monkeypatch.setenv("EDIT_PROVIDER", "ollama")
    assert provider_service.story_provider() == "openai"
    assert provider_service.edit_provider() == "ollama"


def test_story_model_fallback(monkeypatch):
    monkeypatch.delenv("OLLAMA_STORY_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    assert provider_service.story_model() == "llama3.1"
    monkeypatch.setenv("OLLAMA_STORY_MODEL", "llama3.2")
    assert provider_service.story_model() == "llama3.2"


# ── Story: OpenAI regression ─────────────────────────────────────────────────


def test_story_llm_openai_regression(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    sentinel = [{"story_id": "s"}]
    with patch("pipeline.story_engine._run_openai_story", return_value=sentinel) as m:
        result = _run_story_llm("prompt")
    assert result is sentinel
    m.assert_called_once_with("prompt", model=None)


# ── Story: Ollama ────────────────────────────────────────────────────────────


def test_story_llm_ollama_parses_realistic_output(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, OLLAMA_STORIES_JSON)
    stories = _run_story_llm("prompt")
    assert len(stories) == 1
    s = stories[0]
    assert s["title"] == "The Impossible Comeback"
    assert s["archetype"] == "COMEBACK"
    assert s["hook"]
    assert s["emotional_arc"]
    assert s["recommended_formats"] == ["SHORT", "MEDIUM"]
    assert s["moment_ids"] == ["001", "002", "003"]


def test_story_generation_ollama_full_flow(monkeypatch, media_file, jobs_root):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, OLLAMA_STORIES_JSON)
    job = _setup_completed(media_file, jobs_root)
    result = story_engine.generate_story_suggestions(job["job_id"], jobs_dir=jobs_root)
    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["story_count"] == 1
    stories = story_engine.read_story_suggestions(job["job_id"], jobs_dir=jobs_root)
    assert stories[0]["story_id"] == "impossible_comeback"


def test_story_generation_ollama_malformed_output_preserves_moments(monkeypatch, media_file, jobs_root):
    from pipeline.detection import read_moments
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, "not valid json at all")
    job = _setup_completed(media_file, jobs_root)
    job_id = job["job_id"]
    result = story_engine.generate_story_suggestions(job_id, jobs_dir=jobs_root)
    assert result["ok"] is False
    assert result["status"] == "FAILED"
    state = story_engine.read_story_state(job_id, jobs_dir=jobs_root)
    assert state["story_status"] == "FAILED"
    assert read_moments(job_id, jobs_dir=jobs_root), "Moments must remain intact"


# ── Edit Brief: OpenAI regression ────────────────────────────────────────────


def test_brief_llm_openai_regression(monkeypatch):
    monkeypatch.setenv("EDIT_PROVIDER", "openai")
    sentinel = {"editorial_intent": "intent"}
    with patch("pipeline.edit_brief._run_openai_brief", return_value=sentinel) as m:
        result = _run_brief_llm("prompt")
    assert result is sentinel
    m.assert_called_once_with("prompt", model=None)


# ── Edit Brief: Ollama ───────────────────────────────────────────────────────


def test_brief_llm_ollama_parses_realistic_output(monkeypatch):
    monkeypatch.setenv("EDIT_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, OLLAMA_BRIEF_JSON)
    brief = _run_brief_llm("prompt")
    assert brief["editorial_intent"]
    assert brief["emotional_arc"] == ["DESPAIR", "BELIEF", "RELEASE"]
    assert brief["beats"][0]["moment_id"] == "001"


def test_edit_brief_ollama_full_flow(monkeypatch, media_file, jobs_root):
    from pipeline.story_engine import write_story_suggestions
    monkeypatch.setenv("EDIT_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, OLLAMA_BRIEF_JSON)
    job = _setup_completed(media_file, jobs_root)
    job_id = job["job_id"]
    write_story_suggestions(job_id, [
        {"story_id": "s1", "title": "Story", "archetype": "COMEBACK",
         "recommended_formats": ["SHORT", "MEDIUM"], "estimated_duration": 48}
    ], jobs_dir=jobs_root)
    result = edit_brief.generate_edit_brief(job_id, "s1", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is True
    assert result["status"] == "COMPLETE"
    assert result["brief"]["format"] == "SHORT"


def test_edit_brief_ollama_malformed_output_preserves_story(monkeypatch, media_file, jobs_root):
    from pipeline.story_engine import read_story_suggestions, write_story_suggestions
    monkeypatch.setenv("EDIT_PROVIDER", "ollama")
    _patch_ollama_generate(monkeypatch, "not valid json at all")
    job = _setup_completed(media_file, jobs_root)
    job_id = job["job_id"]
    write_story_suggestions(job_id, [
        {"story_id": "s1", "title": "Story", "archetype": "COMEBACK",
         "recommended_formats": ["SHORT"], "estimated_duration": 48}
    ], jobs_dir=jobs_root)
    result = edit_brief.generate_edit_brief(job_id, "s1", "SHORT", jobs_dir=jobs_root)
    assert result["ok"] is False
    assert result["status"] == "FAILED"
    stories = read_story_suggestions(job_id, jobs_dir=jobs_root)
    assert stories and stories[0]["story_id"] == "s1", "Story must remain intact"


# ── Provider readiness per stage ─────────────────────────────────────────────


def test_stage_ready_when_ollama_reachable_and_model_present(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    monkeypatch.setattr(provider_service, "_ollama_ready",
                        lambda model=None: {"ready": True, "message": "Ollama is ready."})
    status = provider_service.story_provider_status()
    assert status["ready"] is True
    assert status["configured_provider"] == "ollama"


def test_stage_blocked_when_ollama_model_missing(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    monkeypatch.setattr(provider_service, "_ollama_ready",
                        lambda model=None: {"ready": False, "message": "Ollama is reachable but model 'x' is not pulled."})
    status = provider_service.story_provider_status()
    assert status["ready"] is False


def test_stage_blocked_when_openai_selected_no_key(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    status = provider_service.story_provider_status()
    assert status["ready"] is False
    assert "OPENAI_API_KEY" in status["message"]


def test_ollama_healthy_no_openai_warning_for_local_stage(monkeypatch):
    from pipeline.system_health import core_health_report
    monkeypatch.setenv("STORY_PROVIDER", "ollama")
    monkeypatch.setenv("EDIT_PROVIDER", "ollama")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(provider_service, "_ollama_ready",
                        lambda model=None: {"ready": True, "message": "Ollama is ready."})
    checks = core_health_report()
    provider_status = {c["check_id"]: c["status"] for c in checks}
    assert provider_status["detection_provider"] == "PASS"
    assert provider_status["story_provider"] == "PASS"
    assert provider_status["edit_provider"] == "PASS"
    assert provider_status["openai"] != "WARN", "Fully-local workflow must not warn about missing OpenAI"


def test_require_story_provider_safe_message(monkeypatch):
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(provider_service.StoryProviderUnavailable) as exc:
        provider_service.require_story_provider()
    assert "Story generation is unavailable" in str(exc.value)


def test_require_edit_provider_safe_message(monkeypatch):
    monkeypatch.setenv("EDIT_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(provider_service.EditProviderUnavailable) as exc:
        provider_service.require_edit_provider()
    assert "Edit generation is unavailable" in str(exc.value)


def test_operator_generate_stories_provider_gate(monkeypatch):
    from pipeline import operator_console
    monkeypatch.setenv("STORY_PROVIDER", "openai")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    result = operator_console.generate_stories("nonexistent_job")
    assert result["ok"] is False
    assert result["error_code"] == "STORY_PROVIDER_UNAVAILABLE"
    assert "Story generation is unavailable" in result["error"]


def test_smoke_attributes_post_transcription_failure_to_detection_stage(monkeypatch, tmp_path):
    """A failure after transcription completed (e.g. detection provider error) must be
    attributed to the detection stage: transcription reports REUSED, never FAIL."""
    from types import SimpleNamespace

    import pipeline.runtime_service as rs
    from pipeline import smoke_service
    from pipeline.smoke_service import SmokeError

    blocked = {"ok": False, "status": "FAILED",
               "error": "500 Server Error: Internal Server Error for url: http://localhost:11434/api/generate",
               "error_code": "ANALYSIS_FAILED",
               "runtime_run_id": "run_x"}
    reused_event = SimpleNamespace(event_type="TRANSCRIPT_REUSED")
    monkeypatch.setattr(rs, "list_pipeline_events", lambda **k: [reused_event])
    with patch("pipeline.operator_console.analyze_project", return_value=blocked):
        executors = smoke_service._real_analysis_executors("job_x", "project_x", tmp_path)
        transcribe = executors["transcribe"](None)
    assert transcribe["reused"] is True
    with pytest.raises(SmokeError):
        executors["detect"](None)


def test_smoke_transcription_stage_fails_when_transcription_never_completed(monkeypatch, tmp_path):
    from types import SimpleNamespace

    import pipeline.runtime_service as rs
    from pipeline import smoke_service
    from pipeline.smoke_service import SmokeError

    blocked = {"ok": False, "status": "FAILED",
               "error": "Transcription failed because model is unavailable",
               "error_code": "TRANSCRIPTION_FAILED",
               "runtime_run_id": "run_y"}
    monkeypatch.setattr(rs, "list_pipeline_events", lambda **k: [SimpleNamespace(event_type="TRANSCRIPTION_STARTED")])
    with patch("pipeline.operator_console.analyze_project", return_value=blocked):
        executors = smoke_service._real_analysis_executors("job_x", "project_x", tmp_path)
        with pytest.raises(SmokeError):
            executors["transcribe"](None)


# ── JSON parsing ─────────────────────────────────────────────────────────────


def test_parse_stories_json_handles_markdown_wrap():
    wrapped = "```json\n" + OLLAMA_STORIES_JSON + "\n```"
    assert _parse_stories_json(wrapped)[0]["story_id"] == "impossible_comeback"


def test_parse_brief_json_handles_markdown_wrap():
    wrapped = "```json\n" + OLLAMA_BRIEF_JSON + "\n```"
    assert _parse_brief_json(wrapped)["editorial_intent"]