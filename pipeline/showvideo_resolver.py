from __future__ import annotations

import html as html_module
import json
import logging
import re
import subprocess
import warnings
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests
import urllib3

from pipeline.utils import ROOT, slugify

logger = logging.getLogger(__name__)

warnings.filterwarnings("ignore", category=urllib3.exceptions.InsecureRequestWarning)

MEDIA_EXTENSIONS = {".mp4", ".m4v", ".mov", ".webm", ".mkv"}
PREFERRED_TYPES = {"video/mp4", "video/quicktime", "video/x-msvideo", "video/webm"}
HLS_EXTENSIONS = {".m3u8"}

DISCOVERY_PATTERNS: list[tuple[str, str]] = [
    ("video_tag", r'<video[^>]+src\s*=\s*"([^"]+)"'),
    ("source_tag", r'<source[^>]+src\s*=\s*"([^"]+)"'),
    ("direct_mp4_string", r'["\']([^"\']+\.mp4[^"\']*)["\']'),
    ("escaped_mp4", r'\\/\\/([^"\'\\]+\.mp4[^"\'\\]*)'),
    ("file_config_key", r'file\s*:\s*["\']([^"\']+)["\']'),
    ("src_config_key", r'(?:src|url|link|video)\s*:\s*["\']([^"\']+)["\']'),
    ("hls_m3u8", r'["\']([^"\']+\.m3u8[^"\']*)["\']'),
    ("direct_m3u8", r'(https?://[^"\'<>\s]+\.m3u8[^"\'<>\s]*)'),
]

FETCH_STRATEGIES: list[tuple[str, str, dict[str, Any] | None]] = [
    ("requests", "requests", None),
    ("cloudscraper", "cloudscraper", None),
    ("curl", "curl", None),
]

# ── Tournament index page types ──

_TOURNEY_TYPE_PATTERNS: list[tuple[str, str]] = [
    (r"^Highlights$", "highlights"),
    (r"^Short Highlights$", "short_highlights"),
    (r"^Long Highlights$", "long_highlights"),
    (r"^Full match record$", "full_match"),
    (r"^\d+:\d+", "goals"),
]

VALID_TOURNEY_TYPES = {"highlights", "short_highlights", "long_highlights", "full_match", "goals", "all"}


@dataclass
class TourneyEntry:
    showvideo_url: str
    showvideo_id: str
    match_name: str
    raw_match_name: str
    label: str
    video_type: str
    league: str


def _classify_tourney_label(label: str) -> str:
    for pattern, vtype in _TOURNEY_TYPE_PATTERNS:
        if re.search(pattern, label.strip()):
            return vtype
    return "other"


def parse_tourney_page(html: str, base_url: str = "https://livetv.sx", league: str = "WORLD_CUP") -> list[TourneyEntry]:
    entries: list[TourneyEntry] = []
    seen_ids: set[str] = set()

    # Find all match name positions in the page (boundary-based to handle nested HTML)
    match_name_spans: list[tuple[int, re.Match]] = []
    for m in re.finditer(
        r'<b>([^<]*?)(?:&ndash;|–|-)([^<]*?)</b>',
        html, re.IGNORECASE,
    ):
        match_name_spans.append((m.start(), m))

    for i, (pos, name_m) in enumerate(match_name_spans):
        # Section end = next match start or end of HTML
        end_pos = match_name_spans[i + 1][0] if i + 1 < len(match_name_spans) else len(html)
        section = html[pos:end_pos]

        raw_team1 = html_module.unescape(name_m.group(1).strip())
        raw_team2 = html_module.unescape(name_m.group(2).strip())

        def _strip_code(name: str) -> tuple[str, str]:
            parts = name.strip().split()
            if parts and len(parts[-1]) <= 4 and parts[-1].isupper():
                return " ".join(parts[:-1]), parts[-1]
            return name, ""

        base1, _code1 = _strip_code(raw_team1)
        base2, _code2 = _strip_code(raw_team2)
        raw_match_name = f"{raw_team1} – {raw_team2}"
        match_name = f"{base1} vs {base2}" if base1 and base2 else raw_match_name

        for link_m in re.finditer(
            r'href="([^"]*showvideo/(\d+)[^"]*)"[^>]*>([^<]+)</a>',
            section, re.IGNORECASE,
        ):
            href = link_m.group(1).strip()
            vid_id = link_m.group(2)
            label = html_module.unescape(link_m.group(3).strip())

            if vid_id in seen_ids:
                continue
            seen_ids.add(vid_id)

            url = _normalize_tourney_url(href, base_url)
            vtype = _classify_tourney_label(label)
            entries.append(TourneyEntry(
                showvideo_url=url,
                showvideo_id=vid_id,
                match_name=match_name,
                raw_match_name=raw_match_name,
                label=label,
                video_type=vtype,
                league=league,
            ))

    return entries


