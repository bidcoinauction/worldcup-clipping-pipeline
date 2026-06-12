from unittest.mock import patch

from pipeline.livetv_resolver import LiveTVResult
from scripts.resolve_livetv_event import main

SAMPLE_HASH = "09efde1ad03b0f8b5be1bc4d97720e5ff6af3f38"


def _make_result(best_hash: str | None = None, rating: str = "95",
                 bitrate: str = "4000", language: str = "English",
                 availability: str = "live", fetch_method: str = "requests",
                 total_hashes: int = 1) -> LiveTVResult:
    ranked = []
    if best_hash:
        ranked = [{
            "hash": best_hash, "score": 115, "type": "acestream",
            "lid": "12345", "ci": "1", "si": "2",
            "bitrate": bitrate, "rating": rating, "language": language,
        }]
    return LiveTVResult(
        best_hash=best_hash,
        metadata={"total_hashes": total_hashes, "all_hashes": [best_hash] if best_hash else []},
        availability=availability,
        ranked=ranked,
        fetch_method=fetch_method,
    )


def test_prints_best_hash(capsys):
    with patch("sys.argv", ["resolve_livetv_event", "http://example.com"]), \
         patch("scripts.resolve_livetv_event.resolve_event_url",
               return_value=_make_result(best_hash=SAMPLE_HASH)) as mock_resolve:
        try:
            main()
        except SystemExit:
            pass

    mock_resolve.assert_called_once_with("http://example.com")
    out = capsys.readouterr().out
    assert SAMPLE_HASH in out
    assert "acestream://" in out
    assert "4000" in out
    assert "English" in out
    assert "95%" in out
    assert "live" in out
    assert "requests" in out


def test_json_output(capsys):
    with patch("sys.argv", ["resolve_livetv_event", "http://example.com", "--json"]), \
         patch("scripts.resolve_livetv_event.resolve_event_url",
               return_value=_make_result(best_hash=SAMPLE_HASH)):
        try:
            main()
        except SystemExit:
            pass

    out = capsys.readouterr().out
    import json
    data = json.loads(out.strip())
    assert data["best_hash"] == SAMPLE_HASH
    assert data["acestream_url"] == f"acestream://{SAMPLE_HASH}"
    assert data["bitrate"] == "4000"
    assert data["language"] == "English"
    assert data["rating"] == "95"
    assert data["availability"] == "live"
    assert data["fetch_method"] == "requests"
    assert data["total_hashes"] == 1


def test_text_no_hashes(capsys):
    with patch("sys.argv", ["resolve_livetv_event", "http://example.com"]), \
         patch("scripts.resolve_livetv_event.resolve_event_url",
               return_value=_make_result()):
        try:
            main()
        except SystemExit as e:
            assert e.code == 1

    out = capsys.readouterr().out
    assert "No Ace Stream hashes found" in out


def test_json_no_hashes(capsys):
    with patch("sys.argv", ["resolve_livetv_event", "http://example.com", "--json"]), \
         patch("scripts.resolve_livetv_event.resolve_event_url",
               return_value=_make_result()):
        try:
            main()
        except SystemExit as e:
            assert e.code == 1

    out = capsys.readouterr().out
    import json
    data = json.loads(out.strip())
    assert data["best_hash"] is None
    assert data["acestream_url"] is None
    assert data["availability"] == "live"


def test_prints_no_metadata_when_no_ranked(capsys):
    result = _make_result(best_hash=SAMPLE_HASH, rating="", bitrate="", language="")
    with patch("sys.argv", ["resolve_livetv_event", "http://example.com"]), \
         patch("scripts.resolve_livetv_event.resolve_event_url", return_value=result):
        try:
            main()
        except SystemExit:
            pass

    out = capsys.readouterr().out
    assert "N/A" in out
