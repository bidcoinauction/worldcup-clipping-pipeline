from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from pipeline import live_capture as lc

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")
requires_ffmpeg = pytest.mark.skipif(not (FFMPEG and FFPROBE), reason="ffmpeg/ffprobe not available")


# ── Transport resolution ─────────────────────────────────────────────────────


def test_resolve_source_ace():
    src = lc.resolve_source(lc.CAPTURE_KIND_ACE, ace_id="abc123")
    assert src == {"kind": "ace", "input": "http://localhost:6878/ace/getstream?id=abc123", "reference": "ace:abc123"}


def test_resolve_source_ace_custom_host():
    src = lc.resolve_source(lc.CAPTURE_KIND_ACE, ace_id="x", ace_host="http://192.168.1.5:7000")
    assert src["input"] == "http://192.168.1.5:7000/ace/getstream?id=x"


def test_resolve_source_ace_requires_id():
    with pytest.raises(lc.LiveCaptureError):
        lc.resolve_source(lc.CAPTURE_KIND_ACE)


def test_resolve_source_hls():
    src = lc.resolve_source(lc.CAPTURE_KIND_HLS, url="https://example.test/live/stream.m3u8")
    assert src["kind"] == "hls"
    assert src["input"] == "https://example.test/live/stream.m3u8"
    assert src["reference"] == "https://example.test/live/stream.m3u8"


def test_resolve_source_http_redacts_credentials_and_query():
    src = lc.resolve_source(lc.CAPTURE_KIND_HTTP, url="https://user:secret@host.test:8443/path/stream?token=abc")
    assert src["input"].startswith("https://user:secret@host.test")  # input keeps the real URL
    assert src["reference"] == "https://host.test:8443/path/stream"  # reference is secret-free


def test_resolve_source_file(tmp_path):
    f = tmp_path / "clip.mp4"
    f.write_text("x")
    src = lc.resolve_source(lc.CAPTURE_KIND_FILE, path=f)
    assert src["kind"] == "file"
    assert src["input"] == str(f)


def test_resolve_source_file_missing(tmp_path):
    with pytest.raises(lc.LiveCaptureError):
        lc.resolve_source(lc.CAPTURE_KIND_FILE, path=tmp_path / "nope.mp4")


def test_resolve_source_synthetic():
    src = lc.resolve_source(lc.CAPTURE_KIND_SYNTHETIC)
    assert src == {"kind": "synthetic", "input": None, "reference": "synthetic:testsrc2"}


def test_resolve_source_unknown_kind():
    with pytest.raises(lc.LiveCaptureError):
        lc.resolve_source("rtmp")


def test_redact_url_variants():
    assert lc.redact_url("https://a:b@h.test/x?k=v") == "https://h.test/x"
    assert lc.redact_url("not a url") == "<redacted-url>"


# ── Engine argv ──────────────────────────────────────────────────────────────


def test_build_segment_argv_matches_legacy_shape():
    argv = lc.build_segment_argv("http://h/ace/getstream?id=a", "out_S%04d.ts", segment_seconds=900, list_path="l.txt")
    assert argv == [
        "ffmpeg", "-y", "-i", "http://h/ace/getstream?id=a", "-c", "copy",
        "-f", "segment", "-segment_time", "900", "-reset_timestamps", "1",
        "-segment_list", "l.txt", "out_S%04d.ts",
    ]


def test_build_segment_argv_reconnect_and_start_number():
    argv = lc.build_segment_argv("u", "p", segment_seconds=60, reconnect=True, start_number=7)
    assert lc.RECONNECT_FLAGS[0] in argv
    idx = argv.index("-start_number")
    assert argv[idx + 1] == "7"


def test_build_segment_argv_omits_start_number_zero():
    argv = lc.build_segment_argv("u", "p", segment_seconds=60, start_number=0)
    assert "-start_number" not in argv


def test_build_full_argv_reconnect_flags():
    argv = lc.build_full_argv("http://h/ace/getstream?id=a", "/out.ts")
    assert argv[:2] == ["ffmpeg", "-y"]
    assert argv[2:2 + len(lc.RECONNECT_FLAGS)] == lc.RECONNECT_FLAGS
    assert argv[argv.index("-i") + 1] == "http://h/ace/getstream?id=a"
    assert argv[-1] == "/out.ts"