def _normalize_tourney_url(href: str, base_url: str) -> str:
    cleaned = re.sub(r'_+/', '/', href)  # strip trailing __ before /
    if cleaned.startswith("//"):
        return "https:" + cleaned
    if cleaned.startswith("/"):
        return base_url.rstrip("/") + cleaned
    if not cleaned.startswith("http"):
        return base_url.rstrip("/") + "/" + cleaned.lstrip("/")
    return cleaned


def _add_candidate(
    url: str, discovery_method: str,
    candidates: list[dict[str, Any]], seen: set[str],
) -> None:
    raw = url.strip()
    cleaned = raw.replace("\\/", "/")
    if not cleaned.startswith(("http://", "https://")):
        if cleaned.startswith("//"):
            cleaned = "https:" + cleaned
        else:
            return
    norm = cleaned.rstrip("/")
    if norm in seen:
        return
    seen.add(norm)
    ext = Path(cleaned.split("?")[0].split("#")[0]).suffix.lower()
    candidates.append({
        "url": cleaned,
        "discovery_method": discovery_method,
        "extension": ext,
        "is_hls": ext in HLS_EXTENSIONS,
        "is_media": ext in MEDIA_EXTENSIONS,
    })


def _extract_media_urls(html: str) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()

    for name, pattern in DISCOVERY_PATTERNS:
        for match in re.finditer(pattern, html, re.IGNORECASE):
            raw = match.group(1).strip()
            # Handle comma-separated fallback mirrors (player convention)
            for part in raw.split(","):
                _add_candidate(part, name, candidates, seen)

    return candidates


def _has_cloudflare_challenge(html: str) -> bool:
    return "Just a moment" in html or "cf-browser-request" in html


def fetch_page(url: str, timeout: int = 20, user_agent: str | None = None) -> tuple[str | None, str]:
    ua = user_agent or (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    )

    # requests
    try:
        resp = requests.get(
            url, timeout=timeout, verify=False,
            headers={
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        if resp.status_code == 200 and not _has_cloudflare_challenge(resp.text):
            return resp.text, "requests"
    except Exception:
        pass

    # cloudscraper fallback
    try:
        import cloudscraper
        scraper = cloudscraper.create_scraper()
        resp = scraper.get(url, timeout=timeout, verify=False, headers={"User-Agent": ua})
        if resp.status_code == 200 and not _has_cloudflare_challenge(resp.text):
            return resp.text, "cloudscraper"
    except Exception:
        pass

    # curl fallback
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", str(timeout),
             "-H", f"User-Agent: {ua}", url],
            capture_output=True, text=True, timeout=timeout + 5,
        )
        if result.returncode == 0 and not _has_cloudflare_challenge(result.stdout):
            return result.stdout, "curl"
    except Exception:
        pass

    return None, "all strategies failed"


_AD_KEYWORDS = ("getbanner", "doubleclick", "googleads", "facebook.com/plugins", "googlesyndication")
_VIDEO_KEYWORDS = ("player", "video.php", "embed")


