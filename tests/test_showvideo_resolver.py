from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import Mock, call, patch

import pytest

from pipeline.showvideo_resolver import (
    _extract_media_urls,
    _has_cloudflare_challenge,
    download_media,
    download_hls_via_ffmpeg,
    fetch_page,
    pick_best_candidate,
    read_sidecars,
    resolve_and_download,
    resolve_iframe_url,
    validate_media_url,
    write_sidecar,
)

# ── Fixtures ──

DIRECT_VIDEO_HTML = """<!DOCTYPE html>
<html><head><title>Match Video</title></head>
<body>
<video controls src="https://cdn.example.com/video/123456.mp4?st=abc123">
Your browser does not support the video tag.
</video>
</body></html>"""

DIRECT_SOURCE_HTML = """<!DOCTYPE html>
<html><body>
<video controls>
<source src="https://cdn.example.com/video/789012.mp4?st=def456" type="video/mp4">
</video>
</body></html>"""

ESCAPED_MP4_HTML = """<!DOCTYPE html>
<html><body>
<script>
var videoUrl = "https:\\/\\/cdn.example.com\\/video\\/escaped_123.mp4?st=xyz789";
var player = new Player({ file: videoUrl });
</script>
</body></html>"""

IFRAME_HTML = """<!DOCTYPE html>
<html><body>
<iframe src="https://player.example.com/embed/98765" width="640" height="360"></iframe>
</body></html>"""

FILE_CONFIG_HTML = """<!DOCTYPE html>
<html><body>
<script>
var jwplayer = {
  setup: function(config) {
    console.log(config.file);
  }
};
jwplayer.setup({
  file: "https://cdn.example.com/video/jw_123.mp4",
  image: "https://cdn.example.com/thumbs/123.jpg"
});
</script>
</body></html>"""

SRC_CONFIG_HTML = """<!DOCTYPE html>
<html><body>
<script>
var flowplayerConf = {
  clip: { url: "https://cdn.example.com/video/flow_456.mp4" },
  plugins: { controls: {} }
};
</script>
</body></html>"""

M3U8_HTML = """<!DOCTYPE html>
<html><body>
<script>
var hlsUrl = "https://cdn.example.com/hls/stream.m3u8?token=abc";
var hls = new Hls();
hls.loadSource(hlsUrl);
</script>
</body></html>"""

NO_VIDEO_HTML = """<!DOCTYPE html>
<html><head><title>No Video Here</title></head>
<body>
<p>This page contains no media.</p>
</body></html>"""

LINK_M3U8_HTML = """<!DOCTYPE html>
<html><body>
<a href="https://cdn.example.com/hls/live.m3u8">HLS Stream</a>
</body></html>"""

MULTIPLE_CANDIDATES_HTML = """<!DOCTYPE html>
<html><body>
<video src="https://cdn.example.com/video/high_quality.mp4"></video>
<script>
var alt = "https://cdn.example.com/video/low_quality.mp4";
var hlsAlt = "https://cdn.example.com/hls/fallback.m3u8";
</script>
</body></html>"""

CLOUDFLARE_HTML = """<!DOCTYPE html>
<html><body>
<iframe src="https://cloudflare-ok.example.com/embed/123"></iframe>
Just a moment... checking your browser
</body></html>"""


# ── _extract_media_urls ──

