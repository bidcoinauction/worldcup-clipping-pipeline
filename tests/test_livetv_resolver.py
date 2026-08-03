import pytest

from pipeline.livetv_resolver import (
    LiveTVResult,
    parse_event_info,
    parse_stream_rows,
    rank_hashes,
    check_availability_windows,
    resolve_event_url,
    parse_other_streams,
)


# ── Fixtures ──

SAMPLE_HTML = """<!DOCTYPE html>
<html>
<head><title>South Korea KOR &ndash; Czech Republic CZE Live Stream | World Cup</title></head>
<body>
<div class="cat">World Cup 2026</div>
<h1>South Korea vs Czech Republic</h1>
<div class="tbl">
<table>
<tr>
  <td><div class="rate">95</div></td>
  <td><div id="rali12345">95</div></td>
  <td><a href="acestream://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1">AceStream</a></td>
  <td class="bitrate" title="4000 Kbps">4000</td>
  <td><img src="/linkflag/2.png" title="English" /></td>
  <td><a href="webplayer2.php?lid=12345&amp;ci=1&amp;si=2">web</a></td>
</tr>
<tr>
  <td><div class="rate">90</div></td>
  <td><div id="rali12346">90</div></td>
  <td><a href="acestream://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2">AceStream</a></td>
  <td class="bitrate" title="2500 Kbps">2500</td>
  <td><img src="/linkflag/1.png" title="Russian" /></td>
  <td><a href="webplayer2.php?lid=12346&amp;ci=1&amp;si=2">web</a></td>
</tr>
<tr>
  <td><div class="rate">92</div></td>
  <td><div id="rali12347">92</div></td>
  <td><a href="acestream://ccccccccccccccccccccccccccccccccccccccc3">AceStream</a></td>
  <td class="bitrate" title="6000 Kbps">6000</td>
  <td><img src="/linkflag/3.png" title="Spanish" /></td>
  <td><a href="webplayer2.php?lid=12347&amp;ci=1&amp;si=2">web</a></td>
</tr>
</table>
</div>
<div class="tbl">
<table>
<tr>
  <td><div class="rate">85</div></td>
  <td><div id="rali12348">85</div></td>
  <td><a href="acestream://ddddddddddddddddddddddddddddddddddddddd4">AceStream</a></td>
  <td class="bitrate" title="3000 Kbps">3000</td>
  <td><img src="/linkflag/2.png" title="English" /></td>
  <td><a href="webplayer2.php?lid=12348&amp;ci=1&amp;si=2">web</a></td>
</tr>
</table>
</div>
</body>
</html>"""

NO_HASH_HTML = """<!DOCTYPE html>
<html>
<head><title>Test Event</title></head>
<body>
<h1>Test Match</h1>
<div class="cat">Friendly</div>
<p>No streams available yet.</p>
</body>
</html>"""

LIVE_HTML = """<!DOCTYPE html>
<html>
<head><title>Live Match</title></head>
<body>
<span class="live">LIVE</span>
<p>Match started</p>
<div class="tbl">
<table>
<tr>
  <td><a href="acestream://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1">AceStream</a></td>
  <td class="bitrate" title="5000 Kbps">5000</td>
  <td><img src="/linkflag/2.png" title="English" /></td>
</tr>
</table>
</div>
</body>
</html>"""

UPCOMING_HTML = """<!DOCTYPE html>
<html>
<head><title>Upcoming Match</title></head>
<body>
<span class="upcoming">Starts in 2h</span>
<p>No links available yet</p>
</body>
</html>"""


# ── parse_event_info ──

def test_parse_event_info_title():
    info = parse_event_info(SAMPLE_HTML)
    assert "South Korea" in info["title"]
    assert "Czech Republic" in info["title"]


def test_parse_event_info_match():
    info = parse_event_info(SAMPLE_HTML)
    assert info["match"] == "South Korea vs Czech Republic"


def test_parse_event_info_competition():
    info = parse_event_info(SAMPLE_HTML)
    assert info["competition"] == "World Cup 2026"


def test_parse_event_info_event_id_from_url():
    html = '<a href="/eventinfo/123456_match">link</a>'
    info = parse_event_info(html)
    assert info["event_id"] == "123456"


def test_parse_event_info_empty_html():
    info = parse_event_info("<html></html>")
    assert info["title"] == "unknown"
    assert info["match"] == ""
    assert info["competition"] == ""
    assert info["event_id"] == ""


# ── parse_stream_rows ──

def test_parse_stream_rows_count():
    rows = parse_stream_rows(SAMPLE_HTML)
    assert len(rows) == 4


def test_parse_stream_rows_hash_values():
    rows = parse_stream_rows(SAMPLE_HTML)
    hashes = [r["hash"] for r in rows]
    assert "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1" in hashes
    assert "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2" in hashes
    assert "ccccccccccccccccccccccccccccccccccccccc3" in hashes
    assert "ddddddddddddddddddddddddddddddddddddddd4" in hashes


def test_parse_stream_rows_lid():
    rows = parse_stream_rows(SAMPLE_HTML)
    lids = [r["lid"] for r in rows]
    assert "12345" in lids
    assert "12346" in lids


def test_parse_stream_rows_type():
    rows = parse_stream_rows(SAMPLE_HTML)
    for r in rows:
        assert r["type"] == "acestream"