def resolve_iframe_url(html: str, base_url: str) -> str | None:
    iframes = re.findall(r'<iframe[^>]+src\s*=\s*"([^"]+)"', html, re.IGNORECASE)
    if not iframes:
        return None

    candidates: list[tuple[int, str]] = []

    for raw_src in iframes:
        cleaned = raw_src.replace("\n", "").replace("\r", "")
        cleaned = re.sub(r"""['"]?\s*\+\s*['"]?""", "", cleaned).strip().strip("'").strip('"')
        if not cleaned:
            continue

        if any(kw in cleaned.lower() for kw in _AD_KEYWORDS):
            continue

        if cleaned.startswith("//"):
            cleaned = "https:" + cleaned
        elif cleaned.startswith("/"):
            from urllib.parse import urlparse
            parsed = urlparse(base_url)
            cleaned = f"{parsed.scheme}://{parsed.netloc}{cleaned}"
        elif not cleaned.startswith("http"):
            from urllib.parse import urljoin
            cleaned = urljoin(base_url, cleaned)

        score = sum(1 for kw in _VIDEO_KEYWORDS if kw in cleaned.lower())
        candidates.append((score, cleaned))

    if not candidates:
        return None

    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def validate_media_url(url: str, referer: str | None = None, timeout: int = 15) -> dict[str, Any]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        headers["Referer"] = referer

    # Try HEAD first
    try:
        head_resp = requests.head(url, timeout=timeout, headers=headers, verify=False, allow_redirects=True)
        ct = (head_resp.headers.get("Content-Type") or "").lower()
        cl = head_resp.headers.get("Content-Length")
        if head_resp.status_code == 200 and ct in PREFERRED_TYPES:
            return {
                "valid": True,
                "method": "head",
                "content_type": ct,
                "content_length": int(cl) if cl and cl.isdigit() else None,
                "status_code": head_resp.status_code,
                "accept_ranges": head_resp.headers.get("Accept-Ranges", ""),
            }
        if ct and ct.startswith("video/"):
            return {
                "valid": True,
                "method": "head",
                "content_type": ct,
                "content_length": int(cl) if cl and cl.isdigit() else None,
                "status_code": head_resp.status_code,
                "accept_ranges": head_resp.headers.get("Accept-Ranges", ""),
            }
    except Exception:
        pass

    # Fallback to Range GET (0-0 byte) if HEAD was blocked
    try:
        range_headers = dict(headers)
        range_headers["Range"] = "bytes=0-0"
        range_resp = requests.get(url, timeout=timeout, headers=range_headers, verify=False, allow_redirects=True)
        ct = (range_resp.headers.get("Content-Type") or "").lower()
        cr = range_resp.headers.get("Content-Range", "")
        status = range_resp.status_code
        if status in (200, 206) and ct in PREFERRED_TYPES:
            cl = None
            if cr:
                m = re.search(r"/\s*(\d+)", cr)
                if m:
                    cl = int(m.group(1))
            return {
                "valid": True,
                "method": "range_get",
                "content_type": ct,
                "content_length": cl,
                "status_code": status,
                "accept_ranges": "bytes",
            }
        if status in (200, 206) and ct and ct.startswith("video/"):
            return {
                "valid": True,
                "method": "range_get",
                "content_type": ct,
                "content_length": None,
                "status_code": status,
                "accept_ranges": "bytes",
            }
    except Exception:
        pass

    return {"valid": False, "method": "failed", "content_type": None, "content_length": None, "status_code": 0}