class TestExtractMediaUrls:
    def test_direct_video_tag(self):
        candidates = _extract_media_urls(DIRECT_VIDEO_HTML)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/video/123456.mp4?st=abc123" in urls

    def test_direct_source_tag(self):
        candidates = _extract_media_urls(DIRECT_SOURCE_HTML)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/video/789012.mp4?st=def456" in urls

    def test_escaped_mp4(self):
        candidates = _extract_media_urls(ESCAPED_MP4_HTML)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/video/escaped_123.mp4?st=xyz789" in urls

    def test_file_config_key(self):
        candidates = _extract_media_urls(FILE_CONFIG_HTML)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/video/jw_123.mp4" in urls

    def test_src_config_key(self):
        candidates = _extract_media_urls(SRC_CONFIG_HTML)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/video/flow_456.mp4" in urls

    def test_m3u8_detected(self):
        candidates = _extract_media_urls(M3U8_HTML)
        m3u8_candidates = [c for c in candidates if c["is_hls"]]
        assert len(m3u8_candidates) > 0
        assert any("stream.m3u8" in c["url"] for c in m3u8_candidates)

    def test_m3u8_via_link(self):
        candidates = _extract_media_urls(LINK_M3U8_HTML)
        m3u8s = [c for c in candidates if c["is_hls"]]
        assert any("live.m3u8" in c["url"] for c in m3u8s)

    def test_no_video_found(self):
        candidates = _extract_media_urls(NO_VIDEO_HTML)
        assert candidates == []

    def test_multiple_candidates(self):
        candidates = _extract_media_urls(MULTIPLE_CANDIDATES_HTML)
        assert len(candidates) >= 3
        media = [c for c in candidates if c["is_media"]]
        assert len(media) >= 2

    def test_deduplicates_urls(self):
        html = ('<video src="https://cdn.example.com/v/1.mp4"></video>'
                '<source src="https://cdn.example.com/v/1.mp4">')
        candidates = _extract_media_urls(html)
        urls = [c["url"] for c in candidates]
        assert urls.count("https://cdn.example.com/v/1.mp4") == 1

    def test_protocol_relative_url(self):
        html = '<video src="//cdn.example.com/v/video.mp4"></video>'
        candidates = _extract_media_urls(html)
        urls = [c["url"] for c in candidates]
        assert "https://cdn.example.com/v/video.mp4" in urls

    def test_media_flag(self):
        candidates = _extract_media_urls(DIRECT_VIDEO_HTML)
        assert candidates[0]["is_media"] is True
        assert candidates[0]["is_hls"] is False

        hls_candidates = _extract_media_urls(M3U8_HTML)
        hls = [c for c in hls_candidates if c["is_hls"]]
        assert all(c["is_media"] is False for c in hls)


# ── _has_cloudflare_challenge ──

class TestHasCloudflareChallenge:
    def test_detects_challenge(self):
        assert _has_cloudflare_challenge(CLOUDFLARE_HTML) is True

    def test_clean_page(self):
        assert _has_cloudflare_challenge(DIRECT_VIDEO_HTML) is False

    def test_empty_html(self):
        assert _has_cloudflare_challenge("") is False


# ── fetch_page ──

