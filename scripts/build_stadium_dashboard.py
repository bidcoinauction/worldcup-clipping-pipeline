#!/usr/bin/env python3
"""Build a local static HTML review dashboard for exported clips.

Reads clips from FootballArchive/CLIPS/, enriches with metadata from
CLIP_MANIFESTS, MATCH_RESEARCH, and DETECTIONS, and produces a self-contained
HTML page with embedded video playback, editorial metadata, and timeline markers.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.utils import ROOT, slugify, timestamp_to_seconds


VIDEO_EXTENSIONS = {".mp4", ".mkv", ".mov", ".webm", ".m4v"}

DEFAULT_CLIPS_DIR = ROOT / "FootballArchive" / "CLIPS"
DEFAULT_MANIFEST_PATH = ROOT / "CLIP_MANIFESTS" / "researched_clip_exports.csv"
DEFAULT_RESEARCH_DIR = ROOT / "MATCH_RESEARCH"
DEFAULT_DETECTIONS_DIR = ROOT / "DETECTIONS"
DEFAULT_OUTPUT = DEFAULT_CLIPS_DIR / "review_dashboard.html"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build local static clip review dashboard HTML."
    )
    parser.add_argument(
        "--clips-dir",
        default=str(DEFAULT_CLIPS_DIR),
        help="Directory containing exported MP4 clips.",
    )
    parser.add_argument(
        "--manifest",
        default=str(DEFAULT_MANIFEST_PATH),
        help="Export manifest CSV path.",
    )
    parser.add_argument(
        "--research-dir",
        default=str(DEFAULT_RESEARCH_DIR),
        help="Root directory for MATCH_RESEARCH JSON files.",
    )
    parser.add_argument(
        "--detections-dir",
        default=str(DEFAULT_DETECTIONS_DIR),
        help="Directory containing detection JSON files.",
    )
    parser.add_argument(
        "--output",
        default=str(DEFAULT_OUTPUT),
        help="Output path for the generated HTML dashboard.",
    )
    parser.add_argument(
        "--title",
        default="Stadium Signal Review Desk",
        help="Page title.",
    )
    return parser.parse_args(argv)


def path_to_file_url(path: Path) -> str:
    return path.resolve().as_uri()


def read_manifest(path: Path) -> list[dict[str, str]]:
    if not path.exists():
        return []
    with path.open("r", newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def read_research(research_dir: Path) -> dict[str, dict]:
    result: dict[str, dict] = {}
    if not research_dir.exists():
        return result
    for json_path in sorted(research_dir.rglob("match_research.json")):
        match_slug = json_path.parent.name
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            result[match_slug] = data
        except (json.JSONDecodeError, OSError):
            continue
    return result


def read_detections(detections_dir: Path) -> dict[str, list[dict]]:
    result: dict[str, list[dict]] = {}
    if not detections_dir.exists():
        return result
    for json_path in sorted(detections_dir.glob("*_clips.json")):
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
            clips = data if isinstance(data, list) else data.get("clips", [])
            key = json_path.stem
            result[key] = clips
        except (json.JSONDecodeError, OSError):
            continue
    return result


def manifest_by_local_path(rows: list[dict[str, str]]) -> dict[str, dict[str, str]]:
    mapped: dict[str, dict[str, str]] = {}
    for row in rows:
        local_path = row.get("local_export_path", "")
        if not local_path:
            continue
        try:
            mapped[str(Path(local_path).resolve()).lower()] = row
        except OSError:
            mapped[local_path.lower()] = row
    return mapped


def discover_clips(
    clips_dir: Path,
    manifest_rows: list[dict[str, str]],
    research_by_match: dict[str, dict],
    detections_by_match: dict[str, list[dict]],
) -> list[dict]:
    by_path = manifest_by_local_path(manifest_rows)
    clipped_rows: list[dict] = []
    seen_paths: set[str] = set()

    for video_path in sorted(clips_dir.rglob("*")):
        if not video_path.is_file() or video_path.suffix.lower() not in VIDEO_EXTENSIONS:
            continue
        resolved_key = str(video_path.resolve()).lower()
        seen_paths.add(resolved_key)
        row = dict(by_path.get(resolved_key, {}))

        relative = video_path.relative_to(clips_dir).as_posix()
        match_slug = video_path.parent.name if video_path.parent != clips_dir else ""

        if not row:
            row = {
                "clip_id": video_path.stem,
                "match_title": match_slug.replace("_", " ").title() if match_slug else "Unmatched Clips",
                "source_file": "",
                "start_time": "",
                "end_time": "",
                "moment_label": video_path.stem.replace("_", " "),
                "emotional_angle": "",
                "platform": "",
                "export_profile": "",
                "local_export_path": str(video_path),
                "status": "",
                "reason": "",
            }
        else:
            row["clip_id"] = row.get("clip_id") or video_path.stem

        row["relative_export_path"] = relative
        row["match_slug"] = match_slug
        row["media_url"] = path_to_file_url(video_path)
        row["file_size_mb"] = f"{video_path.stat().st_size / (1024 * 1024):.1f}"

        # Enrich with detection editorial fields
        detection_key = None
        for dk in detections_by_match:
            if match_slug and match_slug in dk:
                detection_key = dk
                break
        if detection_key:
            row["_detection"] = detections_by_match[detection_key]
            # Pull editorial fields from first detection clip for demo
            # Full merge is done in build_html
        else:
            row["_detection"] = []

        # Attach research data
        row["_research"] = research_by_match.get(match_slug, {})

        clipped_rows.append(row)

    # Add manifest rows whose files weren't found on disk
    for row in manifest_rows:
        local_path = row.get("local_export_path", "")
        if not local_path:
            continue
        try:
            resolved_key = str(Path(local_path).resolve()).lower()
        except OSError:
            resolved_key = local_path.lower()
        if resolved_key not in seen_paths:
            missing = dict(row)
            missing["relative_export_path"] = ""
            missing["media_url"] = ""
            missing["file_size_mb"] = ""
            missing["match_slug"] = ""
            missing["_detection"] = []
            missing["_research"] = {}
            clipped_rows.append(missing)

    return clipped_rows


def match_title_for_slug(match_slug: str, research: dict) -> str:
    if research:
        m = research.get("match", {})
        home = m.get("home_team", "")
        away = m.get("away_team", "")
        if home and away:
            return f"{home} vs {away}"
    return match_slug.replace("_", " ").title()


def build_html(
    rows: list[dict],
    research_by_match: dict[str, dict],
    title: str,
    generated_at: str,
) -> str:
    # Sort rows by match_title
    rows.sort(key=lambda r: r.get("match_title", ""))

    data_json = json.dumps(rows, ensure_ascii=True)
    research_json = json.dumps(research_by_match, ensure_ascii=True)

    return f"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{html_escape(title)}</title>
  <style>
    :root {{
      --bg:#10120f; --panel:#1b201b; --panel2:#242b24; --text:#f4f0e5; --muted:#b8b4a8;
      --line:#394238; --accent:#d9ef6f; --cyan:#65d8c0; --ok:#8adf86; --warn:#ff9c73;
      font-family: Inter, ui-sans-serif, system-ui, "Segoe UI", sans-serif;
    }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; min-height:100vh; background:var(--bg); color:var(--text); }}
    header {{ position:sticky; top:0; z-index:5; background:rgba(16,18,15,.97); border-bottom:1px solid var(--line); padding:14px 20px; }}
    .topbar {{ max-width:1540px; margin:0 auto; display:grid; grid-template-columns:minmax(250px,1fr) minmax(260px,430px) 210px; gap:12px; align-items:center; }}
    h1 {{ margin:0; font-size:18px; line-height:1.2; letter-spacing:0; }}
    .sub {{ color:var(--muted); font-size:12px; margin-top:4px; }}
    input, select, textarea, button {{ border:1px solid var(--line); background:var(--panel); color:var(--text); border-radius:6px; font:inherit; }}
    input, select, button {{ min-height:38px; padding:0 10px; }}
    button {{ cursor:pointer; font-weight:650; }}
    button:hover, .active {{ border-color:var(--accent); color:var(--accent); }}
    main {{ max-width:1540px; margin:0 auto; padding:18px 20px 34px; display:grid; grid-template-columns:390px minmax(0,1fr); gap:18px; }}
    .list {{ display:grid; gap:8px; align-content:start; }}
    .clip-row {{ width:100%; min-height:88px; text-align:left; padding:10px; display:grid; gap:6px; background:var(--panel); border:1px solid var(--line); border-radius:6px; }}
    .clip-row strong {{ font-size:13px; line-height:1.25; color:var(--text); }}
    .meta-line {{ color:var(--muted); font-size:12px; line-height:1.3; overflow-wrap:anywhere; }}
    .status-line {{ display:flex; gap:6px; flex-wrap:wrap; }}
    .pill {{ font-size:11px; border:1px solid var(--line); border-radius:999px; padding:3px 7px; color:var(--muted); }}
    .pill.ok {{ color:var(--ok); border-color:rgba(138,223,134,.45); }}
    .pill.warn {{ color:var(--warn); border-color:rgba(255,156,115,.45); }}
    .viewer {{ display:grid; grid-template-columns:minmax(300px,430px) minmax(0,1fr); gap:18px; align-items:start; }}
    .video-wrap {{ background:#070807; border:1px solid var(--line); border-radius:8px; padding:10px; }}
    video {{ display:block; width:100%; aspect-ratio:9/16; background:#000; border-radius:4px; }}
    .details {{ display:grid; gap:14px; align-content:start; }}
    .panel {{ border:1px solid var(--line); border-radius:8px; background:var(--panel); padding:14px; }}
    .panel h2 {{ margin:0 0 8px; font-size:24px; line-height:1.12; letter-spacing:0; }}
    .panel h3 {{ margin:0 0 10px; font-size:13px; text-transform:uppercase; color:var(--muted); letter-spacing:.08em; }}
    .angle {{ font-size:18px; line-height:1.4; color:var(--accent); margin:0; }}
    .grid {{ display:grid; grid-template-columns:repeat(2,minmax(0,1fr)); gap:10px; }}
    .kv {{ border-top:1px solid var(--line); padding-top:8px; min-width:0; }}
    .kv span {{ color:var(--muted); font-size:11px; display:block; text-transform:uppercase; letter-spacing:.08em; margin-bottom:3px; }}
    .kv code {{ color:var(--text); font-size:12px; overflow-wrap:anywhere; }}
    .empty {{ border:1px dashed var(--line); color:var(--muted); padding:28px; border-radius:8px; text-align:center; }}

    /* Timeline */
    .timeline {{ margin-top:12px; padding-top:10px; border-top:1px solid var(--line); }}
    .timeline-ruler {{ position:relative; height:32px; background:var(--panel2); border-radius:4px; margin:4px 0 6px; }}
    .timeline-ruler .marker {{ position:absolute; top:0; }}
    .marker-dot {{ width:10px; height:10px; border-radius:50%; background:var(--accent); transform:translate(-50%,10px); cursor:pointer; border:2px solid var(--bg); }}
    .marker-dot:hover {{ transform:translate(-50%,10px) scale(1.3); }}
    .marker-dot.goal {{ background:#ff6b6b; }}
    .marker-dot.penalty {{ background:#ff9c73; }}
    .marker-dot.full_time {{ background:#65d8c0; }}
    .marker-dot.celebration {{ background:#d9ef6f; }}
    .marker-dot.yellow_card {{ background:#f0db4f; }}
    .marker-dot.red_card {{ background:#ff4444; }}
    .clip-bar {{ position:absolute; height:6px; border-radius:3px; background:rgba(100,200,255,0.5); transform:translate(0,20px); cursor:pointer; }}
    .clip-bar:hover {{ background:rgba(100,200,255,0.8); }}
    .timeline-labels {{ display:flex; justify-content:space-between; font-size:10px; color:var(--muted); padding:0 2px; }}

    /* Detection list in metadata */
    .detection-item {{ border-top:1px solid var(--line); padding:8px 0; font-size:12px; }}
    .detection-item:first-child {{ border-top:none; padding-top:0; }}
    .detection-item .field {{ display:flex; gap:6px; margin:2px 0; }}
    .detection-item .field-label {{ color:var(--muted); min-width:90px; }}
    .detection-item .field-value {{ color:var(--text); }}
    .thesis {{ color:var(--cyan); font-size:13px; line-height:1.4; margin:6px 0; padding:8px; background:var(--bg); border-radius:4px; }}

    @media (max-width:1080px) {{ .topbar, main, .viewer {{ grid-template-columns:1fr; }} header {{ position:static; }} }}
  </style>
</head>
<body>
  <header>
    <div class="topbar">
      <div>
        <h1>{html_escape(title)}</h1>
        <div class="sub">Generated {html_escape(generated_at)}. {len(rows)} clips.</div>
      </div>
      <input id="search" type="search" placeholder="Search clips, matches, metadata">
      <div>
        <select id="matchFilter" aria-label="Filter by match"></select>
      </div>
    </div>
  </header>
  <main>
    <section>
      <div class="sub" id="count"></div>
      <div class="list" id="clipList"></div>
    </section>
    <section id="viewer" class="viewer"></section>
  </main>
  <script id="clip-data" type="application/json">{data_json}</script>
  <script id="research-data" type="application/json">{research_json}</script>
  <script>
    const clips = JSON.parse(document.getElementById("clip-data").textContent);
    const researchData = JSON.parse(document.getElementById("research-data").textContent || "{{}}");
    let filtered = [...clips];
    let selectedIndex = 0;
    const search = document.getElementById("search");
    const matchFilter = document.getElementById("matchFilter");
    const clipList = document.getElementById("clipList");
    const viewer = document.getElementById("viewer");
    const count = document.getElementById("count");

    const esc = v => String(v || "").replace(/[&<>"']/g, c => ({{"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#039;"}}[c]));
    const okValues = ["exported","uploaded","synced","sent"];
    const pill = (label, val) => `<span class="pill ${{okValues.includes(String(val||"").toLowerCase())?"ok":val?"warn":""}}">${{esc(label)}}: ${{esc(val||"n/a")}}</span>`;

    function videoSrc(clip) {{
      return clip.media_url || "";
    }}

    function researchFor(clip) {{
      const slug = clip.match_slug;
      return slug ? researchData[slug] : null;
    }}

    function markersFor(clip) {{
      const r = researchFor(clip);
      if (!r || !r.events || r.events.length === 0) return [];
      const events = r.events;
      const total = Math.max(...events.map(e => e.video_time_seconds || 0)) + 120;
      return events.map(e => ({{
        ...e,
        pct: total > 0 ? ((e.video_time_seconds || 0) / total * 100) : 0,
        total_sec: total,
      }}));
    }}

    function clipWindowsFor(clip, markers) {{
      const total = markers.length > 0 ? markers[0].total_sec : 5400;
      const st = parseFloat(clip.start_time) || 0;
      const et = parseFloat(clip.end_time) || 0;
      if (!st && !et) return [];
      const startPct = total > 0 ? (st / total * 100) : 0;
      const endPct = total > 0 ? (et / total * 100) : startPct + 1;
      return [{{ start_pct: startPct, width_pct: Math.max(endPct - startPct, 0.5), label: clip.clip_id }}];
    }}

    function populateFilters() {{
      const matches = [...new Set(clips.map(c => c.match_title || "Unmatched"))].sort();
      matchFilter.innerHTML = `<option value="">All matches</option>` + matches.map(m => `<option value="${{esc(m)}}">${{esc(m)}}</option>`).join("");
    }}

    function applyFilters() {{
      const q = search.value.trim().toLowerCase();
      const m = matchFilter.value;
      filtered = clips.filter(c => {{
        const text = [c.clip_id, c.match_title, c.moment_label, c.emotional_angle, c.relative_export_path].join(" ").toLowerCase();
        return (!m || (c.match_title || "Unmatched") === m) && (!q || text.includes(q));
      }});
      selectedIndex = Math.min(selectedIndex, Math.max(filtered.length - 1, 0));
      renderList(); renderViewer();
    }}

    function renderList() {{
      count.textContent = filtered.length + " of " + clips.length + " clips";
      clipList.innerHTML = filtered.map((c, i) => {{
        const det = (c._detection && c._detection.length > 0) ? c._detection[0] : {{}};
        return '<button class="clip-row' + (i === selectedIndex ? ' active' : '') + '" data-index="' + i + '">' +
          '<strong>' + esc(c.moment_label || c.clip_id) + '</strong>' +
          '<div class="meta-line">' + esc(c.match_title || "Unmatched") + ' | ' + esc(c.start_time) + (c.end_time ? ' \u2192 ' + esc(c.end_time) : '') + '</div>' +
          (det.editorial_thesis ? '<div class="meta-line" style="color:var(--cyan)">' + esc(det.editorial_thesis.substring(0,80)) + '</div>' : '') +
          '<div class="status-line">' +
          pill("Export", c.status) +
          (det.legacy_value ? '<span class="pill">Legacy: ' + esc(det.legacy_value) + '</span>' : '') +
          (c.file_size_mb ? '<span class="pill">' + esc(c.file_size_mb) + ' MB</span>' : '') +
          '</div>' +
          '</button>';
      }}).join("");
      clipList.querySelectorAll(".clip-row").forEach(b => b.addEventListener("click", () => {{
        selectedIndex = Number(b.dataset.index); renderList(); renderViewer();
      }}));
    }}

    function renderTimeline(clip, container) {{
      const markers = markersFor(clip);
      const bars = clipWindowsFor(clip, markers);
      if (markers.length === 0 && bars.length === 0) return;
      const totalSec = markers.length > 0 ? markers[0].total_sec : 5400;
      const minutes = Math.ceil(totalSec / 60);
      let html = '<div class="timeline"><div class="timeline-ruler">';

      bars.forEach(b => {{
        html += '<div class="clip-bar" style="left:' + b.start_pct.toFixed(1) + '%;width:' + b.width_pct.toFixed(1) + '%" title="' + esc(b.label) + '"></div>';
      }});

      markers.forEach(m => {{
        const cls = 'marker-dot ' + (m.type || '');
        html += '<div class="marker" style="left:' + m.pct.toFixed(1) + '%" title="' + esc(m.description || m.type) + ' - ' + m.video_time_seconds + 's"><div class="' + cls + '"></div></div>';
      }});

      html += '</div><div class="timeline-labels">';
      for (let i = 0; i <= 6; i++) {{
        const min = Math.round(i * minutes / 6);
        html += '<span>' + String(Math.floor(min/60)).padStart(2,'0') + ':' + String(min%60).padStart(2,'0') + '</span>';
      }}
      html += '</div></div>';
      container.innerHTML = html;

      // Click markers to seek video
      const video = container.parentElement.querySelector('video');
      if (video) {{
        container.querySelectorAll('.marker-dot').forEach((dot, idx) => {{
          dot.addEventListener('click', () => {{
            video.currentTime = markers[idx].video_time_seconds || 0;
            video.play();
          }});
        }});
        container.querySelectorAll('.clip-bar').forEach((bar, idx) => {{
          bar.addEventListener('click', () => {{
            const st = parseFloat(clip.start_time) || 0;
            video.currentTime = st;
            video.play();
          }});
        }});
      }}
    }}

    function renderViewer() {{
      const c = filtered[selectedIndex];
      if (!c) {{ viewer.innerHTML = '<div class="empty">No clips match filters.</div>'; return; }}
      const det = (c._detection && c._detection.length > 0) ? c._detection : [];
      const r = researchFor(c);

      viewer.innerHTML =
        '<div class="video-wrap">' +
          '<video controls playsinline preload="metadata" src="' + esc(videoSrc(c)) + '"></video>' +
          '<div id="timelineContainer"></div>' +
        '</div>' +
        '<div class="details">' +
          '<div class="panel"><h2>' + esc(c.moment_label || c.clip_id) + '</h2>' +
            (c.emotional_angle ? '<p class="angle">' + esc(c.emotional_angle) + '</p>' : '') +
          '</div>' +
          '<div class="panel"><h3>Editorial Thesis</h3>' +
            (det.length > 0 && det[0].editorial_thesis
              ? '<div class="thesis">' + esc(det[0].editorial_thesis) + '</div>'
              : '<div class="sub">No editorial thesis available.</div>') +
          '</div>' +
          '<div class="panel"><h3>Clip Data</h3>' +
            '<div class="grid">' +
              '<div class="kv"><span>Match</span><code>' + esc(c.match_title || "—") + '</code></div>' +
              '<div class="kv"><span>Window</span><code>' + esc(c.start_time || "—") + (c.end_time ? ' \u2192 ' + esc(c.end_time) : '') + '</code></div>' +
              '<div class="kv"><span>Status</span><code>' + esc(c.status || "—") + '</code></div>' +
              '<div class="kv"><span>ID</span><code>' + esc(c.clip_id || "—") + '</code></div>' +
              '<div class="kv"><span>Size</span><code>' + esc(c.file_size_mb || "—") + ' MB</code></div>' +
              '<div class="kv"><span>Profile</span><code>' + esc(c.export_profile || "—") + '</code></div>' +
              '<div class="kv"><span>Platform</span><code>' + esc(c.platform || "—") + '</code></div>' +
              '<div class="kv"><span>Source</span><code>' + esc(c.source_file || "—") + '</code></div>' +
            '</div>' +
          '</div>' +
          (det.length > 0
            ? '<div class="panel"><h3>Detection Clips (' + det.length + ')</h3>' +
              det.map((d, i) =>
                '<div class="detection-item">' +
                  '<div class="field"><span class="field-label">#' + (i+1) + '</span><span class="field-value">' + esc(d.narrative_role || "") + ' | ' + esc(d.category || "") + ' | ' + esc(d.start_time || "") + '\u2013' + esc(d.end_time || "") + 's</span></div>' +
                  (d.editorial_thesis ? '<div class="thesis">' + esc(d.editorial_thesis) + '</div>' : '') +
                  '<div class="field"><span class="field-label">Angle</span><span class="field-value">' + esc(d.emotional_angle || "—") + '</span></div>' +
                  (d.legacy_value ? '<div class="field"><span class="field-label">Legacy</span><span class="field-value">' + esc(d.legacy_value) + '/10</span></div>' : '') +
                  (d.virality_score ? '<div class="field"><span class="field-label">Virality</span><span class="field-value">' + esc(d.virality_score) + '/10</span></div>' : '') +
                '</div>'
              ).join("") +
            '</div>'
            : '') +
          (r && r.events && r.events.length > 0
            ? '<div class="panel"><h3>Research Events (' + r.events.length + ')</h3>' +
              '<div class="grid">' +
                (r.match
                  ? '<div class="kv"><span>Match</span><code>' + esc((r.match.home_team || "") + " vs " + (r.match.away_team || "")) + '</code></div>' +
                    '<div class="kv"><span>Competition</span><code>' + esc(r.match.competition || "") + '</code></div>' +
                    '<div class="kv"><span>Stage</span><code>' + esc(r.match.stage || "") + '</code></div>'
                  : '') +
              '</div>' +
              r.events.slice(0, 8).map(e =>
                '<div class="detection-item">' +
                  '<div class="field"><span class="field-label">' + esc(e.type || "") + '</span><span class="field-value">' + esc((e.video_time_seconds || "—") + "s") + '</span></div>' +
                  '<div class="field"><span class="field-value" style="color:var(--muted)">' + esc((e.description || "").substring(0, 120)) + '</span></div>' +
                '</div>'
              ).join("") +
            '</div>'
            : '') +
        '</div>';

      renderTimeline(c, document.getElementById("timelineContainer"));
    }}

    document.addEventListener("keydown", e => {{
      if (["INPUT","TEXTAREA","SELECT"].includes(e.target.tagName)) return;
      if (e.key === "ArrowDown") {{ selectedIndex = Math.min(selectedIndex + 1, filtered.length - 1); renderList(); renderViewer(); }}
      if (e.key === "ArrowUp") {{ selectedIndex = Math.max(selectedIndex - 1, 0); renderList(); renderViewer(); }}
      if (e.key === "/") {{ e.preventDefault(); search.focus(); }}
    }});
    search.addEventListener("input", applyFilters);
    matchFilter.addEventListener("change", applyFilters);
    populateFilters(); applyFilters();
  </script>
</body>
</html>
"""


def html_escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;").replace("'", "&#039;")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    clips_dir = Path(args.clips_dir)
    manifest_path = Path(args.manifest)
    research_dir = Path(args.research_dir)
    detections_dir = Path(args.detections_dir)
    output_path = Path(args.output)

    manifest_rows = read_manifest(manifest_path)
    research_by_match = read_research(research_dir)
    detections_by_match = read_detections(detections_dir)
    clip_rows = discover_clips(clips_dir, manifest_rows, research_by_match, detections_by_match)
    generated_at = datetime.now().isoformat(timespec="seconds")

    html = build_html(clip_rows, research_by_match, args.title, generated_at)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(html, encoding="utf-8")
    print(f"Built {output_path} with {len(clip_rows)} clips")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
