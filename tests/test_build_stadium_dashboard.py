import json
from pathlib import Path

from scripts.build_stadium_dashboard import (
    discover_clips,
    read_manifest,
    read_research,
    read_detections,
    build_html,
    path_to_file_url,
    html_escape,
    main,
)


SAMPLE_MANIFEST_CSV = (
    "clip_id,match_title,source_file,start_time,end_time,moment_label,emotional_angle,platform,export_profile,local_export_path,status,reason,updated_at\n"
    "clip_001,Test Match,source.mp4,00:01:00,00:01:15,Test Moment,Test Emotion,shorts,vertical_clean,/tmp/clips/test_match/clip_001.mp4,exported,,2026-01-01T00:00:00\n"
)

SAMPLE_RESEARCH = {
    "match": {
        "home_team": "Germany",
        "away_team": "Italy",
        "competition": "UEFA Euro 2012",
        "stage": "Semi-final",
    },
    "events": [
        {"video_time_seconds": 1230, "type": "goal", "description": "Balotelli header", "importance": "high"},
        {"video_time_seconds": 2190, "type": "goal", "description": "Balotelli thunderstrike", "importance": "high"},
    ],
}

SAMPLE_DETECTION = [
    {
        "sequence_order": 1,
        "narrative_role": "setup",
        "clip_id": "001",
        "category": "EMOTION",
        "start_time": "30",
        "end_time": "51",
        "editorial_thesis": "The thesis of this moment.",
        "emotional_angle": "Excitement and tension",
        "legacy_value": 7,
        "virality_score": 8,
    },
    {
        "sequence_order": 2,
        "narrative_role": "rupture",
        "clip_id": "002",
        "category": "CHAOS",
        "start_time": "52",
        "end_time": "70",
        "editorial_thesis": "The rupture moment.",
        "emotional_angle": "Shock and disbelief",
        "legacy_value": 9,
    },
]


# ── discover_clips ─────────────────────────────────────────────────────────


