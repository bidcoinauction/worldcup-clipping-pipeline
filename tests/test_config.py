from pipeline.config import (
    get_clip_mode,
    get_default_clip_mode,
    get_leagues,
    get_model,
    get_path,
    get_provider,
    load_config,
    reload_config,
)


def _clear():
    reload_config()


def test_load_config_returns_dict():
    cfg = load_config()
    assert isinstance(cfg, dict)
    for key in ("leagues", "models", "paths"):
        assert key in cfg


def test_load_config_caches():
    a = load_config()
    b = load_config()
    assert a is b
    _clear()


def test_get_leagues_returns_list():
    leagues = get_leagues()
    assert isinstance(leagues, list)
    assert len(leagues) > 0
    assert all(isinstance(l, str) for l in leagues)
    _clear()


def test_get_model_returns_string():
    for name in ("detection", "transcription"):
        val = get_model(name)
        assert isinstance(val, str)
        assert len(val) > 0
    _clear()


def test_get_path_returns_string():
    val = get_path("thumbnail_template")
    assert isinstance(val, str)
    assert val.endswith(".txt")
    _clear()


def test_get_provider_returns_string():
    for name in ("transcription", "detection"):
        val = get_provider(name)
        assert isinstance(val, str)
        assert val in ("openai", "faster-whisper", "ollama")
    _clear()


def test_get_clip_mode_story_returns_dict():
    mode = get_clip_mode("story")
    assert isinstance(mode, dict)
    assert mode["min_seconds"] == 8
    assert mode["max_seconds"] == 45
    assert mode["min_clips"] == 3
    assert mode["max_clips"] == 5
    _clear()


def test_get_clip_mode_micro_returns_dict():
    mode = get_clip_mode("micro")
    assert isinstance(mode, dict)
    assert mode["min_seconds"] == 1.5
    assert mode["max_seconds"] == 3.8
    assert mode["min_clips"] == 5
    assert mode["max_clips"] == 12
    _clear()


def test_get_default_clip_mode_returns_story():
    val = get_default_clip_mode()
    assert isinstance(val, str)
    assert val == "story"
    _clear()
