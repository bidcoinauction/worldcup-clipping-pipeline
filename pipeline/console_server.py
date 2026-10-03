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
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from pipeline.operator_console import (
    list_available_sports,
    list_projects,
    get_project,
    get_project_status,
    validate_project_intake,
    create_project,
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
    generate_brief,
    get_brief,
    get_brief_status,
    confirm_project_rights,
    list_all_briefs,
    build_timeline,
    get_edl,
    get_edl_status,
    list_all_edls,
    render_rough_cut,
    get_render_status,
    get_render_capabilities,
)
from pipeline.pilot import JobExistsError, JobNotFoundError, JobRevisionError, JobTransitionError

_TEMPLATE_DIR = Path(__file__).resolve().parent / "console_templates"
_STATIC_DIR = Path(__file__).resolve().parent / "console_static"
_DEFAULT_PORT = 8420

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

    def log_message(self, fmt, *args):  # noqa: ANN001
        pass  # suppress noisy request logging

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        qs = parse_qs(parsed.query)

        if path == "/style.css":
            return _css_response(self)

        if path == "/":
            return self._render_projects()
        if path == "/projects/new":
            return self._render_new_project(qs)
        if path.startswith("/projects/") and "/status" in path:
            job_id = path.split("/projects/")[1].split("/status")[0]
            return self._render_project_status(job_id)
        if path.startswith("/projects/"):
            job_id = path.split("/projects/")[1]
            return self._render_project_detail(job_id)
        if path == "/api/sports":
            return _json_response(self, list_available_sports())
        if path == "/api/projects":
            return _json_response(self, list_projects())

        _html_response(self, self._page("Not Found", "<h1>404</h1><p>Page not found.</p>"), 404)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/")

        if path == "/api/projects/create":
            return self._api_create_project()
        if path.startswith("/api/projects/") and path.endswith("/transition"):
            job_id = path.split("/api/projects/")[1].split("/transition")[0]
            return self._api_transition_project(job_id)
        if path.startswith("/api/projects/") and path.endswith("/rights/confirm"):
            job_id = path.split("/api/projects/")[1].split("/rights/confirm")[0]
            return self._api_confirm_rights(job_id)
        if path.startswith("/api/projects/") and path.endswith("/analyze"):
            job_id = path.split("/api/projects/")[1].split("/analyze")[0]
            return self._api_analyze_project(job_id)
        if path.startswith("/api/projects/") and path.endswith("/stories"):
            job_id = path.split("/api/projects/")[1].split("/stories")[0]
            return self._api_generate_stories(job_id)
        if "/api/projects/" in path and "/stories/" in path and path.endswith("/brief"):
            parts = path.split("/api/projects/")[1].split("/")
            job_id = parts[0]
            story_id = parts[2]
            return self._api_generate_brief(job_id, story_id)
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
        if path == "/api/intake/validate":
            return self._api_validate_intake()
        if path.startswith("/video/"):
            return self._serve_video(path)

        _json_response(self, {"error": "not found"}, 404)

    # ── Page renders ─────────────────────────────────────────────────────────

    def _page(self, title: str, content: str) -> str:
        base = _read_template("base.html")
        return base.replace("{{TITLE}}", title).replace("{{CONTENT}}", content)

    def _render_projects(self):
        projects = list_projects()
        sports = list_available_sports()
        rows = ""
        for p in projects:
            state_class = p["current_state"].lower().replace("_", "-")
            rows += f"""
            <tr>
              <td><a href="/projects/{p['job_id']}">{p['job_id']}</a></td>
              <td>{p['project_id']}</td>
              <td><span class=\"badge badge-{state_class}\">{p['current_state']}</span></td>
              <td>{p['created_at'][:19] if p['created_at'] else '—'}</td>
            </tr>
            """
        if not projects:
            rows = '<tr><td colspan="4" class="empty">No projects yet. Create one to get started.</td></tr>'

        sport_cards = ""
        for s in sports:
            marker = " (default)" if s["default"] else ""
            safe = "production-ready" if s["production_safe"] else "sandbox only"
            analysis = "analysis: yes" if s["analysis_supported"] else "analysis: not yet"
            sport_cards += f'<div class="sport-card"><strong>{s["display_name"]}</strong><br><span class="muted">{safe}{marker}</span><br><span class="muted">{analysis}</span></div>'

        content = f"""
        <div class="grid">
          <section>
            <h2>Projects</h2>
            <a href="/projects/new" class="btn btn-primary">New Project</a>
            <table>
              <thead><tr><th>Project</th><th>Sport</th><th>State</th><th>Created</th></tr></thead>
              <tbody>{rows}</tbody>
            </table>
          </section>
          <section>
            <h2>Sports</h2>
            <div class="sport-grid">{sport_cards}</div>
          </section>
        </div>
        """
        return _html_response(self, self._page("Projects", content))

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
        <div class="card">
          <h2>New Project</h2>
          {error}
          <form id="new-project-form" onsubmit="return handleSubmit(event)">
            <div class="form-group">
              <label for="sport">Sport</label>
              <select id="sport" name="sport">{sport_options}</select>
            </div>
            <div class="form-row">
              <div class="form-group">
                <label for="pilot_id">Project ID</label>
                <input type="text" id="pilot_id" name="pilot_id" placeholder="e.g. project_alpha" required pattern="[A-Za-z0-9_-]+">
              </div>
              <div class="form-group">
                <label for="source_id">Source ID</label>
                <input type="text" id="source_id" name="source_id" placeholder="e.g. source_001" required pattern="[A-Za-z0-9_-]+">
              </div>
            </div>
            <div class="form-group">
              <label for="event_name">Event / Match Name</label>
              <input type="text" id="event_name" name="event_name" placeholder="e.g. Mexico vs South Africa">
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
            <div class="form-group">
              <label for="local_file_path">Media File Path</label>
              <input type="text" id="local_file_path" name="local_file_path" placeholder="/path/to/source.ts">
            </div>
            <div class="form-actions">
              <a href="/" class="btn">Cancel</a>
              <button type="submit" class="btn btn-primary">Create Project</button>
            </div>
          </form>
        </div>
        <script>
        async function handleSubmit(e) {{
          e.preventDefault();
          const form = e.target;
          const data = {{
            sport: form.sport.value,
            pilot_id: form.pilot_id.value.trim(),
            source_id: form.source_id.value.trim(),
            event_name: form.event_name.value.trim(),
            reference_deployment: form.reference_deployment.value.trim(),
            delivery_method: form.delivery_method.value,
            local_file_path: form.local_file_path.value.trim(),
          }};
          const resp = await fetch("/api/projects/create", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify(data),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.href = "/projects/" + result.job_id;
          }} else {{
            window.location.href = "/projects/new?error=" + encodeURIComponent(result.error);
          }}
        }}
        </script>
        """
        return _html_response(self, self._page("New Project", content))

    def _render_project_detail(self, job_id: str):
        try:
            detail = get_project(job_id)
        except Exception as exc:
            return _html_response(self, self._page("Error", f'<div class="card"><h2>Error</h2><p>{exc}</p><a href="/" class="btn">Back</a></div>'), 404)

        state = detail.get("current_state", "")
        transitions = detail.get("allowed_next_states", [])
        events = detail.get("events", [])
        readiness = detail.get("readiness_summary", {})

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

        # Moments
        moments = list_moments(job_id)

        # Stories
        try:
            story_status = get_story_status(job_id)
        except Exception:
            story_status = {"story_status": "", "story_count": 0, "story_error": ""}
        stories = list_stories(job_id)

        # Source file from job intake
        source_file = ""
        intake_path = detail.get("intake_manifest_path", "")
        if intake_path:
            try:
                intake_data = json.loads(Path(intake_path).read_text(encoding="utf-8"))
                source_file = intake_data.get("media", {}).get("local_file_path", "")
                if not source_file:
                    source_file = intake_data.get("media", {}).get("original_filename", "")
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

        # Moments section
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
            <div class="card">
              <h3>Moments ({len(moments)})</h3>
              <table>
                <thead><tr><th>Category</th><th>Start</th><th>End</th><th>Score</th><th>Caption</th><th>Status</th></tr></thead>
                <tbody>{moment_rows}</tbody>
              </table>
            </div>
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

        content = f"""
        <div class="card">
          <div class="card-header">
            <div>
              <h2>{job_id}</h2>
              <span class="muted">{detail.get('project_id', '').title()}</span>
            </div>
            <span class="badge badge-{state_class}">{state}</span>
          </div>
          <div class="detail-grid">
              <div><strong>Source:</strong> {_escape(source_file) if source_file else '—'}</div>
            <div><strong>Source status:</strong> <span class="{source_status_class}">{source_status_text}</span></div>
            <div><strong>Transcript:</strong> <span class="{transcript_class}">{transcript_text}</span></div>
            <div><strong>Created:</strong> {detail.get('created_at', '—')[:19]}</div>
            <div><strong>Updated:</strong> {detail.get('updated_at', '—')[:19]}</div>
          </div>
        </div>

        <div class="card">
          <h3>Analysis</h3>
          {analysis_section}
          <div class="form-actions" style="margin-top:1rem;">
            {analyze_btn}
          </div>
        </div>

        {moments_section}

        {stories_section}

        <div class="grid">
          <section class="card">
            <h3>Actions</h3>
            <div class="transition-buttons">{transition_buttons or '<span class="muted">No transitions available</span>'}</div>
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

        <div class="card">
          <h3>Event History</h3>
          <table>
            <thead><tr><th>#</th><th>Type</th><th>Transition</th><th>Message</th><th>Time</th></tr></thead>
            <tbody>{event_rows or '<tr><td colspan="5" class="empty">No events yet.</td></tr>'}</tbody>
          </table>
        </div>

        <a href="/" class="btn">← Back to Projects</a>

        <script>
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
            alert("Transition failed: " + result.error);
          }}
        }}

        async function doConfirmRights() {{
          const operator = prompt("Operator name (optional):") || "";
          const confirmedBy = prompt("Confirmed by (rights owner/client):", operator || "console_operator") || "";
          if (!confirmedBy.trim()) {{
            alert("Rights confirmation requires a confirmer.");
            return;
          }}
          const statement = prompt("Confirmation statement:", "Rights confirmed for clipping, storage, review, and delivery.") || "";
          if (!statement.trim()) {{
            alert("Rights confirmation requires a statement.");
            return;
          }}
          const today = new Date().toISOString().slice(0, 10);
          const confirmationDate = prompt("Confirmation date (YYYY-MM-DD):", today) || "";
          if (!confirmationDate.trim()) {{
            alert("Rights confirmation requires a date.");
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
            alert("Confirm Rights failed: " + (result.error || "Unknown error"));
          }}
        }}

        async function doCancelProject() {{
          const reason = prompt("Cancellation reason (required):") || "";
          if (!reason.trim()) {{
            alert("Cancel Project requires a reason.");
            return;
          }}
          const operator = prompt("Operator name:", "console_operator") || "";
          if (!operator.trim()) {{
            alert("Cancel Project requires an operator name.");
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
            alert("Cancel Project failed: " + (result.error || "Unknown error"));
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
            alert("Analysis: " + (result.error || "Unknown error"));
            window.location.reload();
          }}
        }}

        async function doGenerateStories() {{
          const btn = document.querySelector('[onclick="doGenerateStories()"]');
          if (btn) {{
            btn.disabled = true;
            btn.textContent = "Generating stories...";
          }}
          const resp = await fetch("/api/projects/{job_id}/stories", {{
            method: "POST",
            headers: {{"Content-Type": "application/json"}},
            body: JSON.stringify({{}}),
          }});
          const result = await resp.json();
          if (result.ok) {{
            window.location.reload();
          }} else {{
            alert("Stories: " + (result.error || "Unknown error"));
            window.location.reload();
          }}
        }}

        async function doGenerateBrief(storyId, format) {{
          const btn = event.target;
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
            alert("Edit Brief: " + (result.error || "Unknown error"));
            window.location.reload();
          }}
        }}

        async function doBuildEdl(storyId, format) {{
          const btn = event.target;
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
            alert("Timeline: " + (result.error || "Unknown error"));
            window.location.reload();
          }}
        }}

        async function doRender(storyId, format, mode) {{
          const btn = event.target;
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
            alert("Render: " + (result.error || "Unknown error"));
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
                "source_validation_completed": False,
            },
            "rights": {
                "status": "UNCONFIRMED",
                "permitted_uses": ["review"],
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

        try:
            job = create_project(intake, operator="console_operator")
            return _json_response(self, {"ok": True, "job_id": job["job_id"], "state": job["current_state"]})
        except JobExistsError as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 409)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 400)

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
            result = analyze_project(job_id)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
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
            result = generate_brief(job_id, story_id, fmt)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _api_build_render(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        fmt = data.get("format", "SHORT")
        mode = data.get("mode", "REFERENCE")
        try:
            result = render_rough_cut(job_id, story_id, fmt, mode=mode)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)

    def _serve_video(self, path: str):
        """Serve rendered video files."""
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
        self.send_response(200)
        self.send_header("Content-Type", mime_type)
        self.send_header("Content-Length", str(video_path.stat().st_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        with open(video_path, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def _api_build_edl(self, job_id: str, story_id: str):
        data = _parse_form_body(self)
        fmt = data.get("format", "SHORT")
        try:
            result = build_timeline(job_id, story_id, fmt)
            status_code = 200 if result.get("ok") else 400
            return _json_response(self, result, status_code)
        except Exception as exc:
            return _json_response(self, {"ok": False, "error": str(exc)}, 500)


def run_server(host: str = "127.0.0.1", port: int = _DEFAULT_PORT) -> None:
    """Start the Operator Console server. Blocks until interrupted."""
    server = HTTPServer((host, port), ConsoleHandler)
    print(f"Operator Console running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()