def test_discover_clips_scans_mp4_files(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    match_dir = clips_dir / "test_match"
    match_dir.mkdir(parents=True)
    video = match_dir / "test_001.mp4"
    video.write_text("fake mp4")

    rows = discover_clips(clips_dir, [], {}, {})
    assert len(rows) == 1
    assert rows[0]["clip_id"] == "test_001"
    assert rows[0]["match_slug"] == "test_match"
    assert "file_size_mb" in rows[0]
    assert rows[0]["media_url"].startswith("file://")


def test_discover_clips_empty_directory(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    clips_dir.mkdir()
    rows = discover_clips(clips_dir, [], {}, {})
    assert rows == []


def test_discover_clips_ignores_non_video_files(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    clips_dir.mkdir()
    (clips_dir / "notes.txt").write_text("not a video")
    (clips_dir / "clip.mp4").write_text("video")
    rows = discover_clips(clips_dir, [], {}, {})
    assert len(rows) == 1
    assert rows[0]["clip_id"] == "clip"


def test_discover_clips_matches_manifest_by_path(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    match_dir = clips_dir / "test_match"
    match_dir.mkdir(parents=True)
    video = match_dir / "clip_001.mp4"
    video.write_text("fake mp4")

    local_path = str(video.resolve())
    manifest_csv = (
        "clip_id,match_title,source_file,start_time,end_time,moment_label,emotional_angle,platform,export_profile,local_export_path,status,reason,updated_at\n"
        f"clip_001,Test Match,source.mp4,00:01:00,00:01:15,Test Moment,Test Emotion,shorts,vertical_clean,{local_path},exported,,2026-01-01\n"
    )
    manifest_path = tmp_path / "manifest.csv"
    manifest_path.write_text(manifest_csv)
    manifest_rows = read_manifest(manifest_path)

    rows = discover_clips(clips_dir, manifest_rows, {}, {})
    assert len(rows) == 1
    assert rows[0]["status"] == "exported"
    assert rows[0]["moment_label"] == "Test Moment"
    assert rows[0]["emotional_angle"] == "Test Emotion"


def test_discover_clips_shows_unmatched_manifest_rows(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    clips_dir.mkdir()

    manifest_rows = [
        {
            "clip_id": "missing_001",
            "match_title": "Missing Match",
            "local_export_path": str(tmp_path / "nonexistent" / "clip_001.mp4"),
            "status": "failed",
            "moment_label": "Missing Clip",
            "emotional_angle": "",
            "source_file": "",
            "start_time": "",
            "end_time": "",
            "platform": "",
            "export_profile": "",
        }
    ]
    rows = discover_clips(clips_dir, manifest_rows, {}, {})
    assert len(rows) == 1
    assert rows[0]["status"] == "failed"
    assert rows[0]["media_url"] == ""


def test_discover_clips_attaches_detection(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    match_dir = clips_dir / "test_match"
    match_dir.mkdir(parents=True)
    video = match_dir / "clip_001.mp4"
    video.write_text("fake mp4")

    detections = {"test_match_qwen15b_clips": SAMPLE_DETECTION}
    rows = discover_clips(clips_dir, [], {}, detections)
    assert len(rows) == 1
    assert len(rows[0]["_detection"]) == 2
    assert rows[0]["_detection"][0]["editorial_thesis"] == "The thesis of this moment."


# ── read_manifest ──────────────────────────────────────────────────────────


def test_read_manifest_parses_csv(tmp_path):
    manifest = tmp_path / "manifest.csv"
    manifest.write_text(SAMPLE_MANIFEST_CSV)
    rows = read_manifest(manifest)
    assert len(rows) == 1
    assert rows[0]["clip_id"] == "clip_001"
    assert rows[0]["match_title"] == "Test Match"
    assert rows[0]["status"] == "exported"


def test_read_manifest_not_found(tmp_path):
    rows = read_manifest(tmp_path / "nonexistent.csv")
    assert rows == []


def test_read_manifest_handles_bom(tmp_path):
    manifest = tmp_path / "manifest.csv"
    bom_content = "\ufeff" + SAMPLE_MANIFEST_CSV
    manifest.write_text(bom_content)
    rows = read_manifest(manifest)
    assert len(rows) == 1
    assert rows[0]["clip_id"] == "clip_001"


# ── read_research ──────────────────────────────────────────────────────────


def test_read_research_finds_json(tmp_path):
    research_dir = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "test_match"
    research_dir.mkdir(parents=True)
    (research_dir / "match_research.json").write_text(json.dumps(SAMPLE_RESEARCH))

    result = read_research(tmp_path / "MATCH_RESEARCH")
    assert "test_match" in result
    assert result["test_match"]["match"]["home_team"] == "Germany"
    assert len(result["test_match"]["events"]) == 2


def test_read_research_empty_directory(tmp_path):
    research_dir = tmp_path / "MATCH_RESEARCH"
    research_dir.mkdir()
    result = read_research(research_dir)
    assert result == {}


def test_read_research_no_file(tmp_path):
    result = read_research(tmp_path / "nonexistent")
    assert result == {}


def test_read_research_skips_invalid_json(tmp_path):
    research_dir = tmp_path / "MATCH_RESEARCH" / "bad_match"
    research_dir.mkdir(parents=True)
    (research_dir / "match_research.json").write_text("not valid json")
    result = read_research(tmp_path / "MATCH_RESEARCH")
    assert result == {}


# ── read_detections ────────────────────────────────────────────────────────


def test_read_detections_finds_json(tmp_path):
    detections_dir = tmp_path / "DETECTIONS"
    detections_dir.mkdir()
    (detections_dir / "test_match_qwen15b_clips.json").write_text(json.dumps(SAMPLE_DETECTION))

    result = read_detections(detections_dir)
    assert "test_match_qwen15b_clips" in result
    assert len(result["test_match_qwen15b_clips"]) == 2


def test_read_detections_not_found(tmp_path):
    result = read_detections(tmp_path / "nonexistent")
    assert result == {}


def test_read_detections_handles_wrapped_json(tmp_path):
    detections_dir = tmp_path / "DETECTIONS"
    detections_dir.mkdir()
    wrapped = {"clips": SAMPLE_DETECTION}
    (detections_dir / "test_clips.json").write_text(json.dumps(wrapped))
    result = read_detections(detections_dir)
    assert len(result["test_clips"]) == 2


# ── build_html ─────────────────────────────────────────────────────────────


def test_build_html_returns_string():
    rows = [
        {
            "clip_id": "test_001",
            "match_title": "Test Match",
            "source_file": "source.mp4",
            "start_time": "00:01:00",
            "end_time": "00:01:15",
            "moment_label": "Test Moment",
            "emotional_angle": "Test Emotion",
            "platform": "shorts",
            "export_profile": "vertical_clean",
            "status": "exported",
            "match_slug": "test_match",
            "media_url": "file:///tmp/video.mp4",
            "file_size_mb": "1.2",
            "_detection": [],
            "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test Dashboard", "2026-01-01T00:00:00")
    assert isinstance(html, str)
    assert len(html) > 500
    assert "Test Dashboard" in html
    assert "Test Moment" in html
    assert "clip-data" in html
    assert "research-data" in html
    assert "33" not in html  # not hardcoded from our real data


def test_build_html_contains_editorial_fields(tmp_path):
    rows = [
        {
            "clip_id": "det_001",
            "match_title": "Detection Test",
            "match_slug": "detection_test",
            "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5",
            "start_time": "10",
            "end_time": "20",
            "status": "exported",
            "moment_label": "Detected Moment",
            "emotional_angle": "Tension",
            "_detection": [
                {
                    "editorial_thesis": "This is the editorial thesis.",
                    "emotional_angle": "Excitement and tension",
                    "legacy_value": 8,
                    "virality_score": 7,
                    "narrative_role": "setup",
                    "category": "EMOTION",
                }
            ],
            "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "This is the editorial thesis." in html
    assert "Excitement and tension" in html
    assert "8" in html  # legacy_value
    assert "7" in html  # virality_score


def test_build_html_contains_research_data():
    rows = [
        {
            "clip_id": "res_001",
            "match_title": "Research Test",
            "match_slug": "research_test",
            "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5",
            "start_time": "",
            "end_time": "",
            "status": "",
            "moment_label": "Research Moment",
            "emotional_angle": "",
            "_detection": [],
            "_research": SAMPLE_RESEARCH,
        }
    ]
    research_by_match = {"research_test": SAMPLE_RESEARCH}
    html = build_html(rows, research_by_match, "Test", "now")
    # Research data is embedded as JSON in <script id="research-data">
    # and rendered by JS. Check the JSON data is present.
    assert '"Balotelli header"' in html
    assert '"Balotelli thunderstrike"' in html
    assert '"Germany"' in html  # home_team in embedded research JSON
    assert '"Italy"' in html     # away_team in embedded research JSON
    assert '"UEFA Euro 2012"' in html


def test_build_html_escapes_content():
    rows = [
        {
            "clip_id": "esc_001",
            "match_title": 'Safe Match',
            "match_slug": "esc_test",
            "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5",
            "start_time": "",
            "end_time": "",
            "status": "",
            "moment_label": 'Moment',
            "emotional_angle": "",
            "_detection": [],
            "_research": {},
        }
    ]
    html = build_html(rows, {}, '<script>alert(1)</script>', "now")
    # Title is rendered via html_escape() in the static template
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in html
    assert "<script>alert(1)</script>" not in html


# ── main ───────────────────────────────────────────────────────────────────


def test_main_writes_output_file(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    match_dir = clips_dir / "test_match"
    match_dir.mkdir(parents=True)
    (match_dir / "clip_001.mp4").write_text("fake mp4")
    output = tmp_path / "dashboard.html"

    exit_code = main([
        "--clips-dir", str(clips_dir),
        "--output", str(output),
    ])

    assert exit_code == 0
    assert output.exists()
    content = output.read_text()
    assert "clip_001" in content
    assert "clip-data" in content


def test_main_default_output_path(tmp_path):
    clips_dir = tmp_path / "FootballArchive" / "CLIPS"
    clips_dir.mkdir(parents=True)
    (clips_dir / "clip.mp4").write_text("fake mp4")

    # main uses ROOT-based defaults, so we override both clips-dir and output
    output = clips_dir / "review_dashboard.html"
    exit_code = main([
        "--clips-dir", str(clips_dir),
        "--output", str(output),
    ])
    assert exit_code == 0
    assert output.exists()


# ── edge cases ─────────────────────────────────────────────────────────────


def test_build_html_with_no_clips():
    html = build_html([], {}, "Empty Dashboard", "now")
    assert "Empty Dashboard" in html
    assert "0 clips" in html or "0" in html


def test_discover_clips_with_nested_directories(tmp_path):
    clips_dir = tmp_path / "CLIPS"
    match_dir = clips_dir / "match_a"
    match_dir.mkdir(parents=True)
    (match_dir / "clip_a1.mp4").write_text("data")
    sub_dir = match_dir / "extra"
    sub_dir.mkdir()
    (sub_dir / "clip_a2.mp4").write_text("data")
    (clips_dir / "root_clip.mp4").write_text("data")

    rows = discover_clips(clips_dir, [], {}, {})
    assert len(rows) == 3


# ── helpers ────────────────────────────────────────────────────────────────


def test_html_escape():
    assert html_escape('<script>"&\'') == "&lt;script&gt;&quot;&amp;&#039;"
    assert html_escape("safe text") == "safe text"


def test_path_to_file_url(tmp_path):
    p = tmp_path / "test.mp4"
    p.write_text("data")
    url = path_to_file_url(p)
    assert url.startswith("file://")
    assert "test.mp4" in url


# ── review dashboard new features ──────────────────────────────────────────

def test_build_html_contains_clip_category():
    rows = [
        {
            "clip_id": "cat_001",
            "match_title": "Category Test",
            "match_slug": "cat_test",
            "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5",
            "start_time": "00:01:00",
            "end_time": "00:01:15",
            "status": "exported",
            "moment_label": "Category Moment",
            "emotional_angle": "Tension",
            "clip_category": "goal_strike",
            "export_profile": "goal_context",
            "_detection": [],
            "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "goal_strike" in html
    assert "goal_context" in html


def test_build_html_contains_review_buttons():
    rows = [
        {
            "clip_id": "rvw_001", "match_title": "Review Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "", "moment_label": "Test", "emotional_angle": "",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "Keep" in html
    assert "Needs crop" in html
    assert "Needs trim" in html
    assert "Discard" in html


def test_build_html_contains_notes_textarea():
    rows = [
        {
            "clip_id": "note_001", "match_title": "Notes Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "", "moment_label": "Test", "emotional_angle": "",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "review-notes" in html
    assert "Review notes" in html


def test_build_html_contains_copy_path():
    rows = [
        {
            "clip_id": "copy_001", "match_title": "Copy Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "", "moment_label": "Test", "emotional_angle": "",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "Copy path" in html
    assert "filePathCode" in html
    assert "copyPathBtn" in html


def test_build_html_contains_duration_function():
    rows = [
        {
            "clip_id": "dur_001", "match_title": "Duration Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "00:01:00",
            "end_time": "00:01:15", "status": "", "moment_label": "Test",
            "emotional_angle": "", "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "durationStr" in html
    assert "function durationStr" in html


def test_build_html_contains_localStorage_calls():
    rows = [
        {
            "clip_id": "ls_001", "match_title": "LS Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "", "moment_label": "Test", "emotional_angle": "",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "localStorage.getItem" in html
    assert "localStorage.setItem" in html


def test_build_html_contains_filter_dropdowns():
    rows = [
        {
            "clip_id": "flt_001", "match_title": "Filter Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "exported", "moment_label": "Test", "emotional_angle": "",
            "export_profile": "vertical_clean", "clip_category": "goal_strike",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "statusFilter" in html
    assert "profileFilter" in html
    assert "categoryFilter" in html
    assert "All statuses" in html
    assert "All profiles" in html
    assert "All categories" in html


def test_build_html_contains_review_dot():
    rows = [
        {
            "clip_id": "dot_001", "match_title": "Dot Test",
            "match_slug": "test", "media_url": "file:///tmp/v.mp4",
            "file_size_mb": "0.5", "start_time": "", "end_time": "",
            "status": "", "moment_label": "Test", "emotional_angle": "",
            "_detection": [], "_research": {},
        }
    ]
    html = build_html(rows, {}, "Test", "now")
    assert "review-dot" in html
    assert "rd-" in html
