from unittest.mock import MagicMock, patch
from pathlib import Path
import csv
import io

from pipeline.utils import slugify, timestamp_to_seconds
from scripts.export_research_windows import (
    ClipRow,
    read_rows,
    resolve_source,
    export_path,
    ffmpeg_filter,
    export_clip,
    append_manifest,
    parse_timestamp,
    build_manifest_row,
)


SAMPLE_CSV = (
    "clip_id,match_title,source_file,start_time,end_time,moment_label,emotional_angle,platform,export_profile\n"
    "test_001,Test Match,germany_italy_2012.mp4,00:01:00,00:01:15,Moment One,Excitement,shorts,vertical_clean\n"
    "test_002,Test Match,germany_italy_2012.mp4,00:05:00,00:05:30,Moment Two,Tension,shorts,vertical_blur\n"
)

ROW_1 = ClipRow(
    clip_id="test_001",
    match_title="Test Match",
    source_file="germany_italy_2012.mp4",
    start_time="00:01:00",
    end_time="00:01:15",
    moment_label="Moment One",
    emotional_angle="Excitement",
    platform="shorts",
    export_profile="vertical_clean",
)

ROW_2 = ClipRow(
    clip_id="test_002",
    match_title="Test Match",
    source_file="germany_italy_2012.mp4",
    start_time="00:05:00",
    end_time="00:05:30",
    moment_label="Moment Two",
    emotional_angle="Tension",
    platform="shorts",
    export_profile="vertical_blur",
)


# ── read_rows ──────────────────────────────────────────────────────────────

def test_read_rows_parses_valid_csv(tmp_path):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(SAMPLE_CSV)
    rows = read_rows(csv_file, "vertical_clean")
    assert len(rows) == 2
    assert rows[0] == ROW_1
    assert rows[1] == ROW_2


def test_read_rows_missing_required_fields(tmp_path):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(
        "clip_id,match_title,source_file,start_time,end_time\n"
        "valid,Match,match.mp4,00:00:00,00:00:10\n"
        ",Match,match.mp4,00:00:00,00:00:10\n"
        "no_source,Match,,00:00:00,00:00:10\n"
    )
    rows = read_rows(csv_file, "source")
    assert len(rows) == 1
    assert rows[0].clip_id == "valid"


def test_read_rows_field_fallbacks(tmp_path):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(
        "clip_id,match,source_video,start,end,event,hook\n"
        "fb_001,Fallback Match,match.mp4,00:01:00,00:01:15,The Event,The Hook\n"
    )
    rows = read_rows(csv_file, "source")
    assert len(rows) == 1
    r = rows[0]
    assert r.clip_id == "fb_001"
    assert r.match_title == "Fallback Match"
    assert r.source_file == "match.mp4"
    assert r.start_time == "00:01:00"
    assert r.end_time == "00:01:15"
    assert r.moment_label == "The Event"
    assert r.emotional_angle == "The Hook"
    assert r.export_profile == "source"


def test_read_rows_skips_zero_length_clip(tmp_path):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(
        "clip_id,match_title,source_file,start_time,end_time\n"
        "good,Match,match.mp4,00:00:10,00:00:20\n"
        "bad,Match,match.mp4,00:00:30,00:00:30\n"
    )
    rows = read_rows(csv_file, "source")
    assert len(rows) == 1
    assert rows[0].clip_id == "good"


def test_read_rows_file_not_found():
    try:
        read_rows(Path("/nonexistent/path.csv"), "source")
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_read_rows_default_profile(tmp_path):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(
        "clip_id,match_title,source_file,start_time,end_time\n"
        "prof,Match,match.mp4,00:00:00,00:00:10\n"
    )
    rows = read_rows(csv_file, "vertical_blur")
    assert rows[0].export_profile == "vertical_blur"


# ── resolve_source ─────────────────────────────────────────────────────────

def test_resolve_source_absolute(tmp_path):
    source = tmp_path / "video.mp4"
    source.write_text("data")
    result = resolve_source(str(source), [])
    assert result == source.resolve()


def test_resolve_source_in_raw_dir(tmp_path):
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "match.mp4"
    source.write_text("data")
    result = resolve_source("match.mp4", [raw_dir])
    assert result == source.resolve()