def test_build_record_argv_dispatch():
    ace = lc.resolve_source(lc.CAPTURE_KIND_ACE, ace_id="a")
    seg = lc.build_record_argv(ace, output="o_S%04d.ts", list_path="l.txt", segment_seconds=30)
    assert "ace/getstream" in seg[seg.index("-i") + 1]
    assert lc.RECONNECT_FLAGS[0] in seg  # network inputs reconnect

    file_src = {"kind": "file", "input": "/local/clip.mp4", "reference": "/local/clip.mp4"}
    file_seg = lc.build_record_argv(file_src, output="o_S%04d.ts", list_path="l.txt")
    assert lc.RECONNECT_FLAGS[0] not in file_seg  # local inputs do not reconnect

    synth = lc.build_record_argv(lc.resolve_source(lc.CAPTURE_KIND_SYNTHETIC), output="o_S%04d.ts", test_duration=15)
    assert "testsrc2" in synth[synth.index("-i") + 1]


def test_build_synthetic_full_argv():
    argv = lc.build_synthetic_full_argv("/out.mp4", duration=12)
    assert "duration=12" in argv[argv.index("-i") + 1]
    assert argv[-1] == "/out.mp4"


# ── Naming / list helpers ────────────────────────────────────────────────────


def test_segment_helpers():
    assert lc.segment_filename("m", 3) == "m_S0003.ts"
    assert lc.segment_number_from_path(Path("m_S0042.ts")) == 42
    assert lc.segment_number_from_name("m_S0042.ts") == 42
    assert lc.segment_number_from_name("bad.ts") is None
    assert "m_S%04d.ts" in lc.build_output_pattern(Path("s"), "m")
    assert lc.build_list_path(Path("s"), "m", attempt=0).endswith("m_list.txt")
    assert lc.build_list_path(Path("s"), "m", attempt=2).endswith("m_list_attempt2.txt")


def test_read_segment_list(tmp_path):
    lst = tmp_path / "l.txt"
    lst.write_text("m_S0000.ts\n\n/abs/m_S0001.ts\n")
    assert lc.read_segment_list(lst) == {"m_S0000.ts", "m_S0001.ts"}
    assert lc.read_segment_list(tmp_path / "missing.txt") == set()


# ── Manifest ─────────────────────────────────────────────────────────────────


def _manifest(tmp_path, **kw):
    return lc.new_manifest(
        session_id="m_20260101T000000Z", match_id="m", source={"kind": "hls", "reference": "https://h/x"},
        segment_seconds=30, staging_dir=tmp_path / "staging", ready_dir=tmp_path / "ready", **kw,
    )


def test_new_manifest_shape(tmp_path):
    m = _manifest(tmp_path)
    assert m["manifest_version"] == lc.MANIFEST_VERSION
    assert m["state"] == lc.SESSION_RECORDING
    assert m["segments"] == []
    assert m["source"]["reference"] == "https://h/x"


def test_save_load_manifest(tmp_path):
    m = _manifest(tmp_path)
    p = lc.manifest_path(tmp_path, m["session_id"])
    lc.save_manifest(p, m)
    loaded = lc.load_manifest(p)
    assert loaded["session_id"] == m["session_id"]
    assert lc.load_manifest(tmp_path / "missing.json") is None


def test_save_manifest_atomic_no_tmp(tmp_path):
    p = tmp_path / "s.manifest.json"
    lc.save_manifest(p, _manifest(tmp_path))
    assert p.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_load_manifest_corrupt(tmp_path):
    p = tmp_path / "s.manifest.json"
    p.write_text("not json")
    assert lc.load_manifest(p) is None


def test_upsert_segment_entry_sorts(tmp_path):
    m = _manifest(tmp_path)
    lc.upsert_segment_entry(m, {"index": 2})
    lc.upsert_segment_entry(m, {"index": 0})
    lc.upsert_segment_entry(m, {"index": 1})
    assert [e["index"] for e in m["segments"]] == [0, 1, 2]
    lc.upsert_segment_entry(m, {"index": 1, "state": "finalized"})
    assert len(m["segments"]) == 3
    assert lc.find_segment_entry(m, 1)["state"] == "finalized"


# ── Integrity ────────────────────────────────────────────────────────────────


def test_validate_segment_missing(tmp_path):
    result = lc.validate_segment(tmp_path / "nope.ts")
    assert result["valid"] is False
    assert result["reason"] == "missing"


