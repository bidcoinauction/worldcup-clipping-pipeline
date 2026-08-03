from __future__ import annotations

import dataclasses
import json
import re
import subprocess
import warnings
from collections import defaultdict
from typing import Any

ACESTREAM_RE = re.compile(r"acestream://([a-f0-9]{40})")
LID_RE = re.compile(r"lid=(\d+)")
CI_RE = re.compile(r"ci=(\d+)")
SI_RE = re.compile(r"si=(\d+)")

_LANG_MAP = {
    "1": "Russian", "2": "English", "3": "Spanish",
    "4": "Portuguese", "5": "German", "6": "French",
    "7": "Italian", "8": "Arabic", "9": "Dutch",
}


@dataclasses.dataclass
class LiveTVResult:
    best_hash: str | None
    metadata: dict[str, Any]
    availability: str
    ranked: list[dict[str, Any]]
    fetch_method: str


# ── Fetch strategies ──

def fetch_via_requests(url: str, timeout: int = 20) -> str | None:
    try:
        import requests as req
        warnings.filterwarnings("ignore", category=req.packages.urllib3.exceptions.InsecureRequestWarning)
        resp = req.get(
            url,
            timeout=timeout,
            verify=False,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/131.0.0.0 Safari/537.36"
                ),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.5",
            },
        )
        return resp.text if resp.status_code == 200 else None
    except Exception:
        return None


def fetch_via_curl(url: str) -> str | None:
    try:
        result = subprocess.run(
            ["curl", "-s", "-L", "--max-time", "15",
             "-H", "User-Agent: Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
             url],
            capture_output=True, text=True, timeout=20,
        )
        if result.returncode == 0 and len(result.stdout) > 1000:
            return result.stdout
        return None
    except Exception:
        return None


def fetch_page(url: str) -> tuple[str | None, str]:
    strategies = [
        ("requests", fetch_via_requests),
        ("curl", fetch_via_curl),
    ]
    for name, fn in strategies:
        html = fn(url)
        if html and "Just a moment" not in html and "cf-browser-request" not in html:
            return html, name
    return None, "all strategies failed"


# ── Parsing ──

def parse_event_info(html: str) -> dict[str, Any]:
    info: dict[str, Any] = {}

    m = re.search(r"<title>(.*?)</title>", html, re.DOTALL)
    info["title"] = m.group(1).strip() if m else "unknown"

    m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.DOTALL)
    info["match"] = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    m = re.search(r"/eventinfo/(\d+)", html)
    info["event_id"] = m.group(1) if m else ""

    m = re.search(r'class="cat"[^>]*>(.*?)</', html, re.DOTALL)
    info["competition"] = re.sub(r"<[^>]+>", "", m.group(1)).strip() if m else ""

    return info