def test_resolve_source_glob_match(tmp_path):
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "germany_italy_2012.mp4"
    source.write_text("data")
    result = resolve_source("germany*.mp4", [raw_dir])
    assert result == source.resolve()


def test_resolve_source_not_found(tmp_path):
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    try:
        resolve_source("nonexistent.mp4", [raw_dir])
        assert False, "Expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_resolve_source_searches_multiple_dirs(tmp_path):
    dir1 = tmp_path / "RAW1"
    dir1.mkdir()
    dir2 = tmp_path / "RAW2"
    dir2.mkdir()
    source = dir2 / "match.mp4"
    source.write_text("data")
    result = resolve_source("match.mp4", [dir1, dir2])
    assert result == source.resolve()


def test_resolve_source_prefers_first_match(tmp_path):
    dir1 = tmp_path / "RAW1"
    dir1.mkdir()
    dir2 = tmp_path / "RAW2"
    dir2.mkdir()
    s1 = dir1 / "match.mp4"
    s1.write_text("data1")
    s2 = dir2 / "match.mp4"
    s2.write_text("data2")
    result = resolve_source("match.mp4", [dir1, dir2])
    assert result == s1.resolve()


# ── export_path ────────────────────────────────────────────────────────────

def test_export_path_with_slug(tmp_path):
    row = ClipRow(
        clip_id="clip_001", match_title="Germany vs Italy 2012",
        source_file="", start_time="", end_time="",
        moment_label="", emotional_angle="", platform="", export_profile="",
    )
    path = export_path(row, tmp_path)
    assert path == tmp_path / "germany_vs_italy_2012" / "clip_001.mp4"


def test_export_path_creates_parent(tmp_path):
    row = ClipRow(
        clip_id="c1", match_title="Some Match",
        source_file="", start_time="", end_time="",
        moment_label="", emotional_angle="", platform="", export_profile="",
    )
    path = export_path(row, tmp_path)
    assert not path.parent.exists()
    path.parent.mkdir(parents=True)
    assert path.parent.exists()


# ── ffmpeg_filter ──────────────────────────────────────────────────────────

def test_ffmpeg_filter_source():
    result = ffmpeg_filter("source")
    assert result == ["-c", "copy"]


def test_ffmpeg_filter_vertical_clean():
    result = ffmpeg_filter("vertical_clean")
    assert "-filter_complex" in result
    fc_idx = result.index("-filter_complex")
    assert "boxblur=28:2" in result[fc_idx + 1]
    assert "scale=1080" in result[fc_idx + 1]
    assert "-c:v" in result


def test_ffmpeg_filter_vertical_blur():
    result = ffmpeg_filter("vertical_blur")
    assert "-filter_complex" in result
    fc_idx = result.index("-filter_complex")
    assert "boxblur=28:2" in result[fc_idx + 1]


def test_ffmpeg_filter_vertical_safe_contains_crop():
    result = ffmpeg_filter("vertical_safe")
    assert "-filter_complex" in result
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    # Should crop before split (crop appears before split=2)
    crop_idx = fg.index("crop=")
    split_idx = fg.index("split=2")
    assert crop_idx < split_idx, "crop must happen before split"
    assert "boxblur=28:2" in fg
    assert "scale=1080" in fg
    assert "-c:v" in result


def test_ffmpeg_filter_vertical_safe_uses_percentage_defaults():
    result = ffmpeg_filter("vertical_safe")
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    # top=0.18, bottom=0.02, left=0.0, right=0.08
    # keep_h = 1 - 0.18 - 0.02 = 0.80
    # keep_w = 1 - 0.0 - 0.08 = 0.92
    assert "iw*0.9200" in fg
    assert "ih*0.8000" in fg
    assert "iw*0.0000" in fg  # left offset
    assert "ih*0.1800" in fg  # top offset