def test_validate_segment_empty(tmp_path):
    f = tmp_path / "m_S0000.ts"
    f.write_text("")
    result = lc.validate_segment(f)
    assert result["valid"] is False
    assert result["reason"] == "empty"


def test_validate_segment_nonempty_probe_disabled(tmp_path):
    f = tmp_path / "m_S0000.ts"
    f.write_text("some bytes")
    result = lc.validate_segment(f, probe=False)
    assert result["valid"] is True
    assert result["size_bytes"] == len("some bytes")


# ── Finalize + idempotence ───────────────────────────────────────────────────


def test_finalize_segment_moves_and_records(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    src = staging / "m_S0000.ts"
    src.write_text("payload")
    m = _manifest(tmp_path)

    entry = lc.finalize_segment(m, src=src, ready_dir=ready, probe=False)
    assert entry["state"] == lc.SEGMENT_FINALIZED
    assert entry["skipped"] is False
    assert (ready / "m_S0000.ts").exists()
    assert not src.exists()
    assert (ready / "m_S0000.status.json").exists()
    assert lc.has_completion_marker(ready / "m_S0000.ts")
    assert entry["sha256"] == lc.sha256_file(ready / "m_S0000.ts")
    assert len(m["segments"]) == 1


def test_finalize_segment_is_idempotent(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    src = staging / "m_S0000.ts"
    src.write_text("payload")
    m = _manifest(tmp_path)

    lc.finalize_segment(m, src=src, ready_dir=ready, probe=False)
    second = lc.finalize_segment(m, src=staging / "m_S0000.ts", ready_dir=ready, probe=False)
    assert second["skipped"] is True
    assert len(m["segments"]) == 1  # duplicate suppressed


def test_finalize_segment_partial_stays_in_staging(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    src = staging / "m_S0001.ts"
    src.write_text("")  # partially written / empty
    m = _manifest(tmp_path)

    entry = lc.finalize_segment(m, src=src, ready_dir=ready, probe=False)
    assert entry["state"] == lc.SEGMENT_PARTIAL
    assert entry["reason"] == "empty"
    assert src.exists()  # never moved
    assert not (ready / "m_S0001.ts").exists()
    assert not lc.has_completion_marker(ready / "m_S0001.ts")


# ── Recovery ─────────────────────────────────────────────────────────────────


def test_discover_staging_segments_sorted(tmp_path):
    staging = tmp_path / "staging"
    staging.mkdir()
    for name in ["m_S0002.ts", "m_S0000.ts", "bad.ts", "other_S0001.ts"]:
        (staging / name).write_text("x")
    names = [p.name for p in lc.discover_staging_segments(staging, "m")]
    assert names == ["m_S0000.ts", "m_S0002.ts"]


def test_recover_session_finalizes_valid_and_flags_partial(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    (staging / "m_S0000.ts").write_text("complete payload")
    (staging / "m_S0001.ts").write_text("")  # partial
    m = _manifest(tmp_path)
    m["state"] = lc.SESSION_INTERRUPTED

    report = lc.recover_session(m, staging_dir=staging, ready_dir=ready, probe=False)
    assert [e["filename"] for e in report["finalized"]] == ["m_S0000.ts"]
    assert [e["filename"] for e in report["partial"]] == ["m_S0001.ts"]
    assert m["state"] == lc.SESSION_RECOVERED
    assert (ready / "m_S0000.ts").exists()
    assert not (ready / "m_S0001.ts").exists()


def test_recover_session_is_idempotent(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    (staging / "m_S0000.ts").write_text("payload")
    m = _manifest(tmp_path)

    first = lc.recover_session(m, staging_dir=staging, ready_dir=ready, probe=False)
    assert len(first["finalized"]) == 1
    second = lc.recover_session(m, staging_dir=staging, ready_dir=ready, probe=False)
    assert second["finalized"] == []
    assert len(m["segments"]) == 1  # no duplicates


# ── Assembly (dry-run, no ffmpeg) ────────────────────────────────────────────


def test_assemble_requires_segments(tmp_path):
    m = _manifest(tmp_path)
    with pytest.raises(lc.LiveCaptureError):
        lc.assemble_recording(m, out_path=tmp_path / "rec.mp4")


def test_assemble_dry_run_argv(tmp_path):
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    staging.mkdir()
    (staging / "m_S0000.ts").write_text("payload")
    m = _manifest(tmp_path)
    lc.finalize_segment(m, src=staging / "m_S0000.ts", ready_dir=ready, probe=False)

    result = lc.assemble_recording(m, out_path=tmp_path / "rec", dry_run=True)
    assert result["dry_run"] is True
    assert result["segment_count"] == 1
    assert result["path"].endswith(".mp4")
    assert "-f" in result["argv"] and "concat" in result["argv"]


# ── End-to-end: synthetic HLS over HTTP → recover → assemble → register ──────


def _make_hls_source(dest: Path, *, duration: int = 8) -> Path:
    """Generate a real, keyframe-aligned HLS stream (no network dependency)."""
    dest.mkdir(parents=True, exist_ok=True)
    seg_pattern = str(dest / "seg_%03d.ts")
    playlist = dest / "index.m3u8"
    cmd = [
        FFMPEG, "-y",
        "-f", "lavfi", "-i", f"testsrc2=duration={duration}:size=320x240:rate=30",
        "-f", "lavfi", "-i", f"sine=frequency=440:duration={duration}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-g", "30", "-keyint_min", "30", "-sc_threshold", "0",
        "-c:a", "aac", "-b:a", "128k",
        "-f", "hls", "-hls_time", "2", "-hls_list_size", "0",
        "-hls_segment_filename", seg_pattern, str(playlist),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return playlist


@requires_ffmpeg
def test_run_capture_recovers_and_assembles(tmp_path):
    hls_dir = tmp_path / "hls"
    _make_hls_source(hls_dir, duration=8)
    source = lc.resolve_source(lc.CAPTURE_KIND_HLS, url=str(hls_dir / "index.m3u8"))

    staging = tmp_path / "staging"
    ready = tmp_path / "ready"
    result = lc.run_capture(
        source, match_id="synthetic hls", staging_dir=staging, ready_dir=ready,
        segment_seconds=2, poll_interval=0.1, max_attempts=1, ffmpeg_bin=FFMPEG,
    )
    assert result["state"] in (lc.SESSION_STOPPED, lc.SESSION_RECOVERED)
    finalized = [e for e in result["segments"] if e["state"] == lc.SEGMENT_FINALIZED]
    assert len(finalized) >= 2  # segmented capture produced multiple segments
    for entry in finalized:
        assert entry["sha256"]
        assert Path(entry["ready_path"]).exists()
        assert lc.has_completion_marker(Path(entry["ready_path"]))


@requires_ffmpeg
def test_full_pipeline_capture_assemble_register(tmp_path):
    hls_dir = tmp_path / "hls"
    _make_hls_source(hls_dir, duration=8)
    source = lc.resolve_source(lc.CAPTURE_KIND_HLS, url=str(hls_dir / "index.m3u8"))
    staging = tmp_path / "staging"
    ready = tmp_path / "ready"

    result = lc.run_capture(
        source, match_id="e2e match", staging_dir=staging, ready_dir=ready,
        segment_seconds=2, poll_interval=0.1, max_attempts=1, ffmpeg_bin=FFMPEG,
    )
    manifest = result["manifest"]

    recording = lc.assemble_recording(manifest, out_path=tmp_path / "recordings" / "e2e.mp4", ffmpeg_bin=FFMPEG)
    assert Path(recording["path"]).exists()
    assert recording["sha256"] == lc.sha256_file(recording["path"])
    assert recording["duration_seconds"] and recording["duration_seconds"] > 0
    assert manifest["state"] == lc.SESSION_ASSEMBLED

    reg = lc.register_recording(
        recording, pilot_id="ss_lite_live_e2e", jobs_dir=tmp_path / "jobs",
    )
    contract = reg["contract"]
    assert contract["contract_schema"] == "stadium_signal.local_ingest/v1"
    assert contract["contract_version"] == 1
    assert contract["verification"]["verified"] is True
    assert contract["artifact"]["sha256"]
    assert contract["storage"]["status"] == "skipped"  # never uploads automatically
    assert contract["processing"]["status"] == "COMPLETE"


@requires_ffmpeg
def test_run_capture_dry_run_no_writes(tmp_path):
    source = lc.resolve_source(lc.CAPTURE_KIND_HLS, url="https://example.test/live.m3u8", verify_exists=False)
    staging = tmp_path / "staging"
    result = lc.run_capture(
        source, match_id="m", staging_dir=staging, ready_dir=tmp_path / "ready", dry_run=True,
    )
    assert result["dry_run"] is True
    assert not staging.exists()