def parse_stream_rows(html: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    type_sections = re.split(r'<div[^>]*class="tbl"', html)

    for section in type_sections:
        stream_type = _detect_stream_type(section)
        row_pattern = re.compile(
            r"<tr[^>]*>.*?acestream://([a-f0-9]{40}).*?</tr>", re.DOTALL
        )
        for match in row_pattern.finditer(section):
            row_html = match.group(0)
            ahash = match.group(1)
            lid = _extract_lid(row_html)
            rows.append({
                "hash": ahash,
                "type": stream_type,
                "lid": lid,
                "ci": _extract_ci(row_html),
                "si": _extract_si(row_html),
                "bitrate": _extract_bitrate(row_html),
                "rating": _extract_rating(row_html, lid),
                "language": _extract_language(row_html),
            })
    return rows


def _detect_stream_type(section: str) -> str:
    section_lower = section.lower()
    if "acestream" in section_lower:
        return "acestream"
    if "sop" in section_lower:
        return "sop"
    if "alieztv" in section_lower:
        return "alieztv"
    if "voodc" in section_lower:
        return "voodc"
    if "youtube" in section_lower:
        return "youtube"
    return "unknown"


def _extract_lid(row_html: str) -> str:
    m = LID_RE.search(row_html)
    return m.group(1) if m else ""


def _extract_ci(row_html: str) -> str:
    m = CI_RE.search(row_html)
    return m.group(1) if m else ""


def _extract_si(row_html: str) -> str:
    m = SI_RE.search(row_html)
    return m.group(1) if m else ""


def _extract_bitrate(row_html: str) -> str:
    m = re.search(r'class="bitrate"[^>]*title="[^"]*?(\d+)', row_html)
    return m.group(1) if m else ""


def _extract_rating(row_html: str, lid: str) -> str:
    rt_m = re.search(r"rali" + lid + r'[^>]*>.*?(-?\d+)', row_html) if lid else None
    if not rt_m:
        rt_m = re.search(r'class="rate"[^>]*>.*?(-?\d+)', row_html)
    if rt_m:
        rating = rt_m.group(1)
        if rating.startswith("-"):
            return "0"
        return rating
    return ""


def _extract_language(row_html: str) -> str:
    lang_m = re.search(r'img[^>]*title="([^"]*)"[^>]*src="[^"]*linkflag', row_html)
    if lang_m:
        return lang_m.group(1)
    lang_m = re.search(r'src="[^"]*linkflag/(\d+)\.png', row_html)
    if lang_m:
        return _LANG_MAP.get(lang_m.group(1), f"flag_{lang_m.group(1)}")
    return ""


def parse_other_streams(html: str) -> list[dict[str, str]]:
    others: list[dict[str, str]] = []
    patterns = [
        (r"c=(\d+)", "alieztv", "Aliez TV"),
        (r"c=([a-z0-9]+)", "voodc", "Voodc"),
        (r"c=([\w-]{11})", "youtube", "YouTube"),
    ]
    found: set[str] = set()
    for pattern, stype, sname in patterns:
        for m in re.finditer(pattern, html):
            cid = m.group(1)
            key = f"{stype}:{cid}"
            if key not in found:
                found.add(key)
                ctx = html[max(0, m.start() - 50):m.end() + 50]
                if stype in ctx.lower():
                    others.append({"id": cid, "type": stype, "name": sname})
    return others


# ── Ranking ──

def rank_hashes(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    scored: list[dict[str, Any]] = []
    for row in rows:
        if row["type"] != "acestream":
            continue
        score = 0

        if row["language"] == "English":
            score += 20
        elif row["language"] == "Spanish":
            score += 15
        elif not row["language"]:
            score += 5

        try:
            score += int(row["rating"])
        except (ValueError, TypeError):
            score += 50

        try:
            bitrate_kbps = int(row["bitrate"])
            if bitrate_kbps > 5000:
                score += 20
            elif bitrate_kbps > 2000:
                score += 10
        except (ValueError, TypeError):
            pass

        if row["si"] == "1":
            score += 5

        scored.append({"hash": row["hash"], "score": score, **row})

    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored


# ── Availability ──

def check_availability_windows(url: str, html: str | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {"state": "unknown", "details": {}}
    if html is None:
        html, method = fetch_page(url)
        result["fetch_method"] = method
    else:
        result["fetch_method"] = "provided"

    if not html:
        result["state"] = "unreachable"
        return result

    indicators = {
        "live": [
            r'class="live"', r">LIVE<", r">Live<",
            r'class="status_live"', r"nowplaying",
            r"started",
        ],
        "upcoming": [
            r'class="upcoming"', r"starts in", r"Starts in",
            r'class="scheduled"', r"will start",
        ],
        "completed": [
            r'class="finished"', r'class="final"', r'class="over"',
            r"Full Time", r"full-time",
            r"Match over", r"finished",
        ],
        "no_links": [
            r"no links", r"No links", r"No streams",
            r"currently no", r"not available",
            r"no links yet",
        ],
    }

    text_lower = html.lower()
    for state, patterns in indicators.items():
        for pat in patterns:
            if re.search(pat, html) or pat.lower() in text_lower:
                result["details"][state] = result["details"].get(state, 0) + 1

    hashes = ACESTREAM_RE.findall(html)
    result["hash_count"] = len(set(hashes))

    has_any_indicator = any(
        result["details"].get(s, 0) > 0
        for s in ("live", "upcoming", "completed", "no_links")
    )

    if result["details"].get("live", 0) > 0 and result["hash_count"] > 0:
        result["state"] = "live"
    elif result["details"].get("completed", 0) > 0:
        result["state"] = "completed"
        if result["hash_count"] > 0:
            result["state"] = "completed_with_hashes"
    elif result["details"].get("upcoming", 0) > 0:
        result["state"] = "upcoming"
    elif result["details"].get("no_links", 0) > 0:
        result["state"] = "no_links"
    elif not has_any_indicator and result["hash_count"] == 0:
        result["state"] = "unknown"

    return result


# ── Public API ──

def resolve_event_url(url: str) -> LiveTVResult:
    html, method = fetch_page(url)

    if not html:
        return LiveTVResult(
            best_hash=None,
            metadata={"error": "could not fetch page", "url": url},
            availability="unreachable",
            ranked=[],
            fetch_method=method,
        )

    metadata = parse_event_info(html)
    rows = parse_stream_rows(html)
    ranked = rank_hashes(rows)
    availability = check_availability_windows(url, html=html)

    all_hashes = sorted(set(r["hash"] for r in ranked))
    metadata["total_hashes"] = len(all_hashes)
    metadata["all_hashes"] = all_hashes

    other_streams = parse_other_streams(html)
    if other_streams:
        metadata["other_streams"] = other_streams

    best_hash = ranked[0]["hash"] if ranked else None
    if best_hash:
        metadata["best_link"] = (
            f"https://livetv.sx/webplayer2.php?t=acestream"
            f"&c={best_hash}"
            f"&eid={metadata.get('event_id', '')}"
            f"&lid={ranked[0].get('lid', '')}"
        )

    return LiveTVResult(
        best_hash=best_hash,
        metadata=metadata,
        availability=availability["state"],
        ranked=ranked,
        fetch_method=method,
    )
