from unittest.mock import patch
import csv

import pytest


TEMPLATE_TEXT = "{angle} - {moment_description}"
MANIFEST_ROWS = [
    {"clip_id": "001", "category": "EMOTION", "thumbnail_idea": "Crowd cheering",
     "hook_text": "Hook text", "caption": "Great moment"},
]


def _fake_root(tmp_path):
    p = tmp_path / "prompts"
    p.mkdir(parents=True, exist_ok=True)
    (p / "thumbnail_prompt_template.txt").write_text(TEMPLATE_TEXT, encoding="utf-8")


def _manifest(tmp_path):
    path = tmp_path / "manifest.csv"
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(MANIFEST_ROWS[0].keys()))
        w.writeheader()
        w.writerows(MANIFEST_ROWS)
    return path


@patch("scripts.generate_asset_prompts.ROOT")
def test_generates_thumbnail_prompt(mock_root, tmp_path):
    _fake_root(tmp_path)
    mock_root.__truediv__ = lambda self, other: tmp_path / other

    from scripts.generate_asset_prompts import THUMB_TEMPLATE, main

    with patch("scripts.generate_asset_prompts.THUMB_TEMPLATE", THUMB_TEMPLATE):
        with patch("sys.argv", ["prog", "--manifest", str(_manifest(tmp_path))]):
            main()

    out = tmp_path / "THUMBNAILS" / "EMOTION" / "001_thumbnail_prompt.txt"
    assert out.exists()
    assert "Crowd cheering" in out.read_text(encoding="utf-8")


@patch("scripts.generate_asset_prompts.ROOT")
def test_generates_caption(mock_root, tmp_path):
    _fake_root(tmp_path)
    mock_root.__truediv__ = lambda self, other: tmp_path / other

    from scripts.generate_asset_prompts import THUMB_TEMPLATE, main

    with patch("scripts.generate_asset_prompts.THUMB_TEMPLATE", THUMB_TEMPLATE):
        with patch("sys.argv", ["prog", "--manifest", str(_manifest(tmp_path))]):
            main()

    out = tmp_path / "CAPTIONS" / "EMOTION" / "001_caption.txt"
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Hook text" in content
    assert "Great moment" in content
    assert "#worldcup" in content


def test_generate_asset_prompts_has_no_hardcoded_hashtags():
    import inspect
    import scripts.generate_asset_prompts as mod
    source = inspect.getsource(mod)
    assert "#worldcup" not in source
    assert "#football" not in source
    assert "#soccer" not in source


@patch("scripts.generate_asset_prompts.ROOT")
def test_caption_uses_brand_hashtags(mock_root, tmp_path):
    _fake_root(tmp_path)
    mock_root.__truediv__ = lambda self, other: tmp_path / other

    from scripts.generate_asset_prompts import THUMB_TEMPLATE, main

    with patch("scripts.generate_asset_prompts.THUMB_TEMPLATE", THUMB_TEMPLATE):
        with patch("scripts.generate_asset_prompts.resolve_brand_hashtags",
                   return_value=["#fixturea", "#fixtureb"]):
            with patch("sys.argv", ["prog", "--manifest", str(_manifest(tmp_path))]):
                main()

    out = tmp_path / "CAPTIONS" / "EMOTION" / "001_caption.txt"
    content = out.read_text(encoding="utf-8")
    assert "#fixturea #fixtureb" in content


@patch("scripts.generate_asset_prompts.ROOT")
def test_invalid_brand_selection_exits_nonzero(mock_root, tmp_path, capsys):
    from scripts.generate_asset_prompts import main, ConfigurationError

    with patch("scripts.generate_asset_prompts.resolve_brand_hashtags",
               side_effect=ConfigurationError("brand profile not found at 'x.json'")):
        with patch("sys.argv", ["prog", "--manifest", str(_manifest(tmp_path))]):
            with pytest.raises(SystemExit) as exc:
                main()
    assert exc.value.code != 0
    captured = capsys.readouterr()
    assert "brand profile not found" in captured.err
