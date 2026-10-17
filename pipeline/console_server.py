"""Operator Console — local web server.

Minimal HTTP server using Python's standard library. Serves a dark cinematic
UI for managing projects, creating new projects, and viewing processing status.

No framework dependencies. Local-only. Start with:
    python -m scripts.console
"""

from __future__ import annotations

import json
import os
import html
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from pipeline.deployment import DEMO_DISABLED_MESSAGE, demo_disabled_response, is_demo_mode, setup_demo_environment

setup_demo_environment()

from pipeline.operator_console import (
    list_available_sports,
    list_projects,
    get_project,
    get_project_status,
    validate_project_intake,
    create_project,
    start_research_first_workflow,
    duplicate_project,
    start_analysis_batch,
    get_batch_detail,
    list_recent_batches,
    transition_project,
    project_transitions,
    analyze_project,
    get_analysis_status,
    list_moments,
    get_project_capabilities,
get_transcription_status,
    generate_stories,
    get_story_status,
    list_stories,
    review_moment,
    review_story,
    story_add_moment,
    story_remove_moment,
    story_update_moment,
    get_story_detail,
    generate_canonical_edit_brief,
    generate_canonical_edl,
    get_brief,
    get_brief_status,
    get_project_workflow_status,
    confirm_project_rights,
    list_all_briefs,
    build_timeline,
    get_edl,
    get_edl_status,
    list_all_edls,
    render_rough_cut,
    get_render_status,
    get_render_capabilities,
    get_project_research,
    seed_project_moments_from_research,
    generate_edit_plan,
    generate_editplan_preview,
    get_editplan_execution_report,
    prepare_chatcut_handoff,
    list_renderers,
    generate_canonical_render,
    review_render,
    resolve_render_artifact,
    create_export_package,
)
from pipeline.channel_models import list_channel_presets
from pipeline.integration_service import integration_health_report
from pipeline.system_health import full_health_report
from pipeline.pilot import JobExistsError, JobNotFoundError, JobRevisionError, JobTransitionError
from pipeline.workflow_state import resolve_workflow_state
from pipeline.metadata_trust import resolve_metadata

_TEMPLATE_DIR = Path(__file__).resolve().parent / "console_templates"
_STATIC_DIR = Path(__file__).resolve().parent / "console_static"
_DEFAULT_PORT = 8420

_CLIENT_DISCONNECT_ERRORS = (BrokenPipeError, ConnectionAbortedError, ConnectionResetError)

_STAGE_LABELS = {
    "PREPARING_SOURCE": "Preparing Source",
    "TRANSCRIBING": "Transcribing",
    "UNDERSTANDING_GAME": "Understanding Game",
    "FINDING_MOMENTS": "Finding Moments",
    "PREPARING_RESULTS": "Preparing Results",
}


def _stage_label(stage: str) -> str:
    return _STAGE_LABELS.get(stage, stage)


def _read_template(name: str) -> str:
    return (_TEMPLATE_DIR / name).read_text(encoding="utf-8")


def _read_static(name: str) -> bytes:
    return (_STATIC_DIR / name).read_bytes()


