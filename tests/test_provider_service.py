from __future__ import annotations

import json

import pytest

from pipeline import operator_console as oc, provider_service
from pipeline.provider_service import DetectionProviderUnavailable, require_detection_provider, require_story_provider
from pipeline.runtime_service import index_existing_project, list_pipeline_events, list_project_artifacts, list_pipeline_runs
from tests.test_runtime_managed_analysis import _make_job


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))
    monkeypatch.setenv("STADIUM_RUNTIME_BACKUPS", str(tmp_path / "backups"))
    for key in ("OPENAI_API_KEY", "OLLAMA_URL"):
        monkeypatch.delenv(key, raising=False)


# ── Provider status ──────────────────────────────────────────────────────────


def test_detection_provider_blocked_safe_message(monkeypatch):
    monkeypatch.setattr(provider_service, "_ollama_ready", lambda **k: {"ready": False, "message": "Ollama is not reachable."})
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": False, "message": "Set OPENAI_API_KEY."})
    status = provider_service.detection_provider_status()
    assert status["ready"] is False
    assert status["configured_provider"] == "ollama"
    text = json.dumps(status)
    assert "localhost" not in text or status["providers"]["ollama"]["message"].lower().startswith("ollama")
    with pytest.raises(DetectionProviderUnavailable):
        require_detection_provider()


def test_detection_provider_ready_via_ollama(monkeypatch):
    monkeypatch.setattr(provider_service, "_ollama_ready", lambda **k: {"ready": True, "message": "ready"})
    status = provider_service.detection_provider_status()
    assert status["ready"] is True
    assert status["configured_provider"] == "ollama"
    assert status["model"] == "qwen2.5:3b"
    available, provider, _ = provider_service.detection_available()
    assert available is True
    assert provider == "ollama"