def download_media(url: str, dest: Path, referer: str | None = None, timeout: int = 120) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/131.0.0.0 Safari/537.36"
        ),
    }
    if referer:
        headers["Referer"] = referer

    tmp_path = dest.with_suffix(dest.suffix + ".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    try:
        resp = requests.get(url, stream=True, timeout=timeout, headers=headers, verify=False)
        resp.raise_for_status()

        content_type = resp.headers.get("Content-Type", "")
        content_length = resp.headers.get("Content-Length")
        total = int(content_length) if content_length and content_length.isdigit() else None

        downloaded = 0
        with open(tmp_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)

        if downloaded == 0:
            if tmp_path.exists():
                tmp_path.unlink()
            return {"success": False, "error": "downloaded 0 bytes", "bytes": 0}

        tmp_path.rename(dest)
        return {
            "success": True,
            "bytes": downloaded,
            "content_type": content_type,
            "total_expected": total,
        }
    except Exception as exc:
        if tmp_path.exists():
            tmp_path.unlink()
        return {"success": False, "error": str(exc), "bytes": 0}


def download_hls_via_ffmpeg(url: str, dest: Path, timeout: int = 300) -> dict[str, Any]:
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        result = subprocess.run(
            ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
             "-i", url,
             "-c", "copy",
             "-movflags", "+faststart",
             str(dest)],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode == 0 and dest.exists() and dest.stat().st_size > 0:
            return {"success": True, "bytes": dest.stat().st_size, "method": "ffmpeg_hls"}
        return {"success": False, "error": result.stderr.strip() or "ffmpeg failed", "bytes": 0}
    except subprocess.TimeoutExpired:
        if dest.exists():
            dest.unlink()
        return {"success": False, "error": "ffmpeg timeout", "bytes": 0}
    except FileNotFoundError:
        return {"success": False, "error": "ffmpeg not found", "bytes": 0}


def write_sidecar(mp4_path: Path, metadata: dict[str, Any]) -> None:
    sidecar_path = mp4_path.with_suffix(mp4_path.suffix + ".import.json")
    sidecar_path.parent.mkdir(parents=True, exist_ok=True)
    sidecar_path.write_text(json.dumps(metadata, indent=2, default=str), encoding="utf-8")


def read_sidecars(directory: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not directory.exists():
        return result
    for f in sorted(directory.iterdir()):
        if f.suffix == ".json" and ".import.json" in f.name:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                resolved = data.get("resolved_media_url", "")
                if resolved:
                    result[resolved] = data
            except (json.JSONDecodeError, OSError):
                continue
    return result


def pick_best_candidate(candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
    if not candidates:
        return None

    direct_media = [c for c in candidates if c["is_media"]]
    if direct_media:
        return direct_media[0]

    hls = [c for c in candidates if c["is_hls"]]
    if hls:
        return hls[0]

    return candidates[0]


def resolve_and_download(
    showvideo_url: str,
    match_name: str,
    league: str = "WORLD_CUP",
    output_root: Path | str | None = None,
    dry_run: bool = False,
    force: bool = False,
    verbose: bool = False,
    sequence_num: int = 1,
) -> dict[str, Any]:
    if output_root is None:
        output_root = ROOT / "FootballArchive" / "RAW_HIGHLIGHTS"
    output_root = Path(output_root)

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="%(message)s")

    match_slug = slugify(match_name)
    output_dir = output_root / match_slug

    result: dict[str, Any] = {
        "source_page_url": showvideo_url,
        "match_name": match_name,
        "league": league,
        "match_slug": match_slug,
        "steps": [],
        "downloaded": None,
        "sidecar": None,
    }

    # Step 1: fetch showvideo page
    logger.info("Fetching showvideo page: %s", showvideo_url)
    html, fetch_method = fetch_page(showvideo_url)
    if not html:
        result["steps"].append({"step": "fetch", "status": "failed", "detail": "could not fetch page"})
        result["error"] = "Could not fetch showvideo page"
        return result
    result["steps"].append({"step": "fetch", "status": "ok", "method": fetch_method, "size": len(html)})
    if verbose:
        logger.debug("Fetched %d bytes via %s", len(html), fetch_method)

    # Step 2: check for iframe and fetch it
    iframe_url = resolve_iframe_url(html, showvideo_url)
    discovered_html = html
    if iframe_url:
        result["steps"].append({"step": "iframe", "status": "found", "url": iframe_url})
        if verbose:
            logger.debug("Found iframe: %s", iframe_url)
        iframe_html, iframe_method = fetch_page(iframe_url)
        if iframe_html:
            discovered_html = iframe_html
            result["steps"].append({"step": "iframe_fetch", "status": "ok", "method": iframe_method, "size": len(iframe_html)})
        else:
            result["steps"].append({"step": "iframe_fetch", "status": "failed", "detail": "could not fetch iframe"})

    # Use iframe URL as referer (video servers expect the player page, not LiveTV)
    referer = iframe_url or showvideo_url

    # Step 3: discover media URLs
    candidates = _extract_media_urls(discovered_html)
    result["steps"].append({"step": "discover", "status": "ok", "candidates_found": len(candidates)})
    if verbose:
        for c in candidates:
            logger.debug("  candidate [%s]: %s", c["discovery_method"], c["url"])

    if not candidates:
        result["error"] = "No media URLs found on the page"
        return result

    # Step 4: pick best candidate
    best = pick_best_candidate(candidates)
    if not best:
        result["error"] = "Could not pick a candidate"
        return result
    result["candidate_selected"] = best
    result["steps"].append({"step": "select", "url": best["url"], "method": best["discovery_method"]})

    # Step 5: check existing sidecars for dedup
    if not force:
        existing_sidecars = read_sidecars(output_dir)
        if best["url"] in existing_sidecars:
            existing = existing_sidecars[best["url"]]
            result["skipped"] = True
            result["existing_sidecar"] = existing
            result["steps"].append({"step": "dedup", "status": "skipped", "detail": "already downloaded"})
            logger.info("Skipping — already downloaded for URL: %s", best["url"])
            return result

    if dry_run:
        result["dry_run"] = True
        logger.info("[dry-run] Would download: %s", best["url"])
        logger.info("[dry-run] Output: %s", output_dir)
        return result

    # Step 6: validate
    if verbose:
        logger.debug("Validating: %s", best["url"])
    validation = validate_media_url(best["url"], referer=referer)
    result["validation"] = validation
    result["steps"].append({"step": "validate", "status": "ok" if validation["valid"] else "invalid", "detail": validation})

    if not validation["valid"] and not best["is_hls"]:
        result["error"] = "Candidate URL failed validation"
        return result

    # Step 7: download
    ext = best["extension"] if best["extension"] else ".mp4"
    dest_filename = f"{match_slug}_livetv_{sequence_num:03d}{ext}"
    dest_path = output_dir / dest_filename

    if dest_path.exists() and not force:
        result["skipped"] = True
        result["steps"].append({"step": "download", "status": "skipped", "detail": "file exists"})
        logger.info("Skipping — file exists: %s", dest_path)
        return result

    if best["is_hls"]:
        logger.info("Downloading HLS via ffmpeg: %s", best["url"])
        dl_result = download_hls_via_ffmpeg(best["url"], dest_path)
    else:
        logger.info("Downloading: %s", best["url"])
        dl_result = download_media(best["url"], dest_path, referer=referer)

    result["download"] = dl_result
    result["steps"].append({"step": "download", "status": "ok" if dl_result["success"] else "failed"})

    if not dl_result["success"]:
        result["error"] = dl_result.get("error", "Download failed")
        return result

    # Step 8: write sidecar
    sidecar_metadata = {
        "source_page_url": showvideo_url,
        "resolved_media_url": best["url"],
        "discovery_method": best["discovery_method"],
        "content_type": validation.get("content_type") or dl_result.get("content_type", ""),
        "content_length": validation.get("content_length") or dl_result.get("bytes", 0),
        "downloaded_bytes": dl_result["bytes"],
        "downloaded_at": datetime.now(timezone.utc).isoformat(),
        "match_name": match_name,
        "league": league,
        "match_slug": match_slug,
        "output_filename": dest_path.name,
        "validation_info": {k: v for k, v in validation.items() if k != "valid"},
        "steps": result["steps"],
    }
    write_sidecar(dest_path, sidecar_metadata)
    result["sidecar"] = sidecar_metadata
    result["output_path"] = str(dest_path)

    logger.info("Downloaded: %s (%d bytes)", dest_path, dl_result["bytes"])
    logger.info("Sidecar: %s.import.json", dest_path)

    return result