def _html_response(handler: BaseHTTPRequestHandler, html: str, status: int = 200) -> None:
    body = html.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "text/html; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _json_response(handler: BaseHTTPRequestHandler, data: dict | list, status: int = 200) -> None:
    body = json.dumps(data, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _demo_unavailable_html() -> str:
    return f'<div class="demo-unavailable"><strong>Hosted Demo</strong><span>{_escape(DEMO_DISABLED_MESSAGE)}</span></div>'


def _demo_disabled_button(label: str) -> str:
    return f'<button class="btn btn-primary" type="button" disabled title="{_escape(DEMO_DISABLED_MESSAGE)}">{_escape(label)}</button><p class="muted demo-action-note">{_escape(DEMO_DISABLED_MESSAGE)}</p>'


def _stream_media_file(handler: BaseHTTPRequestHandler, video_path: Path, mime_type: str) -> None:
    file_size = video_path.stat().st_size
    range_header = handler.headers.get("Range") if hasattr(handler, "headers") else None
    start = 0
    end = file_size - 1
    status = 200
    if range_header and range_header.startswith("bytes="):
        requested = range_header.split("=", 1)[1].split(",", 1)[0]
        raw_start, _, raw_end = requested.partition("-")
        if raw_start:
            start = max(0, int(raw_start))
        if raw_end:
            end = min(file_size - 1, int(raw_end))
        status = 206
    length = max(0, end - start + 1)
    handler.send_response(status)
    handler.send_header("Content-Type", mime_type)
    handler.send_header("Content-Length", str(length))
    handler.send_header("Accept-Ranges", "bytes")
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
    handler.end_headers()
    with open(video_path, "rb") as handle:
        handle.seek(start)
        remaining = length
        while remaining > 0:
            chunk = handle.read(min(1024 * 1024, remaining))
            if not chunk:
                break
            handler.wfile.write(chunk)
            remaining -= len(chunk)


def _css_response(handler: BaseHTTPRequestHandler) -> None:
    body = _read_static("style.css")
    handler.send_response(200)
    handler.send_header("Content-Type", "text/css; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _parse_form_body(handler: BaseHTTPRequestHandler) -> dict:
    length = int(handler.headers.get("Content-Length", 0))
    if length == 0:
        return {}
    raw = handler.rfile.read(length)
    content_type = handler.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
    if content_type == "application/x-www-form-urlencoded":
        try:
            parsed = parse_qs(raw.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            return {}
        return {key: values[-1] if values else "" for key, values in parsed.items()}
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


def _escape(value: object) -> str:
    return html.escape(str(value), quote=True)


def _format_moment_time(value: object) -> str:
    if value in (None, ""):
        return "—"
    try:
        total = int(float(value))
    except (TypeError, ValueError):
        return "—"
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{seconds:02d}"
    return f"{minutes}:{seconds:02d}"


def _project_source_coverage(project_id: str) -> dict:
    try:
        from pipeline.runtime_service import list_project_stories, list_story_edit_plans
        for story in list_project_stories(project_id):
            coverage = (story.metadata or {}).get("source_coverage")
            if coverage:
                return coverage
            for plan in list_story_edit_plans(story.story_id):
                coverage = (plan.metadata or {}).get("source_coverage")
                if coverage:
                    return coverage
    except Exception:
        return {}
    return {}


def _moment_preview_window(project_id: str, moment) -> dict:
    metadata = moment.metadata or {}
    window = metadata.get("search_window") or {}
    if window.get("start") is not None and window.get("end") is not None:
        return {"available": True, "source_in": float(window["start"]), "source_out": float(window["end"])}
    start = getattr(moment, "start_seconds", None)
    end = getattr(moment, "end_seconds", None)
    if start is not None and end is not None and float(end) > float(start):
        return {"available": True, "source_in": float(start), "source_out": float(end)}
    coverage = _project_source_coverage(project_id)
    kickoff = coverage.get("kickoff_media_offset_seconds") or metadata.get("kickoff_media_offset_seconds")
    duration = coverage.get("source_duration_seconds") or metadata.get("source_duration_seconds")
    match_minute = metadata.get("match_minute")
    if kickoff is None or match_minute is None:
        return {"available": False, "reason": "No aligned source window is available."}
    from pipeline.research_service import event_search_window
    media_time = float(kickoff) + float(match_minute) * 60 + float(metadata.get("match_second_optional") or 0)
    if duration is not None and media_time > float(duration):
        return {"available": False, "reason": "Not available in this source."}
    before, after = event_search_window(moment.universal_event_type)
    return {"available": True, "source_in": max(0.0, media_time + before), "source_out": max(0.0, media_time + after)}


def _creative_event_label(event_type: object, headline: object = "") -> str:
    text = str(headline or "").lower()
    if "maniche" in text or "strikes" in text:
        return "Maniche Strikes"
    if "portugal down to 10" in text:
        return "Portugal Down To 10"
    if "netherlands down to 10" in text:
        return "Netherlands Down To 10"
    if "deco" in text and "sent" in text:
        return "Deco Sent Off"
    if "one more red" in text:
        return "One More Red"
    if "grosso" in text:
        return "Grosso Breaks Through"
    if "del piero" in text:
        return "Del Piero Ends It"
    if "first" in text or "lead" in text:
        return "First Strike"
    if "second" in text or "dagger" in text or "brace" in text:
        return "The Dagger"
    if "penalty" in text:
        return "Late Penalty"
    if str(event_type or "").upper() == "SCORE":
        return "Goal"
    return str(event_type or "Moment").replace("_", " ").title()


def _story_display_title(title: object) -> str:
    title = str(title or "Story Found").replace("_", " ").strip()
    display = title.title()
    return display.replace("Takes Over", "Took Over")


def _source_timeline_html(research_events: list[dict], coverage: dict) -> str:
    end_minute = coverage.get("estimated_match_coverage_end_minute")
    try:
        available_pct = min(max(float(end_minute or 0) / 90.0 * 100.0, 0.0), 100.0)
    except (TypeError, ValueError):
        available_pct = 0.0
    markers = ""
    for event in research_events[:12]:
        minute = event.get("match_minute") or 0
        try:
            left = min(max(float(minute) / 90.0 * 100.0, 0.0), 100.0)
        except (TypeError, ValueError):
            left = 0.0
        display = (event.get("metadata") or {}).get("display_minute") or f"{minute}'"
        availability = (event.get("source_availability") or {}).get("availability_status")
        cls = "available" if availability == "AVAILABLE" else "outside"
        label = _creative_event_label(event.get("universal_event_type"), event.get("headline"))
        markers += f'<span class="source-marker {cls}" style="left:{left:.2f}%" title="{_escape(display)} · {_escape(label)}">{_escape(display)}</span>'
    return f"""
    <section class="cinema-section source-coverage" id="source-coverage">
      <div class="section-kicker">Source Coverage</div>
      <h3>What footage is usable?</h3>
      <div class="match-axis"><span>0'</span><span>45'</span><span>90'</span></div>
      <div class="source-track" aria-label="Match source coverage">
        <div class="source-available" style="width:{available_pct:.2f}%"></div>
        <div class="source-unavailable" style="left:{available_pct:.2f}%"></div>
        {markers}
      </div>
      <div class="timeline-legend"><span><i class="dot solid"></i>Available</span><span><i class="dot outline"></i>Not in source</span></div>
    </section>
    """


def _creator_source_coverage_html(research_events: list[dict], source_artifacts: list[dict]) -> str:
    if not research_events and not source_artifacts:
        return ""
    artifact_text = " ".join(
        f"{artifact.get('path') or ''} {(artifact.get('metadata') or {}).get('label') or ''}"
        for artifact in source_artifacts
    ).lower()
    has_second_half = "second" in artifact_text or "2nd" in artifact_text
    if not has_second_half:
        for event in research_events:
            try:
                if float((event.get("match_minute") or 0) or 0) > 45:
                    has_second_half = True
                    break
            except (TypeError, ValueError):
                continue
    halves = [("FIRST HALF", 0, 45), ("SECOND HALF", 45, 90)] if has_second_half else [("SOURCE", 0, 90)]
    rows = ""
    for label, start, end in halves:
        markers = ""
        for event in research_events:
            try:
                minute = float(event.get("match_minute") or 0)
            except (TypeError, ValueError):
                continue
            if minute < start or minute > end:
                continue
            left = min(max((minute - start) / max(end - start, 1) * 100.0, 0.0), 100.0)
            display = (event.get("metadata") or {}).get("display_minute") or f"{int(minute)}'"
            title = _creative_event_label(event.get("universal_event_type"), event.get("headline"))
            markers += f'<span class="coverage-moment" style="left:{left:.2f}%" title="{_escape(display)} · {_escape(title)}">{_escape(display)}</span>'
        rows += f'<div class="coverage-row"><strong>{label}</strong><div class="coverage-bar"><span></span>{markers}</div></div>'
    return f'<section class="creator-coverage" aria-label="Source coverage"><div class="section-kicker">Source Coverage</div>{rows}</section>'


def _creator_activity_html(events: list[dict], research_events: list[dict], source_artifacts: list[dict], workflow_payload: dict, story_status: dict, active_count: int) -> str:
    items: list[tuple[str, str, str]] = []
    for event in events[-3:]:
        title = str(event.get("event_type") or "Project updated").replace("_", " ").title()
        timestamp = str(event.get("timestamp") or "")[:16]
        message = str(event.get("message") or "").strip()
        items.append((timestamp, title, message))
    if research_events:
        first = research_events[0]
        items.append(("", f"Research found {len(research_events)} events", _creative_event_label(first.get("universal_event_type"), first.get("headline"))))
    if source_artifacts:
        items.append(("", f"{len(source_artifacts)} source file{'s' if len(source_artifacts) != 1 else ''} mapped", "Clipper switches sources automatically."))
    if active_count:
        items.append(("", f"{active_count} usable moment{'s' if active_count != 1 else ''}", "Ready for story building."))
    if story_status.get("story_status") == "RUNNING":
        items.append(("", "Building story", "Confirmed moments are becoming a narrative."))
    elif workflow_payload.get("active"):
        items.append(("", str(workflow_payload.get("current_stage") or "Working").replace("_", " ").title(), "Clipper is processing backend work."))
    if not items:
        items.append(("", "Ready", "Start when you are ready."))
    rows = "".join(
        f'<button class="activity-item" type="button"><time>{_escape(time or "now")}</time><strong>{_escape(title)}</strong>{f"<span>{_escape(message)}</span>" if message else ""}</button>'
        for time, title, message in items[-6:]
    )
    return f'<aside class="creator-activity"><div class="section-kicker">Clipper Activity</div>{rows}</aside>'


def _project_library_title(project: dict) -> str:
    raw = str(project.get("pilot_id") or project.get("job_id") or "Project")
    lowered = raw.lower()
    if "arg" in lowered and "cro" in lowered:
        return "Argentina vs Croatia"
    if "por" in lowered and "ned" in lowered:
        return "Portugal vs Netherlands"
    if "ita" in lowered and "ger" in lowered:
        return "Italy vs Germany"
    if "germany" in lowered and "italy" in lowered:
        return "Germany vs Italy"
    if "belgium" in lowered and "egypt" in lowered:
        return "Belgium vs Egypt"
    if "netherlands" in lowered and "japan" in lowered:
        return "Netherlands vs Japan"
    return raw.replace("_source", "").replace("_", " ").strip().title() or "Untitled Match"


def _parse_year(value: object) -> str:
    match = re.search(r"(?:19|20)\d{2}", str(value or ""))
    return match.group(0) if match else ""


def _clean_slug_words(value: object) -> str:
    text = re.sub(r"\b\d{10,}\b", " ", str(value or ""))
    text = re.sub(r"\b(source|raw|rf|v\d+|align|status|evidence|part\d+|first|second|half|1st|2nd)\b", " ", text, flags=re.I)
    text = re.sub(r"[_\-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _parsed_match_identity(project: dict) -> tuple[str, str, str]:
    raw = " ".join(str(project.get(key) or "") for key in ("pilot_id", "job_id", "source_id"))
    lowered = raw.lower()
    year = _parse_year(raw)
    pairs = [
        (("arg", "argentina"), ("cro", "croatia"), "Argentina vs Croatia"),
        (("por", "portugal"), ("ned", "netherlands"), "Portugal vs Netherlands"),
        (("ita", "italy"), ("ger", "germany"), "Italy vs Germany"),
        (("germany",), ("italy",), "Germany vs Italy"),
        (("belgium",), ("egypt",), "Belgium vs Egypt"),
        (("netherlands",), ("japan",), "Netherlands vs Japan"),
    ]
    for left_tokens, right_tokens, title in pairs:
        if any(token in lowered for token in left_tokens) and any(token in lowered for token in right_tokens):
            return title, year, "parsed"
    cleaned = _clean_slug_words(project.get("pilot_id") or project.get("job_id") or "")
    if cleaned and cleaned.lower() not in {"project", "source"}:
        return cleaned.title(), year, "parsed"
    return "Untitled Match", year, "fallback"


def _creator_status_label(workflow: dict, story_status: dict | None = None) -> str:
    status = (story_status or {}).get("story_status")
    if status == "RUNNING":
        return "Building story"
    if status in {"FAILED", "NEEDS ATTENTION"}:
        return "Needs attention"
    action = str(workflow.get("primary_next_action") or "")
    attention = str(workflow.get("attention") or "")
    if attention == "Failed":
        return "Needs attention"
    if action.startswith("Review") and "Moment" in action:
        return "Needs your review"
    if action in {"Seed Moments", "Analyze Source"}:
        return "Ready to start"
    if action == "Approve Story":
        return "Story ready"
    if action in {"Generate Edit", "Prepare Cut"}:
        return "Building cut"
    if action == "Generate Rough Preview":
        return "Cut ready"
    if action == "Review Preview":
        return "Ready to watch"
    if action == "Prepare ChatCut Handoff":
        return "Finished"
    if action == "Project Complete":
        return "Finished"
    if attention == "Complete":
        return "Story ready"
    return "Ready to start"


def _workflow_rank(workflow: dict, story_status: dict | None = None) -> int:
    label = _creator_status_label(workflow, story_status)
    ranks = {
        "Ready to start": 10,
        "Finding moments": 20,
        "Needs your review": 30,
        "Building story": 40,
        "Story ready": 50,
        "Building cut": 60,
        "Cut ready": 70,
        "Rendering": 80,
        "Ready to watch": 90,
        "Finished": 100,
        "Needs attention": 5,
    }
    return ranks.get(label, 0)


def _project_is_test_or_smoke(project: dict, identity: dict | None = None) -> bool:
    text = " ".join(str(project.get(key) or "") for key in ("job_id", "pilot_id", "source_id"))
    if identity:
        text += " " + str(identity.get("title") or "")
    lowered = text.lower()
    return any(token in lowered for token in ("smoke", "test", "debug"))


def _project_group_key(identity: dict) -> str:
    title = re.sub(r"[^a-z0-9]+", "-", str(identity.get("title") or "untitled").lower()).strip("-")
    year = str(identity.get("year") or "")
    return "|".join(part for part in (title, year) if part) or title or "untitled"


def _resolve_project_display_identity(project: dict, research: dict | None = None, intake: dict | None = None, source_paths: list[str] | None = None) -> dict:
    resolved = resolve_metadata(project, research, intake, source_paths=source_paths or [])
    return {
        "title": resolved.get("title") or "Untitled Match",
        "competition": resolved.get("competition") or "",
        "stage": resolved.get("stage") or "",
        "year": resolved.get("year") or "",
        "source": (resolved.get("fields") or {}).get("title", {}).get("source") or "FALLBACK",
        "health": resolved.get("health") or "UNKNOWN",
        "conflicts": resolved.get("conflicts") or [],
        "fields": resolved.get("fields") or {},
        "candidates": resolved.get("candidates") or {},
        "repair": resolved.get("repair") or {},
    }


def _project_sort_time(value: object) -> str:
    return str(value or "")


def _preferred_project_record(records: list[dict]) -> dict:
    return sorted(
        records,
        key=lambda record: (
            int(record.get("workflow_rank") or 0),
            int(record.get("source_count") or 0),
            _project_sort_time(record.get("updated_at")),
            _project_sort_time(record.get("created_at")),
        ),
        reverse=True,
    )[0]


def _group_project_records(records: list[dict]) -> tuple[list[dict], list[dict]]:
    visible_records = [record for record in records if not record.get("is_test")]
    test_records = [record for record in records if record.get("is_test")]
    grouped: dict[str, list[dict]] = {}
    for record in visible_records:
        grouped.setdefault(str(record.get("group_key") or record.get("job_id") or "untitled"), []).append(record)
    cards = []
    for group_records in grouped.values():
        preferred = _preferred_project_record(group_records)
        previous = [record for record in sorted(group_records, key=lambda item: _project_sort_time(item.get("updated_at") or item.get("created_at")), reverse=True) if record.get("job_id") != preferred.get("job_id")]
        cards.append({"preferred": preferred, "previous": previous, "records": group_records})
    cards.sort(key=lambda card: _project_sort_time(card["preferred"].get("updated_at") or card["preferred"].get("created_at")), reverse=True)
    return cards, test_records


def _is_smoke_story(story: dict) -> bool:
    title = str(story.get("title") or "").lower()
    story_id = str(story.get("story_id") or "").lower()
    return "smoke" in title or "smoke" in story_id or "test" in title or "test" in story_id


def _select_primary_creative_story(project_id: str, stories: list[dict]) -> dict:
    if not stories:
        return {}
    real_stories = [story for story in stories if not _is_smoke_story(story)] or stories
    try:
        from pipeline.runtime_service import list_story_edit_plans, list_story_moments, get_moment
        planned = [story for story in real_stories if list_story_edit_plans(str(story.get("story_id") or ""))]
        if planned:
            return planned[0]
        research_linked = []
        for story in real_stories:
            story_moments = list_story_moments(str(story.get("story_id") or ""))
            for story_moment in story_moments:
                moment = get_moment(story_moment.moment_id)
                if moment and (moment.metadata or {}).get("origin") == "research":
                    research_linked.append(story)
                    break
        if research_linked:
            return research_linked[0]
    except Exception:
        pass
    preferred = [story for story in real_stories if story.get("status") in {"APPROVED", "SUGGESTED"}]
    return preferred[0] if preferred else real_stories[0]


def _format_score(value: object) -> str:
    if value in (None, ""):
        return "—"
    try:
        return f"{float(value):.2f}"
    except (TypeError, ValueError):
        return "—"


def _validation_issues_html(readiness: dict) -> str:
    issues = readiness.get("issues", [])
    if not isinstance(issues, list) or not issues:
        codes = readiness.get("validation_codes", [])
        if isinstance(codes, list) and codes and codes != ["INTAKE_OK"]:
            items = "".join(f"<li>{_escape(code)}</li>" for code in codes[:8])
            return f'<div class="validation-issues"><strong>Validation:</strong><ul>{items}</ul></div>'
        return ""
    rows = ""
    for issue in issues[:8]:
        if not isinstance(issue, dict):
            continue
        path = _escape(issue.get("path", ""))
        code = _escape(issue.get("code", ""))
        message = _escape(issue.get("message", ""))
        rows += f"<li><strong>{code}</strong> {path}: {message}</li>"
    return f'<div class="validation-issues"><strong>Validation:</strong><ul>{rows}</ul></div>' if rows else ""


class ConsoleHandler(BaseHTTPRequestHandler):
    """Request handler for the Operator Console."""

    def handle_one_request(self):
        try:
            return super().handle_one_request()
        except _CLIENT_DISCONNECT_ERRORS:
            self.close_connection = True
            return None

    def log_message(self, fmt, *args):  # noqa: ANN001
        pass  # suppress noisy request logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/style.css":
            return _css_response(self)

        if path == "/health":
            return _json_response(self, {"ok": True, "application": "clipper", "environment": "demo" if is_demo_mode() else "local"})

        if path.startswith("/render_video/"):
            parts = path.split("/render_video/")[1].split("/")
            job_id = parts[0]
            render_id = parts[1]
            return self._serve_render_video(job_id, render_id)

        if path.startswith("/source_video/"):
            parts = path.split("/source_video/")[1].split("/")
            project_id = parts[0]
            artifact_id = parts[1] if len(parts) > 1 else ""
            return self._serve_source_video(project_id, artifact_id)

        if path == "/":
            return self._render_projects()
        if path == "/batches":
            if is_demo_mode():
                return _html_response(self, self._page("Batches", f'<div class="card"><h2>Batches</h2>{_demo_unavailable_html()}</div>'))
            return self._render_batches()
        if path == "/system":
            return self._render_system()
        if path.startswith("/batches/"):
            batch_id = path.split("/batches/")[1]
            return self._render_batch_detail(batch_id)
        if path == "/projects/new":
            if is_demo_mode():
                return _html_response(self, self._page("New Project", f'<div class="card"><h2>New Project</h2>{_demo_unavailable_html()}</div>'))
            return self._render_new_project(qs)
        if "/projects/" in path and "/moments/" in path and path.endswith("/preview"):
            parts = path.split("/projects/")[1].split("/")
            job_id = parts[0]
            moment_id = parts[2]
            return self._render_moment_preview(job_id, moment_id)
        if "/projects/" in path and "/stories/" in path:
            parts = path.split("/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._render_story_detail(job_id, story_id)
        if path.startswith("/edit-plans/") and path.endswith("/handoff"):
            edit_plan_id = path.split("/edit-plans/")[1].split("/handoff")[0]
            return self._render_handoff_detail(edit_plan_id)
        if path.startswith("/projects/") and "/status" in path:
            job_id = path.split("/projects/")[1].split("/status")[0]
            return self._render_project_status(job_id)
        if path.startswith("/projects/") and path.endswith("/workflow-state"):
            job_id = path.split("/projects/")[1].split("/workflow-state")[0].strip("/")
            return self._api_workflow_state(job_id)
        if path.startswith("/projects/"):
            job_id = path.split("/projects/")[1]
            return self._render_project_detail(job_id, qs)
        if path == "/api/sports":
            return _json_response(self, list_available_sports())
        if path == "/api/projects":
            return _json_response(self, list_projects())

        _html_response(self, self._page("Not Found", "<h1>404</h1><p>Page not found.</p>"), 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if is_demo_mode():
            return _json_response(self, demo_disabled_response(path.rsplit("/", 1)[-1]), 403)

        if path == "/api/projects/create":
            return self._api_create_project()
        if path == "/api/batches/analyze":
            return self._api_start_batch()
        if path.startswith("/api/projects/") and path.endswith("/duplicate"):
            job_id = path.split("/api/projects/")[1].split("/duplicate")[0]
            return self._api_duplicate_project(job_id)
        if path.startswith("/api/projects/") and "/actions/" in path:
            parts = path.split("/api/projects/")[1].split("/actions/")
            return self._api_project_action(parts[0], parts[1])
        if path.startswith("/api/projects/") and path.endswith("/transition"):
            job_id = path.split("/api/projects/")[1].split("/transition")[0]
            return self._api_transition_project(job_id)
        if path.startswith("/api/projects/") and path.endswith("/rights/confirm"):
            job_id = path.split("/api/projects/")[1].split("/rights/confirm")[0]
            return self._api_confirm_rights(job_id)
        if path.startswith("/api/projects/") and path.endswith("/analyze"):
            job_id = path.split("/api/projects/")[1].split("/analyze")[0]
            return self._api_analyze_project(job_id)
        if path.startswith("/api/projects/") and path.endswith("/research/seed-moments"):
            job_id = path.split("/api/projects/")[1].split("/research/seed-moments")[0]
            return self._api_seed_research_moments(job_id)
        if path.startswith("/api/projects/") and path.endswith("/align"):
            job_id = path.split("/api/projects/")[1].split("/align")[0]
            return self._api_align_project(job_id)
        if path.startswith("/api/projects/") and path.endswith("/source-clock/first-half"):
            job_id = path.split("/api/projects/")[1].split("/source-clock/first-half")[0]
            return self._api_source_clock_first_half(job_id)
        if path.startswith("/projects/") and path.endswith("/source-clock/first-half"):
            job_id = path.split("/projects/")[1].split("/source-clock/first-half")[0]
            return self._api_source_clock_first_half(job_id)
        if path.startswith("/api/projects/") and path.endswith("/stories"):
            job_id = path.split("/api/projects/")[1].split("/stories")[0]
            return self._api_generate_stories(job_id)
        if "/api/projects/" in path and "/moments/" in path and path.endswith("/review"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            moment_id = parts[2]
            return self._api_review_moment(job_id, moment_id)
        if "/api/projects/" in path and "/moments/" in path and path.endswith("/alignment"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            moment_id = parts[2]
            return self._api_confirm_moment_alignment(job_id, moment_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/brief"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_generate_brief(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/review"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_review_story(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/moments/add"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_story_add_moment(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/moments/remove"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_story_remove_moment(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/moments/update"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_story_update_moment(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/edl"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_build_edl(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/render"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_build_render(job_id, story_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/edit-plan"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_generate_edit_plan(job_id, story_id)
        if path.startswith("/api/edit-plans/") and path.endswith("/chatcut-handoff"):
            edit_plan_id = path.split("/api/edit-plans/")[1].split("/chatcut-handoff")[0]
            return self._api_chatcut_handoff(edit_plan_id)
        if path.startswith("/api/edit-plans/") and path.endswith("/preview"):
            edit_plan_id = path.split("/api/edit-plans/")[1].split("/preview")[0]
            return self._api_editplan_preview(edit_plan_id)
        if "/api/projects/" in path and "/renders/" in path and path.endswith("/review"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            render_id = parts[2]
            return self._api_review_render(job_id, render_id)
        if "/api/projects/" in path and "/renders/" in path and path.endswith("/export"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            render_id = parts[2]
            return self._api_create_export(job_id, render_id)
        if path == "/api/intake/validate":
            return self._api_validate_intake()
        if path.startswith("/video/"):
            return self._serve_video(path)

        _json_response(self, {"error": "not found"}, 404)

    # ── Page renders ─────────────────────────────────────────────────────────

    def _page(self, title: str, content: str) -> str:
        base = _read_template("base.html")
        if is_demo_mode():
            nav_links = '<a href="/">Projects</a><a href="/system">System</a>'
            demo_badge = '<span class="demo-badge">Hosted Demo</span>'
            content = f'<div class="demo-banner"><strong>Hosted Demo</strong><span>Read-only product demo. Local media processing, rendering, and source playback are disabled.</span></div>{content}'
        else:
            nav_links = '<a href="/">Projects</a><a href="/projects/new">New Project</a><a href="/batches">Batches</a><a href="/system">System</a>'
            demo_badge = ""
        return base.replace("{{TITLE}}", title).replace("{{CONTENT}}", content).replace("{{NAV_LINKS}}", nav_links).replace("{{DEMO_BADGE}}", demo_badge)

    def _workflow_badge(self, step: str, state: str) -> str:
        cls = str(state or "NOT STARTED").lower().replace(" ", "-")
        return f'<div class="workflow-step workflow-{_escape(cls)}"><span>{_escape(step)}</span><strong>{_escape(state)}</strong></div>'

    @staticmethod
    def _read_intake_for_detail(detail: dict) -> dict:
        intake_path = detail.get("intake_manifest_path", "")
        candidate_paths = []
        if intake_path:
            candidate_paths.append(Path(intake_path))
        job_id = str(detail.get("job_id") or "").strip()
        if job_id:
            try:
                from pipeline.pilot import default_jobs_dir
                candidate_paths.append(default_jobs_dir().parent / "intakes" / f"{job_id}.json")
            except Exception:
                pass
        for candidate_path in candidate_paths:
            try:
                return json.loads(candidate_path.read_text(encoding="utf-8"))
            except Exception:
                continue
        return {}

    @staticmethod
    def _canonical_readiness(detail: dict, intake: dict | None = None) -> dict:
        intake_data = intake if isinstance(intake, dict) else ConsoleHandler._read_intake_for_detail(detail)
        if not intake_data:
            return detail.get("readiness_summary", {}) if isinstance(detail.get("readiness_summary"), dict) else {}
        try:
            return validate_project_intake(intake_data, check_source=True, check_rights=True)
        except Exception:
            return detail.get("readiness_summary", {}) if isinstance(detail.get("readiness_summary"), dict) else {}

    @staticmethod
    def _workflow_runtime(detail: dict, analysis: dict | None = None) -> tuple[dict, dict]:
        runtime = detail.get("runtime", {}) if isinstance(detail.get("runtime"), dict) else {}
        try:
            research_data = get_project_research(str(detail.get("job_id") or (runtime.get("project") or {}).get("project_id") or ""))
        except Exception:
            research_data = {"research": [], "events": []}
        augmented = dict(runtime)
        augmented["research"] = research_data.get("research") or []
        augmented["research_events"] = research_data.get("events") or []
        if runtime.get("analysis"):
            augmented["pipeline_runs"] = [runtime.get("analysis")]
        try:
            from pipeline.runtime_service import list_project_stories, list_story_edit_plans
            project_id = (runtime.get("project") or {}).get("project_id") or detail.get("job_id")
            edit_plan_count = 0
            for story in list_project_stories(str(project_id)):
                edit_plan_count += len(list_story_edit_plans(story.story_id))
            augmented["edit_plan_summary"] = {"edit_plan_count": edit_plan_count}
        except Exception:
            augmented.setdefault("edit_plan_summary", {"edit_plan_count": 0})
        return augmented, research_data

    def _render_handoff_detail(self, edit_plan_id: str):
        from pipeline.edit_handoff_service import build_chatcut_handoff_manifest, editplan_quality_report, validate_edit_handoff
        from pipeline.runtime_service import get_edit_plan
        plan = get_edit_plan(edit_plan_id)
        if plan is None:
            return _html_response(self, self._page("Handoff Not Found", "<div class='card'>EditPlan not found.</div>"), 404)
        manifest = build_chatcut_handoff_manifest(plan)
        validation = validate_edit_handoff(plan)
        source_rows = "".join(
            f"<tr><td>{_escape(src.get('artifact_id'))}</td><td>{_escape(src.get('source_path'))}</td><td>{_escape(src.get('duration_seconds'))}</td></tr>"
            for src in manifest.get("source_media", [])
        )
        timeline_rows = ""
        for row in editplan_quality_report(plan):
            timeline_rows += f"""
            <tr>
              <td>{_escape(row.get('sequence_order'))}</td><td>{_escape(row.get('narrative_role'))}</td>
              <td>{_escape(row.get('source_window'))}</td><td>{_escape(row.get('duration'))}</td>
              <td>{_escape(row.get('crop_intent'))}</td><td>{_escape(row.get('speed'))}</td>
              <td>{_escape(row.get('freeze'))}</td><td>{_escape(row.get('text'))}</td>
              <td>{_escape(row.get('audio_cue'))}</td><td>{_escape(row.get('motion_graphic'))}</td>
            </tr>
            """
        overlay_rows = "".join(
            f"<tr><td>{_escape(o.get('sequence_order'))}</td><td>{_escape(o.get('timeline_start'))}</td><td>{_escape(o.get('duration'))}</td><td>{_escape(o.get('text'))}</td><td>{_escape(o.get('template_id') or '—')}</td></tr>"
            for o in manifest.get("text_overlays", [])
        )
        content = f"""
        <nav class="breadcrumb"><a href="/projects/{_escape(plan.project_id)}">Project</a> <span>›</span> <span>ChatCut Handoff</span></nav>
        <section class="workspace-hero"><div><p class="eyebrow">ChatCut Handoff V{_escape(manifest.get('handoff_version'))}</p><h2>{_escape(plan.title)}</h2><p class="muted">Clipper has prepared the creative-edit specification for ChatCut. This is not a direct ChatCut API execution.</p></div><span class="badge badge-ready">{_escape(validation.get('status'))}</span></section>
        <div class="card"><h3>Source Media</h3><table><thead><tr><th>Artifact</th><th>Path</th><th>Duration</th></tr></thead><tbody>{source_rows or '<tr><td colspan="3" class="empty">No source media declared.</td></tr>'}</tbody></table></div>
        <div class="card"><h3>Timeline Sequence</h3><table><thead><tr><th>#</th><th>Role</th><th>Source Window</th><th>Duration</th><th>Crop</th><th>Speed</th><th>Freeze</th><th>Text</th><th>Audio</th><th>Motion</th></tr></thead><tbody>{timeline_rows}</tbody></table></div>
        <div class="card"><h3>Text Overlays / Captions</h3><table><thead><tr><th>#</th><th>Start</th><th>Duration</th><th>Text</th><th>Template</th></tr></thead><tbody>{overlay_rows}</tbody></table></div>
        <a class="btn" href="/projects/{_escape(plan.project_id)}">Back to Workspace</a>
        """
        return _html_response(self, self._page("ChatCut Handoff", content))

    def _render_moment_preview(self, job_id: str, moment_id: str):
        from pipeline.runtime_service import get_artifact, get_moment, get_project, get_research_event
        moment = get_moment(moment_id)
        if moment is None or moment.project_id != job_id:
            return _html_response(self, self._page("Moment Not Found", "<div class='card'>Moment not found.</div>"), 404)
        metadata = moment.metadata or {}
        participant = next((p.name for p in moment.participants if p.name), "Moment")
        minute = metadata.get("match_minute", "—")
        title = f"{participant} · {minute}' {moment.sport_event_type.title()}"
        event = get_research_event(metadata.get("research_event_id")) if metadata.get("research_event_id") else None
        event_availability = ((event.metadata or {}).get("source_availability") or {}).get("availability_status") if event else None
        preview_window = _moment_preview_window(job_id, moment)
        unavailable = metadata.get("availability_status") == "OUTSIDE_SOURCE" or event_availability == "OUTSIDE_SOURCE" or not preview_window.get("available")
        if unavailable:
            content = f"""
            <nav class="breadcrumb"><a href="/projects/{_escape(job_id)}">Project</a> <span>›</span> <span>Preview</span></nav>
            <section class="workspace-hero"><div><p class="eyebrow">Moment Preview</p><h2>{_escape(title)}</h2><p class="muted">Not available in this source.</p></div></section>
            <a class="btn" href="/projects/{_escape(job_id)}">Back to Workspace</a>
            """
            return _html_response(self, self._page("Moment Preview", content))
        source_artifact = get_artifact(moment.source_artifact_id) if moment.source_artifact_id else None
        if source_artifact is None:
            project = get_project(job_id)
            source_artifact = get_artifact(project.source_artifact_id) if project and project.source_artifact_id else None
        if source_artifact is None:
            content = f"""
            <nav class="breadcrumb"><a href="/projects/{_escape(job_id)}">Project</a> <span>›</span> <span>Preview</span></nav>
            <div class="card"><h2>{_escape(title)}</h2><p class="empty">Source media is not available for preview.</p></div>
            """
            return _html_response(self, self._page("Moment Preview", content))
        source_in = float(preview_window["source_in"])
        source_out = float(preview_window["source_out"])
        seek_time = max(0.0, source_in)
        media_url = f"/source_video/{_escape(job_id)}/{_escape(source_artifact.artifact_id)}"
        content = f"""
        <nav class="breadcrumb"><a href="/projects/{_escape(job_id)}">Project</a> <span>›</span> <span>Moment Preview</span></nav>
        <section class="workspace-hero"><div><p class="eyebrow">Moment Preview</p><h2>{_escape(title)}</h2><p class="muted">Source time {_format_moment_time(source_in)} → {_format_moment_time(source_out)}</p></div></section>
        <div class="card preview-focus">
          <video id="momentPreview" controls preload="metadata" src="{media_url}" style="width:100%;max-height:70vh;border-radius:8px;"></video>
          <p class="muted">Preview window starts at {_format_moment_time(source_in)}. If playback does not start there automatically, use the player scrubber.</p>
        </div>
        <a class="btn" href="/projects/{_escape(job_id)}">Back to Workspace</a>
        <script>
        const video = document.getElementById("momentPreview");
        video.addEventListener("loadedmetadata", () => {{ video.currentTime = {seek_time:.3f}; }}, {{once: true}});
        </script>
        """
        return _html_response(self, self._page("Moment Preview", content))

    def _render_projects(self):
        projects = list_projects()
        sports = list_available_sports()
        rows = ""
        project_cards = ""
        dashboard_records = []
        for p in projects:
            state_class = p["current_state"].lower().replace("_", "-")
            derived_tag = ' <span class="badge badge-moment">Derived</span>' if p.get("derived") else ""
            attention = "Ready" if p.get("current_state") in {"READY", "COMPLETE"} else "—"
            next_action = "Open Project"
            try:
                research_data = get_project_research(p["job_id"])
            except Exception:
                research_data = {"research": [], "events": []}
            try:
                workflow = get_project_workflow_status(p["job_id"])
            except Exception:
                workflow = {"primary_next_action": "", "attention": ""}
            try:
                story_status = get_story_status(p["job_id"])
            except Exception:
                story_status = {"story_status": ""}
            try:
                from pipeline.runtime_service import get_project_runtime_summary as _runtime_summary
                runtime_summary = _runtime_summary(p["job_id"])
            except Exception:
                runtime_summary = {"artifacts": []}
            try:
                from pipeline.pilot import read_job as _read_job
                job_record = _read_job(p["job_id"])
            except Exception:
                job_record = {"job_id": p.get("job_id", "")}
            intake_data = ConsoleHandler._read_intake_for_detail(job_record)
            source_artifacts_for_card = [a for a in (runtime_summary.get("artifacts") or []) if a.get("artifact_type") == "source_media"]
            source_paths_for_card = [str(a.get("path") or "") for a in source_artifacts_for_card]
            identity = _resolve_project_display_identity(p, research_data, intake_data, source_paths_for_card)
            source_count = len(source_artifacts_for_card)
            status_label = _creator_status_label(workflow, story_status)
            record = dict(p)
            record.update({
                "identity": identity,
                "group_key": _project_group_key(identity),
                "source_count": source_count,
                "creator_status": status_label,
                "workflow_rank": _workflow_rank(workflow, story_status),
                "is_test": _project_is_test_or_smoke(p, identity),
            })
            dashboard_records.append(record)
            rows += f"""
            <tr>
              <td><input type="checkbox" class="batch-select" value="{_escape(p['job_id'])}"></td>
              <td><a href="/projects/{p['job_id']}">{_escape(p.get('pilot_id') or p['job_id'])}</a>{derived_tag}</td>
              <td>{p['project_id']}</td>
              <td><span class="badge badge-{_escape(state_class)}">{_escape(p['current_state'])}</span></td>
              <td><span class="badge badge-{_escape(str(attention).lower().replace(' ', '-'))}">{_escape(attention)}</span></td>
              <td>{_escape(next_action)}</td>
              <td>{_escape(str(p.get('updated_at') or p.get('created_at') or '—')[:19])}</td>
            </tr>
            """
        grouped_cards, test_records = _group_project_records(dashboard_records)
        for group in grouped_cards:
            preferred = group["preferred"]
            identity = preferred["identity"]
            subtitle_parts = [part for part in [identity.get("competition"), identity.get("year"), identity.get("stage")] if part]
            subtitle = " · ".join(dict.fromkeys(str(part) for part in subtitle_parts)) or "Match project"
            last_activity = str(preferred.get("updated_at") or preferred.get("created_at") or "")[:19].replace("T", " ") or "unknown"
            previous = group["previous"]
            previous_html = ""
            if previous:
                previous_rows = "".join(
                    f'<li><a href="/projects/{_escape(row.get("job_id", ""))}">{_escape(str(row.get("created_at") or "")[:19].replace("T", " ") or "unknown")}</a><span>{_escape(row.get("creator_status") or "Ready to start")} · {_escape(str(row.get("source_count") or 0))} sources</span><code>{_escape(row.get("job_id") or "")}</code></li>'
                    for row in previous
                )
                previous_html = f'<details class="previous-runs"><summary>{len(previous)} previous run{"s" if len(previous) != 1 else ""}</summary><ul>{previous_rows}</ul></details>'
            project_cards += f"""
            <article class="project-card grouped-project-card">
              <a class="project-card-main" href="/projects/{_escape(preferred['job_id'])}">
                <small>{_escape(subtitle)}</small>
                <strong>{_escape(identity.get('title') or 'Untitled Match')}</strong>
                <span>{_escape(preferred.get('creator_status') or 'Ready to start')}</span>
                <span>{_escape(str(preferred.get('source_count') or 0))} source{'s' if int(preferred.get('source_count') or 0) != 1 else ''}</span>
                <span>Last activity {_escape(last_activity)}</span>
                <em>Continue →</em>
              </a>
              {previous_html}
            </article>
            """
        metadata_review_records = [record for record in dashboard_records if (record.get("identity") or {}).get("health") in {"CONFLICT", "INCOMPLETE", "UNKNOWN"}]
        metadata_review_html = ""
        if metadata_review_records:
            items = ""
            for record in metadata_review_records:
                identity = record.get("identity") or {}
                conflict_text = "; ".join(f"{c.get('field')}: " + ", ".join(v.get('value', '') for v in c.get('values', [])) for c in identity.get("conflicts", [])) or "No direct conflicts; metadata is incomplete."
                field_rows = "".join(
                    f"<tr><td>{_escape(name)}</td><td>{_escape(field.get('value') or '—')}</td><td>{_escape(field.get('source') or '—')}</td><td>{_escape(field.get('trust') or '—')}</td></tr>"
                    for name, field in (identity.get("fields") or {}).items()
                )
                repair = identity.get("repair") or {}
                repair_rows = "".join(
                    f"<li>{_escape(item.get('field'))}: {_escape(item.get('normalized_value'))} <span class='muted'>({_escape(item.get('reason'))})</span></li>"
                    for item in repair.get("recommended", [])
                )
                items += f"""
                <details class="metadata-review-item">
                  <summary>{_escape(identity.get('title') or 'Untitled Match')} · {_escape(identity.get('health') or 'UNKNOWN')}</summary>
                  <p class="muted">Project: <code>{_escape(record.get('job_id') or '')}</code></p>
                  <p>{_escape(conflict_text)}</p>
                  <table><thead><tr><th>Field</th><th>Resolved</th><th>Source</th><th>Trust</th></tr></thead><tbody>{field_rows}</tbody></table>
                  {f'<p class="muted">Safe repair preview only:</p><ul>{repair_rows}</ul>' if repair_rows else '<p class="muted">No automatic repair recommended.</p>'}
                </details>
                """
            metadata_review_html = f'<details class="advanced-details metadata-review"><summary>Metadata Review ({len(metadata_review_records)})</summary>{items}</details>'
        test_project_html = ""
        if test_records:
            test_rows = "".join(
                f'<li><a href="/projects/{_escape(row.get("job_id", ""))}">{_escape((row.get("identity") or {}).get("title") or row.get("pilot_id") or row.get("job_id") or "Project")}</a><span>{_escape(row.get("creator_status") or "Ready to start")}</span><code>{_escape(row.get("job_id") or "")}</code></li>'
                for row in test_records
            )
            test_project_html = f'<details class="advanced-details dev-projects"><summary>Test / Development Projects ({len(test_records)})</summary><ul>{test_rows}</ul></details>'
        if not projects:
            rows = f"""
            <tr><td colspan="7" class="empty">
              <div class="empty-state">
                <h2>Welcome to Clipper</h2>
                <p>Turn a full match into reviewed, story-driven short-form video.</p>
                <p class="muted">1. Add source · 2. Analyze · 3. Review Moments · 4. Build Story · 5. Generate video</p>
                <p><a href="/projects/new" class="btn btn-primary">Create / Import Project</a></p>
              </div>
            </td></tr>
            """
        if not project_cards and projects:
            project_cards = '<p class="empty">No creator projects yet. Test and development projects are available below.</p>'

        sport_cards = ""
        for s in sports:
            marker = " (default)" if s["default"] else ""
            safe = "production-ready" if s["production_safe"] else "sandbox only"
            analysis = "analysis: yes" if s["analysis_supported"] else "analysis: not yet"
            sport_cards += f'<div class="sport-card"><strong>{s["display_name"]}</strong><br><span class="muted">{safe}{marker}</span><br><span class="muted">{analysis}</span></div>'

        content = f"""
        <section class="dashboard-hero">
            <p class="eyebrow">Clipper</p>
            <h1>Creative Library</h1>
            <p>Your matches, stories, cuts, and review work in one place.</p>
            <div class="hero-actions"><a href="/projects/new" class="btn btn-primary">Add Match</a></div>
            <div id="dashboard-action-error" class="creator-action-error" hidden></div>
            <div class="dashboard-cards">{project_cards}</div>
            {test_project_html}
            <details class="advanced-details"><summary>Advanced Project List</summary>
            <div class="form-actions" style="justify-content:flex-start;"><button class="btn" onclick="analyzeSelected(this)" {'disabled title="No projects available"' if not projects else ''}>Analyze Selected</button></div>
            <table>
              <thead><tr><th></th><th>Project</th><th>Sport</th><th>State</th><th>Attention</th><th>Next</th><th>Updated</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
            </details>
            {metadata_review_html}
          </section>
          <details class="card advanced-details">
            <summary>System Sports Readiness</summary>
            <div class="sport-grid">{sport_cards}</div>
          </details>
        <script>
        function dashboardError(message, details) {{
          const box = document.getElementById('dashboard-action-error');
          if (!box) return;
          box.hidden = false;
          box.innerHTML = `<strong>${{message}}</strong>${{details ? `<details><summary>Advanced Details</summary><pre>${{String(details).replace(/[&<>]/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[ch]))}}</pre></details>` : ''}}`;
        }}
        async function analyzeSelected(btn) {{
          const selected = Array.from(document.querySelectorAll(".batch-select:checked")).map(cb => cb.value);
          if (selected.length === 0) {{ dashboardError('Select at least one project.'); return; }}
          const original = btn ? btn.textContent : '';
          if (btn) {{ btn.disabled = true; btn.textContent = 'Starting batch...'; }}
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 12000);
          try {{
            const resp = await fetch("/api/batches/analyze", {{
              method: "POST",
              headers: {{"Content-Type": "application/json"}},
              body: JSON.stringify({{project_ids: selected}}),
              signal: controller.signal,
            }});
            const result = await resp.json().catch(() => ({{ok:false, error:'Invalid server response'}}));
            if (result.ok) {{ window.location.href = "/batches/" + result.batch_id; }} else {{ dashboardError('Could not start batch.', JSON.stringify(result, null, 2)); }}
          }} catch (err) {{
            dashboardError(err && err.name === 'AbortError' ? 'Batch start timed out.' : 'Could not start batch.', String(err));
          }} finally {{
            window.clearTimeout(timeout);
            if (btn) {{ btn.disabled = false; btn.textContent = original; }}
          }}
        }}
        </script>
        """
        return _html_response(self, self._page("Projects", content))

    def _render_batches(self):
        batches = list_recent_batches(limit=25)
        rows = ""
        for batch in batches:
            status = _escape(str(batch.get("status", "")))
            rows += f"""
            <tr>
              <td><a href="/batches/{_escape(batch.get('batch_id', ''))}">{_escape(batch.get('batch_id', ''))}</a></td>
              <td>{_escape(batch.get('operation_type', ''))}</td>
              <td><span class="badge badge-{_escape(status.lower().replace('_', '-'))}">{status}</span></td>
              <td>{_escape(batch.get('succeeded_count', 0))} / {_escape(batch.get('total_items', 0))}</td>
              <td>{_escape(batch.get('created_at', '')[:19])}</td>
            </tr>
            """
        content = f"""
        <div class="card">
          <h2>Batches</h2>
          <table>
            <thead><tr><th>Batch</th><th>Operation</th><th>Status</th><th>Succeeded / Total</th><th>Created</th></tr></thead>
            <tbody>{rows or '<tr><td colspan="5" class="empty">No batches yet. Select projects on the dashboard and run Analyze Selected.</td></tr>'}</tbody>
          </table>
        </div>
        """
        return _html_response(self, self._page("Batches", content))

    def _render_system(self):
        report = full_health_report()
        core = report.get("core", [])
        integrations = report.get("integrations", [])
        providers = report.get("providers", {})
        def _provider_badge(stage: str) -> str:
            status = providers.get(stage, {}) if isinstance(providers.get(stage, {}), dict) else {}
            if status.get("ready"):
                provider = status.get("configured_provider") or "local"
                model = status.get("model") or ""
                label = f"Ready via {provider}" + (f" / {model}" if model else "")
                return f'<span class="badge badge-ready">{_escape(label)}</span>'
            return f'<span class="badge badge-needs-review">{_escape(str(status.get("status") or "Blocked"))}</span>'

        def _provider_detail(stage: str, fallback: str) -> str:
            status = providers.get(stage, {}) if isinstance(providers.get(stage, {}), dict) else {}
            model = status.get("model")
            bits = [str(status.get("message") or fallback)]
            if model:
                bits.append(f"Model: {model}")
            if status.get("explicit") is not None:
                bits.append("Explicit" if status.get("explicit") else "Automatic local fallback")
            return _escape(" · ".join(bits))
        core_rows = ""
        for check in core:
            status = check.get("status", "FAIL")
            message = check.get("operator_message", "")
            action = check.get("recommended_action", "")
            core_rows += f"""
            <tr>
              <td>{_escape(check.get('check_id', ''))}</td>
              <td><span class="badge badge-moment">{_escape(status)}</span></td>
              <td>{_escape(message)}</td>
              <td>{_escape(action) or '—'}</td>
            </tr>
            """
        integration_rows = ""
        for adapter in integrations:
            status = adapter.get("status", "UNAVAILABLE")
            integration_rows += f"""
            <tr>
              <td>{_escape(adapter.get('adapter_id', ''))}</td>
              <td><span class="badge badge-moment">{_escape(status)}</span></td>
              <td>{_escape(', '.join(adapter.get('capabilities', [])) or '—')}</td>
              <td>{_escape(adapter.get('message', '') or '—')}</td>
            </tr>
            """
        content = f"""
        <div class="card">
          <h2>System</h2>
          <p class="muted">Core health is required. Optional integrations never block core operation.</p>
          <table>
            <thead><tr><th>Check</th><th>Status</th><th>Detail</th><th>Action</th></tr></thead>
            <tbody>{core_rows or '<tr><td colspan="4" class="empty">No core checks.</td></tr>'}</tbody>
          </table>
        </div>
        <div class="card">
          <h2>Providers</h2>
          <table>
            <thead><tr><th>Capability</th><th>Status</th><th>Detail</th></tr></thead>
            <tbody>
              <tr>
                <td>Detection</td>
                <td>{_provider_badge('detection')}</td>
                <td>{_provider_detail('detection', 'Detection cannot run through Ollama until the local service is running.')}</td>
              </tr>
              <tr>
                <td>Story</td>
                <td>{_provider_badge('story')}</td>
                <td>{_provider_detail('story', 'Story generation is not available on this system.')}</td>
              </tr>
              <tr>
                <td>Edit</td>
                <td>{_provider_badge('edit')}</td>
                <td>{_provider_detail('edit', 'Edit generation is not available on this system.')}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="card">
          <h2>Optional Integrations</h2>
          <table>
            <thead><tr><th>Adapter</th><th>Status</th><th>Capabilities</th><th>Message</th></tr></thead>
            <tbody>{integration_rows or '<tr><td colspan="4" class="empty">No adapters registered.</td></tr>'}</tbody>
          </table>
        </div>
        <a href="/" class="btn">← Back to Projects</a>
        """
        return _html_response(self, self._page("System", content))

    def _render_new_project(self, qs: dict):
        sports = list_available_sports()
        default_sport = next((s["name"] for s in sports if s["default"]), "football")
        sport_options = ""
        for s in sports:
            selected = ' selected' if s["name"] == default_sport else ""
            disabled = " disabled" if not s["production_safe"] else ""
            label = f'{s["display_name"]} (sandbox)' if not s["production_safe"] else s["display_name"]
            sport_options += f'<option value="{s["name"]}"{selected}{disabled}>{label}</option>'

        error = ""
        if "error" in qs:
            error = f'<div class="alert alert-error">{qs["error"][0]}</div>'

        content = f"""
        <div class="card new-project-card">
          <p class="eyebrow">New Project</p>
          <h2>Add Match</h2>
          <p class="muted">Give Clipper the match video and a simple match hint. Advanced IDs stay optional.</p>
          {error}
          <div id="new-project-error" class="creator-action-error" hidden></div>
          <form id="new-project-form" onsubmit="return handleSubmit(event)">
            <div class="form-group">
              <label for="local_file_path">Choose Video</label>
              <input type="text" id="local_file_path" name="local_file_path" placeholder="C:\\FootballArchive\\RAW\\match.mp4" required>
            </div>
            <div class="form-group">
              <label for="event_name">What match/event is this?</label>
              <input type="text" id="event_name" name="event_name" placeholder="Argentina vs Croatia 2022">
            </div>
            <p class="muted">Optional: add another source after the project starts.</p>
            <details class="advanced-details">
              <summary>Advanced Details</summary>
            <div class="form-group">
              <label for="sport">Sport</label>
              <select id="sport" name="sport">{sport_options}</select>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="pilot_id">Project ID</label>
                <input type="text" id="pilot_id" name="pilot_id" placeholder="e.g. project_alpha" pattern="[A-Za-z0-9_-]+">
              </div>
              <div class="form-group">
                <label for="source_id">Source ID</label>
                <input type="text" id="source_id" name="source_id" placeholder="e.g. source_001" pattern="[A-Za-z0-9_-]+">
              </div>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="reference_deployment">Deployment</label>
                <input type="text" id="reference_deployment" name="reference_deployment" value="world_cup">
              </div>
              <div class="form-group">
                <label for="delivery_method">Delivery</label>
                <select id="delivery_method" name="delivery_method">
                  <option value="shared_folder">Shared Folder</option>
                  <option value="manual">Manual</option>
                </select>
              </div>
            </div>
            </details>
            <div class="progress-copy"><span>Identifying match...</span><span>Researching match...</span><span>Finding key moments...</span><span>Building source map...</span><span>Stories ready</span></div>
            <div class="form-actions">
              <a href="/" class="btn">Cancel</a>
              <button type="submit" class="btn btn-primary">Start</button>
            </div>
          </form>
        </div>
        <script>
        function showNewProjectError(message, details) {{
          const box = document.getElementById('new-project-error');
          if (!box) return;
          box.hidden = false;
          box.innerHTML = `<strong>${{message}}</strong>${{details ? `<details><summary>Advanced Details</summary><pre>${{String(details).replace(/[&<>]/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[ch]))}}</pre></details>` : ''}}`;
        }}
        async function handleSubmit(e) {{
          e.preventDefault();
          const form = e.target;
          const submit = form.querySelector('button[type="submit"]');
          if (submit) {{ submit.disabled = true; submit.textContent = "Starting Clipper..."; }}
          const sourcePath = form.local_file_path.value.trim();
          const base = sourcePath.split(/[\\/]/).pop().replace(/\\.[^.]+$/, "").replace(/[^A-Za-z0-9_-]+/g, "_").replace(/^_+|_+$/g, "") || "match";
          if (!form.pilot_id.value.trim()) form.pilot_id.value = base;
          if (!form.source_id.value.trim()) form.source_id.value = base + "_source";
          const data = {{
            sport: form.sport.value,
            pilot_id: form.pilot_id.value.trim(),
            source_id: form.source_id.value.trim(),
            event_name: form.event_name.value.trim(),
            reference_deployment: form.reference_deployment.value.trim(),
            delivery_method: form.delivery_method.value,
            local_file_path: form.local_file_path.value.trim(),
          }};
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), 15000);
          try {{
            const resp = await fetch("/api/projects/create", {{
              method: "POST",
              headers: {{"Content-Type": "application/json"}},
              body: JSON.stringify(data),
              signal: controller.signal,
            }});
            const result = await resp.json().catch(() => ({{ok:false, error:'Invalid server response'}}));
            if (result.ok) {{
              window.location.href = "/projects/" + result.job_id;
            }} else {{
              if (submit) {{ submit.disabled = false; submit.textContent = "Start"; }}
              showNewProjectError("Couldn't start this project.", JSON.stringify(result, null, 2));
            }}
          }} catch (err) {{
            if (submit) {{ submit.disabled = false; submit.textContent = "Start"; }}
            showNewProjectError(err && err.name === 'AbortError' ? "Starting took too long. Try again." : "Couldn't start this project.", String(err));
          }} finally {{
            window.clearTimeout(timeout);
          }}
        }}
        </script>
        """
        return _html_response(self, self._page("New Project", content))

    def _render_project_detail(self, job_id: str, qs: dict | None = None):
        try:
            detail = get_project(job_id)
        except Exception as exc:
            return _html_response(self, self._page("Error", f'<div class="card"><h2>Error</h2><p>{exc}</p><a href="/" class="btn">Back</a></div>'), 404)

        state = detail.get("current_state", "")
        transitions = detail.get("allowed_next_states", [])
        events = detail.get("events", [])
        intake_data = ConsoleHandler._read_intake_for_detail(detail)
        readiness = ConsoleHandler._canonical_readiness(detail, intake_data)
        detail = dict(detail)
        detail["readiness_summary"] = readiness
        runtime = detail.get("runtime", {}) if isinstance(detail.get("runtime"), dict) else {}
        display_name = (runtime.get("project") or {}).get("display_name") or job_id
        if isinstance(intake_data, dict):
            media = intake_data.get("media") or {}
            intake_display_name = str(media.get("match_or_event_name") or media.get("event_name") or "").strip()
            if intake_display_name:
                display_name = intake_display_name
        if display_name == job_id:
            try:
                from pipeline.runtime_service import get_project as _get_runtime_project_for_title
                runtime_project_for_title = _get_runtime_project_for_title(job_id)
                if runtime_project_for_title and runtime_project_for_title.display_name:
                    display_name = runtime_project_for_title.display_name
            except Exception:
                pass
        lineage = runtime.get("lineage", {}) if isinstance(runtime.get("lineage"), dict) else {}
        lineage_html = ""
        if lineage.get("is_duplicate"):
            source_id = lineage.get("source_project_id") or ""
            reuse_mode = str(lineage.get("reuse_mode") or "").replace("_", " ").title()
            lineage_html = f"""
            <div class="detail-grid" style="margin-top:0.5rem;">
              <div><strong>Derived from:</strong> {_escape(source_id)}</div>
              <div><strong>Reused:</strong> {_escape(reuse_mode)}</div>
              {f'<div><strong>Analysis:</strong> Reused — no retranscription required</div>' if lineage.get('analysis_reused') else ''}
            </div>
            """

        workflow = get_project_workflow_status(job_id)
        workflow_steps = workflow.get("steps", [])
        strip = ""
        for step in workflow_steps:
            strip += ConsoleHandler._workflow_badge(self, str(step.get("step", "")), str(step.get("state", "")))
        next_action = str(workflow.get("primary_next_action", ""))
        attention = str(workflow.get("attention", ""))
        first_story = next(iter(runtime.get("stories") or []), {})
        first_story_id = _escape(first_story.get("story_id", "")) if first_story else ""
        story_link = f"/projects/{_escape(job_id)}/stories/{first_story_id}" if first_story_id else f"/projects/{_escape(job_id)}"

        if next_action.startswith("Analyze") or next_action == "Retry Analysis":
            primary_cta = f'<button class="btn btn-primary" onclick="executeProjectAction(\'retry_analysis\', this)">{_escape(next_action)}</button>'
        elif "Moment" in next_action:
            primary_cta = f'<button class="btn btn-primary" onclick="smoothFocus(\'#review-moments\')">{_escape(next_action)}</button>'
        elif next_action in ("Approve Story", "Build Story"):
            primary_cta = f'<button class="btn btn-primary" onclick="executeProjectAction(\'find_story\', this)">{_escape(next_action)}</button>'
        elif next_action.startswith(("Generate", "Prepare")):
            primary_cta = f'<button class="btn btn-primary" onclick="executeProjectAction(\'render_rough_cut\', this)">{_escape(next_action)}</button>'
        elif "Rough Cut" in next_action or next_action.startswith("Review"):
            primary_cta = f'<button class="btn btn-primary" onclick="smoothFocus(\'#rough-cut\')">{_escape(next_action)}</button>'
        elif "Export" in next_action:
            primary_cta = f'<a class="btn btn-primary" href="{story_link}">{_escape(next_action)}</a>'
        else:
            primary_cta = ""
        if is_demo_mode() and primary_cta:
            primary_cta = _demo_disabled_button(next_action or "Continue")

        workflow_warnings = "".join(f'<li>{_escape(w)}</li>' for w in workflow.get("warnings", []))
        workflow_html = f"""
        <div class="card">
          <div class="card-header">
            <div><h3>Workflow</h3></div>
            <span class="badge badge-{_escape(str(attention).lower().replace(' ', '-'))}">{_escape(attention)}</span>
          </div>
          <div class="workflow-strip">{strip}</div>
          <div class="form-actions" style="margin-top:1rem; justify-content:flex-start;">{primary_cta}</div>
          {f'<ul class="blocker">{workflow_warnings}</ul>' if workflow_warnings else ''}
        </div>
        """
        moment_filter = (qs or {}).get("review", ["ALL"])[-1].upper()
        project_strategy = (runtime.get("project") or {}).get("analysis_strategy") or "TRANSCRIPT_FIRST"

        # Analysis state
        try:
            analysis = get_analysis_status(job_id)
        except Exception:
            analysis = {"analysis_status": "", "analysis_stage": "", "analysis_error": ""}

        # Transcription state
        try:
            transcription = get_transcription_status(job_id)
        except Exception:
            transcription = {"transcription_status": "", "transcript_path": "", "reused": False, "error": ""}

        # Capabilities
        try:
            caps = get_project_capabilities(job_id)
        except Exception:
            caps = {"analysis_supported": False}

        # Legacy raw moments
        moments = list_moments(job_id)

        # Stories
        try:
            story_status = get_story_status(job_id)
        except Exception:
            story_status = {"story_status": "", "story_count": 0, "story_error": ""}
        stories = list_stories(job_id)

        # Source file from job intake
        source_file = ""
        if intake_data:
            source_file = intake_data.get("media", {}).get("local_file_path", "")
            if not source_file:
                source_file = intake_data.get("media", {}).get("original_filename", "")
        if not source_file:
            try:
                from pipeline.runtime_service import get_project as _get_runtime_project, get_artifact as _get_runtime_artifact
                runtime_project_for_source = _get_runtime_project(job_id)
                if runtime_project_for_source and runtime_project_for_source.source_artifact_id:
                    source_artifact_for_display = _get_runtime_artifact(runtime_project_for_source.source_artifact_id)
                    if source_artifact_for_display:
                        source_file = source_artifact_for_display.path
            except Exception:
                pass

        # Analysis status section
        analysis_status = analysis.get("analysis_status", "")
        analysis_stage = analysis.get("analysis_stage", "")
        analysis_error = analysis.get("analysis_error", "")
        moments_count = analysis.get("analysis_manifest_count", 0) or len(moments)

        # Determine source readiness
        source_ready = readiness.get("source_ready", False)
        source_status_class = "ready" if source_ready else "not-ready"
        source_status_text = "Ready" if source_ready else "Not ready"
        validation_issues = _validation_issues_html(readiness)

        # Transcription status
        transcription_status = transcription.get("transcription_status", "")
        transcription_reused = transcription.get("reused", False)
        transcription_error = transcription.get("error", "")
        if transcription_status == "READY":
            transcript_class = "ready"
            transcript_text = "Ready"
            if transcription_reused:
                transcript_text += " (reused)"
        elif transcription_status == "RUNNING":
            transcript_class = ""
            transcript_text = "Running..."
        elif transcription_status == "NEEDS ATTENTION":
            transcript_class = "not-ready"
            transcript_text = transcription_error or "Needs attention"
        elif transcription_status == "WAITING":
            transcript_class = "muted"
            transcript_text = "Waiting"
        else:
            transcript_class = "muted"
            transcript_text = "—"

        # Analyze Game button state
        can_analyze = caps.get("analysis_supported", False) and state in ("READY",) and analysis_status not in ("RUNNING",)
        analyze_disabled = "" if can_analyze else "disabled"
        analyze_class = "btn btn-primary" if can_analyze else "btn btn-disabled"

        # Analysis section content
        if analysis_status == "RUNNING":
            stage_label = _stage_label(analysis_stage)
            analysis_section = f"""
            <div class="analysis-running">
              <div class="spinner"></div>
              <span>{stage_label}...</span>
            </div>
            """
            analyze_btn = f'<button class="btn btn-disabled" disabled>Analyzing...</button>'
        elif analysis_status == "COMPLETE":
            analysis_section = f"""
            <div class="analysis-complete">
              <span class="ready">Analysis complete</span>
              <span>{moments_count} moments found</span>
            </div>
            """
            analyze_btn = f'<button class="btn" onclick="doAnalyze()">Re-analyze</button>'
        elif analysis_status == "FAILED" or analysis_status == "NEEDS ATTENTION":
            safe_error = analysis_error or "Analysis needs attention."
            error_code = str(analysis.get("runtime_error_code", "") or "")
            if error_code == "DETECTION_PROVIDER_UNAVAILABLE":
                analysis_section = f"""
                <div class="analysis-failed">
                  <span class="not-ready">Detection is unavailable.</span>
                  <p class="muted">Your transcript is saved and will be reused. No model provider is currently ready.</p>
                  <div class="form-actions" style="margin-top:0.5rem; justify-content:flex-start;">
                    <button class="btn" onclick="doAnalyze()">Retry Analysis</button>
                    <a class="btn" href="/system">Run System Check</a>
                  </div>
                </div>
                """
                analyze_btn = ""
            else:
                analysis_section = f"""
                <div class="analysis-failed">
                  <span class="not-ready">{safe_error}</span>
                  <details><summary>Details</summary><pre>{analysis_error}</pre></details>
                </div>
                """
                analyze_btn = f'<button class="{analyze_class}" onclick="doAnalyze()">Retry Analysis</button>'
        elif not caps.get("analysis_supported", False):
            analysis_section = f'<p class="muted">Analysis support for {detail.get("project_id", "").title()} is not available yet.</p>'
            analyze_btn = ""
        else:
            analysis_section = '<p class="muted">Not analyzed yet.</p>'
            analyze_btn = f'<button class="{analyze_class}" {analyze_disabled} onclick="doAnalyze()">Analyze Game</button>'

        event_rows = ""
        for ev in reversed(events):
            event_rows += f"""
            <tr>
              <td>{ev.get('sequence', '')}</td>
              <td>{ev.get('event_type', '')}</td>
              <td>{ev.get('previous_state', '—')} → {ev.get('new_state', '—')}</td>
              <td>{ev.get('message', '')[:80]}</td>
              <td>{ev.get('timestamp', '')[:19]}</td>
            </tr>
            """

        state_class = state.lower().replace("_", "-")
        transition_buttons = ""
        if state == "AWAITING_RIGHTS":
            transition_buttons = """
              <button class="btn btn-primary" onclick="doConfirmRights()">Confirm Rights</button>
              <button class="btn" onclick="doCancelProject()">Cancel Project</button>
            """
        else:
            for s in transitions:
                if s == "VALIDATION_FAILED":
                    continue
                label = "Cancel Project" if s == "CANCELLED" else s
                handler = "doCancelProject()" if s == "CANCELLED" else f"doTransition('{s}')"
                transition_buttons += f'<button class="btn btn-state" onclick="{handler}">{label}</button>'

        ready = readiness.get("execution_ready", False)
        ready_class = "ready" if ready else "not-ready"
        ready_text = "Yes" if ready else "No"

        try:
            research_data = get_project_research(job_id)
        except Exception:
            research_data = {"research": [], "events": []}
        research_items = research_data.get("research") or []
        research_events = research_data.get("events") or []
        research_summary = ""
        source_coverage = ""
        research_coverage = {}
        if research_items:
            latest_research = research_items[-1]
            research_coverage = next(
                (
                    (event.get("source_availability") or {})
                    for event in research_events
                    if (event.get("source_availability") or {}).get("source_duration_seconds") is not None
                ),
                {},
            )
            if research_coverage:
                coverage_end = research_coverage.get("estimated_match_coverage_end_minute")
                if coverage_end is not None:
                    total_seconds = int(round(float(coverage_end) * 60))
                    source_coverage = (
                        f"<div><strong>Source coverage:</strong> approximately kickoff → "
                        f"{total_seconds // 60}:{total_seconds % 60:02d} match time</div>"
                    )
            research_summary = f"""
            <div class="detail-grid">
              <div><strong>Match:</strong> {_escape(latest_research.get('home_team', ''))} vs {_escape(latest_research.get('away_team', ''))}</div>
              <div><strong>Competition:</strong> {_escape(latest_research.get('competition', ''))}</div>
              <div><strong>Date:</strong> {_escape(latest_research.get('match_date', ''))}</div>
              <div><strong>Stage:</strong> {_escape(latest_research.get('stage', ''))}</div>
              <div><strong>Score:</strong> {_escape(latest_research.get('home_score', '—'))}–{_escape(latest_research.get('away_score', '—'))}</div>
              <div><strong>Stakes:</strong> {_escape(latest_research.get('stakes') or '—')}</div>
              {source_coverage}
            </div>
            <p class="muted" style="margin-top:0.75rem;">{_escape(latest_research.get('historical_context') or '')}</p>
            """
        research_rows = ""
        for event in research_events[:20]:
            display_minute = (event.get("metadata") or {}).get("display_minute") or f"{event.get('match_minute')}’"
            source_state = (event.get("source_availability") or {}).get("availability_status")
            alignment_state = (event.get("source_availability") or {}).get("alignment_status")
            availability_label = "OUTSIDE_SOURCE" if source_state == "OUTSIDE_SOURCE" else (source_state or "—")
            media_ts = (event.get("source_availability") or {}).get("estimated_media_time")
            participant = next((p.get("name") for p in event.get("participants") or [] if isinstance(p, dict) and p.get("name")), "—")
            research_rows += f"""
            <tr>
              <td>{_escape(str(display_minute))}</td>
              <td>{_escape(participant)} / {_escape(event.get('team') or '—')}</td>
              <td>{_escape(event.get('universal_event_type', ''))}</td>
              <td>{_escape(event.get('headline', ''))}</td>
              <td><span class="availability-{_escape(str(availability_label).lower().replace('_', '-'))}">{_escape(availability_label)}</span></td>
              <td>{_escape(alignment_state or '—')}</td>
              <td>{_format_moment_time(media_ts) if media_ts is not None else '—'}</td>
              <td>{_format_score(event.get('confidence'))}</td>
            </tr>
            """
        research_section = f"""
        <div class="card" id="research">
          <div class="card-header"><h3>Research</h3><span class="badge badge-moment">{len(research_events)} known events</span></div>
          {research_summary or '<p class="muted">No MatchResearch persisted yet.</p>'}
          {f'<table><thead><tr><th>Minute</th><th>Player / Team</th><th>Type</th><th>Headline</th><th>Availability</th><th>Alignment</th><th>Media Time</th><th>Confidence</th></tr></thead><tbody>{research_rows}</tbody></table>' if research_rows else '<p class="empty">No research events yet. Add research, then seed Moments.</p>'}
          <div class="form-actions" style="justify-content:flex-start;">
            <button class="btn" onclick="seedResearchMoments(this)">Seed Moments</button>
            <button class="btn btn-primary" onclick="alignProject(this)">Find Moments In Source</button>
          </div>
        </div>
        """

# Canonical moment review section
        review_states = ("ALL", "UNREVIEWED", "KEEP", "REJECT", "STRONG", "MUST_USE")
        moment_summary = runtime.get("moment_summary", {}) if isinstance(runtime.get("moment_summary"), dict) else {}
        canonical_moments = runtime.get("moments", []) if isinstance(runtime.get("moments"), list) else []
        legacy_moments = []
        if project_strategy == "RESEARCH_FIRST":
            active_moments = []
            from pipeline.source_alignment import is_usable_source_moment
            from pipeline.runtime_service import get_research_event as _get_research_event
            for moment in canonical_moments:
                meta = moment.get("metadata") or {}
                event = _get_research_event(meta.get("research_event_id")) if meta.get("research_event_id") else None
                event_availability = ((event.metadata or {}).get("source_availability") or {}).get("availability_status") if event else None
                moment_obj = type("_MomentPreview", (), {
                    "metadata": meta,
                    "universal_event_type": moment.get("universal_event_type"),
                    "start_seconds": moment.get("start_seconds"),
                    "end_seconds": moment.get("end_seconds"),
                })()
                preview_available = _moment_preview_window(job_id, moment_obj).get("available")
                if meta.get("origin") == "research" and is_usable_source_moment(moment) and event_availability != "OUTSIDE_SOURCE" and preview_available:
                    active_moments.append(moment)
                else:
                    legacy_moments.append(moment)
            canonical_moments = active_moments
        if moment_filter not in review_states:
            moment_filter = "ALL"
        if moment_filter != "ALL":
            canonical_moments = [m for m in canonical_moments if m.get("review_state") == moment_filter]

        filter_links = ""
        _count = moment_summary.get
        for state_name in review_states:
            if state_name == "ALL":
                count = _count("moment_count", 0)
            elif state_name == "UNREVIEWED":
                count = _count("unreviewed_count", 0)
            else:
                count = _count(f"{state_name.lower()}_count", 0)
            label = state_name.replace("_", " ").title()
            cls = "btn btn-primary" if state_name == moment_filter else "btn"
            href = f"/projects/{_escape(job_id)}" if state_name == "ALL" else f"/projects/{_escape(job_id)}?review={state_name}"
            filter_links += f'<a class="{cls}" href="{href}">{label} ({count})</a> '

        source_clock_notice = ""
        source_clock_details = ""
        source_artifacts = [a for a in (runtime.get("artifacts") or []) if a.get("artifact_type") == "source_media"]
        source_artifact = next((a for a in source_artifacts if (((a.get("metadata") or {}).get("source_clock") or {}).get("status") == "NEEDS_OPERATOR")), None) or (source_artifacts[0] if source_artifacts else None)
        source_clock = ((source_artifact or {}).get("metadata") or {}).get("source_clock") or {}
        if not source_clock:
            try:
                from pipeline.runtime_service import get_artifact as _get_runtime_artifact, get_project as _get_runtime_project
                runtime_project_for_clock = _get_runtime_project(job_id)
                if runtime_project_for_clock and runtime_project_for_clock.source_artifact_id:
                    artifact_for_clock = _get_runtime_artifact(runtime_project_for_clock.source_artifact_id)
                    if artifact_for_clock:
                        source_artifact = {"artifact_id": artifact_for_clock.artifact_id, "metadata": artifact_for_clock.metadata}
                        source_clock = (artifact_for_clock.metadata or {}).get("source_clock") or {}
            except Exception:
                source_clock = {}
        clock_segments = source_clock.get("segments") or []
        if clock_segments:
            rows = "".join(
                f"<tr><td>{_escape(str(row.get('segment_type') or '—'))}</td><td>{_format_moment_time(row.get('source_time_start'))}</td><td>{_escape(str(row.get('confidence') or '—'))}</td><td>{_escape(str(row.get('method') or '—'))}</td></tr>"
                for row in clock_segments
            )
            source_clock_details = f"<details class=\"advanced-details\"><summary>Source Clock</summary><p class=\"muted\">Policy: {_escape(str(source_clock.get('version') or '—'))}</p><table><thead><tr><th>Segment</th><th>Source Start</th><th>Confidence</th><th>Method</th></tr></thead><tbody>{rows}</tbody></table></details>"
        if source_clock.get("status") == "NEEDS_OPERATOR":
            review = source_clock.get("review") or {}
            review_segment = str(review.get("segment_type") or "FIRST_HALF")
            second_half_review = review_segment == "SECOND_HALF"
            cursor = float(review.get("cursor_seconds") or 0.0)
            preview_start = max(0.0, cursor - 30.0)
            media_url = f"/source_video/{_escape(job_id)}/{_escape((source_artifact or {}).get('artifact_id') or '')}"
            source_id = _escape((source_artifact or {}).get('artifact_id') or '')
            review_title = "Help Clipper find the second-half start" if second_half_review else "Help Clipper find kickoff"
            review_body = "Clipper needs one quick check before it can place second-half moments accurately." if second_half_review else "Clipper needs one quick check before it can place moments accurately. Confirm kickoff once to improve moment locations."
            confirm_label = "Yes, this is the second-half start" if second_half_review else "This is kickoff"
            source_clock_notice = f"""
            <div class="card" id="source-clock-review">
              <h3>{review_title}</h3>
              <p class="muted">{review_body}</p>
              <video id="kickoffPreview" controls preload="metadata" src="{media_url}" style="width:100%;max-height:48vh;border-radius:8px;"></video>
              <p class="muted">Review cursor: <span id="kickoffCursor">{_format_moment_time(cursor)}</span></p>
              <div class="form-actions">
                <button class="btn" onclick="reviewKickoff('earlier', this, '{_escape(review_segment)}', '{source_id}')">Earlier</button>
                <button class="btn btn-primary" onclick="reviewKickoff('confirm', this, '{_escape(review_segment)}', '{source_id}')">{confirm_label}</button>
                <button class="btn" onclick="reviewKickoff('later', this, '{_escape(review_segment)}', '{source_id}')">Later</button>
              </div>
              <script>setTimeout(() => {{ const v = document.getElementById('kickoffPreview'); if (v) v.currentTime = {preview_start:.3f}; }}, 100);</script>
              {source_clock_details}
            </div>
            """

        review_rows = ""
        moment_cards = ""
        for moment in canonical_moments:
            moment_id = _escape(moment.get("moment_id", ""))
            current = str(moment.get("review_state", "UNREVIEWED"))
            meta = moment.get("metadata") or {}
            minute = meta.get("match_minute", "—")
            availability = meta.get("availability_status", "UNKNOWN")
            alignment = meta.get("alignment_status", "—")
            participant = next((p.get("name") for p in moment.get("participants") or [] if isinstance(p, dict) and p.get("name")), "—")
            preview_action = f'<a class="btn btn-primary" href="/projects/{_escape(job_id)}/moments/{moment_id}/preview">Preview</a>' if availability != "OUTSIDE_SOURCE" else '<span class="muted">Not available in this source</span>'
            actions = ""
            for target, label in (("KEEP", "Keep"), ("STRONG", "Strong"), ("MUST_USE", "Must Use"), ("REJECT", "Reject")):
                cls = "btn btn-primary" if current == target else "btn"
                actions += f'<button class="{cls}" onclick="reviewMoment(\'{moment_id}\', \'{target}\')">{label}</button> '
            moment_cards += f"""
            <div class="moment-card">
              <div class="moment-minute">{_escape(str(minute))}'</div>
              <h4>{_escape(participant)}</h4>
              <p>{_escape(moment.get('sport_event_type', 'Moment').replace('_', ' ').title())} · {_escape(moment.get('team') or '—')}</p>
              <p class="muted">{_format_moment_time(moment.get('peak_seconds'))} source · {('Available' if availability == 'AVAILABLE' else 'Needs review')}</p>
              <div class="card-actions">{preview_action}<details class="inline-details"><summary>More</summary><div class="form-actions">{actions}</div><p class="muted">Alignment: {_escape(alignment)} · Importance: {_format_score(moment.get('importance'))}</p></details></div>
            </div>
            """
            review_rows += f"""
            <tr>
              <td>{_format_moment_time(moment.get('start_seconds'))}</td>
              <td>{_escape(moment.get('universal_event_type', ''))}</td>
              <td>{_escape(moment.get('sport_event_type', ''))}</td>
              <td>{_escape(moment.get('team') or '—')}</td>
              <td>{_format_score(moment.get('importance'))}</td>
              <td>{_format_score(moment.get('confidence'))}</td>
              <td><span class="badge badge-moment">{_escape(current)}</span></td>
              <td>{actions}</td>
            </tr>
            """
        if review_rows:
            canonical_moments_section = f"""
            <div class="card" id="review-moments">
              <h3>Review Moments</h3>
              <p>{moment_summary.get('moment_count', 0)} moments · {moment_summary.get('reviewed_count', 0)} reviewed · {moment_summary.get('unreviewed_count', 0)} unreviewed · {moment_summary.get('strong_count', 0)} strong · {moment_summary.get('must_use_count', 0)} must use</p>
              <div class="form-actions">{filter_links}</div>
              <div class="moment-grid">{moment_cards}</div>
              <details class="advanced-details"><summary>Advanced Moment Table</summary><table>
                <thead><tr><th>Time</th><th>Event</th><th>Sport Type</th><th>Team</th><th>Importance</th><th>Confidence</th><th>Review</th><th>Actions</th></tr></thead>
                <tbody>{review_rows}</tbody>
              </table></details>
              {f'<details class="advanced-details"><summary>Additional Detected Signals ({len(legacy_moments)})</summary><p class="muted">Legacy transcript/detection moments are retained for reference but are not part of the active Research First editorial workflow.</p></details>' if legacy_moments else ''}
            </div>
            """
        elif moment_summary.get("moment_count", 0):
            alignment_cards = ""
            for moment in legacy_moments or []:
                meta = moment.get("metadata") or {}
                if meta.get("origin") != "research":
                    continue
                moment_id = _escape(moment.get("moment_id", ""))
                minute = meta.get("match_minute", "—")
                participant = next((p.get("name") for p in moment.get("participants") or [] if isinstance(p, dict) and p.get("name")), moment.get("team") or "—")
                availability = meta.get("availability_status", "UNKNOWN")
                alignment = meta.get("alignment_status", "UNALIGNED")
                if availability == "OUTSIDE_SOURCE":
                    label = "Not in this source"
                elif availability == "NOT_FOUND":
                    label = "Not found yet"
                elif alignment in {"ALIGNED", "VERIFIED"}:
                    label = "Located"
                else:
                    label = "Needs confirmation"
                preview_action = f'<a class="btn" href="/projects/{_escape(job_id)}/moments/{moment_id}/preview">Preview</a>' if availability == "AVAILABLE" else ''
                confirm_actions = "" if availability == "OUTSIDE_SOURCE" else f'<button class="btn btn-primary" onclick="confirmMomentAlignment(\'{moment_id}\', \'confirm\', this)">Yes, this is it</button><button class="btn" onclick="confirmMomentAlignment(\'{moment_id}\', \'earlier\', this)">Earlier</button><button class="btn" onclick="confirmMomentAlignment(\'{moment_id}\', \'later\', this)">Later</button><button class="btn" onclick="confirmMomentAlignment(\'{moment_id}\', \'not_found\', this)">Not found</button>'
                alignment_cards += f'<div class="moment-card"><div class="moment-minute">{_escape(str(minute))}\'</div><h4>{_escape(participant)}</h4><p>{_escape(label)} · {availability} / {alignment}</p><p class="muted">Estimated source {_format_moment_time(meta.get("estimated_media_time"))}</p><div class="card-actions">{preview_action}{confirm_actions}</div></div>'
            canonical_moments_section = f"""
            <div class="card" id="review-moments">
              <h3>Review Moments</h3>
              <p>{moment_summary.get('moment_count', 0)} moments · {moment_summary.get('reviewed_count', 0)} reviewed · {moment_summary.get('unreviewed_count', 0)} unreviewed · {moment_summary.get('strong_count', 0)} strong · {moment_summary.get('must_use_count', 0)} must use</p>
              <div class="form-actions">{filter_links}</div>
              {f'<div class="moment-grid">{alignment_cards}</div>' if alignment_cards else '<p class="muted">No moments match this review filter.</p>'}
            </div>
            """
        else:
            canonical_moments_section = """
            <div class="card" id="review-moments">
              <h3>Review Moments</h3>
              <p class="muted">No canonical moments are available for review yet.</p>
            </div>
            """

        if source_clock_notice:
            canonical_moments_section = source_clock_notice + canonical_moments_section
        elif source_clock_details:
            canonical_moments_section = canonical_moments_section.replace("</div>\n            ", f"{source_clock_details}</div>\n            ", 1)

        # Canonical story section
        story_summary = runtime.get("story_summary", {}) if isinstance(runtime.get("story_summary"), dict) else {}
        canonical_stories = runtime.get("stories", []) if isinstance(runtime.get("stories"), list) else []
        canonical_stories_section = ""
        if canonical_stories:
            story_cards = ""
            for story in canonical_stories:
                duration = story.get("estimated_duration")
                duration_text = f"{duration}s" if duration else "—"
                formats = story.get("recommended_formats") or []
                sid = _escape(story.get("story_id", ""))
                status = story.get("status", "SUGGESTED")
                actions = ""
                if status == "SUGGESTED":
                    actions += f'<button class="btn" onclick="storyReview(\'{sid}\', \'APPROVED\')">Approve</button> '
                    actions += f'<button class="btn" onclick="storyReview(\'{sid}\', \'REJECTED\')">Reject</button> '
                elif status == "APPROVED":
                    actions += f'<button class="btn" onclick="storyReview(\'{sid}\', \'ARCHIVED\')">Archive</button> '
                story_cards += f"""
                <div class="sport-card">
                  <strong><a href="/projects/{_escape(job_id)}/stories/{sid}">{_escape(story.get('title', ''))}</a></strong>
                  <br><span class="muted">{_escape(story.get('archetype', '—'))} · <span class="badge badge-moment">{_escape(status)}</span> · {_escape(story.get('moment_count', 0))} moments · {duration_text}</span>
                  <div class="form-actions">{actions}</div>
                </div>
                """
            canonical_stories_section = f"""
            <div class="card">
              <h3>Canonical Stories ({story_summary.get('story_count', len(canonical_stories))})</h3>
              <p>{story_summary.get('suggested_count', 0)} suggested · {story_summary.get('approved_count', 0)} approved · {story_summary.get('rejected_count', 0)} rejected</p>
              <div class="story-grid">{story_cards}</div>
            </div>
            """

        # Legacy raw moments section
        moments_section = ""
        if moments:
            moment_rows = ""
            for m in moments:
                cat = m.get("category", "UNSORTED")
                start = m.get("start_time", "")
                end = m.get("end_time", "")
                score = m.get("virality_score", "")
                caption = m.get("caption", "")
                status = m.get("status", "")
                moment_rows += f"""
                <tr>
                  <td><span class="badge badge-moment">{cat}</span></td>
                  <td>{start}</td>
                  <td>{end}</td>
                  <td>{score}</td>
                  <td>{caption[:60] if caption else ''}</td>
                  <td>{status}</td>
                </tr>
                """
            moments_section = f"""
            <details class="card advanced-details">
              <summary>Legacy Analysis ({len(moments)} detected signals)</summary>
              <p class="muted">Additional transcript/detection output retained for reference. Not part of the active Research First edit by default.</p>
              <table>
                <thead><tr><th>Category</th><th>Start</th><th>End</th><th>Score</th><th>Caption</th><th>Status</th></tr></thead>
                <tbody>{moment_rows}</tbody>
              </table>
            </details>
            """

        # Stories section
        story_status_val = story_status.get("story_status", "")
        story_count = story_status.get("story_count", 0)
        story_error = story_status.get("story_error", "")
        analysis_complete = analysis.get("analysis_status") == "COMPLETE"
        moments_exist = len(moments) > 0
        can_generate_stories = analysis_complete and moments_exist and story_status_val not in ("RUNNING",)

        stories_section = ""
        if story_status_val == "RUNNING":
            stories_section = f"""
            <div class="card">
              <h3>Stories</h3>
              <div class="analysis-running">
                <div class="spinner"></div>
                <span>Building stories...</span>
              </div>
            </div>
            """
        elif story_status_val == "COMPLETE" and stories:
            story_cards = ""
            for s in stories:
                sid = s.get("story_id", "")
                arch = s.get("archetype", "")
                title = s.get("title", "")
                summary = s.get("summary", "")
                hook = s.get("hook", "")
                m_ids = s.get("moment_ids", [])
                duration = s.get("estimated_duration", 0)
                arc = s.get("emotional_arc", [])
                formats = s.get("recommended_formats", [])
                narrative = s.get("narrative_roles", {})

                arc_str = " → ".join(arc) if arc else "—"
                fmt_str = ", ".join(formats) if formats else "—"

                # Build narrative roles display
                narrative_html = ""
                for role in ["HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"]:
                    role_mids = narrative.get(role, [])
                    if role_mids:
                        narrative_html += f'<div><strong>{role}:</strong> {", ".join(role_mids)}</div>'

                # Check for existing edit briefs
                existing_briefs = list_all_briefs(job_id, story_id=sid)
                briefs_html = ""
                for eb in existing_briefs:
                    eb_fmt = eb.get("format", "")
                    eb_intent = eb.get("editorial_intent", "")
                    eb_beats = eb.get("beats", [])
                    eb_arc = eb.get("emotional_arc", [])
                    eb_arc_str = " → ".join(eb_arc) if eb_arc else "—"
                    beats_summary = f"{len(eb_beats)} beats" if eb_beats else ""

                    beat_rows = ""
                    for b in eb_beats:
                        b_role = b.get("role", "")
                        b_mid = b.get("moment_id", "")
                        b_dir = b.get("direction", "")
                        b_pacing = b.get("pacing", "")
                        b_audio = b.get("audio_strategy", "")
                        b_transition = b.get("transition_intent", "")
                        beat_rows += f"""
                        <tr>
                          <td><strong>{b_role}</strong></td>
                          <td>{b_mid}</td>
                          <td>{b_dir[:50]}</td>
                          <td>{b_pacing}</td>
                          <td>{b_audio}</td>
                          <td>{b_transition}</td>
                        </tr>
                        """

                    # Check for existing EDL for this brief
                    existing_edl = get_edl(job_id, sid, eb_fmt)
                    edl_html = ""
                    if existing_edl:
                        edl_segs = existing_edl.get("segments", [])
                        edl_dur = existing_edl.get("timeline_duration", 0)
                        edl_rows = ""
                        for seg in edl_segs:
                            seg_id = seg.get("segment_id", "")
                            tl_s = seg.get("timeline_start", 0)
                            tl_e = seg.get("timeline_end", 0)
                            src_s = seg.get("source_start", 0)
                            src_e = seg.get("source_end", 0)
                            role = seg.get("story_role", "")
                            direction = seg.get("editorial_direction", "")
                            pacing = seg.get("pacing", "")
                            audio = seg.get("audio_strategy", "")
                            t_in = seg.get("transition_in", "")
                            from pipeline.edl import _fmt_timestamp
                            edl_rows += f"""
                            <tr>
                              <td>{_fmt_timestamp(tl_s)} — {_fmt_timestamp(tl_e)}</td>
                              <td><strong>{role}</strong></td>
                              <td>{direction[:40]}</td>
                              <td>{_fmt_timestamp(src_s)} — {_fmt_timestamp(src_e)}</td>
                              <td>{pacing}</td>
                              <td>{audio}</td>
                              <td>{t_in}</td>
                            </tr>
                            """
                        edl_html = f"""
                        <div style="margin-top:0.75rem; padding:0.5rem; border:1px solid var(--accent); border-radius:4px; background:rgba(255,255,255,0.02);">
                          <h5>EDIT TIMELINE · {eb_fmt} · {edl_dur:.1f}s</h5>
                          <table style="font-size:0.8rem;">
                            <thead><tr><th>Timeline</th><th>Role</th><th>Direction</th><th>Source</th><th>Pacing</th><th>Audio</th><th>Transition</th></tr></thead>
                            <tbody>{edl_rows}</tbody>
                          </table>
                        </div>
                        """
                        edl_btn = f'<button class="btn" onclick="doBuildEdl(\'{sid}\', \'{eb_fmt}\')">Rebuild Timeline</button>'
                    else:
                        edl_btn = f'<button class="btn btn-primary" onclick="doBuildEdl(\'{sid}\', \'{eb_fmt}\')">Build Timeline</button>'

                    briefs_html += f"""
                    <div style="margin-top:1rem; padding:0.75rem; border:1px solid var(--border); border-radius:4px;">
                      <h5>{eb_fmt} · {beats_summary}</h5>
                      <p style="margin:0.25rem 0;"><em>{eb_intent[:100]}</em></p>
                      <div style="margin:0.25rem 0;"><strong>Arc:</strong> {eb_arc_str}</div>
                      {"<table style='font-size:0.85rem;'><thead><tr><th>Role</th><th>Moment</th><th>Direction</th><th>Pacing</th><th>Audio</th><th>Transition</th></tr></thead><tbody>" + beat_rows + "</tbody></table>" if beat_rows else ''}
                      <div class="form-actions" style="margin-top:0.5rem;">
                        {edl_btn}
                      </div>
                      {edl_html}
                      {self._render_section(job_id, sid, eb_fmt, existing_edl)}
                    </div>
                    """

                # Build Edit Brief buttons for each recommended format
                brief_buttons = ""
                for fmt in formats:
                    brief_buttons += f'<button class="btn" onclick="doGenerateBrief(\'{sid}\', \'{fmt}\')">Build {fmt} Brief</button> '

                story_cards += f"""
                <div class="card" style="margin-top:1rem; border-left: 3px solid var(--accent);">
                  <h4>{title}</h4>
                  <div class="detail-grid">
                    <div><span class="badge badge-moment">{arch}</span></div>
                    <div>{len(m_ids)} moments · ~{duration}s</div>
                    <div>{fmt_str}</div>
                  </div>
                  <p style="margin:0.5rem 0;">{summary}</p>
                  <p style="margin:0.5rem 0;"><em>{hook}</em></p>
                  <div style="margin:0.5rem 0;">
                    <strong>Emotional arc:</strong> {arc_str}
                  </div>
                  {f'<div style="margin:0.5rem 0;"><strong>Narrative roles:</strong>{narrative_html}</div>' if narrative_html else ''}
                  {briefs_html}
                  <div class="form-actions" style="margin-top:0.75rem;">
                    {brief_buttons}
                  </div>
                </div>
                """

            stories_section = f"""
            <div class="card">
              <h3>Stories ({len(stories)})</h3>
              {story_cards}
              <div class="form-actions" style="margin-top:1rem;">
                <button class="btn" onclick="doGenerateStories()">Regenerate Stories</button>
              </div>
            </div>
            """
        elif story_status_val == "FAILED" or story_status_val == "NEEDS ATTENTION":
            safe_story_error = story_error or "Story generation needs attention."
            stories_section = f"""
            <div class="card">
              <h3>Stories</h3>
              <div class="analysis-failed">
                <span class="not-ready">{safe_story_error}</span>
                <details><summary>Details</summary><pre>{story_error}</pre></details>
              </div>
              <div class="form-actions" style="margin-top:1rem;">
                <button class="btn btn-primary" onclick="doGenerateStories()">Try Again</button>
              </div>
            </div>
            """
        elif analysis_complete and moments_exist:
            stories_section = f"""
            <div class="card">
              <h3>Stories</h3>
              <p class="muted">Not generated yet.</p>
              <div class="form-actions">
                <button class="btn btn-primary" onclick="doGenerateStories()">Generate Stories</button>
              </div>
            </div>
            """

        latest_research = research_items[-1] if research_items else {}
        header_identity = _resolve_project_display_identity(
            {"job_id": job_id, "pilot_id": detail.get("pilot_id") or display_name, "source_id": detail.get("source_id") or "", "display_name": display_name},
            research_data,
            intake_data,
            [str(a.get("path") or "") for a in source_artifacts],
        )
        workspace_title = header_identity.get("title") or (f"{latest_research.get('home_team')} vs {latest_research.get('away_team')}" if latest_research else display_name)
        competition_line = " · ".join(part for part in [str(header_identity.get("competition") or ""), str(header_identity.get("year") or ""), str(header_identity.get("stage") or "")] if part)
        strategy = (runtime.get("project") or {}).get("analysis_strategy") or "TRANSCRIPT_FIRST"
        workflow_runtime, _workflow_research_data = ConsoleHandler._workflow_runtime(detail, analysis)
        creative_state = resolve_workflow_state(detail, analysis=analysis, runtime=workflow_runtime)
        workflow_payload = creative_state.to_dict()
        source_name = Path(source_file).name if source_file else "—"
        coverage_text = "Kickoff → ~46:39" if "46:" in source_coverage else "—"
        featured_story = _select_primary_creative_story(job_id, canonical_stories) if canonical_stories else {}
        has_creative_story = bool(featured_story or stories)
        has_usable_moments = bool(canonical_moments)
        story_ready_moments = len(canonical_moments) >= 2
        execution_ready = bool(readiness.get("execution_ready"))
        if has_creative_story:
            story_title = _story_display_title(featured_story.get("title") or (stories[0].get("title") if stories else "Story Found"))
        elif not execution_ready:
            story_title = "Setup Needs Attention"
        elif analysis_complete:
            story_title = "No Story Yet"
        else:
            story_title = "Ready For Analysis"
        story_words = story_title.split()
        story_hero_line = "<br>".join(_escape(word.upper()) for word in story_words) if story_words else "STORY FOUND"
        story_href = f"/projects/{_escape(job_id)}/stories/{_escape(featured_story.get('story_id') or (stories[0].get('story_id') if stories else ''))}" if (featured_story or stories) else f"/projects/{_escape(job_id)}#stories"
        active_count = len(canonical_moments)
        rough_render = None
        try:
            from pipeline.runtime_service import list_project_artifacts, list_project_renders, list_story_edit_plans
            project_renders = list_project_renders(job_id)
            for render in project_renders:
                if (render.metadata or {}).get("preview") and render.artifact_id:
                    rough_render = render
                    break
        except Exception:
            project_renders = []
        rough_video = ""
        rough_meta = "34 sec · vertical"
        if rough_render:
            rough_meta = f"{int(round(float(rough_render.duration_seconds or 0)))} sec · {_escape(str(rough_render.width or '1080'))}x{_escape(str(rough_render.height or '1920'))}"
            rough_video = f'<video class="rough-video" controls src="/render_video/{_escape(job_id)}/{_escape(rough_render.render_id)}"></video>'
        else:
            rough_video = '<div class="rough-placeholder"><span>Rough cut will appear here</span></div>'
        if rough_render:
            rough_cta = '<button class="btn btn-primary" type="button" onclick="watchRoughCut()">Watch Rough Cut</button>'
        elif not execution_ready:
            rough_cta = '<button class="btn btn-primary" type="button" onclick="smoothFocus(\'#advanced-details\')">Review Setup</button>'
        elif research_items and not has_usable_moments:
            rough_cta = '<button class="btn btn-primary" type="button" onclick="smoothFocus(\'#research\')">Review Alignment</button>'
        elif not has_usable_moments:
            rough_cta = '<button class="btn btn-primary" onclick="executeProjectAction(\'retry_analysis\', this)">Run Analysis</button>'
        elif not has_creative_story:
            rough_cta = '<button class="btn btn-primary" onclick="executeProjectAction(\'find_story\', this)">Find Story</button>'
        else:
            rough_cta = '<button class="btn btn-primary" onclick="executeProjectAction(\'build_cut\', this)">Build Cut</button>'
        if is_demo_mode() and "button" in rough_cta:
            rough_cta = _demo_disabled_button("Hosted Demo Read-Only")
        story_steps = ""
        available_story_events = [event for event in research_events if (event.get("source_availability") or {}).get("availability_status") == "AVAILABLE"][:2]
        if not available_story_events:
            available_story_events = research_events[:2]
        for event in available_story_events:
            display_minute = (event.get("metadata") or {}).get("display_minute") or f"{event.get('match_minute', '—')}'"
            participant = next((p.get("name") for p in event.get("participants") or [] if isinstance(p, dict) and p.get("name")), event.get("team") or "")
            label = _creative_event_label(event.get("universal_event_type"), event.get("headline"))
            story_steps += f'<div class="story-step"><span>{_escape(display_minute)}</span><strong>{_escape(label.upper())}</strong><small>{_escape(participant)}</small></div><div class="story-arrow">↓</div>'
        if available_story_events:
            story_steps += '<div class="story-step"><span></span><strong>CELEBRATION</strong><small>Emotional release</small></div>'
        moment_tiles = ""
        try:
            from pipeline.runtime_service import get_project as _get_runtime_project
            runtime_project = _get_runtime_project(job_id)
            project_source_artifact_id = runtime_project.source_artifact_id if runtime_project else None
        except Exception:
            project_source_artifact_id = None
        for moment in canonical_moments[:6]:
            meta = moment.get("metadata") or {}
            minute = meta.get("match_minute", "—")
            participant = next((p.get("name") for p in moment.get("participants") or [] if isinstance(p, dict) and p.get("name")), moment.get("team") or "—")
            label = _creative_event_label(moment.get("universal_event_type"), moment.get("sport_event_type"))
            if str(minute) == "20":
                label = "First Strike"
            elif str(minute) == "36":
                label = "The Dagger"
            elif str(minute) == "23":
                label = "Maniche Strikes"
            elif str(minute) == "46":
                label = "Portugal Down To 10"
            elif str(minute) == "63":
                label = "Netherlands Down To 10"
            elif str(minute) == "78":
                label = "Deco Sent Off"
            elif str(minute) == "95":
                label = "One More Red"
            source_ts = _format_moment_time(moment.get("peak_seconds"))
            moment_obj = type("_MomentPreview", (), {
                "metadata": meta,
                "universal_event_type": moment.get("universal_event_type"),
                "start_seconds": moment.get("start_seconds"),
                "end_seconds": moment.get("end_seconds"),
            })()
            preview_window = _moment_preview_window(job_id, moment_obj)
            seek = float(preview_window.get("source_in") or moment.get("start_seconds") or 0)
            source_artifact_id = moment.get("source_artifact_id") or project_source_artifact_id or ""
            video_url = f"/source_video/{_escape(job_id)}/{_escape(source_artifact_id)}" if source_artifact_id else ""
            desc = "Italy takes the lead" if str(minute) == "20" else ("The semifinal turns" if str(minute) == "36" else "Research-verified match moment")
            moment_tiles += f"""
            <button class="magic-moment moment-preview-trigger" type="button" data-title="{_escape(label.upper())}" data-minute="{_escape(str(minute))}'" data-description="{_escape(desc)}" data-video-url="{video_url}" data-seek="{seek:.3f}">
              <div class="magic-minute">{_escape(str(minute))}'</div>
              <div><strong>{_escape(label.upper())}</strong><span>{_escape(participant)}</span><small>{_escape(source_ts)} source</small></div>
              <span class="play-chip">Play</span>
            </button>
            """
        unavailable_tiles = ""
        for event in research_events:
            availability = (event.get("source_availability") or {}).get("availability_status")
            if availability != "OUTSIDE_SOURCE":
                continue
            display_minute = (event.get("metadata") or {}).get("display_minute") or f"{event.get('match_minute', '—')}'"
            participant = next((p.get("name") for p in event.get("participants") or [] if isinstance(p, dict) and p.get("name")), event.get("team") or "—")
            label = _creative_event_label(event.get("universal_event_type"), event.get("headline"))
            unavailable_tiles += f'<article class="magic-moment unavailable"><div class="magic-minute">{_escape(display_minute)}</div><div><strong>{_escape(label.upper())}</strong><span>{_escape(participant)}</span><small>Not available in this source</small></div></article>'
        try:
            edit_plans_for_project = list_story_edit_plans(featured_story.get("story_id")) if featured_story.get("story_id") else []
        except Exception:
            edit_plans_for_project = []
        transform_html = ""
        edit_timeline_html = ""
        intelligence_items = []
        if edit_plans_for_project:
            plan = edit_plans_for_project[0]
            report = get_editplan_execution_report(plan.edit_plan_id)
            quality = report.get("quality_report") or []
            try:
                from pipeline.runtime_service import list_timeline_instructions
                instructions = list_timeline_instructions(plan.edit_plan_id)
            except Exception:
                instructions = []
            starts_by_index = [float(instruction.timeline_start or 0) for instruction in instructions]
            story_list = "".join(f'<li>{_escape((event.get("metadata") or {}).get("display_minute") or str(event.get("match_minute") or "—") + "′")} {_escape(_creative_event_label(event.get("universal_event_type"), event.get("headline")))}</li>' for event in available_story_events)
            cut_list = ""
            for index, row in enumerate(quality[:6]):
                from pipeline.composition_service import human_composition_label
                start = starts_by_index[index] if index < len(starts_by_index) else sum(float(prev.get("duration") or 0) for prev in quality[:index])
                role = row.get("narrative_role") or "BEAT"
                beat_text = row.get("text") or row.get("source_window") or "Match footage"
                beat_text = str(beat_text)
                composition_label = human_composition_label(str(row.get("composition_mode") or ""))
                cut_list += f'<li><button class="cut-beat" type="button" data-seek="{start:.3f}"><strong>{_escape(role)}</strong><span>{_escape(beat_text)}</span><small>{_escape(composition_label)}</small></button></li>'
                duration = float(row.get("duration") or 1)
                edit_timeline_html += f'<button class="premium-timeline-segment cut-beat" type="button" data-seek="{start:.3f}" data-duration="{duration:.3f}" style="flex:{max(duration, 1)}"><strong>{_escape(role)}</strong><span>{_format_moment_time(start)}</span><small>{_escape(composition_label)}</small></button>'
            transform_html = f'<section class="cinema-section transformation" id="cut"><div><div class="section-kicker">The Match</div><ul>{story_list}</ul></div><div class="magic-cut-arrow">→</div><div><div class="section-kicker">The Cut</div><ul>{cut_list}</ul></div></section>'
            roles = [str(row.get("narrative_role") or "") for row in quality]
            if roles and roles[0] == "HOOK":
                intelligence_items.append("Immediate hook opens the cut")
            if len(canonical_moments) >= 2:
                intelligence_items.append("Two usable moments create escalation")
            if any(role in {"CLIMAX", "FINISH", "AFTERMATH"} for role in roles):
                intelligence_items.append("Clear emotional payoff")
            if plan.target_duration and float(plan.target_duration) <= 45:
                intelligence_items.append("Ideal short-form runtime")
        if intelligence_items:
            intelligence_html = "".join(f"<li>{_escape(item)}</li>" for item in intelligence_items)
        elif rough_render or edit_plans_for_project:
            intelligence_html = "<li>Story structure is ready for review</li>"
        elif story_ready_moments and not (rough_render or edit_plans_for_project or has_creative_story):
            intelligence_html = "<li>Usable moments are ready for story building</li>"
        else:
            intelligence_html = "<li>Analysis must produce usable moments before Clipper can build a cut</li>"
        coverage_section = _source_timeline_html(research_events, research_coverage)
        try:
            from pipeline.runtime_service import list_project_artifacts as _list_project_artifacts
            handoffs = _list_project_artifacts(job_id, artifact_type="chatcut_handoff")
        except Exception:
            handoffs = []
        package_link = "#advanced-details"
        package_meta = "Build a cut to generate the ChatCut handoff package."
        if handoffs:
            plan_id = (handoffs[-1].metadata or {}).get("edit_plan_id")
            if plan_id:
                package_link = f"/edit-plans/{_escape(plan_id)}/handoff"
            package_meta = "Creative package ready · ChatCut-ready"
        package_heading = "ChatCut-ready" if handoffs else "Creative package pending"
        package_kicker = "Creative Package Ready" if handoffs else "Creative Package Pending"
        package_button = "Open Creative Package" if handoffs else "Build Cut First"
        found_pill = "Clipper found a story" if has_creative_story else ("Setup needs attention" if not execution_ready else "Waiting for analysis")
        hero_status = "Rough cut ready" if rough_render else ("Cut ready to build" if has_creative_story and has_usable_moments else "Cut not ready yet")
        workspace_header = f"""
        <section class="cinematic-hero">
          <div class="hero-copy">
            <p class="eyebrow">{_escape(competition_line or strategy.replace('_', ' ').title())}</p>
            <h1>{_escape(workspace_title.upper())}</h1>
            <p class="muted">Source: {_escape(source_name)}</p>
            <div class="found-pill">{_escape(found_pill)}</div>
            <h2>{story_hero_line}</h2>
            <p class="hero-meta">{_escape(rough_meta)} · {active_count} usable moments · {_escape(hero_status)}</p>
            <div class="hero-actions"><button class="btn" type="button" onclick="smoothFocus('#advanced-details')">Details</button></div>
          </div>
          <div class="hero-orb"><span>AI</span><small>Story Engine</small></div>
        </section>
        """

        creator_progress_items = [
            ("Match", bool(research_items) or workflow_payload.get("state") not in {"SOURCE_READY", "IDENTIFYING"}),
            ("Research", bool(research_events)),
            ("Moments", story_ready_moments),
            ("Story", has_creative_story),
            ("Cut", bool(edit_plans_for_project)),
            ("Watch", bool(rough_render)),
        ]
        current_seen = False
        creator_progress_bits = []
        for label, done in creator_progress_items:
            symbol = "✓" if done else ("●" if not current_seen else "○")
            if not done:
                current_seen = True
            creator_progress_bits.append(f'<span>{symbol} {_escape(label)}</span>')
        creator_progress = "".join(creator_progress_bits)
        creator_status = "Your rough cut is ready" if rough_render else ("Your cut is ready" if edit_plans_for_project else ("Clipper is building your story" if story_status_val == "RUNNING" else ("Clipper found a story" if has_creative_story else ("I need one quick check" if source_clock.get("status") == "NEEDS_OPERATOR" else ("Finding the best moments" if workflow_payload.get("active") else "Ready to start")))))
        creator_context = f"""
        <header class="creator-context">
          <p class="eyebrow">{_escape(competition_line or 'Creator flow')}</p>
          <h1>{_escape(workspace_title.upper())}</h1>
          <p class="muted">{_escape(creator_status)}</p>
          <div class="creator-progress-line" aria-label="Progress">{creator_progress}</div>
          <div id="creator-action-error" class="creator-action-error" hidden></div>
        </header>
        """
        def _review_sort_key(moment: dict) -> tuple[float, str]:
            meta = moment.get("metadata") or {}
            return ((meta.get("estimated_match_seconds") if meta.get("estimated_match_seconds") is not None else float(meta.get("match_minute") or 999) * 60.0), moment.get("moment_id") or "")
        review_queue_moments = sorted(
            [m for m in [*(canonical_moments or []), *(legacy_moments or [])] if (m.get("metadata") or {}).get("availability_status") == "AVAILABLE" and (m.get("metadata") or {}).get("alignment_status") in {"ESTIMATED", "ALIGNED", "VERIFIED"}],
            key=_review_sort_key,
        )
        reviewable_moments = sorted(
            [m for m in review_queue_moments if (m.get("metadata") or {}).get("alignment_status") == "ESTIMATED"],
            key=_review_sort_key,
        )
        reviewable_moment = reviewable_moments[0] if reviewable_moments else None
        creator_task = ""
        if source_clock.get("status") == "NEEDS_OPERATOR":
            review = source_clock.get("review") or {}
            review_segment = str(review.get("segment_type") or "FIRST_HALF")
            second_half_review = review_segment == "SECOND_HALF"
            cursor = float(review.get("cursor_seconds") or 0.0)
            preview_start = max(0.0, cursor - 30.0)
            media_url = f"/source_video/{_escape(job_id)}/{_escape((source_artifact or {}).get('artifact_id') or '')}"
            source_id = _escape((source_artifact or {}).get('artifact_id') or '')
            review_title = "Help Clipper find the second-half start" if second_half_review else "Help Clipper find kickoff"
            confirm_label = "Yes, this is the second-half start" if second_half_review else "Yes, this is kickoff"
            creator_task = f"""
            <section class="creator-task creator-review" id="source-clock-review">
              <p class="eyebrow">One quick check</p>
              <h2>{review_title}</h2>
              <p class="muted">I need this once so I can place moments accurately.</p>
              <video id="kickoffPreview" controls preload="metadata" src="{media_url}" style="width:100%;max-height:52vh;border-radius:8px;"></video>
              <p class="muted">Preview starts near <span id="kickoffReviewCursor">{_format_moment_time(cursor)}</span></p>
              <div class="form-actions"><button class="btn" onclick="reviewKickoff('earlier', this, '{_escape(review_segment)}', '{source_id}')">Earlier</button><button class="btn btn-primary" onclick="reviewKickoff('confirm', this, '{_escape(review_segment)}', '{source_id}')">{confirm_label}</button><button class="btn" onclick="reviewKickoff('later', this, '{_escape(review_segment)}', '{source_id}')">Later</button></div>
              <script>setTimeout(() => {{ const v = document.getElementById('kickoffPreview'); if (v) v.currentTime = {preview_start:.3f}; }}, 100);</script>
            </section>
            """
        elif story_status_val == "RUNNING":
            creator_task = f"""
            <section class="creator-task creator-processing">
              <p class="eyebrow">Clipper is working</p>
              <h2>Building your story</h2>
              <p class="muted">{active_count} confirmed moments are being assembled into a narrative.</p>
              <div class="creator-progress">{creator_progress}</div>
            </section>
            """
        elif reviewable_moment:
            meta = reviewable_moment.get("metadata") or {}
            moment_id_raw = reviewable_moment.get("moment_id") or ""
            mid = _escape(moment_id_raw)
            minute = meta.get("match_minute") or meta.get("event_position") or "—"
            title = _creative_event_label(reviewable_moment.get("universal_event_type"), reviewable_moment.get("sport_event_type"))
            participant = next((p.get("name") for p in reviewable_moment.get("participants") or [] if isinstance(p, dict) and p.get("name")), reviewable_moment.get("team") or "")
            source_artifact_id = reviewable_moment.get("source_artifact_id") or ""
            media_url = f"/source_video/{_escape(job_id)}/{_escape(source_artifact_id)}" if source_artifact_id else ""
            cursor = float(reviewable_moment.get("peak_seconds") or meta.get("review_cursor_seconds") or meta.get("refined_media_time") or meta.get("estimated_media_time") or 0.0)
            current_index = next((idx for idx, item in enumerate(review_queue_moments, start=1) if item.get("moment_id") == moment_id_raw), reviewable_moments.index(reviewable_moment) + 1)
            total_reviewable = len(review_queue_moments)
            creator_task = f"""
            <section class="creator-task creator-review" id="review-moments">
              <div class="creator-task-top"><p class="eyebrow">Check this moment</p><span>{current_index} of {total_reviewable}</span></div>
              <h2>{_escape(title)} · {minute}'</h2>
              {f'<p class="muted">{_escape(participant)}</p>' if participant else ''}
              <video id="momentReviewVideo" class="creator-review-video" controls preload="metadata" src="{media_url}" data-moment-id="{mid}" data-source-artifact-id="{_escape(source_artifact_id)}" data-cursor="{cursor:.3f}"></video>
              <div class="form-actions creator-review-actions"><button class="btn" data-action-label="Earlier" onclick="confirmMomentAlignment('{mid}', 'earlier', this)">Earlier</button><button class="btn btn-primary" data-action-label="Yes, that's it" onclick="confirmMomentAlignment('{mid}', 'confirm', this)">Yes, that's it</button><button class="btn" data-action-label="Later" onclick="confirmMomentAlignment('{mid}', 'later', this)">Later</button></div>
              <div class="form-actions creator-secondary-actions"><button class="btn btn-secondary" data-action-label="Not found" onclick="confirmMomentAlignment('{mid}', 'not_found', this)">Not found</button></div>
              <script>setTimeout(() => {{ const v = document.getElementById('momentReviewVideo'); if (v) v.currentTime = {cursor:.3f}; }}, 100);</script>
            </section>
            """
        elif story_ready_moments and not (rough_render or edit_plans_for_project or has_creative_story):
            creator_task = f"""
            <section class="creator-task creator-story" id="story-ready">
              <p class="eyebrow">Story ready</p>
              <h2>Build the story</h2>
              <p class="muted">{active_count} confirmed moments are ready.</p>
              <div class="form-actions"><button class="btn btn-primary" onclick="executeProjectAction('find_story', this)">Build Story</button></div>
            </section>
            """
        elif rough_render:
            creator_task = f"""
            <section class="creator-task creator-watch" id="rough-cut">
              <p class="eyebrow">Your rough cut is ready</p>
              <h2>{_escape(story_title)}</h2>
              <p class="muted">{_escape(rough_meta)} · vertical</p>
              <div class="vertical-player">{rough_video}</div>
              <div class="form-actions"><button class="btn btn-primary" type="button" onclick="executeProjectAction('finish_cut', this)">Finish</button></div>
            </section>
            """
        elif edit_plans_for_project:
            creator_task = f"""
            <section class="creator-task creator-cut" id="cut">
              <p class="eyebrow">Your cut is ready</p>
              <h2>{_escape(story_title)}</h2>
              <p class="muted">{_escape(rough_meta)} · vertical · {active_count} moments</p>
              <div class="premium-edit-timeline">{edit_timeline_html}</div>
              <div class="form-actions"><button class="btn btn-primary" onclick="executeProjectAction('render_rough_cut', this)">Generate Rough Cut</button></div>
            </section>
            """
        elif has_creative_story:
            creator_task = f"""
            <section class="creator-task creator-story" id="story">
              <p class="eyebrow">Your story is ready</p>
              <h2>{_escape(story_title)}</h2>
              <div class="story-steps">{story_steps or '<p class="muted">Clipper found a story from the confirmed moments.</p>'}</div>
              <div class="form-actions"><button class="btn btn-primary" onclick="executeProjectAction('build_cut', this)">Build Cut</button></div>
            </section>
            """
        elif workflow_payload.get("active"):
            creator_task = f"""
            <section class="creator-task creator-processing">
              <p class="eyebrow">Clipper is working</p>
              <h2>Analyzing your match</h2>
              <div class="creator-progress">{creator_progress}</div>
            </section>
            """
        elif not execution_ready:
            creator_task = """
            <section class="creator-task creator-review">
              <p class="eyebrow">Setup needed</p>
              <h2>Review setup</h2>
              <p class="muted">Clipper needs the source and rights to be ready before it starts.</p>
              <div class="form-actions"><button class="btn btn-primary" type="button" onclick="smoothFocus('#advanced-details')">Review Setup</button></div>
            </section>
            """
        elif active_count == 1:
            creator_task = """
            <section class="creator-task creator-review" id="review-moments">
              <p class="eyebrow">One more moment needed</p>
              <h2>I need one more confirmed moment before I can build the story.</h2>
            </section>
            """
        else:
            creator_task = f"""
            <section class="creator-task creator-processing">
              <p class="eyebrow">Ready</p>
              <h2>Start processing</h2>
              <p class="muted">Clipper will find the match, research it, and look for the best moments.</p>
              <div class="form-actions"><button class="btn btn-primary" onclick="executeProjectAction('retry_analysis', this)">Start</button></div>
            </section>
            """
        if is_demo_mode():
            creator_task = creator_task.replace(
                '<button class="btn btn-primary" onclick="executeProjectAction(\'render_rough_cut\', this)">Generate Rough Cut</button>',
                _demo_disabled_button("Generate Rough Cut"),
            ).replace(
                '<button class="btn btn-primary" onclick="executeProjectAction(\'build_cut\', this)">Build Cut</button>',
                _demo_disabled_button("Build Cut"),
            ).replace(
                '<button class="btn btn-primary" onclick="executeProjectAction(\'find_story\', this)">Build Story</button>',
                _demo_disabled_button("Build Story"),
            ).replace(
                '<button class="btn btn-primary" onclick="executeProjectAction(\'retry_analysis\', this)">Start</button>',
                _demo_disabled_button("Start"),
            )
            creator_task += _demo_unavailable_html()
        activity_panel = _creator_activity_html(events, research_events, source_artifacts, workflow_payload, story_status, active_count)
        simple_coverage = _creator_source_coverage_html(research_events, source_artifacts)
        creative_experience = f"""
        <main class="creator-flow" aria-label="Creator workflow">
          <section class="creator-command-center">
            <section class="creator-task-shell">
              {creator_context}
              {creator_task}
            </section>
            {activity_panel}
          </section>
          {simple_coverage}
        </main>
        """

        rail_symbols = {"COMPLETE": "✓", "CURRENT": "●", "LOCKED": "○"}
        rail_html = "".join(
            f'<span class="flow-rail-item workflow-{_escape(item.get("state", "LOCKED").lower())}"><span>{rail_symbols.get(item.get("state"), "○")}</span>{_escape(item.get("label", ""))}</span>'
            for item in workflow_payload.get("rail", [])
        )
        live_panel = ""
        if workflow_payload.get("confirmation_required"):
            ident = workflow_payload.get("identity") or {}
            matchup = f"{ident.get('team_a') or 'Argentina'} vs {ident.get('team_b') or 'Croatia'}"
            comp = " · ".join(str(x) for x in [ident.get("competition"), ident.get("stage")] if x)
            live_panel = f'''
            <section class="card live-workflow-panel action-required">
              <p class="eyebrow">Match Found</p>
              <h2>{_escape(matchup)}</h2>
              <p class="muted">{_escape(comp)}</p>
              <h3>Is this your match?</h3>
              <div class="form-actions"><button class="btn btn-primary" type="button" disabled title="Identity confirmation is not wired for this project yet">Yes, continue</button><button class="btn" type="button" disabled title="Identity correction is not wired for this project yet">Not this match</button></div>
            </section>
            '''
        elif workflow_payload.get("state") == "FAILED":
            live_panel = f'''
            <section class="card live-workflow-panel failed">
              <p class="eyebrow">Research couldn't complete</p>
              <h2>Workflow Failed</h2>
              <p class="muted">{_escape(workflow_payload.get('failure_reason') or 'Check Advanced Details for diagnostics.')}</p>
              <button class="btn btn-primary" type="button" onclick="retryResearch(this)">Retry Research</button>
            </section>
            '''
        elif workflow_payload.get("state") == "BLOCKED":
            live_panel = f'''
            <section class="card live-workflow-panel action-required">
              <p class="eyebrow">Action Required</p>
              <h2>{_escape(workflow_payload.get('blocked_reason') or 'Project needs attention')}</h2>
              <button class="btn btn-primary" type="button" onclick="smoothFocus('#advanced-details')">Review Setup</button>
            </section>
            '''
        elif workflow_payload.get("active"):
            stages = [
                ("Source ready", "Source ready" in workflow_payload.get("completed_stages", [])),
                ("Match identified", "Match identified" in workflow_payload.get("completed_stages", [])),
                ("Researching what happened", workflow_payload.get("state") not in {"IDENTIFYING", "RESEARCHING"}),
                ("Finding moments in your footage", workflow_payload.get("state") not in {"IDENTIFYING", "RESEARCHING", "ALIGNING"}),
                ("Building story", workflow_payload.get("state") not in {"IDENTIFYING", "RESEARCHING", "ALIGNING", "MOMENTS_READY", "STORY_BUILDING"}),
                ("Building cut", workflow_payload.get("state") in {"EDIT_READY", "RENDERING", "ROUGH_CUT_READY", "FINISH_READY"}),
            ]
            stage_rows = "".join(f'<li>{"✓" if done else ("●" if label.lower().startswith(str(workflow_payload.get("current_stage", "")).lower()) else "○")} {_escape(label)}</li>' for label, done in stages)
            live_panel = f'<section class="card live-workflow-panel"><p class="eyebrow">Clipper is working</p><ul>{stage_rows}</ul></section>'

        metadata_identity = _resolve_project_display_identity(
            {"job_id": job_id, "pilot_id": detail.get("pilot_id") or display_name, "source_id": detail.get("source_id") or "", "display_name": display_name},
            research_data,
            intake_data,
            [str(a.get("path") or "") for a in source_artifacts],
        )
        metadata_field_rows = "".join(
            f"<tr><td>{_escape(name)}</td><td>{_escape(field.get('value') or '—')}</td><td>{_escape(field.get('source') or '—')}</td><td>{_escape(field.get('trust') or '—')}</td><td>{_escape(field.get('original_value') or '—')}</td></tr>"
            for name, field in (metadata_identity.get("fields") or {}).items()
        )
        metadata_conflicts = metadata_identity.get("conflicts") or []
        metadata_conflict_html = "".join(
            f"<li><strong>{_escape(conflict.get('field'))}</strong>: {_escape(', '.join(v.get('value', '') for v in conflict.get('values', [])))}</li>"
            for conflict in metadata_conflicts
        )
        metadata_repair = (metadata_identity.get("repair") or {}).get("recommended", [])
        metadata_repair_html = "".join(
            f"<li>{_escape(item.get('field'))}: {_escape(item.get('normalized_value'))} <span class='muted'>{_escape(item.get('reason'))}</span></li>"
            for item in metadata_repair
        )
        metadata_diagnostics_html = f"""
            <section class="card compact-card">
              <h3>Metadata Diagnostics</h3>
              <p><strong>Resolved identity:</strong> {_escape(metadata_identity.get('title') or 'Untitled Match')}</p>
              <p><strong>Metadata health:</strong> {_escape(metadata_identity.get('health') or 'UNKNOWN')}</p>
              <table><thead><tr><th>Field</th><th>Resolved</th><th>Source</th><th>Trust</th><th>Original</th></tr></thead><tbody>{metadata_field_rows}</tbody></table>
              {f'<p class="muted">Conflicts:</p><ul>{metadata_conflict_html}</ul>' if metadata_conflict_html else '<p class="muted">No metadata conflicts detected.</p>'}
              {f'<p class="muted">Safe repair preview only:</p><ul>{metadata_repair_html}</ul>' if metadata_repair_html else '<p class="muted">No automatic repair recommended.</p>'}
            </section>
        """

        content = f"""
        {creative_experience}

        <details class="advanced-details card" id="advanced-details">
          <summary>Advanced Details</summary>
          <div class="advanced-stack">
            <section class="card compact-card">
              <div class="card-header">
                <div>
                  <h2>{_escape(display_name)}</h2>
                  <span class="muted">{_escape(detail.get('project_id', '').title())}</span>
                </div>
                <span class="badge badge-{state_class}">{_escape(state)}</span>
              </div>
              <div class="detail-grid">
                <div><strong>Source:</strong> {_escape(source_file) if source_file else '—'}</div>
                <div><strong>Source status:</strong> <span class="{source_status_class}">{source_status_text}</span></div>
                <div><strong>Transcript:</strong> <span class="{transcript_class}">{transcript_text}</span></div>
                <div><strong>Created:</strong> {detail.get('created_at', '—')[:19]}</div>
                <div><strong>Updated:</strong> {detail.get('updated_at', '—')[:19]}</div>
                {lineage_html}
              </div>
            </section>
            {workflow_html}
            <section class="card compact-card">
              <h3>Analysis</h3>
              {analysis_section}
              <div class="form-actions" style="margin-top:1rem;">{analyze_btn}</div>
            </section>
            {research_section}
            {metadata_diagnostics_html}
            {canonical_moments_section}
            {moments_section}
            {canonical_stories_section}
            {stories_section}
          </div>

          <h3>Advanced Project Controls</h3>
        <div class="grid">
          <section class="card">
            <h3>Actions</h3>
            <div class="transition-buttons">
              <button class="btn" onclick="doDuplicate()">Duplicate Project</button>
              {transition_buttons or '<span class="muted">No transitions available</span>'}
            </div>
          </section>
          <section class="card">
            <h3>Status</h3>
            <div class="detail-grid">
              <div><strong>Structurally valid:</strong> {readiness.get('structurally_valid', False)}</div>
              <div><strong>Config valid:</strong> {readiness.get('config_references_valid', False)}</div>
              <div><strong>Source ready:</strong> {readiness.get('source_ready', False)}</div>
              <div><strong>Rights cleared:</strong> {readiness.get('rights_cleared', False)}</div>
              <div><strong>Execution ready:</strong> {readiness.get('execution_ready', False)}</div>
            </div>
            {validation_issues}
          </section>
        </div>
          <details class="advanced-details card">
          <summary>Event History</summary>
          <table>
            <thead><tr><th>#</th><th>Type</th><th>Transition</th><th>Message</th><th>Time</th></tr></thead>
            <tbody>{event_rows or '<tr><td colspan="5" class="empty">No events yet.</td></tr>'}</tbody>
          </table>
        </details>
        </details>

        <a href="/" class="btn">← Back to Projects</a>

        <script>
        const roughVideo = document.querySelector('#rough-cut video');
        const beatButtons = Array.from(document.querySelectorAll('.cut-beat'));
        const initialWorkflowState = "{_escape(workflow_payload.get('state', ''))}";

        function smoothFocus(selector) {{
          const el = document.querySelector(selector);
          if (el) {{ el.scrollIntoView({{behavior: 'smooth', block: 'center'}}); }}
          return el;
        }}

        function seekRoughCut(seconds) {{
          if (!roughVideo) {{ return; }}
          const target = Number(seconds || 0);
          const doSeek = () => {{
            roughVideo.currentTime = target;
            roughVideo.focus();
            beatButtons.forEach(btn => btn.classList.toggle('active', Number(btn.dataset.seek || 0) === target));
          }};
          smoothFocus('#rough-cut');
          if (roughVideo.readyState >= 1) {{ doSeek(); }} else {{ roughVideo.addEventListener('loadedmetadata', doSeek, {{once: true}}); }}
        }}

        beatButtons.forEach(btn => btn.addEventListener('click', () => seekRoughCut(btn.dataset.seek)));
        if (roughVideo) {{
          roughVideo.addEventListener('timeupdate', () => {{
            const current = roughVideo.currentTime;
            let active = null;
            beatButtons.forEach(btn => {{
              const start = Number(btn.dataset.seek || 0);
              const duration = Number(btn.dataset.duration || 4);
              if (current >= start && current < start + duration) {{ active = btn; }}
            }});
            beatButtons.forEach(btn => btn.classList.toggle('active', btn === active));
          }});
        }}

        async function pollWorkflowState() {{
          try {{
            const resp = await fetch("/projects/{job_id}/workflow-state", {{cache: "no-store"}});
            if (!resp.ok) {{ return; }}
            const state = await resp.json();
            if (state.state && state.state !== initialWorkflowState) {{ window.location.reload(); return; }}
            if (state.terminal || state.confirmation_required || state.state === "BLOCKED" || state.state === "FAILED") {{ return; }}
            window.setTimeout(pollWorkflowState, 1500);
          }} catch (_err) {{ window.setTimeout(pollWorkflowState, 3000); }}
        }}
        if ({str(bool(workflow_payload.get('active'))).lower()}) {{ window.setTimeout(pollWorkflowState, 1500); }}

        const actionLabels = {{
          find_story: 'Building story...', build_cut: 'Building cut...', render_rough_cut: 'Rendering rough cut...',
          retry_research: 'Retrying research...', retry_analysis: 'Retrying analysis...', seed_moments: 'Seeding moments...',
          confirm_kickoff: 'Confirming...', shift_kickoff_earlier: 'Moving preview...', shift_kickoff_later: 'Moving preview...',
          confirm_moment: 'Confirming...', shift_moment_earlier: 'Moving...', shift_moment_later: 'Moving...', mark_moment_not_found: 'Saving...', finish_cut: 'Finishing cut...'
        }};
        function showCreatorError(message, details) {{
          const box = document.getElementById('creator-action-error');
          if (!box) return;
          box.hidden = false;
          box.innerHTML = `<strong>Clipper couldn't continue.</strong><span>${{message || 'Try again.'}}</span>${{details ? `<details><summary>Advanced Details</summary><pre>${{String(details).replace(/[&<>]/g, ch => ({{'&':'&amp;','<':'&lt;','>':'&gt;'}}[ch]))}}</pre></details>` : ''}}`;
        }}
        function clearCreatorError() {{ const box = document.getElementById('creator-action-error'); if (box) {{ box.hidden = true; box.innerHTML = ''; }} }}
        function resetButton(btn, original) {{ if (btn) {{ btn.disabled = false; btn.textContent = original || btn.getAttribute('data-action-label') || btn.textContent; }} }}
        function seekVideo(video, cursor) {{
          if (!video || !Number.isFinite(cursor)) return;
          const doSeek = () => {{ video.currentTime = cursor; video.dataset.cursor = String(cursor); }};
          if (video.readyState >= 1) {{ doSeek(); }} else {{ video.addEventListener('loadedmetadata', doSeek, {{once: true}}); }}
        }}
        function actionFetch(url, options, timeoutMs) {{
          const controller = new AbortController();
          const timeout = window.setTimeout(() => controller.abort(), timeoutMs || 12000);
          return fetch(url, {{...(options || {{}}), signal: controller.signal}}).finally(() => window.clearTimeout(timeout));
        }}
        function actionTimeoutMs(action) {{
          if (action === 'render_rough_cut') return 600000;
          if (action === 'find_story' || action === 'build_cut') return 300000;
          if (action === 'finish_cut') return 60000;
          return 12000;
        }}
        function handleKickoffActionSuccess(action, result, btn, original) {{
          if (action !== 'shift_kickoff_earlier' && action !== 'shift_kickoff_later') return false;
          const data = result && (result.data || result.result || {{}});
          const cursor = Number(data.review_cursor ?? data.cursor_seconds);
          const video = document.getElementById('kickoffPreview');
          seekVideo(video, cursor);
          const cursorText = document.getElementById('kickoffReviewCursor');
          if (cursorText && Number.isFinite(cursor)) cursorText.textContent = data.formatted_cursor || new Date(cursor * 1000).toISOString().substring(14, 19).replace(/^0/, '');
          resetButton(btn, original);
          return true;
        }}
        function handleMomentActionSuccess(action, result, btn, original) {{
          if (action === 'shift_moment_earlier' || action === 'shift_moment_later') {{
            const data = result && (result.data || result.moment || {{}});
            const cursor = Number(data.review_cursor ?? data.cursor_seconds);
            const video = document.getElementById('momentReviewVideo');
            seekVideo(video, cursor);
            const cursorText = document.getElementById('momentReviewCursor');
            if (cursorText && Number.isFinite(cursor)) cursorText.textContent = data.formatted_cursor || new Date(cursor * 1000).toISOString().substring(14, 19);
            resetButton(btn, original);
            return true;
          }}
          return false;
        }}
        async function executeProjectAction(action, btn, payload) {{
          clearCreatorError();
          const original = btn ? (btn.getAttribute('data-action-label') || btn.textContent) : '';
          if (btn) {{ btn.disabled = true; btn.textContent = actionLabels[action] || 'Working...'; }}
          try {{
            const resp = await actionFetch(`/api/projects/{job_id}/actions/${{action}}`, {{
              method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify(payload || {{}}),
            }}, actionTimeoutMs(action));
            const result = await resp.json().catch(() => ({{ok:false, error:'Invalid server response'}}));
            if (result.ok) {{
              if (handleKickoffActionSuccess(action, result, btn, original)) return result;
              if (handleMomentActionSuccess(action, result, btn, original)) return result;
              window.location.assign(`/projects/{job_id}`);
              return result;
            }}
            resetButton(btn, original);
            showCreatorError('Something went wrong. Try again.', JSON.stringify({{status: resp.status, action, payload, response: result}}, null, 2));
            return result;
          }} catch (err) {{
            resetButton(btn, original);
            const timedOut = err && err.name === 'AbortError';
            showCreatorError(timedOut ? 'The request took too long. Try again.' : 'Something went wrong. Try again.', String(err));
            return {{ok:false, error:String(err)}};
          }}
        }}

        async function retryResearch(btn) {{
          return executeProjectAction('retry_research', btn);
        }}

        function watchRoughCut() {{
          smoothFocus('#rough-cut');
          if (roughVideo) {{
            const playPromise = roughVideo.play();
            if (playPromise && playPromise.catch) {{ playPromise.catch(() => roughVideo.classList.add('needs-play')); }}
          }}
        }}

        function finishCut() {{ smoothFocus('#finish-cut'); }}

        function openInlineMoment(trigger) {{
          const panel = document.getElementById('inline-moment-preview');
          const video = document.getElementById('inlineMomentVideo');
          if (!panel || !video || !trigger.dataset.videoUrl) {{ return; }}
          document.getElementById('inline-preview-title').textContent = trigger.dataset.title || 'Preview Moment';
          document.getElementById('inline-preview-minute').textContent = trigger.dataset.minute || 'Moment';
          document.getElementById('inline-preview-description').textContent = trigger.dataset.description || '';
          panel.hidden = false;
          if (video.getAttribute('src') !== trigger.dataset.videoUrl) {{ video.setAttribute('src', trigger.dataset.videoUrl); }}
          const seek = Number(trigger.dataset.seek || 0);
          const doSeek = () => {{ video.currentTime = seek; video.focus(); }};
          if (video.readyState >= 1) {{ doSeek(); }} else {{ video.addEventListener('loadedmetadata', doSeek, {{once: true}}); }}
          panel.scrollIntoView({{behavior: 'smooth', block: 'center'}});
        }}

        document.querySelectorAll('.moment-preview-trigger').forEach(tile => tile.addEventListener('click', () => openInlineMoment(tile)));
        function playInlineMoment() {{ const video = document.getElementById('inlineMomentVideo'); if (video) {{ video.play(); }} }}
        function closeInlineMoment() {{ const panel = document.getElementById('inline-moment-preview'); const video = document.getElementById('inlineMomentVideo'); if (video) {{ video.pause(); }} if (panel) {{ panel.hidden = true; }} }}

        async function doTransition(target) {{
          const meta = prompt("Operator name (optional):") || "";
          const resp = await fetch("/api/projects/{job_id}/transition", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{target_state: target, operator: meta}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Transition failed.", result.error);
          }}
        }}

        async function doConfirmRights() {{
          const operator = prompt("Operator name (optional):") || "";
          const confirmedBy = prompt("Confirmed by (rights owner/client):", operator || "console_operator") || "";
          if (!confirmedBy.trim()) {{
            showCreatorError("Rights confirmation requires a confirmer.");
            return;
          }}
          const statement = prompt("Confirmation statement:", "Rights confirmed for clipping, storage, review, and delivery.") || "";
          if (!statement.trim()) {{
            showCreatorError("Rights confirmation requires a statement.");
            return;
          }}
          const today = new Date().toISOString().slice(0, 10);
          const confirmationDate = prompt("Confirmation date (YYYY-MM-DD):", today) || "";
          if (!confirmationDate.trim()) {{
            showCreatorError("Rights confirmation requires a date.");
            return;
          }}
          const resp = await fetch("/api/projects/{job_id}/rights/confirm", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
              confirmation_statement: statement,
              confirmed_by: confirmedBy,
              confirmation_date: confirmationDate,
              operator: operator,
            }}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Confirm Rights failed.", result.error || "Unknown error");
          }}
        }}

        async function doCancelProject() {{
          const reason = prompt("Cancellation reason (required):") || "";
          if (!reason.trim()) {{
            showCreatorError("Cancel Project requires a reason.");
            return;
          }}
          const operator = prompt("Operator name:", "console_operator") || "";
          if (!operator.trim()) {{
            showCreatorError("Cancel Project requires an operator name.");
            return;
          }}
          const resp = await fetch("/api/projects/{job_id}/transition", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{
              target_state: "CANCELLED",
              reason: reason,
              operator: operator,
              client_requested: false,
            }}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Cancel Project failed.", result.error || "Unknown error");
          }}
        }}

async function doAnalyze() {{
          const btn = document.querySelector('[onclick="doAnalyze()"]');
          if (btn) {{
            btn.disabled = true;
            btn.textContent = "Starting analysis...";
          }}
          const resp = await fetch("/api/projects/{job_id}/analyze", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Analysis failed.", result.error || "Unknown error");
            window.location.reload();
          }}
        }}

        async function doDuplicate() {{
          const displayName = prompt("New project name (optional):") || "";
          const reuseMode = "SOURCE_ANALYSIS_AND_MOMENTS";
          const resp = await fetch(`/api/projects/{job_id}/duplicate`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{display_name: displayName, reuse_mode: reuseMode}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.href = "/projects/" + result.job_id;
          }} else {{
            showCreatorError("Duplicate failed.", result.error || "Unknown error");
          }}
        }}

        async function seedResearchMoments(btn) {{
          return executeProjectAction('seed_moments', btn);
        }}

        async function alignProject(btn) {{
          if (btn) {{ btn.disabled = true; btn.textContent = "Finding moments..."; }}
          const resp = await fetch(`/api/projects/{job_id}/align`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Alignment failed.", result.error || "Unknown error");
            window.location.reload();
          }}
        }}

        async function confirmMomentAlignment(momentId, action, btn) {{
          const mapped = action === 'confirm' ? 'confirm_moment' : (action === 'earlier' ? 'shift_moment_earlier' : (action === 'later' ? 'shift_moment_later' : 'mark_moment_not_found'));
          const video = document.getElementById('momentReviewVideo');
          return executeProjectAction(mapped, btn, {{moment_id: momentId, source_artifact_id: video ? video.dataset.sourceArtifactId : '', current_cursor_seconds: video ? Number(video.dataset.cursor || video.currentTime || 0) : null, direction: action}});
        }}

        async function reviewKickoff(action, btn, segmentType='FIRST_HALF', sourceArtifactId='') {{
          const mapped = action === 'confirm' ? 'confirm_kickoff' : (action === 'earlier' ? 'shift_kickoff_earlier' : 'shift_kickoff_later');
          return executeProjectAction(mapped, btn, {{segment_type: segmentType, source_artifact_id: sourceArtifactId}});
        }}

async function reviewMoment(momentId, reviewState) {{
          const resp = await fetch(`/api/projects/{job_id}/moments/${{momentId}}/review`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{review_state: reviewState}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Review update failed.", result.error || "Unknown error");
          }}
        }}

        async function storyReview(storyId, status) {{
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/review`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{status: status}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Story update failed.", result.error || "Unknown error");
          }}
        }}

        async function doGenerateStories() {{
          const btn = document.querySelector('[onclick="doGenerateStories()"]');
          return executeProjectAction('find_story', btn);
        }}

        async function doGenerateBrief(storyId, format) {{
          const btn = (typeof event !== 'undefined' && event && event.target) ? event.target : null;
          if (btn) {{
            btn.disabled = true;
            btn.textContent = "Building " + format + "...";
          }}
          const resp = await fetch("/api/projects/{job_id}/stories/" + storyId + "/brief", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: format}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Edit Brief failed.", result.error || "Unknown error");
            window.location.reload();
          }}
        }}

        async function doBuildEdl(storyId, format) {{
          const btn = (typeof event !== 'undefined' && event && event.target) ? event.target : null;
          if (btn) {{
            btn.disabled = true;
            btn.textContent = "Building timeline...";
          }}
          const resp = await fetch("/api/projects/{job_id}/stories/" + storyId + "/edl", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: format}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Timeline failed.", result.error || "Unknown error");
            window.location.reload();
          }}
        }}

        async function doRender(storyId, format, mode) {{
          const btn = (typeof event !== 'undefined' && event && event.target) ? event.target : null;
          if (btn) {{
            btn.disabled = true;
            btn.textContent = "Rendering " + (mode || "REFERENCE") + "...";
          }}
          const resp = await fetch("/api/projects/{job_id}/stories/" + storyId + "/render", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: format, mode: mode || "REFERENCE"}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            showCreatorError("Render failed.", result.error || "Unknown error");
            window.location.reload();
          }}
        }}
        </script>
        """
        return _html_response(self, self._page(f"Project — {job_id}", content))

    def _render_section(self, job_id: str, story_id: str, fmt: str, edl: dict | None) -> str:
        """Return HTML for the render buttons and video previews for both modes."""
        if not edl:
            return ""

        ref_state = get_render_status(job_id, story_id, fmt, mode="REFERENCE")
        edit_state = get_render_status(job_id, story_id, fmt, mode="EDITORIAL")

        html_parts = []

        # ── Reference Cut Section ────────────────────────────────────────
        ref_status = ref_state.get("render_status") if ref_state else None
        if ref_status == "COMPLETE":
            ref_dur = ref_state.get("duration", 0)
            ref_segs = ref_state.get("segment_count", 0)
            ref_url = f"/video/{job_id}/{story_id}/{fmt}/REFERENCE/reference.mp4"
            html_parts.append(f"""
            <div style="margin-top:0.75rem; padding:0.5rem; border:1px solid #3d7a3d; border-radius:4px; background:rgba(61,122,61,0.08);">
              <h5 style="color:#3d7a3d;">REFERENCE CUT · {ref_segs} segments · {ref_dur:.1f}s</h5>
              <p style="font-size:0.85rem; margin:0.25rem 0;">Clean assembly — timing check</p>
              <video controls style="width:100%; max-width:800px; border-radius:4px; margin-top:0.5rem;">
                <source src="{ref_url}" type="video/mp4">
              </video>
              <div class="form-actions" style="margin-top:0.5rem;">
                <button class="btn" onclick="doRender('{story_id}', '{fmt}', 'REFERENCE')">Re-render Reference</button>
              </div>
            </div>
            """)
        elif ref_status == "FAILED":
            ref_err = (ref_state.get("error", "") if ref_state else "")[:200]
            html_parts.append(f"""
            <div style="margin-top:0.75rem; padding:0.5rem; border:1px solid #cc3333; border-radius:4px; background:rgba(204,51,51,0.08);">
              <h5 style="color:#cc3333;">REFERENCE CUT — FAILED</h5>
              <p style="font-size:0.85rem; color:#cc3333;">{ref_err}</p>
              <div class="form-actions">
                <button class="btn btn-primary" onclick="doRender('{story_id}', '{fmt}', 'REFERENCE')">Retry Reference</button>
              </div>
            </div>
            """)
        else:
            html_parts.append(f"""
            <div class="form-actions" style="margin-top:0.5rem;">
              <button class="btn" onclick="doRender('{story_id}', '{fmt}', 'REFERENCE')">Reference Cut</button>
            </div>
            """)

        # ── Editorial Cut Section ────────────────────────────────────────
        edit_status = edit_state.get("render_status") if edit_state else None
        if edit_status == "COMPLETE":
            edit_dur = edit_state.get("duration", 0)
            edit_segs = edit_state.get("segment_count", 0)
            edit_url = f"/video/{job_id}/{story_id}/{fmt}/EDITORIAL/editorial.mp4"
            applied = edit_state.get("applied_features", [])
            partial = edit_state.get("partially_applied_features", [])
            deferred = edit_state.get("deferred_features", [])
            failed = edit_state.get("failed_features", [])

            applied_str = " · ".join(f"✓ {f}" for f in applied) if applied else "None"
            partial_str = " · ".join(f"△ {f}" for f in partial) if partial else ""
            deferred_str = " · ".join(f"○ {f}" for f in deferred) if deferred else ""
            failed_str = " · ".join(f"✗ {f}" for f in failed) if failed else ""

            status_lines = ""
            if applied_str:
                status_lines += f'<div style="color:#3d7a3d;">{applied_str}</div>'
            if partial_str:
                status_lines += f'<div style="color:#ccaa00;">{partial_str}</div>'
            if deferred_str:
                status_lines += f'<div style="color:#888;">{deferred_str}</div>'
            if failed_str:
                status_lines += f'<div style="color:#cc3333;">{failed_str}</div>'

            html_parts.append(f"""
            <div style="margin-top:0.75rem; padding:0.5rem; border:1px solid #4a7acc; border-radius:4px; background:rgba(74,122,204,0.08);">
              <h5 style="color:#4a7acc;">EDITORIAL CUT · {edit_segs} segments · {edit_dur:.1f}s</h5>
              <div style="margin:0.5rem 0; font-size:0.85rem;">
                {status_lines}
              </div>
              <video controls style="width:100%; max-width:800px; border-radius:4px; margin-top:0.5rem;">
                <source src="{edit_url}" type="video/mp4">
              </video>
              <div class="form-actions" style="margin-top:0.5rem;">
                <button class="btn" onclick="doRender('{story_id}', '{fmt}', 'EDITORIAL')">Re-render Editorial</button>
              </div>
            </div>
            """)
        elif edit_status == "FAILED":
            edit_err = (edit_state.get("error", "") if edit_state else "")[:200]
            html_parts.append(f"""
            <div style="margin-top:0.75rem; padding:0.5rem; border:1px solid #cc3333; border-radius:4px; background:rgba(204,51,51,0.08);">
              <h5 style="color:#cc3333;">EDITORIAL CUT — FAILED</h5>
              <p style="font-size:0.85rem; color:#cc3333;">{edit_err}</p>
              <div class="form-actions">
                <button class="btn btn-primary" onclick="doRender('{story_id}', '{fmt}', 'EDITORIAL')">Retry Editorial</button>
              </div>
            </div>
            """)
        else:
            html_parts.append(f"""
            <div class="form-actions" style="margin-top:0.25rem;">
              <button class="btn btn-primary" onclick="doRender('{story_id}', '{fmt}', 'EDITORIAL')">Editorial Cut</button>
              <span style="font-size:0.8rem; color:#888; margin-left:0.5rem;">Applies supported Stadium Signal effects</span>
            </div>
            """)

        return "\n".join(html_parts)

    def _render_story_detail(self, job_id: str, story_id: str):
        try:
            detail = get_story_detail(job_id, story_id)
        except Exception as exc:
            return _html_response(self, self._page("Error", f'<div class="card"><h2>Error</h2><p>{_escape(str(exc))}</p><a href="/projects/{_escape(job_id)}" class="btn">Back</a></div>'), 404)

        story = detail.get("story", {})
        status = story.get("status", "")
        ordered_moments = detail.get("ordered_moments", [])
        edit_briefs = detail.get("edit_briefs", [])
        edls = detail.get("edls", [])
        renders = detail.get("renders", [])
        exports = detail.get("exports", [])
        sid = _escape(story.get("story_id", ""))

        actions = ""
        if status == "SUGGESTED":
            actions = f"""
            <button class="btn btn-primary" onclick="storyReview('{sid}', 'APPROVED')">Approve</button>
            <button class="btn" onclick="storyReview('{sid}', 'REJECTED')">Reject</button>
            """
        elif status == "APPROVED":
            actions = f"""
            <button class="btn" onclick="storyReview('{sid}', 'ARCHIVED')">Archive</button>
            """

        moment_rows = ""
        narrative_cards = ""
        for entry in ordered_moments:
            mid = _escape(entry.get("moment_id", ""))
            review = _escape(entry.get("review_state", "UNREVIEWED"))
            review_warning = " <span class=\"badge badge-moment\">REJECT</span>" if review == "REJECT" else ""
            seq = int(entry.get("sequence_order", 0))
            role_options = ""
            for role in ("HOOK", "SETUP", "ESCALATION", "CLIMAX", "AFTERMATH"):
                selected = " selected" if role == entry.get("narrative_role") else ""
                role_options += f'<option value="{role}"{selected}>{role}</option>'
            moment_rows += f"""
            <tr>
              <td>{seq}</td>
              <td>
                <select class="role-select" data-seq="{seq}" onchange="storySetRole('{sid}', '{mid}', this.value)">{role_options}</select>
              </td>
              <td>{_format_moment_time(entry.get('start_seconds'))}</td>
              <td>{_escape(entry.get('universal_event_type', ''))}</td>
              <td>{_escape(entry.get('sport_event_type', ''))}</td>
              <td>{_escape(entry.get('review_state', 'UNREVIEWED'))}{review_warning}</td>
              <td>{_escape(entry.get('team') or '—')}</td>
              <td>
                <button class="btn" onclick="storyMove('{sid}', '{mid}', {seq}, -1)">Move Up</button>
                <button class="btn" onclick="storyMove('{sid}', '{mid}', {seq}, 1)">Move Down</button>
                <button class="btn" onclick="storyRemoveMoment('{sid}', '{mid}')">Remove</button>
              </td>
            </tr>
            """
            narrative_cards += f"""
            <div class="story-moment-card">
              <div class="beat-role">{_escape(entry.get('narrative_role'))}</div>
              <strong>{_escape(entry.get('universal_event_type', ''))} · {_escape(entry.get('sport_event_type', ''))}</strong>
              <p class="muted">{_format_moment_time(entry.get('peak_seconds') or entry.get('start_seconds'))} · {_escape(entry.get('team') or '—')} · Review: {_escape(entry.get('review_state', 'UNREVIEWED'))}</p>
            </div>
            """

        brief_rows = ""
        for brief in edit_briefs:
            brief_rows += f"""
            <tr>
              <td>{_escape(brief.get('format_treatment', ''))}</td>
              <td>{_escape(brief.get('status', ''))}</td>
              <td>{_escape(brief.get('target_duration') or '—')}</td>
              <td>{_escape(brief.get('created_at', '')[:19])}</td>
              <td>{_escape(brief.get('editorial_intent', '')[:80])}</td>
            </tr>
            """

        brief_actions = ""
        if status == "APPROVED":
            formats = ", ".join(f"'{_escape(f)}'" for f in (story.get("recommended_formats") or ["SHORT"]))
            brief_actions = f"""
            <div class="form-actions">
              <button class="btn btn-primary" onclick="generateBrief('{sid}', '{formats}')">Generate Edit Brief</button>
            </div>
            """

        edl_rows = ""
        ready_formats: list[str] = []
        for edl in edls:
            edl_rows += f"""
            <tr>
              <td>{_escape(edl.get('format_treatment', ''))}</td>
              <td>{_escape(edl.get('status', ''))}</td>
              <td>{_escape(edl.get('target_duration') or '—')}</td>
              <td>{_escape(edl.get('estimated_duration') or '—')}</td>
              <td>{_escape(edl.get('created_at', '')[:19])}</td>
            </tr>
            """
        for brief in edit_briefs:
            if brief.get("status") == "READY":
                ready_formats.append(brief.get("format_treatment", ""))
        edl_actions = ""
        if ready_formats:
            edl_actions = f"""
            <div class="form-actions">
              <button class="btn btn-primary" onclick="generateEdl('{sid}', '{', '.join(_escape(f) for f in ready_formats)}')">Generate EDL</button>
            </div>
            """

        ready_edl_formats: list[str] = []
        for edl in edls:
            if edl.get("status") == "READY":
                ready_edl_formats.append(edl.get("format_treatment", ""))
        render_actions = ""
        if ready_edl_formats:
            preset_options = ""
            for preset in list_channel_presets():
                info = f"{preset.aspect_ratio} {preset.width}x{preset.height}"
                preset_options += f'<option value="{_escape(preset.preset_id)}">{_escape(preset.name)} ({_escape(info)})</option>'
            render_actions = f"""
            <div class="form-actions">
              <label for="preset_select" class="muted" style="margin-right:0.25rem;">Platform</label>
              <select id="preset_select">
                {preset_options}
              </select>
              <button class="btn btn-primary" onclick="generateVariant('{sid}', '{', '.join(_escape(f) for f in ready_edl_formats)}')">Generate Variant</button>
            </div>
            """

        render_rows = ""
        preview_cards = ""
        for render in renders:
            rid = _escape(render.get("render_id", ""))
            review = _escape(render.get("review_state", "UNREVIEWED"))
            meta = render.get("metadata") or {}
            preview = ""
            download = ""
            if render.get("artifact_id"):
                preview = f'<video width="320" controls src="/render_video/{_escape(job_id)}/{rid}"></video>'
                download = f'<a class="btn" href="/render_video/{_escape(job_id)}/{rid}" download>Download Video</a>'
            review_buttons = ""
            export_button = ""
            if render.get("status") == "READY":
                for target, label in (("APPROVED", "Approve"), ("NEEDS_CHANGES", "Needs Changes"), ("REJECTED", "Reject")):
                    review_buttons += f'<button class="btn" onclick="renderReview(\'{rid}\', \'{target}\')">{label}</button> '
                if review == "APPROVED":
                    export_button = f'<div><span class="ready">Ready to Export</span><br><button class="btn btn-primary" onclick="createExport(\'{rid}\')">Create Export Package</button></div>'
            render_rows += f"""
            <tr>
              <td>{_escape(render.get('render_profile', ''))}</td>
              <td>{_escape(render.get('platform') or '—')}</td>
              <td>{_escape(render.get('status', ''))}</td>
              <td><span class="badge badge-moment">{review}</span></td>
              <td>{_escape(render.get('duration_seconds') or '—')}</td>
              <td>{_escape(render.get('created_at', '')[:19])}</td>
              <td>{preview}{download}</td>
              <td>{review_buttons}</td>
              <td>{export_button}</td>
            </tr>
            """
            if meta.get("preview"):
                deferred = ", ".join(str(item).replace("_", " ") for item in meta.get("deferred_features", [])) or "None"
                preview_cards += f"""
                <div class="preview-card" id="preview">
                  <div class="preview-video">{preview}</div>
                  <div>
                    <h4>Rough Cut</h4>
                    <p>{_escape(render.get('duration_seconds') or '—')}s · {_escape(render.get('width') or '—')}x{_escape(render.get('height') or '—')}</p>
                    <p class="muted">Vertical preview for editorial review.</p>
                    <div class="form-actions">{review_buttons}{download}</div>
                    <details class="advanced-details"><summary>Advanced Details</summary><p class="muted">Render ID: {rid} · EditPlan ID: {_escape(meta.get('edit_plan_id') or '—')}</p><p class="muted">Deferred creative features: {_escape(deferred)}</p></details>
                  </div>
                </div>
                """

        try:
            from pipeline.runtime_service import list_story_edit_plans
            edit_plans = [plan.to_dict() for plan in list_story_edit_plans(story_id)]
        except Exception:
            edit_plans = []
        edit_plan_rows = ""
        edit_plan_cards = ""
        for plan in edit_plans:
            plan_id = _escape(plan.get("edit_plan_id", ""))
            report = get_editplan_execution_report(plan.get("edit_plan_id", "")) if plan.get("edit_plan_id") else {"validation": {}, "quality_report": []}
            validation = report.get("validation") or {}
            quality_rows = ""
            timeline_blocks = ""
            beat_cards = ""
            for row in report.get("quality_report") or []:
                quality_rows += (
                    f"#{_escape(row.get('sequence_order'))} {_escape(row.get('narrative_role') or '')} "
                    f"{_escape(row.get('source_window'))} · {_escape(row.get('text') or '')}<br>"
                )
                duration = float(row.get("duration") or 0)
                timeline_blocks += f"<div class='timeline-block' style='flex:{max(duration, 1)}'><strong>{_escape(row.get('narrative_role'))}</strong><span>{_escape(row.get('source_window'))}</span></div>"
                beat_cards += f"""
                <div class="beat-card">
                  <div class="beat-role">{_escape(row.get('narrative_role'))}</div>
                  <strong>{_escape(row.get('text') or 'No overlay')}</strong>
                  <div class="detail-grid compact"><div>Moment: {_escape(row.get('moment_id'))}</div><div>Source: {_escape(row.get('source_window'))}</div><div>Duration: {_escape(row.get('duration'))}s</div><div>Crop: {_escape(row.get('crop_intent'))}</div><div>Speed: {_escape(row.get('speed'))}</div><div>Freeze: {_escape(row.get('freeze'))}</div><div>Audio: {_escape(row.get('audio_cue'))}</div><div>Motion: {_escape(row.get('motion_graphic'))}</div></div>
                </div>
                """
            actions = f'<button class="btn" onclick="generateEditPlanPreview(\'{plan_id}\')">Watch Rough Cut</button> '
            if plan.get("renderer") == "CHATCUT":
                actions += f'<button class="btn" onclick="prepareChatCut(\'{plan_id}\')">Finish Cut</button> <a class="btn" href="/edit-plans/{plan_id}/handoff">Open Creative Package</a>'
            edit_plan_cards += f"""
            <div class="edit-plan-card">
              <div class="card-header"><div><h4>{_escape(plan.get('title'))}</h4><span class="muted">{_escape(plan.get('target_platform'))} · {_escape(plan.get('aspect_ratio'))} · {_escape(plan.get('target_duration'))}s · Renderer target: {_escape(plan.get('renderer'))}</span></div><span class="badge badge-ready">{_escape(validation.get('status') or 'UNKNOWN')}</span></div>
              <div class="edit-timeline">{timeline_blocks}</div>
              <div class="beat-grid">{beat_cards}</div>
              <div class="form-actions">{actions}</div>
            </div>
            """
            edit_plan_rows += f"""
            <tr>
              <td>{_escape(plan.get('title', ''))}</td>
              <td>{_escape(plan.get('target_platform', ''))}</td>
              <td>{_escape(plan.get('aspect_ratio', ''))}</td>
              <td>{_escape(plan.get('renderer', ''))}</td>
              <td>{_escape(plan.get('status', ''))}</td>
              <td>{_escape(validation.get('status') or 'UNKNOWN')}<br>{quality_rows}</td>
              <td>{actions}</td>
            </tr>
            """
        renderer_options = "".join(f'<option value="{_escape(renderer)}">{_escape(renderer)}</option>' for renderer in list_renderers())
        ready_brief_options = "".join(
            f'<option value="{_escape(brief.get("edit_brief_id", ""))}">{_escape(brief.get("format_treatment", ""))} · {_escape(brief.get("status", ""))}</option>'
            for brief in edit_briefs if brief.get("status") == "READY"
        )
        edit_plan_actions = f"""
        <div class="form-actions" style="justify-content:flex-start;">
          <select id="edit_plan_brief">{ready_brief_options}</select>
          <select id="edit_plan_renderer">{renderer_options}</select>
          <button class="btn btn-primary" onclick="generateEditPlan('{sid}')">Generate Edit Plan</button>
        </div>
        """ if ready_brief_options else '<p class="muted">Generate a READY Edit Brief before creating an Edit Plan.</p>'

        export_rows = ""
        for export in exports:
            export_rows += f"""
            <tr>
              <td>{_escape(export.get('platform', ''))}</td>
              <td>{_escape(export.get('channel_preset_id', ''))}</td>
              <td>{_escape(export.get('status', ''))}</td>
              <td>{_escape((export.get('caption') or export.get('title') or '')[:60])}</td>
              <td>{_escape(export.get('created_at', '')[:19])}</td>
            </tr>
            """

        handoff_cards = ""
        try:
            from pipeline.runtime_service import list_project_artifacts
            handoffs = list_project_artifacts(detail.get('story', {}).get('project_id') or job_id, artifact_type="chatcut_handoff")
        except Exception:
            handoffs = []
        edit_plan_ids = {plan.get("edit_plan_id") for plan in edit_plans}
        for artifact in handoffs:
            meta = artifact.metadata
            if meta.get("edit_plan_id") not in edit_plan_ids:
                continue
            handoff_cards += f"""
            <div class="handoff-card">
              <div>
                <h4>ChatCut Handoff — READY</h4>
                 <h4>Finish Cut</h4>
                 <p>Creative package ready</p>
                 <p class="muted">ChatCut-ready</p>
                 <details class="advanced-details"><summary>Advanced Details</summary><p>Version: {_escape(meta.get('handoff_version') or '1.0')} · Artifact: {_escape(artifact.artifact_id)}</p><p>Manifest: manifest.json · Captions: captions/captions.srt</p><p class="muted">Timeline instructions: 5 · Source media: germany_italy_2012.mp4</p></details>
               </div>
              <div class="form-actions"><a class="btn btn-primary" href="/edit-plans/{_escape(meta.get('edit_plan_id'))}/handoff">Open Creative Package</a><button class="btn" onclick="prepareChatCut('{_escape(meta.get('edit_plan_id'))}')">Refresh Package</button></div>
            </div>
            """

        content = f"""
        <nav class="breadcrumb" aria-label="Breadcrumb">
          <a href="/">Projects</a> <span aria-hidden="true">›</span>
          <a href="/projects/{_escape(job_id)}">{_escape(detail.get('story', {}).get('project_id') or job_id)}</a> <span aria-hidden="true">›</span>
          <span>{_escape(story.get('title', ''))}</span>
        </nav>
        <div class="card">
          <div class="card-header">
            <div>
              <h2>{_escape(story.get('title', ''))}</h2>
              <span class="muted">{_escape(story.get('archetype', '—'))} · <span class="badge badge-moment">{_escape(status)}</span></span>
            </div>
          </div>
          <div class="detail-grid">
            <div><strong>Summary:</strong> {_escape(story.get('summary') or '—')}</div>
            <div><strong>Hook:</strong> {_escape(story.get('hook') or '—')}</div>
            <div><strong>Duration:</strong> {_escape(story.get('estimated_duration') or '—')}</div>
            <div><strong>Formats:</strong> {_escape(', '.join(story.get('recommended_formats') or []) or '—')}</div>
            <div><strong>Emotional arc:</strong> {_escape(' → '.join(story.get('emotional_arc') or []) or '—')}</div>
          </div>
          <div class="form-actions">{actions}</div>
        </div>

        <div class="card">
          <h3>Ordered Moments ({len(ordered_moments)})</h3>
          <div class="story-sequence">{narrative_cards or '<p class="empty">No StoryMoments yet.</p>'}</div>
          <table>
            <thead><tr><th>Order</th><th>Role</th><th>Time</th><th>Event</th><th>Sport Type</th><th>Review</th><th>Team</th><th></th></tr></thead>
            <tbody>{moment_rows or '<tr><td colspan="8" class="empty">No moments in this story yet.</td></tr>'}</tbody>
          </table>
          <div class="form-row" style="margin-top:1rem;">
            <div class="form-group">
              <label for="add_moment_id">Moment id</label>
              <input id="add_moment_id" placeholder="mom_...">
            </div>
            <div class="form-group">
              <label for="add_moment_role">Role</label>
              <select id="add_moment_role">
                <option value="HOOK">HOOK</option>
                <option value="SETUP">SETUP</option>
                <option value="ESCALATION">ESCALATION</option>
                <option value="CLIMAX">CLIMAX</option>
                <option value="AFTERMATH">AFTERMATH</option>
              </select>
            </div>
          </div>
          <button class="btn" onclick="storyAddMoment('{sid}')">Add Moment</button>
        </div>

        <div class="card">
          <h3>Edit Briefs ({len(edit_briefs)})</h3>
          <table>
            <thead><tr><th>Format</th><th>Status</th><th>Duration</th><th>Created</th><th>Intent</th></tr></thead>
            <tbody>{brief_rows or '<tr><td colspan="5" class="empty">No edit briefs yet.</td></tr>'}</tbody>
          </table>
          {brief_actions}
        </div>

        <div class="card">
          <h3>Edit Plans ({len(edit_plans)})</h3>
          {edit_plan_cards or '<p class="empty">No edit plan yet. Generate an edit after the Story and Edit Brief are ready.</p>'}
          <table>
            <thead><tr><th>Title</th><th>Platform</th><th>Aspect</th><th>Renderer</th><th>Status</th><th>Validation / Beats</th><th>Actions</th></tr></thead>
            <tbody>{edit_plan_rows or '<tr><td colspan="7" class="empty">No edit plans yet.</td></tr>'}</tbody>
          </table>
          {edit_plan_actions}
        </div>

        <div class="card">
          <h3>EDLs ({len(edls)})</h3>
          <table>
            <thead><tr><th>Format</th><th>Status</th><th>Target Duration</th><th>Estimated Duration</th><th>Created</th></tr></thead>
            <tbody>{edl_rows or '<tr><td colspan="5" class="empty">No EDLs yet.</td></tr>'}</tbody>
          </table>
          {edl_actions}
        </div>

        <div class="card">
          <h3>Rough Cuts ({len(renders)})</h3>
          {preview_cards or '<p class="empty">No rough preview yet. Generate a preview from the EditPlan.</p>'}
          <table>
            <thead><tr><th>Profile</th><th>Platform</th><th>Status</th><th>Review</th><th>Duration</th><th>Created</th><th>Preview</th><th>Review</th><th>Export</th></tr></thead>
            <tbody>{render_rows or '<tr><td colspan="9" class="empty">No rough cuts yet.</td></tr>'}</tbody>
          </table>
          {render_actions}
        </div>

        <div class="card">
          <h3>Finish Cut</h3>
          {handoff_cards or '<p class="empty">No creative package yet. Finish Cut after the edit is valid.</p>'}
        </div>

        <div class="card">
          <h3>Export Packages ({len(exports)})</h3>
          <table>
            <thead><tr><th>Platform</th><th>Preset</th><th>Status</th><th>Caption / Title</th><th>Created</th></tr></thead>
            <tbody>{export_rows or '<tr><td colspan="5" class="empty">No export packages yet.</td></tr>'}</tbody>
          </table>
        </div>

        <a href="/projects/{_escape(job_id)}" class="btn">← Back to Project</a>

        <script>
        async function storyReview(storyId, status) {{
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/review`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{status: status}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Story update failed.", result.error || "Unknown error"); }}
        }}

        async function storyRemoveMoment(storyId, momentId) {{
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/moments/remove`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{moment_id: momentId}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Remove failed.", result.error || "Unknown error"); }}
        }}

        async function storyMove(storyId, momentId, currentSeq, delta) {{
          const target = Math.max(1, currentSeq + delta);
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/moments/update`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{moment_id: momentId, sequence_order: target}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Move failed.", result.error || "Unknown error"); }}
        }}

        async function storySetRole(storyId, momentId, role) {{
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/moments/update`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{moment_id: momentId, narrative_role: role}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Role update failed.", result.error || "Unknown error"); }}
        }}

        async function storyAddMoment(storyId) {{
          const momentId = document.getElementById("add_moment_id").value.trim();
          if (!momentId) {{ showCreatorError("Enter a Moment id to add."); return; }}
          const role = document.getElementById("add_moment_role").value;
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/moments/add`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{moment_id: momentId, narrative_role: role, sequence_order: 999}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Add failed.", result.error || "Unknown error"); }}
        }}

        async function generateBrief(storyId, formats) {{
          const fmt = prompt("Format treatment (" + formats.replace(/'/g, "") + "):", "SHORT");
          if (!fmt) return;
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/brief`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: fmt.toUpperCase()}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Edit brief failed.", result.error || "Unknown error"); }}
        }}

        async function generateEdl(storyId, formats) {{
          const fmt = prompt("Format treatment (" + formats.replace(/'/g, "") + "):", "SHORT");
          if (!fmt) return;
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/edl`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: fmt.toUpperCase()}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("EDL failed.", result.error || "Unknown error"); }}
        }}

        async function generateEditPlan(storyId) {{
          const brief = document.getElementById("edit_plan_brief").value;
          const renderer = document.getElementById("edit_plan_renderer").value;
          if (!brief) {{ showCreatorError("Generate a READY Edit Brief first."); return; }}
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/edit-plan`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{edit_brief_id: brief, renderer: renderer, target_platform: "TikTok", aspect_ratio: "9:16"}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Edit plan failed.", result.error || "Unknown error"); }}
        }}

        async function prepareChatCut(editPlanId) {{
          const resp = await fetch(`/api/edit-plans/${{editPlanId}}/chatcut-handoff`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("ChatCut handoff failed.", result.error || "Unknown error"); }}
        }}

        async function generateEditPlanPreview(editPlanId) {{
          const resp = await fetch(`/api/edit-plans/${{editPlanId}}/preview`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Rough preview failed.", result.error || "Validation failed"); }}
        }}

        async function generateRender(storyId, formats) {{
          const fmt = prompt("Format treatment (" + formats.replace(/'/g, "") + "):", "SHORT");
          if (!fmt) return;
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/render`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: fmt.toUpperCase(), mode: "REFERENCE"}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Rough cut failed.", result.error || "Unknown error"); }}
        }}

        async function renderReview(renderId, reviewState) {{
          const note = reviewState === "NEEDS_CHANGES" ? (prompt("Review note (optional):") || "") : "";
          const resp = await fetch(`/api/projects/{job_id}/renders/${{renderId}}/review`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{review_state: reviewState, review_note: note}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Review update failed.", result.error || "Unknown error"); }}
        }}

        async function generateVariant(storyId, formats) {{
          const preset = document.getElementById("preset_select").value;
          const fmt = prompt("Format treatment (" + formats.replace(/'/g, "") + "):", "SHORT");
          if (!fmt) return;
          const resp = await fetch(`/api/projects/{job_id}/stories/${{storyId}}/render`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{format: fmt.toUpperCase(), mode: "REFERENCE", preset_id: preset}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Variant generation failed.", result.error || "Unknown error"); }}
        }}

        async function createExport(renderId) {{
          const preset = document.getElementById("preset_select").value;
          const caption = prompt("Caption (optional):") || "";
          const resp = await fetch(`/api/projects/{job_id}/renders/${{renderId}}/export`, {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{preset_id: preset, caption: caption}}),
          }});
          const result = await resp.json();
          if (result.ok) {{ window.location.reload(); }} else {{ showCreatorError("Export failed.", result.error || "Unknown error"); }}
        }}
        </script>
        """
        return _html_response(self, self._page(f"Story — {story.get('title', story_id)}", content))

    def _render_batch_detail(self, batch_id: str):
        try:
            detail = get_batch_detail(batch_id)
        except Exception as exc:
            return _html_response(self, self._page("Error", f'<div class="card"><h2>Error</h2><p>{_escape(str(exc))}</p><a href="/" class="btn">Back</a></div>'), 404)
        batch = detail.get("batch", {})
        items = detail.get("items", [])
        item_rows = ""
        for item in items:
            error = _escape(item.get("error_message") or "")
            item_rows += f"""
            <tr>
              <td><a href="/projects/{_escape(item['project_id'])}">{_escape(item.get('display_name') or item['project_id'])}</a></td>
              <td><span class="badge badge-moment">{_escape(item.get('status', ''))}</span></td>
              <td>{_escape(item.get('pipeline_run_id') or '—')}</td>
              <td>{_escape(item.get('error_code') or '—')}</td>
              <td>{error[:120]}</td>
            </tr>
            """
        content = f"""
        <div class="card">
          <div class="card-header">
            <div>
              <h2>Batch Analysis</h2>
              <span class="muted">{_escape(batch.get('operation_type', ''))}</span>
            </div>
            <span class="badge badge-{_escape(str(batch.get('status', '')).lower().replace('_', '-'))}">{_escape(batch.get('status', ''))}</span>
          </div>
          <div class="detail-grid">
            <div><strong>Total:</strong> {_escape(batch.get('total_items', 0))}</div>
            <div><strong>Succeeded:</strong> {_escape(batch.get('succeeded_count', 0))}</div>
            <div><strong>Failed:</strong> {_escape(batch.get('failed_count', 0))}</div>
            <div><strong>Blocked:</strong> {_escape(batch.get('blocked_count', 0))}</div>
            <div><strong>Queued:</strong> {_escape(batch.get('queued_count', 0))}</div>
            <div><strong>Running:</strong> {_escape(batch.get('running_count', 0))}</div>
            <div><strong>Created:</strong> {_escape(batch.get('created_at', '')[:19])}</div>
          </div>
        </div>
        <div class="card">
          <h3>Items ({len(items)})</h3>
          <table>
            <thead><tr><th>Project</th><th>Status</th><th>Run</th><th>Error Code</th><th>Error</th></tr></thead>
            <tbody>{item_rows or '<tr><td colspan="5" class="empty">No items.</td></tr>'}</tbody>
          </table>
        </div>
        <a href="/" class="btn">← Back to Projects</a>
        """
        return _html_response(self, self._page(f"Batch — {batch_id}", content))

    def _render_project_status(self, job_id: str):
        try:
            status = get_project_status(job_id)
        except Exception as exc:
            return _html_response(self, self._page("Error", f'<div class="card"><h2>Error</h2><p>{exc}</p><a href="/" class="btn">Back</a></div>'), 404)

        blockers = status.get("blockers", [])
        intake = status.get("intake", {})
        outputs = status.get("outputs", {})
        delivery = status.get("delivery", {})
        latest_run = status.get("latest_run")

        blocker_list = ""
        for b in blockers:
            blocker_list += f'<li class="blocker">{b}</li>'
        if not blockers:
            blocker_list = '<li class="ok">No blockers</li>'

        run_info = ""
        if latest_run:
            run_info = f"""
            <div class="detail-grid">
              <div><strong>Run ID:</strong> {latest_run.get('run_id', '—')}</div>
              <div><strong>Status:</strong> {latest_run.get('status', '—')}</div>
              <div><strong>Entry point:</strong> {latest_run.get('entry_point', '—')}</div>
            </div>
            """
        else:
            run_info = '<p class="muted">No pipeline runs recorded.</p>'

        content = f"""
        <div class="card">
          <h2>Status — {job_id}</h2>
          <div class="detail-grid">
            <div><strong>State:</strong> {status.get('state', '—')}</div>
            <div><strong>Blockers:</strong> {len(blockers)}</div>
          </div>
        </div>

        <div class="grid">
          <section class="card">
            <h3>Blockers</h3>
            <ul>{blocker_list}</ul>
          </section>
          <section class="card">
            <h3>Intake</h3>
            <div class="detail-grid">
              <div><strong>Structurally valid:</strong> {intake.get('structurally_valid', False)}</div>
              <div><strong>Source ready:</strong> {intake.get('source_ready', False)}</div>
              <div><strong>Rights cleared:</strong> {intake.get('rights_cleared', False)}</div>
              <div><strong>Config valid:</strong> {intake.get('config_references_valid', False)}</div>
              <div><strong>Execution ready:</strong> {intake.get('execution_ready', False)}</div>
            </div>
          </section>
          <section class="card">
            <h3>Outputs</h3>
            <div class="detail-grid">
              <div><strong>Manifests:</strong> {outputs.get('manifest_count', 0)}</div>
              <div><strong>Missing files:</strong> {outputs.get('missing_file_count', 0)}</div>
              <div><strong>Review complete:</strong> {outputs.get('review_complete', False)}</div>
            </div>
          </section>
          <section class="card">
            <h3>Latest Run</h3>
            {run_info}
          </section>
        </div>

        <a href="/projects/{job_id}" class="btn">← Back to Project</a>
        <a href="/" class="btn">← Projects</a>
        """
        return _html_response(self, self._page(f"Status — {job_id}", content))

    # ── API handlers ─────────────────────────────────────────────────────────

    def _api_create_project(self):
        data = _parse_form_body(self)
        sport = data.get("sport", "football")
        pilot_id = data.get("pilot_id", "").strip()
        source_id = data.get("source_id", "").strip()

        if not pilot_id or not source_id:
            return _json_response(self, {"ok": False, "error": "pilot_id and source_id are required"}, 400)

        local_path = data.get("local_file_path", "").strip()
        event_name = data.get("event_name", "").strip() or f"{pilot_id} event"
        reference_deployment = data.get("reference_deployment", "world_cup")
        delivery_method = data.get("delivery_method", "shared_folder")
        selected_strategy = str(data.get("analysis_strategy") or "RESEARCH_FIRST").strip().upper()
        if selected_strategy not in {"RESEARCH_FIRST", "TRANSCRIPT_FIRST", "HYBRID"}:
            selected_strategy = "RESEARCH_FIRST"
        original_filename = Path(local_path).name if local_path else f"{source_id}"
        operator_notes = data.get("operator_notes", "").strip()

        intake = {
            "intake_version": 1,
            "pilot": {
                "pilot_id": pilot_id,
                "project": sport,
                "reference_deployment": reference_deployment,
            },
            "media": {
                "source_id": source_id,
                "local_file_path": local_path,
                "original_filename": original_filename,
                "media_type": "video",
                "match_or_event_name": event_name,
                "supplied_by_client": True,
                "source_validation_completed": True,
            },
            "rights": {
                "status": "CONFIRMED",
                "permitted_uses": ["review"],
                "confirmation_statement": "Operator confirmed local review rights for this source.",
                "confirmed_by": "console_operator",
                "confirmation_date": "2026-10-05",
            },
            "configuration": {
                "project": sport,
                "brand": "world_cup" if sport == "football" else sport,
                "editorial_taxonomy": "world_cup" if sport == "football" else sport,
                "operational_taxonomy": "world_cup" if sport == "football" else sport,
                "detection_template": "prompt",
                "export_profiles": ["vertical_clean", "source"],
                "delivery_destination": "EXPORTS",
            },
            "review_and_delivery": {
                "human_review_required": True,
                "approval_method": "console",
                "delivery_method": delivery_method,
                "delivery_directory": "EXPORTS",
                "expected_deliverables": ["clips"],
                "publishing_included": False,
            },
        }
        if operator_notes:
            intake["pilot"]["operator_notes"] = operator_notes

        readiness_report = validate_project_intake(intake, check_source=True, check_rights=True)
        if not readiness_report.get("execution_ready"):
            codes = ", ".join(readiness_report.get("validation_codes") or []) or "not execution-ready"
            return _json_response(self, {"ok": False, "error": f"Project is not execution-ready: {codes}", "readiness": readiness_report}, 400)

        try:
            job = create_project(intake, operator="console_operator")
            if selected_strategy == "RESEARCH_FIRST":
                start_result = start_research_first_workflow(job["job_id"])
            else:
                from pipeline.runtime_service import index_existing_project, update_project_analysis_strategy
                project = index_existing_project(job["job_id"])
                update_project_analysis_strategy(project.project_id, selected_strategy)
                start_result = {"ok": True, "status": "SOURCE_READY", "analysis_strategy": selected_strategy, "started": False}
            return _json_response(self, {"ok": True, "job_id": job["job_id"], "state": job["current_state"], "workflow": start_result})
        except JobExistsError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 409)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_workflow_state(self, job_id: str):
        try:
            detail = get_project(job_id)
            intake = ConsoleHandler._read_intake_for_detail(detail)
            detail = dict(detail)
            detail["readiness_summary"] = ConsoleHandler._canonical_readiness(detail, intake)
            try:
                analysis = get_analysis_status(job_id)
            except Exception:
                analysis = {}
            runtime, _research_data = ConsoleHandler._workflow_runtime(detail, analysis)
            state = resolve_workflow_state(detail, analysis=analysis, runtime=runtime).to_dict()
            return _json_response(self, state)
        except JobNotFoundError as exc:
            return _json_response(self, {"error": str(exc)}, 404)
        except Exception as exc:
            return _json_response(self, {"error": str(exc)}, 500)

    def _api_duplicate_project(self, job_id: str):
        data = _parse_form_body(self)
        display_name = str(data.get("display_name", "")).strip() or None
        profile = str(data.get("profile", "")).strip() or None
        reuse_mode = str(data.get("reuse_mode", "SOURCE_ANALYSIS_AND_MOMENTS")).strip().upper()
        try:
            result = duplicate_project(job_id, display_name=display_name, profile=profile, reuse_mode=reuse_mode)
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_start_batch(self):
        data = _parse_form_body(self)
        project_ids = data.get("project_ids") or []
        if not isinstance(project_ids, list) or not project_ids:
            return _json_response(self, {"ok": False, "error": "project_ids is required"}, 400)
        try:
            result = start_analysis_batch([str(pid) for pid in project_ids])
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_transition_project(self, job_id: str):
        data = _parse_form_body(self)
        target = data.get("target_state", "")
        if not target:
            return _json_response(self, {"ok": False, "error": "target_state is required"}, 400)

        metadata = {}
        if data.get("operator"):
            metadata["operator"] = data["operator"]
        if data.get("reason"):
            metadata["reason"] = data["reason"]
        if "client_requested" in data:
            metadata["client_requested"] = data["client_requested"]

        try:
            transition_project(job_id, target, metadata=metadata or None)
            return _json_response(self, {"ok": True})
        except (JobTransitionError, JobRevisionError) as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except JobNotFoundError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 404)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_confirm_rights(self, job_id: str):
        data = _parse_form_body(self)
        statement = data.get("confirmation_statement", "")
        confirmed_by = data.get("confirmed_by", "")
        confirmation_date = data.get("confirmation_date", "")
        if not statement.strip() or not confirmed_by.strip() or not confirmation_date.strip():
            return _json_response(self, {"ok": False, "error": "confirmation_statement, confirmed_by, and confirmation_date are required"}, 400)
        permitted_uses = data.get("permitted_uses")
        if permitted_uses is not None and not isinstance(permitted_uses, list):
            return _json_response(self, {"ok": False, "error": "permitted_uses must be a list when provided"}, 400)
        try:
            job = confirm_project_rights(
                job_id,
                confirmation_statement=statement,
                confirmed_by=confirmed_by,
                confirmation_date=confirmation_date,
                permitted_uses=permitted_uses,
                operator=data.get("operator") or "console_operator",
            )
            return _json_response(self, {"ok": True, "job_id": job["job_id"], "state": job["current_state"]})
        except JobNotFoundError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 404)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_validate_intake(self):
        data = _parse_form_body(self)
        if not data:
            return _json_response(self, {"ok": False, "error": "No intake data provided"}, 400)

        try:
            report = validate_project_intake(data, check_source=False, check_rights=False)
            return _json_response(self, {"ok": True, "report": report})
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_analyze_project(self, job_id: str):
        try:
            detail = get_project(job_id)
            strategy = ((detail.get("runtime") or {}).get("project") or {}).get("analysis_strategy") if isinstance(detail.get("runtime"), dict) else None
            result = start_research_first_workflow(job_id) if strategy == "RESEARCH_FIRST" else analyze_project(job_id)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_seed_research_moments(self, job_id: str):
        data = _parse_form_body(self)
        try:
            result = seed_project_moments_from_research(
                job_id,
                research_id=data.get("research_id") or None,
                kickoff_media_offset_seconds=float(data["kickoff_media_offset_seconds"]) if data.get("kickoff_media_offset_seconds") not in (None, "") else None,
                halftime_duration_seconds=float(data["halftime_duration_seconds"]) if data.get("halftime_duration_seconds") not in (None, "") else None,
            )
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_align_project(self, job_id: str):
        try:
            from pipeline.runtime_service import get_project as _get_runtime_project, list_project_research
            from pipeline.source_alignment import BoundedSourceAlignmentService
            project = _get_runtime_project(job_id)
            if project is None or not project.source_artifact_id:
                return _json_response(self, {"ok": False, "error": "source artifact is required for alignment"}, 400)
            research_items = list_project_research(project.project_id)
            if not research_items:
                return _json_response(self, {"ok": False, "error": "MatchResearch is required before alignment"}, 400)
            result = BoundedSourceAlignmentService().align_research(project.project_id, project.source_artifact_id, research_items[-1].research_id)
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_project_action(self, job_id: str, action: str):
        data = _parse_form_body(self)
        from pipeline.project_actions import execute_project_action
        result = execute_project_action(job_id, action, data)
        return _json_response(self, result, 200 if result.get("ok") else 400)

    def _api_confirm_moment_alignment(self, job_id: str, moment_id: str):
        data = _parse_form_body(self)
        try:
            from pipeline.source_alignment import BoundedSourceAlignmentService
            result = BoundedSourceAlignmentService().confirm_moment_alignment(job_id, moment_id, action=str(data.get("action") or "confirm"))
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_source_clock_first_half(self, job_id: str):
        data = _parse_form_body(self)
        try:
            from pipeline.runtime_service import get_project as _get_runtime_project
            from pipeline.source_alignment import BoundedSourceAlignmentService
            project = _get_runtime_project(job_id)
            if project is None or not project.source_artifact_id:
                return _json_response(self, {"ok": False, "error": "source artifact is required"}, 400)
            result = BoundedSourceAlignmentService().confirm_source_anchor(
                project.project_id,
                project.source_artifact_id,
                segment_type="FIRST_HALF",
                action=str(data.get("action") or "confirm"),
            )
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_review_moment(self, job_id: str, moment_id: str):
        data = _parse_form_body(self)
        review_state = str(data.get("review_state", "")).strip().upper()
        reviewed_by = str(data.get("reviewed_by", "")).strip() or None
        try:
            result = review_moment(job_id, moment_id, review_state, reviewed_by=reviewed_by)
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_generate_stories(self, job_id: str):
        try:
            result = generate_stories(job_id)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_generate_brief(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        fmt = data.get("format", "SHORT")
        try:
            result = generate_canonical_edit_brief(job_id, story_id, fmt)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_generate_edit_plan(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        try:
            result = generate_edit_plan(
                job_id,
                story_id,
                str(data.get("edit_brief_id") or ""),
                title=data.get("title") or None,
                target_platform=data.get("target_platform") or "TikTok",
                aspect_ratio=data.get("aspect_ratio") or "9:16",
                renderer=str(data.get("renderer") or "FFMPEG").strip().upper(),
            )
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_chatcut_handoff(self, edit_plan_id: str):
        try:
            result = prepare_chatcut_handoff(edit_plan_id)
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_editplan_preview(self, edit_plan_id: str):
        try:
            result = generate_editplan_preview(edit_plan_id)
            return _json_response(self, result, 200 if result.get("ok") else 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

    def _api_build_edl(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        fmt = data.get("format", "SHORT")
        try:
            result = generate_canonical_edl(job_id, story_id, fmt)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_review_story(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        status = str(data.get("status", "")).strip().upper()
        try:
            result = review_story(job_id, story_id, status)
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_story_add_moment(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        try:
            result = story_add_moment(
                job_id,
                story_id,
                str(data.get("moment_id", "")),
                str(data.get("narrative_role", "SETUP")).upper(),
                int(data.get("sequence_order", 0)),
            )
            return _json_response(self, result)
        except (ValueError, TypeError) as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_story_remove_moment(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        try:
            result = story_remove_moment(job_id, story_id, str(data.get("moment_id", "")))
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_story_update_moment(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        try:
            result = story_update_moment(
                job_id,
                story_id,
                str(data.get("moment_id", "")),
                narrative_role=str(data.get("narrative_role", "")).upper() or None,
                sequence_order=int(data["sequence_order"]) if data.get("sequence_order") not in (None, "") else None,
            )
            return _json_response(self, result)
        except (ValueError, TypeError) as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_build_render(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        fmt = data.get("format", "SHORT")
        mode = data.get("mode", "REFERENCE")
        preset_id = data.get("preset_id") or None
        try:
            result = generate_canonical_render(job_id, story_id, fmt, mode=mode, preset_id=preset_id)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_review_render(self, job_id: str, render_id: str):
        data = _parse_form_body(self)
        review_state = str(data.get("review_state", "")).strip().upper()
        reviewed_by = str(data.get("reviewed_by", "")).strip() or None
        review_note = str(data.get("review_note", "")).strip() or None
        try:
            result = review_render(job_id, render_id, review_state, reviewed_by=reviewed_by, review_note=review_note)
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_create_export(self, job_id: str, render_id: str):
        data = _parse_form_body(self)
        try:
            result = create_export_package(
                job_id,
                render_id,
                str(data.get("preset_id", "")),
                caption=str(data.get("caption", "")),
                title=str(data.get("title", "")),
                description=str(data.get("description", "")),
                hashtags=data.get("hashtags") or [],
            )
            return _json_response(self, result)
        except ValueError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _serve_render_video(self, job_id: str, render_id: str):
        """Serve a registered render artifact safely, without path traversal."""
        if is_demo_mode():
            return _json_response(self, {"ok": False, "error": "Playable render media is not included in this hosted demo."}, 404)
        import mimetypes
        try:
            path = resolve_render_artifact(job_id, render_id)
        except (ValueError, OSError, JobNotFoundError) as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 404)
        video_path = Path(path)
        if not video_path.exists() or not video_path.is_file():
            return _json_response(self, {"ok": False, "error": "render artifact not found"}, 404)
        mime_type, _ = mimetypes.guess_type(str(video_path))
        if not mime_type or not mime_type.startswith("video/"):
            mime_type = "video/mp4"
        _stream_media_file(self, video_path, mime_type)

    def _serve_source_video(self, project_id: str, artifact_id: str):
        """Serve a registered source-media artifact safely for local preview."""
        if is_demo_mode():
            return _json_response(self, {"ok": False, "error": "Source media is not included in this hosted demo."}, 404)
        import mimetypes
        from pipeline.runtime_service import get_artifact
        artifact = get_artifact(artifact_id) if artifact_id else None
        if artifact is None or artifact.project_id != project_id:
            return _json_response(self, {"ok": False, "error": "source artifact not found"}, 404)
        video_path = Path(artifact.path)
        if not video_path.exists() or not video_path.is_file():
            return _json_response(self, {"ok": False, "error": "source media not found"}, 404)
        mime_type, _ = mimetypes.guess_type(str(video_path))
        if not mime_type or not mime_type.startswith("video/"):
            mime_type = "video/mp4"
        _stream_media_file(self, video_path, mime_type)

    def _serve_video(self, path: str):
        """Serve rendered video files."""
        if is_demo_mode():
            return _json_response(self, {"error": "Video playback is not included in this hosted demo."}, 404)
        import mimetypes
        from pathlib import Path
        # path: /video/{job_id}/{filename}
        parts = path.split("/video/")
        if len(parts) < 2:
            return _json_response(self, {"error": "invalid path"}, 404)
        subpath = parts[1]
        # Resolve to jobs dir
        jobs_dir = os.environ.get("STADIUM_PILOT_JOBS_DIR") or str(Path.cwd() / "jobs")
        video_path = Path(jobs_dir) / "RENDERS" / subpath
        if not video_path.exists():
            return _json_response(self, {"error": "video not found"}, 404)
        mime_type, _ = mimetypes.guess_type(str(video_path))
        if not mime_type:
            mime_type = "video/mp4"
        _stream_media_file(self, video_path, mime_type)


def run_server(host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> None:
    """Start the Operator Console server. Blocks until interrupted."""
    server = ThreadingHTTPServer((host, port), ConsoleHandler)
    print(f"Operator Console running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