def test_detection_model_uses_configured_stage_model(monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    assert provider_service.detection_model() == "qwen2.5:3b"


def test_openai_ready_and_story_provider(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-provider-test-value-abcdef123456")
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": True, "message": "OpenAI is configured."})
    status = provider_service.story_provider_status()
    assert status["ready"] is True
    require_story_provider()


def test_story_provider_unavailable_when_no_openai(monkeypatch):
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": False, "message": "Set OPENAI_API_KEY."})
    with pytest.raises(Exception, match="OpenAI"):
        require_story_provider()


def test_no_provider_secrets_leak(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-fake-provider-test-value-abcdef123456")
    status = provider_service.detection_provider_status()
    assert "sk-fake-provider-test-value-abcdef123456" not in json.dumps(status)


# ── Ollama model-presence checking ───────────────────────────────────────────


def _patch_ollama_http(monkeypatch, payload=None, exc=None):
    def fake_get(url, timeout=0):
        if exc is not None:
            raise exc
        class _Resp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"models": payload or []}
        return _Resp()
    monkeypatch.setattr("requests.get", fake_get)


def test_ollama_ready_when_endpoint_and_configured_model_present(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _patch_ollama_http(monkeypatch, payload=[{"name": "qwen2.5:3b"}, {"name": "nomic-embed-text"}])
    status = provider_service._ollama_ready()
    assert status["ready"] is True


def test_ollama_missing_configured_model_not_ready(monkeypatch):
    monkeypatch.delenv("OLLAMA_MODEL", raising=False)
    _patch_ollama_http(monkeypatch, payload=[{"name": "some-other-model"}])
    status = provider_service._ollama_ready()
    assert status["ready"] is False
    assert "qwen2.5:3b" in status["message"]


def test_ollama_unreachable_not_ready(monkeypatch):
    import requests
    _patch_ollama_http(monkeypatch, exc=requests.ConnectionError("refused"))
    status = provider_service._ollama_ready()
    assert status["ready"] is False


# ── Analysis preserves transcript when detection provider is blocked ─────────


def _patch_analysis_until_detection(monkeypatch, tmp_path, project):
    transcript = tmp_path / "transcript.json"
    transcript.write_text('{"segments": []}', encoding="utf-8")

    monkeypatch.setattr(oc, "_analysis_preflight", lambda *_a: {
        "ok": True, "source_file": str(tmp_path / "source.mp4"), "match_name": "Match",
        "existing_transcript": str(transcript),
    })
    monkeypatch.setattr(oc, "_ensure_transcription", lambda *_a, **_k: (
        __import__("pipeline.runtime_service", fromlist=["register_artifact"]).register_artifact(
            project_id=project.project_id, artifact_type="transcript", path=transcript),
        transcript,
    )[1])
    monkeypatch.setattr(oc, "build_prompt", lambda **_k: {"prompt": "p"})
    monkeypatch.setattr(oc, "build_clip_manifest", lambda *_a, **_k: {
        "fieldnames": ["clip_id", "category", "start_time", "end_time"],
        "rows": [{"clip_id": "c1", "category": "GOAL", "start_time": "00:00:01", "end_time": "00:00:03"}],
    })
    return transcript


def test_analyze_detection_provider_blocked_preserves_transcript(tmp_path, monkeypatch):
    monkeypatch.setattr(provider_service, "_ollama_ready", lambda **k: {"ready": False, "message": "not reachable"})
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": False, "message": "no key"})

    job, jobs_dir, _source, _db = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    _patch_analysis_until_detection(monkeypatch, tmp_path, project)

    first = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)
    assert first["ok"] is False

    runs = list_pipeline_runs(project.project_id, stage="analysis")
    failed = runs[0]
    assert failed.status == "FAILED"
    assert failed.error_code == "DETECTION_PROVIDER_UNAVAILABLE"

    transcripts = [a for a in list_project_artifacts(project.project_id) if a.artifact_type == "transcript"]
    assert len(transcripts) == 1

    # Retry reuses the transcript; still provider-blocked, no duplicate artifact.
    second = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)
    assert second["ok"] is False
    transcripts = [a for a in list_project_artifacts(project.project_id) if a.artifact_type == "transcript"]
    assert len(transcripts) == 1
    runs = list_pipeline_runs(project.project_id, stage="analysis")
    assert len(runs) == 2
    assert all(r.error_code == "DETECTION_PROVIDER_UNAVAILABLE" for r in runs)


def test_analyze_detection_uses_configured_model_for_call_and_provenance(tmp_path, monkeypatch):
    monkeypatch.setenv("OLLAMA_MODEL", "llama3.1")
    monkeypatch.setattr(provider_service, "_ollama_ready", lambda **k: {"ready": True, "message": "ready"})
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": False, "message": "no key"})

    job, jobs_dir, _source, _db = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    _patch_analysis_until_detection(monkeypatch, tmp_path, project)

    captured = {}

    def fake_detection_call(*args, **kwargs):
        captured["kwargs"] = kwargs
        raise RuntimeError("stop after detection dispatch")

    monkeypatch.setattr(oc, "run_detection_call", fake_detection_call)

    result = oc.analyze_project(job["job_id"], jobs_dir=jobs_dir)

    assert result["ok"] is False
    assert captured["kwargs"]["provider"] == "ollama"
    assert captured["kwargs"]["model"] == "qwen2.5:3b"
    assert captured["kwargs"]["model"] != "llama3.1"

    events = list_pipeline_events(project_id=project.project_id)
    started = next(event for event in events if event.event_type == "DETECTION_STARTED")
    assert started.metadata["provider"] == "ollama"
    assert started.metadata["model"] == "qwen2.5:3b"


# ── System health includes providers ─────────────────────────────────────────


def test_system_health_includes_providers(monkeypatch):
    monkeypatch.setattr(provider_service, "_ollama_ready", lambda **k: {"ready": False, "message": "not reachable"})
    monkeypatch.setattr(provider_service, "_openai_ready", lambda: {"ready": False, "message": "no key"})
    from pipeline.system_health import full_health_report
    report = full_health_report()
    assert "providers" in report
    assert report["providers"]["detection"]["ready"] is False
    check = next(c for c in report["core"] if c["check_id"] == "detection_provider")
    assert check["status"] == "WARN"