def test_ffmpeg_filter_vertical_clean_unchanged():
    vc = ffmpeg_filter("vertical_clean")
    vs = ffmpeg_filter("vertical_safe")
    fc_vc = vc[vc.index("-filter_complex") + 1]
    fc_vs = vs[vs.index("-filter_complex") + 1]
    # vertical_clean should NOT have crop before split
    assert "crop=trunc" not in fc_vc or "split=2" not in fc_vc[:fc_vc.index("crop=trunc") + 20]
    # vertical_safe should have crop before split
    assert "crop=trunc" in fc_vs
    assert fc_vs.index("crop=") < fc_vs.index("split=2")


def test_ffmpeg_filter_source_unchanged():
    result = ffmpeg_filter("source")
    assert result == ["-c", "copy"]


def test_ffmpeg_filter_vertical_zoom_contains_zoom():
    result = ffmpeg_filter("vertical_zoom")
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    assert "scale=iw*1.5:ih*1.5" in fg


def test_ffmpeg_filter_vertical_zoom_keeps_safe_crop():
    vs = ffmpeg_filter("vertical_safe")
    vz = ffmpeg_filter("vertical_zoom")
    fc_vs = vs[vs.index("-filter_complex") + 1]
    fc_vz = vz[vz.index("-filter_complex") + 1]
    crop_vs = fc_vs[:fc_vs.index("split=2")]
    crop_vz = fc_vz[:fc_vz.index("split=2")]
    assert crop_vs == crop_vz, "vertical_zoom must use same crop as vertical_safe"


def test_ffmpeg_filter_vertical_social_contains_crop():
    result = ffmpeg_filter("vertical_social")
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    assert "iw*0.5500" in fg
    assert "ih*0.6000" in fg
    assert "iw*0.2250" in fg
    assert "ih*0.2200" in fg


def test_ffmpeg_filter_vertical_social_contains_zoom():
    result = ffmpeg_filter("vertical_social")
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    assert "scale=iw*1.6:ih*1.6" in fg


def test_ffmpeg_filter_vertical_social_has_crop_before_split():
    result = ffmpeg_filter("vertical_social")
    fc_idx = result.index("-filter_complex")
    fg = result[fc_idx + 1]
    crop_idx = fg.index("crop=")
    split_idx = fg.index("split=2")
    assert crop_idx < split_idx, "crop must happen before split"
    assert "boxblur=28:2" in fg


# ── export_clip ────────────────────────────────────────────────────────────

