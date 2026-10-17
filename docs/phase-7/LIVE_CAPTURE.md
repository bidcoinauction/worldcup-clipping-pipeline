# Phase 7.2 — Reliable Mac-Native Live Capture

## Purpose

Record authorized live video on Apple Silicon macOS into durable,
integrity-checked segments, recover safely from interruption, and hand a
completed recording to the Phase 7 local-ingestion service — without any
mandatory Docker, database, or network dependency.

```text
authorized live input (HLS/HTTP, local file, synthetic, or optional Ace Stream)
  -> transport-agnostic FFmpeg recording engine (bounded reconnect/retry)
  -> durable session manifest + per-segment completion markers
  -> integrity-validated segments (never treat a partial write as complete)
  -> interruption recovery (idempotent, duplicate-suppressed)
  -> assembled recording (FFmpeg concat, stream copy)
  -> pipeline.local_ingest register/render/verify
  -> stadium_signal.local_ingest/v1 contract for Stadium Signal Lite
```

## Scope And Guardrails

- **Additive.** Existing services are called, not replaced. The World Cup
  reference deployment and `scripts/record_live.py` behaviour are preserved
  (its public helpers now delegate to the shared engine, byte-for-byte).
- **Transport is separate from the engine.** Ace Stream is one optional input
  kind. HLS and HTTP require no Ace Stream and no Docker.
- **No secrets in Git or logs.** URLs are redacted (`user:pass@`, query
  strings) before being written to a manifest or printed.
- **Local-first.** No worker, no database. The only subprocesses are `ffmpeg`
  and `ffprobe`, invoked with structured arguments (never a shell).
- **Uploads stay opt-in.** A completed capture is never uploaded
  automatically; OORT upload is a separate, explicit, verified step (Phase 7.1).

## Module And CLI

- `pipeline/live_capture.py` — transport resolution, the recording engine,
  durable manifests, integrity checks, recovery, assembly, and registration.
- `scripts/live_capture.py` — the operator CLI.

### Source kinds

| Kind | `resolve_source` input | Notes |
| --- | --- | --- |
| `hls` | `url` (`.m3u8`) | Authorized HLS stream; no Docker |
| `http` | `url` | Authorized progressive HTTP input |
| `file` | `path` | Local media file |
| `synthetic` | — | `testsrc2` (offline tests/demo) |
| `ace` | `ace_id`, `ace_host` | **Optional**; Ace Stream proxy |

Reconnect flags are applied only to real network protocols
(`http`/`https`/`rtmp`/`rtsp`), never to local file or playlist inputs.

### Capture

```bash
# Authorized HLS stream, 15-minute segments
python3 scripts/live_capture.py start --match-id "Team A vs Team B" --url https://host/live.m3u8

# Offline synthetic demo
python3 scripts/live_capture.py start --match-id demo --test --segment-minutes 1

# Dry run (prints the exact ffmpeg command; writes nothing)
python3 scripts/live_capture.py start --match-id demo --url https://host/live.m3u8 --dry-run
```

Graceful stop: press `Ctrl+C`; the current segment is finalized and every
valid leftover is recovered. If FFmpeg exits unexpectedly, the engine restarts
up to `--max-attempts` (default 3), continuing segment numbering so nothing is
overwritten.

### Inspect, recover, assemble, register

```bash
python3 scripts/live_capture.py status   --manifest <staging>/<session>.manifest.json
python3 scripts/live_capture.py recover  --manifest <staging>/<session>.manifest.json
python3 scripts/live_capture.py assemble --manifest <staging>/<session>.manifest.json --out <recording>.mp4
python3 scripts/live_capture.py register --recording <recording>.mp4 --pilot-id ss_lite_live_001
# opt-in, never automatic:
python3 scripts/live_capture.py register --recording <recording>.mp4 --pilot-id ss_lite_live_001 --upload
```

## Durable Manifest And Integrity

Each session writes `<session_id>.manifest.json` under the staging directory
(atomic tmp + `os.replace`). The manifest — not the transient FFmpeg list file
— is the source of truth:

- `state`: `recording` → `stopped` / `recovered` / `failed` → `assembled` →
  `registered`.
- `segments[]`: `index`, `filename`, `state` (`writing` / `complete` /
  `finalized` / `partial`), `size_bytes`, `sha256`, `duration_seconds`,
  `ready_path`, `status_path`, `marker_path`, `finalized_at`.
- `source.reference`: a redacted, secret-free label.
- `attempts`, `ffmpeg_exit_code`, `error`, and timestamps.

A segment is **finalized** only after `validate_segment` confirms it exists,
is non-empty, and (when `ffprobe` is available) has a readable duration. A
`<segment>.complete.json` marker records the completion checksum. A partial
write is marked `partial`, left in staging, and **never** treated as complete.

Per-segment `.status.json` files are still written for the existing
`scripts/live_watch.py` transcript watcher, so the two workflows compose.

## Interruption Recovery

`recover_session` re-scans the staging directory and finalizes every valid
leftover segment exactly once. It is idempotent and duplicate-suppressed:
running it repeatedly never moves a segment twice and never duplicates a
manifest entry. Sessions that had been recording/interrupted become
`recovered`.

## Archive Root

All default paths hang off one documented root:
`resolve_archive_root()` — `FOOTBALL_ARCHIVE_ROOT` when set, else the platform
default (`FootballArchive` on macOS). Defaults: `LIVE_SEGMENTS` (staging),
`LIVE_READY` (finalized), `LIVE_RECORDINGS` (assembled). Pass
`--staging-dir`/`--ready-dir`/`--out` to override. Paths recorded in the
manifest are absolute and normalized.

## Registration And The Contract

`register_recording` is a thin delegation to
`pipeline.local_ingest.run_local_ingest`: the assembled recording is
registered, a deterministic EDL is written, a genuine FFmpeg reference render
runs, the artifact is verified (existence, readable duration, provenance to the
source), and the `stadium_signal.local_ingest/v1` contract is emitted. The
contract is unchanged and backward compatible; the `storage` block stays
`skipped` unless an upload is explicitly requested.

## Stage Report: Real Vs Simulated

- **Real:** transport resolution, FFmpeg segmented recording, integrity
  validation, manifests/markers, interruption recovery, assembly, and the
  Phase 7 register → EDL → render → verify loop.
- **Optional/real only when requested:** OORT upload (Phase 7.1), always
  private and verified; never automatic.
- **Not performed here:** transcription and moment detection (Phase 7 reports
  them `unavailable`).

## Tests

```bash
.venv/bin/python -m pytest tests/test_live_capture.py tests/test_record_live.py tests/test_live_watch.py -q
```

The suite is offline: it generates a real keyframe-aligned synthetic HLS
stream with FFmpeg, runs a full capture → recovery → assembly → registration
loop, and asserts the emitted contract verifies. FFmpeg-dependent tests skip
cleanly when FFmpeg/ffprobe are absent.