class TestFetchPage:
    def test_requests_success(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.text = DIRECT_VIDEO_HTML
            mock_req.get.return_value = mock_resp

            html, method = fetch_page("https://livetv.sx/enx/showvideo/123/")
            assert html == DIRECT_VIDEO_HTML
            assert method == "requests"

    def test_requests_cloudflare_fallback(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_resp = Mock()
            mock_resp.status_code = 200
            mock_resp.text = CLOUDFLARE_HTML
            mock_req.get.return_value = mock_resp

            with patch("pipeline.showvideo_resolver.subprocess.run") as mock_run:
                mock_run.return_value = Mock(returncode=0, stdout=DIRECT_VIDEO_HTML, stderr="")
                html, method = fetch_page("https://livetv.sx/enx/showvideo/123/")
                assert html == DIRECT_VIDEO_HTML
                assert method == "curl"

    def test_all_strategies_fail(self):
        with patch("pipeline.showvideo_resolver.requests.get", side_effect=Exception("no net")):
            with patch("pipeline.showvideo_resolver.subprocess.run", return_value=Mock(returncode=1, stdout="", stderr="fail")):
                html, method = fetch_page("https://livetv.sx/enx/showvideo/123/")
                assert html is None
                assert "failed" in method


# ── resolve_iframe_url ──

class TestResolveIframeUrl:
    def test_finds_iframe_src(self):
        url = resolve_iframe_url(IFRAME_HTML, "https://livetv.sx/enx/showvideo/123/")
        assert url == "https://player.example.com/embed/98765"

    def test_protocol_relative_iframe(self):
        html = '<iframe src="//player.example.com/embed/555"></iframe>'
        url = resolve_iframe_url(html, "https://livetv.sx/enx/showvideo/123/")
        assert url == "https://player.example.com/embed/555"

    def test_relative_iframe_path(self):
        html = '<iframe src="/player/embed/333"></iframe>'
        url = resolve_iframe_url(html, "https://livetv.sx/enx/showvideo/123/")
        assert url == "https://livetv.sx/player/embed/333"

    def test_no_iframe(self):
        url = resolve_iframe_url(DIRECT_VIDEO_HTML, "https://livetv.sx/enx/showvideo/123/")
        assert url is None


# ── pick_best_candidate ──

class TestPickBestCandidate:
    def test_prefers_media_over_hls(self):
        candidates = [
            {"url": "https://cdn.example.com/hls/stream.m3u8", "is_media": False, "is_hls": True, "extension": ".m3u8", "discovery_method": "hls_m3u8"},
            {"url": "https://cdn.example.com/video/clip.mp4", "is_media": True, "is_hls": False, "extension": ".mp4", "discovery_method": "video_tag"},
        ]
        best = pick_best_candidate(candidates)
        assert best is not None
        assert best["is_media"] is True

    def test_falls_back_to_hls(self):
        candidates = [
            {"url": "https://cdn.example.com/hls/stream.m3u8", "is_media": False, "is_hls": True, "extension": ".m3u8", "discovery_method": "hls_m3u8"},
        ]
        best = pick_best_candidate(candidates)
        assert best is not None
        assert best["is_hls"] is True

    def test_returns_first_if_none_match(self):
        candidates = [
            {"url": "https://cdn.example.com/unknown/file.xyz", "is_media": False, "is_hls": False, "extension": ".xyz", "discovery_method": "src_config_key"},
        ]
        best = pick_best_candidate(candidates)
        assert best is not None
        assert best["url"] == "https://cdn.example.com/unknown/file.xyz"

    def test_empty_list(self):
        assert pick_best_candidate([]) is None


# ── validate_media_url ──

class TestValidateMediaUrl:
    def test_head_success(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_head = Mock()
            mock_head.status_code = 200
            mock_head.headers = {"Content-Type": "video/mp4", "Content-Length": "12345678", "Accept-Ranges": "bytes"}
            mock_req.head.return_value = mock_head

            result = validate_media_url("https://cdn.example.com/video/123.mp4", referer="https://livetv.sx/")
            assert result["valid"] is True
            assert result["method"] == "head"
            assert result["content_type"] == "video/mp4"
            assert result["content_length"] == 12345678

    def test_head_blocked_range_get_succeeds(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_head = Mock()
            mock_head.status_code = 403
            mock_head.headers = {}
            mock_req.head.return_value = mock_head

            mock_range = Mock()
            mock_range.status_code = 206
            mock_range.headers = {"Content-Type": "video/mp4", "Content-Range": "bytes 0-0/98765432"}
            mock_req.get.return_value = mock_range

            result = validate_media_url("https://cdn.example.com/video/123.mp4")
            assert result["valid"] is True
            assert result["method"] == "range_get"
            assert result["content_length"] == 98765432

    def test_both_fail(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_head = Mock()
            mock_head.status_code = 403
            mock_head.headers = {}
            mock_req.head.return_value = mock_head

            mock_range = Mock()
            mock_range.status_code = 403
            mock_range.headers = {"Content-Type": "text/html"}
            mock_req.get.return_value = mock_range

            result = validate_media_url("https://cdn.example.com/video/123.mp4")
            assert result["valid"] is False

    def test_non_video_content_type(self):
        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_head = Mock()
            mock_head.status_code = 200
            mock_head.headers = {"Content-Type": "text/html", "Content-Length": "5000"}
            mock_req.head.return_value = mock_head

            result = validate_media_url("https://cdn.example.com/page.html")
            assert result["valid"] is False


# ── download_media ──

class TestDownloadMedia:
    def test_download_success(self, tmp_path):
        dest = tmp_path / "test_clip.mp4"

        mock_response = Mock()
        mock_response.headers = {"Content-Type": "video/mp4", "Content-Length": "1024"}
        mock_response.iter_content.return_value = [b"x" * 512, b"y" * 512]
        mock_response.raise_for_status = Mock()

        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_req.get.return_value = mock_response
            result = download_media("https://cdn.example.com/video/test.mp4", dest, referer="https://livetv.sx/")

        assert result["success"] is True
        assert result["bytes"] == 1024
        assert dest.exists()
        assert dest.read_bytes() == b"x" * 512 + b"y" * 512

    def test_download_creates_parent_dir(self, tmp_path):
        dest = tmp_path / "subdir" / "clip.mp4"

        mock_response = Mock()
        mock_response.headers = {"Content-Type": "video/mp4"}
        mock_response.iter_content.return_value = [b"testdata"]
        mock_response.raise_for_status = Mock()

        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_req.get.return_value = mock_response
            result = download_media("https://cdn.example.com/video/test.mp4", dest)

        assert result["success"] is True
        assert dest.exists()

    def test_download_zero_bytes_fails(self, tmp_path):
        dest = tmp_path / "empty.mp4"

        mock_response = Mock()
        mock_response.headers = {"Content-Type": "video/mp4"}
        mock_response.iter_content.return_value = []
        mock_response.raise_for_status = Mock()

        with patch("pipeline.showvideo_resolver.requests") as mock_req:
            mock_req.get.return_value = mock_response
            result = download_media("https://cdn.example.com/video/empty.mp4", dest)

        assert result["success"] is False
        assert not dest.exists()

    def test_network_error_cleans_up_tmp(self, tmp_path):
        dest = tmp_path / "fail.mp4"

        with patch("pipeline.showvideo_resolver.requests.get", side_effect=Exception("connection lost")):
            result = download_media("https://cdn.example.com/video/fail.mp4", dest)

        assert result["success"] is False
        assert "connection lost" in result.get("error", "")
        assert not dest.exists()


# ── download_hls_via_ffmpeg ──

class TestDownloadHlsViaFfmpeg:
    def test_ffmpeg_success(self, tmp_path):
        dest = tmp_path / "stream.mp4"

        def _fake_run(*args, **kwargs):
            dest.write_bytes(b"ffmpeg data")
            return Mock(returncode=0, stdout="", stderr="")

        with patch("pipeline.showvideo_resolver.subprocess.run", side_effect=_fake_run):
            result = download_hls_via_ffmpeg("https://cdn.example.com/hls/stream.m3u8", dest)

        assert result["success"] is True
        assert result["bytes"] > 0

    def test_ffmpeg_failure(self, tmp_path):
        dest = tmp_path / "stream.mp4"

        with patch("pipeline.showvideo_resolver.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=1, stdout="", stderr="Error: connection failed")
            result = download_hls_via_ffmpeg("https://cdn.example.com/hls/stream.m3u8", dest)

        assert result["success"] is False
        assert "connection failed" in result.get("error", "")

    def test_ffmpeg_not_found(self, tmp_path):
        dest = tmp_path / "stream.mp4"

        with patch("pipeline.showvideo_resolver.subprocess.run", side_effect=FileNotFoundError()):
            result = download_hls_via_ffmpeg("https://cdn.example.com/hls/stream.m3u8", dest)

        assert result["success"] is False
        assert "ffmpeg not found" in result.get("error", "")


# ── write_sidecar / read_sidecars ──

class TestSidecar:
    def test_sidecar_schema(self, tmp_path):
        mp4_path = tmp_path / "test_clip.mp4"
        metadata = {
            "source_page_url": "https://livetv.sx/enx/showvideo/123/",
            "resolved_media_url": "https://cdn.example.com/video/test.mp4?st=abc",
            "discovery_method": "video_tag",
            "content_type": "video/mp4",
            "content_length": 12345678,
            "downloaded_bytes": 12345678,
            "downloaded_at": "2026-06-15T12:00:00+00:00",
            "match_name": "Haiti vs Scotland",
            "league": "WORLD_CUP",
            "match_slug": "haiti_vs_scotland",
            "output_filename": "haiti_vs_scotland_livetv_001.mp4",
            "validation_info": {"method": "head", "content_type": "video/mp4", "status_code": 200},
            "steps": [{"step": "fetch", "status": "ok"}],
        }
        write_sidecar(mp4_path, metadata)

        sidecar_path = mp4_path.with_suffix(mp4_path.suffix + ".import.json")
        assert sidecar_path.exists()

        loaded = json.loads(sidecar_path.read_text(encoding="utf-8"))
        assert loaded["source_page_url"] == "https://livetv.sx/enx/showvideo/123/"
        assert loaded["resolved_media_url"] == "https://cdn.example.com/video/test.mp4?st=abc"
        assert loaded["content_type"] == "video/mp4"
        assert loaded["match_name"] == "Haiti vs Scotland"
        assert loaded["league"] == "WORLD_CUP"
        assert loaded["match_slug"] == "haiti_vs_scotland"

    def test_read_sidecars_returns_url_index(self, tmp_path):
        mp4_1 = tmp_path / "clip_a.mp4"
        write_sidecar(mp4_1, {
            "resolved_media_url": "https://cdn.example.com/video/a.mp4",
            "source_page_url": "https://livetv.sx/enx/showvideo/1/",
        })

        mp4_2 = tmp_path / "clip_b.mp4"
        write_sidecar(mp4_2, {
            "resolved_media_url": "https://cdn.example.com/video/b.mp4",
            "source_page_url": "https://livetv.sx/enx/showvideo/2/",
        })

        index = read_sidecars(tmp_path)
        assert "https://cdn.example.com/video/a.mp4" in index
        assert "https://cdn.example.com/video/b.mp4" in index
        assert len(index) == 2

    def test_read_sidecars_empty_dir(self, tmp_path):
        assert read_sidecars(tmp_path) == {}


# ── resolve_and_download (integration-level) ──

class TestResolveAndDownload:
    def test_dry_run_creates_no_files(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                league="WORLD_CUP",
                output_root=tmp_path,
                dry_run=True,
            )

        assert result["dry_run"] is True
        assert result.get("error") is None
        assert not list(tmp_path.iterdir())

    def test_dry_run_reports_candidate(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                dry_run=True,
            )

        assert result["candidate_selected"]["url"] == "https://cdn.example.com/video/123456.mp4?st=abc123"

    def test_no_video_found_error(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.return_value = (NO_VIDEO_HTML, "requests")

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                dry_run=True,
            )

        assert "No media URLs found" in result.get("error", "")

    def test_unreachable_page(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.return_value = (None, "all strategies failed")

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
            )

        assert "Could not fetch" in result.get("error", "")

    def test_successful_download_with_sidecar(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.validate_media_url") as mock_validate, \
             patch("pipeline.showvideo_resolver.download_media") as mock_dl:

            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")
            mock_validate.return_value = {
                "valid": True,
                "method": "head",
                "content_type": "video/mp4",
                "content_length": 1024,
                "status_code": 200,
                "accept_ranges": "bytes",
            }
            mock_dl.return_value = {"success": True, "bytes": 1024, "content_type": "video/mp4"}

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                league="WORLD_CUP",
                output_root=tmp_path,
                force=True,
            )

        assert result.get("error") is None
        assert result["candidate_selected"] is not None
        assert result["download"]["success"] is True
        assert result["sidecar"] is not None
        assert result["sidecar"]["match_name"] == "Haiti vs Scotland"
        assert result["sidecar"]["league"] == "WORLD_CUP"
        assert result["sidecar"]["match_slug"] == "haiti_vs_scotland"
        assert result["sidecar"]["resolved_media_url"] == "https://cdn.example.com/video/123456.mp4?st=abc123"

    def test_dedup_skips_existing(self, tmp_path):
        match_slug = "haiti_vs_scotland"
        output_dir = tmp_path / match_slug
        output_dir.mkdir(parents=True)

        # Write a fake sidecar that references the same URL
        fake_sidecar = {
            "resolved_media_url": "https://cdn.example.com/video/123456.mp4?st=abc123",
            "match_name": "Haiti vs Scotland",
        }
        sidecar_path = output_dir / "existing.mp4.import.json"
        sidecar_path.write_text(json.dumps(fake_sidecar), encoding="utf-8")

        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                league="WORLD_CUP",
                output_root=tmp_path,
            )

        assert result.get("skipped") is True
        assert result.get("error") is None

    def test_force_overwrites_despite_existing_sidecar(self, tmp_path):
        match_slug = "haiti_vs_scotland"
        output_dir = tmp_path / match_slug
        output_dir.mkdir(parents=True)

        fake_sidecar = {
            "resolved_media_url": "https://cdn.example.com/video/123456.mp4?st=abc123",
            "match_name": "Haiti vs Scotland",
        }
        sidecar_path = output_dir / "existing.mp4.import.json"
        sidecar_path.write_text(json.dumps(fake_sidecar), encoding="utf-8")

        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.validate_media_url") as mock_validate, \
             patch("pipeline.showvideo_resolver.download_media") as mock_dl:

            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")
            mock_validate.return_value = {
                "valid": True,
                "method": "head",
                "content_type": "video/mp4",
                "content_length": 1024,
                "status_code": 200,
                "accept_ranges": "bytes",
            }
            mock_dl.return_value = {"success": True, "bytes": 1024, "content_type": "video/mp4"}

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                league="WORLD_CUP",
                output_root=tmp_path,
                force=True,
            )

        assert result.get("skipped") is None or result.get("skipped") is False
        assert result["download"]["success"] is True

    def test_validation_failure_stops_download(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.validate_media_url") as mock_validate:

            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")
            mock_validate.return_value = {
                "valid": False,
                "method": "failed",
                "content_type": None,
                "content_length": None,
                "status_code": 0,
            }

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                force=True,
            )

        assert "failed validation" in result.get("error", "")

    def test_download_failure_reported(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.validate_media_url") as mock_validate, \
             patch("pipeline.showvideo_resolver.download_media") as mock_dl:

            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")
            mock_validate.return_value = {
                "valid": True,
                "method": "head",
                "content_type": "video/mp4",
                "content_length": 1024,
                "status_code": 200,
                "accept_ranges": "bytes",
            }
            mock_dl.return_value = {"success": False, "error": "disk full", "bytes": 0}

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                force=True,
            )

        assert "disk full" in result.get("error", "")

    def test_iframe_followed_and_discovered(self, tmp_path):
        # Page has iframe, and iframe page has the video
        iframe_url = "https://player.example.com/embed/98765"
        iframe_video_html = '<video src="https://cdn.example.com/video/iframe_video.mp4"></video>'

        def mock_fetch_side_effect(url, *args, **kwargs):
            if "showvideo" in url:
                return (IFRAME_HTML, "requests")
            if "player.example.com" in url:
                return (iframe_video_html, "requests")
            return (None, "fallback")

        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch:
            mock_fetch.side_effect = mock_fetch_side_effect

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                dry_run=True,
            )

        assert result["candidate_selected"] is not None
        assert "iframe_video.mp4" in result["candidate_selected"]["url"]

    def test_output_path_uses_slug(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.validate_media_url") as mock_validate, \
             patch("pipeline.showvideo_resolver.download_media") as mock_dl:

            mock_fetch.return_value = (DIRECT_VIDEO_HTML, "requests")
            mock_validate.return_value = {"valid": True, "method": "head", "content_type": "video/mp4",
                                          "content_length": 100, "status_code": 200, "accept_ranges": "bytes"}
            mock_dl.return_value = {"success": True, "bytes": 100, "content_type": "video/mp4"}

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                force=True,
            )

        output_path = Path(result["output_path"])
        assert "haiti_vs_scotland" in str(output_path)
        assert output_path.suffix == ".mp4"

    def test_hls_candidate_skips_validation(self, tmp_path):
        with patch("pipeline.showvideo_resolver.fetch_page") as mock_fetch, \
             patch("pipeline.showvideo_resolver.download_hls_via_ffmpeg") as mock_ffmpeg:

            mock_fetch.return_value = (M3U8_HTML, "requests")
            mock_ffmpeg.return_value = {"success": True, "bytes": 5000, "method": "ffmpeg_hls"}

            result = resolve_and_download(
                showvideo_url="https://livetv.sx/enx/showvideo/123/",
                match_name="Haiti vs Scotland",
                output_root=tmp_path,
                force=True,
            )

        assert result.get("error") is None
        assert result["download"]["success"] is True


# ── Script-level CLI tests ──

class TestCli:
    def test_requires_dry_run_or_execute(self):
        from scripts.import_livetv_showvideo import main
        try:
            main(["--url", "https://livetv.sx/enx/showvideo/123/", "--match-name", "Test Match"])
        except SystemExit as exc:
            assert exc.code == 1

    def test_dry_run_flag_accepted(self):
        from scripts.import_livetv_showvideo import main

        with patch("scripts.import_livetv_showvideo.resolve_and_download") as mock_resolve:
            mock_resolve.return_value = {
                "source_page_url": "https://livetv.sx/enx/showvideo/123/",
                "match_name": "Test Match",
                "league": "WORLD_CUP",
                "match_slug": "test_match",
                "steps": [{"step": "fetch", "status": "ok", "method": "requests", "size": 1000}],
                "dry_run": True,
            }
            try:
                main(["--url", "https://livetv.sx/enx/showvideo/123/",
                      "--match-name", "Test Match",
                      "--league", "WORLD_CUP",
                      "--dry-run"])
            except SystemExit as exc:
                assert exc.code == 0
            mock_resolve.assert_called_once()
            args = mock_resolve.call_args[1]
            assert args["dry_run"] is True
            assert args["league"] == "WORLD_CUP"
            assert args["match_name"] == "Test Match"

    def test_execute_success(self):
        from scripts.import_livetv_showvideo import main

        with patch("scripts.import_livetv_showvideo.resolve_and_download") as mock_resolve:
            mock_resolve.return_value = {
                "source_page_url": "https://livetv.sx/enx/showvideo/123/",
                "match_name": "Test Match",
                "league": "WORLD_CUP",
                "match_slug": "test_match",
                "steps": [{"step": "fetch", "status": "ok"}],
                "output_path": "/tmp/test_match/test_match_livetv_001.mp4",
                "sidecar": {"content_type": "video/mp4", "downloaded_bytes": 1024},
            }
            try:
                main(["--url", "https://livetv.sx/enx/showvideo/123/",
                      "--match-name", "Test Match",
                      "--execute"])
            except SystemExit as exc:
                assert exc.code == 0

    def test_execute_with_error(self):
        from scripts.import_livetv_showvideo import main

        with patch("scripts.import_livetv_showvideo.resolve_and_download") as mock_resolve:
            mock_resolve.return_value = {
                "source_page_url": "https://livetv.sx/enx/showvideo/123/",
                "match_name": "Test Match",
                "league": "WORLD_CUP",
                "match_slug": "test_match",
                "steps": [{"step": "fetch", "status": "failed"}],
                "error": "Could not fetch showvideo page",
            }
            try:
                main(["--url", "https://livetv.sx/enx/showvideo/123/",
                      "--match-name", "Test Match",
                      "--execute"])
            except SystemExit as exc:
                assert exc.code == 1

    def test_add_to_clip_windows(self, tmp_path):
        from scripts.import_livetv_showvideo import append_clip_window_row

        csv_path = tmp_path / "data" / "clip_windows.csv"
        csv_path.parent.mkdir(parents=True)
        import scripts.import_livetv_showvideo as mod
        mod.CLIP_WINDOWS_CSV = csv_path

        fake_output = tmp_path / "haiti_vs_scotland" / "haiti_vs_scotland_livetv_001.mp4"
        fake_output.parent.mkdir(parents=True)
        fake_output.write_bytes(b"fake video data")

        with patch("scripts.import_livetv_showvideo.subprocess.run") as mock_run:
            mock_run.return_value = Mock(returncode=0, stdout="120.5", stderr="")
            append_clip_window_row("Haiti vs Scotland", "haiti_vs_scotland", str(fake_output))

        assert csv_path.exists()
        rows = csv_path.read_text(encoding="utf-8")
        assert "haiti_vs_scotland_livetv_import" in rows
        assert "Haiti vs Scotland" in rows
        assert "LiveTV Import" in rows
        assert "02:00:00" in rows  # 120 seconds -> 02:00:00

    def test_add_to_clip_windows_dedup(self, tmp_path):
        from scripts.import_livetv_showvideo import append_clip_window_row
        import scripts.import_livetv_showvideo as mod

        csv_path = tmp_path / "data" / "clip_windows.csv"
        csv_path.parent.mkdir(parents=True)
        mod.CLIP_WINDOWS_CSV = csv_path

        import csv
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=mod.CLIP_WINDOWS_FIELDS)
            writer.writeheader()
            writer.writerow({
                "clip_id": "haiti_vs_scotland_livetv_import",
                "match_id": "haiti_vs_scotland",
                "moment_id": "",
                "clip_type": "highlight_import",
                "start_time": "00:00:00",
                "end_time": "00:02:00",
                "duration_seconds": "120",
                "series": "LiveTV Import",
                "hook": "Haiti vs Scotland",
                "caption": "already imported",
                "status": "candidate",
            })

        with patch("scripts.import_livetv_showvideo.subprocess.run"):
            append_clip_window_row("Haiti vs Scotland", "haiti_vs_scotland", str(tmp_path / "fake.mp4"))

        rows = list(csv.DictReader(csv_path.open("r", encoding="utf-8")))
        assert len(rows) == 1  # no duplicate added