@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_builds_correct_command(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "out" / "clip.mp4"

    status, reason = export_clip(ROW_1, source, dest, force=False)

    assert status == "exported"
    assert reason == ""
    mock_run.assert_called_once()
    (cmd,) = mock_run.call_args[0]
    assert cmd[0] == "ffmpeg"
    assert "-ss" in cmd
    assert cmd[cmd.index("-ss") + 1] == "00:01:00"
    assert "-i" in cmd
    assert cmd[cmd.index("-i") + 1] == str(source.resolve())
    assert "-t" in cmd
    assert cmd[cmd.index("-t") + 1] == "15.000"
    assert cmd[-1] == str(dest)
    assert "-n" in cmd


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_force_flag(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "out" / "clip.mp4"

    export_clip(ROW_1, source, dest, force=True)
    (cmd,) = mock_run.call_args[0]
    assert "-y" in cmd


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_skips_existing_without_force(mock_run, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "existing.mp4"
    dest.write_text("already there")

    status, reason = export_clip(ROW_1, source, dest, force=False)
    assert status == "skipped"
    assert reason == "already_exported"
    mock_run.assert_not_called()


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_force_overwrites_existing(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "existing.mp4"
    dest.write_text("already there")

    status, reason = export_clip(ROW_1, source, dest, force=True)
    assert status == "exported"
    mock_run.assert_called_once()


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_creates_parent_directory(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "nested" / "deep" / "clip.mp4"

    export_clip(ROW_1, source, dest, force=False)
    assert dest.parent.exists()


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_handles_ffmpeg_failure(mock_run, tmp_path):
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "fail.mp4"
    mock_run.return_value = MagicMock(returncode=1, stderr="some error")

    status, reason = export_clip(ROW_1, source, dest, force=False)
    assert status == "failed"
    assert reason == "some error"


@patch("scripts.export_research_windows.subprocess.run")
def test_export_clip_with_vertical_safe_profile(mock_run, tmp_path):
    mock_run.return_value = MagicMock(returncode=0, stderr="")
    source = tmp_path / "source.mp4"
    source.write_text("data")
    dest = tmp_path / "safe.mp4"

    row = ClipRow(
        clip_id="safe_001",
        match_title="Safe Test",
        source_file="source.mp4",
        start_time="00:01:00",
        end_time="00:01:15",
        moment_label="Safe Moment",
        emotional_angle="Clean",
        platform="shorts",
        export_profile="vertical_safe",
    )

    status, reason = export_clip(row, source, dest, force=False)
    assert status == "exported"
    mock_run.assert_called_once()
    (cmd,) = mock_run.call_args[0]
    fc_idx = cmd.index("-filter_complex")
    fg = cmd[fc_idx + 1]
    assert "crop=trunc" in fg
    assert fg.index("crop=") < fg.index("split=2")
    assert "boxblur=28:2" in fg


# ── append_manifest ────────────────────────────────────────────────────────

def test_append_manifest_creates_new(tmp_path):
    manifest = tmp_path / "manifest.csv"
    rows = [
        {"clip_id": "c1", "status": "exported", "reason": "", "updated_at": "now"},
    ]
    append_manifest(rows, manifest)
    assert manifest.exists()
    content = manifest.read_text()
    assert "clip_id" in content
    assert "c1" in content


def test_append_manifest_merges_existing(tmp_path):
    manifest = tmp_path / "manifest.csv"
    existing_rows = [
        {"clip_id": "c1", "status": "exported", "reason": "", "updated_at": "old"},
        {"clip_id": "c2", "status": "skipped", "reason": "already_exported", "updated_at": "old"},
    ]
    append_manifest(existing_rows, manifest)

    new_rows = [
        {"clip_id": "c1", "status": "exported", "reason": "", "updated_at": "new"},
        {"clip_id": "c3", "status": "failed", "reason": "file not found", "updated_at": "new"},
    ]
    append_manifest(new_rows, manifest)

    with manifest.open("r", newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    ids = {r["clip_id"] for r in rows}
    assert ids == {"c1", "c2", "c3"}
    c1 = [r for r in rows if r["clip_id"] == "c1"][0]
    assert c1["status"] == "exported"
    assert c1["updated_at"] == "new"


# ── main (integration via capsys, mocked ffmpeg) ──────────────────────────

@patch("scripts.export_research_windows.subprocess.run")
def test_main_dry_run_does_not_call_ffmpeg(mock_run, tmp_path, capsys):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(SAMPLE_CSV)
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "germany_italy_2012.mp4"
    source.write_text("data")
    clips_dir = tmp_path / "CLIPS"
    manifest = tmp_path / "manifest.csv"

    from scripts.export_research_windows import main

    exit_code = main([
        "--csv", str(csv_file),
        "--clips-dir", str(clips_dir),
        "--manifest", str(manifest),
        "--raw-dir", str(raw_dir),
        "--dry-run",
    ])

    assert exit_code == 0
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "PLANNED" in captured.out


@patch("scripts.export_research_windows.subprocess.run")
def test_main_execute_calls_ffmpeg(mock_run, tmp_path, capsys):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(SAMPLE_CSV)
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "germany_italy_2012.mp4"
    source.write_text("data")
    clips_dir = tmp_path / "CLIPS"
    manifest = tmp_path / "manifest.csv"

    mock_run.return_value = MagicMock(returncode=0, stderr="")

    from scripts.export_research_windows import main

    exit_code = main([
        "--csv", str(csv_file),
        "--clips-dir", str(clips_dir),
        "--manifest", str(manifest),
        "--raw-dir", str(raw_dir),
        "--execute",
    ])

    assert exit_code == 0
    assert mock_run.call_count == 2
    captured = capsys.readouterr()
    assert "EXPORTED" in captured.out


@patch("scripts.export_research_windows.subprocess.run")
def test_main_source_not_found(mock_run, tmp_path, capsys):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(SAMPLE_CSV)
    clips_dir = tmp_path / "CLIPS"
    manifest = tmp_path / "manifest.csv"

    from scripts.export_research_windows import main

    exit_code = main([
        "--csv", str(csv_file),
        "--clips-dir", str(clips_dir),
        "--manifest", str(manifest),
        "--raw-dir", str(tmp_path),
        "--execute",
    ])

    assert exit_code == 1
    mock_run.assert_not_called()
    captured = capsys.readouterr()
    assert "FAILED" in captured.out


@patch("scripts.export_research_windows.subprocess.run")
def test_main_execute_with_force(mock_run, tmp_path, capsys):
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(SAMPLE_CSV)
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "germany_italy_2012.mp4"
    source.write_text("data")
    clips_dir = tmp_path / "CLIPS"
    manifest = tmp_path / "manifest.csv"

    mock_run.return_value = MagicMock(returncode=0, stderr="")

    from scripts.export_research_windows import main

    exit_code = main([
        "--csv", str(csv_file),
        "--clips-dir", str(clips_dir),
        "--manifest", str(manifest),
        "--raw-dir", str(raw_dir),
        "--execute",
        "--force",
    ])

    assert exit_code == 0
    assert mock_run.call_count == 2
    for call_args in mock_run.call_args_list:
        cmd = call_args[0][0]
        assert "-y" in cmd


@patch("scripts.export_research_windows.subprocess.run")
def test_main_execute_with_vertical_safe_profile(mock_run, tmp_path, capsys):
    # CSV without per-row export_profile — relies on --profile default
    csv_file = tmp_path / "windows.csv"
    csv_file.write_text(
        "clip_id,match_title,source_file,start_time,end_time,moment_label,emotional_angle\n"
        "safe_001,Safe Test,germany_italy_2012.mp4,00:01:00,00:01:15,Safe Moment,Clean\n"
        "safe_002,Safe Test,germany_italy_2012.mp4,00:05:00,00:05:30,Safe Moment 2,Clean\n"
    )
    raw_dir = tmp_path / "RAW"
    raw_dir.mkdir()
    source = raw_dir / "germany_italy_2012.mp4"
    source.write_text("data")
    clips_dir = tmp_path / "CLIPS"
    manifest = tmp_path / "manifest.csv"

    mock_run.return_value = MagicMock(returncode=0, stderr="")

    from scripts.export_research_windows import main

    exit_code = main([
        "--csv", str(csv_file),
        "--clips-dir", str(clips_dir),
        "--manifest", str(manifest),
        "--raw-dir", str(raw_dir),
        "--profile", "vertical_safe",
        "--execute",
    ])

    assert exit_code == 0
    assert mock_run.call_count == 2
    for call_args in mock_run.call_args_list:
        cmd = call_args[0][0]
        fc_idx = cmd.index("-filter_complex")
        fg = cmd[fc_idx + 1]
        assert "crop=trunc" in fg
        assert fg.index("crop=") < fg.index("split=2")


# ── parse_timestamp ────────────────────────────────────────────────────────

def test_parse_timestamp_hms():
    assert parse_timestamp("00:01:30") == 90.0


def test_parse_timestamp_ms():
    assert parse_timestamp("01:30") == 90.0


def test_parse_timestamp_seconds():
    assert parse_timestamp("90.0") == 90.0


def test_parse_timestamp_zero():
    assert parse_timestamp("00:00:00") == 0.0


# ── build_manifest_row ─────────────────────────────────────────────────────

def test_build_manifest_row_with_source(tmp_path):
    row = ROW_1
    dest = tmp_path / "out" / "test_001.mp4"
    source = tmp_path / "source.mp4"
    source.write_text("data")

    result = build_manifest_row(row, dest, source, "exported", "")
    assert result["clip_id"] == "test_001"
    assert result["status"] == "exported"
    assert result["local_export_path"] == str(dest)
    assert result["source_file"] == str(source)
    assert "updated_at" in result


def test_build_manifest_row_without_source():
    row = ROW_1
    dest = Path("/out/test_001.mp4")

    result = build_manifest_row(row, dest, None, "failed", "file not found")
    assert result["status"] == "failed"
    assert result["source_file"] == "germany_italy_2012.mp4"
    assert result["reason"] == "file not found"
