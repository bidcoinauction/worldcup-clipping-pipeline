from unittest.mock import patch
import csv


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
