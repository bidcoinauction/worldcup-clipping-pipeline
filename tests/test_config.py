from pipeline.config import load_config, reload_config, get_leagues, get_model, get_path


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