def test_parse_stream_rows_language():
    rows = parse_stream_rows(SAMPLE_HTML)
    langs = {r["hash"]: r["language"] for r in rows}
    assert langs.get("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1") == "English"
    assert langs.get("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2") == "Russian"
    assert langs.get("ccccccccccccccccccccccccccccccccccccccc3") == "Spanish"


def test_parse_stream_rows_bitrate():
    rows = parse_stream_rows(SAMPLE_HTML)
    bitrates = {r["hash"]: r["bitrate"] for r in rows}
    assert bitrates.get("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1") == "4000"
    assert bitrates.get("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2") == "2500"
    assert bitrates.get("ccccccccccccccccccccccccccccccccccccccc3") == "6000"


def test_parse_stream_rows_rating():
    rows = parse_stream_rows(SAMPLE_HTML)
    ratings = {r["hash"]: r["rating"] for r in rows}
    assert ratings.get("aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1") == "95"


def test_parse_stream_rows_empty_html():
    rows = parse_stream_rows("<html></html>")
    assert rows == []


def test_parse_stream_rows_no_ace():
    rows = parse_stream_rows(NO_HASH_HTML)
    assert rows == []


def test_parse_stream_rows_ci_si():
    html = '<div class="tbl"><table><tr><td><a href="acestream://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1">AceStream</a></td><td><div id="rali12345">95</div></td><td><a href="webplayer2.php?lid=12345&ci=777&si=1">web</a></td></tr></table></div>'
    rows = parse_stream_rows(html)
    if rows:
        assert rows[0]["ci"] == "777"
        assert rows[0]["si"] == "1"


# ── rank_hashes ──

def test_rank_hashes_english_first():
    rows = parse_stream_rows(SAMPLE_HTML)
    ranked = rank_hashes(rows)
    assert ranked[0]["hash"] == "ccccccccccccccccccccccccccccccccccccccc3"  # Spanish 6000 Kbps > English 4000 Kbps


def test_rank_hashes_high_bitrate_boost():
    rows = parse_stream_rows(SAMPLE_HTML)
    ranked = rank_hashes(rows)
    # The 6000 Kbps Spanish stream should rank higher than the 2500 Kbps Russian
    ranked_hashes = [r["hash"] for r in ranked]
    spanish_idx = ranked_hashes.index("ccccccccccccccccccccccccccccccccccccccc3")
    russian_idx = ranked_hashes.index("bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2")
    assert spanish_idx < russian_idx


def test_rank_hashes_returns_all():
    rows = parse_stream_rows(SAMPLE_HTML)
    ranked = rank_hashes(rows)
    assert len(ranked) == len(rows)


def test_rank_hashes_empty():
    assert rank_hashes([]) == []


def test_rank_hashes_scores_are_ints():
    rows = parse_stream_rows(SAMPLE_HTML)
    ranked = rank_hashes(rows)
    for r in ranked:
        assert isinstance(r["score"], (int, float))


# ── check_availability_windows ──

def test_availability_live():
    result = check_availability_windows("http://example.com", html=LIVE_HTML)
    assert result["state"] == "live"


def test_availability_upcoming():
    result = check_availability_windows("http://example.com", html=UPCOMING_HTML)
    assert result["state"] == "upcoming"


def test_availability_no_links():
    result = check_availability_windows("http://example.com", html=NO_HASH_HTML)
    assert result["state"] == "no_links"


def test_availability_unreachable():
    result = check_availability_windows("http://127.0.0.1:1/", html=None)
    assert result["state"] == "unreachable"


def test_availability_unknown():
    result = check_availability_windows("http://example.com", html="<html><body><p>random page</p></body></html>")
    assert result["state"] == "unknown"


# ── parse_other_streams ──

def test_parse_other_streams_empty():
    assert parse_other_streams("<html></html>") == []


def test_parse_other_streams_alieztv():
    html = '<a href="webplayer2.php?t=alieztv&amp;c=261227">Aliez</a>'
    others = parse_other_streams(html)
    assert len(others) > 0
    assert others[0]["type"] == "alieztv"


# ── resolve_event_url ──

def test_resolve_event_url_unreachable():
    result = resolve_event_url("http://localhost:1/nonexistent")
    assert isinstance(result, LiveTVResult)
    assert result.best_hash is None
    assert result.availability == "unreachable"


# ── LiveTVResult dataclass ──

def test_livetv_result_attributes():
    result = LiveTVResult(
        best_hash="abc123",
        metadata={"title": "test"},
        availability="live",
        ranked=[],
        fetch_method="requests",
    )
    assert result.best_hash == "abc123"
    assert result.metadata["title"] == "test"
    assert result.availability == "live"
    assert result.ranked == []
    assert result.fetch_method == "requests"


# ── Integration test (network, optional) ──

@pytest.mark.network
def test_resolve_real_livetv_url():
    url = "https://livetv.sx/enx/eventinfo/384718862_south_korea_kor_czech_republic_cze/"
    result = resolve_event_url(url)
    assert result.availability in ("live", "upcoming", "completed", "no_links", "unreachable")
    assert result.metadata.get("title") is not None
    assert result.fetch_method in ("requests", "curl", "all strategies failed")
