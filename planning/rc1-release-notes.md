# RC1 Release Notes (Draft)

Version: 0.1.0-rc1

Clipper / Stadium Signal is a local-first sports media intelligence and
clipping workflow. Operators move a Project through analysis, Moment review,
Story building, Edit generation, rough-cut review, platform variants, and
Export Packages.

## What RC1 Contains

- Local-first Project runtime backed by a versioned SQLite index with safe,
  ordered, additive migrations and automatic pre-upgrade backups.
- Managed analysis with durable PipelineRuns and append-only events.
- Canonical Moments with review states and typed, directed Moment relations
  (Moment Graph foundation).
- Stories, Edit Briefs, EDLs, Renders with rough-cut review, platform/channel
  presets, and Export Packages.
- Project duplication (reuse source/analysis/Moments) and batch analysis.
- Optional integration adapter boundary (OORT / Airtable / Slack) with health
  checks; core never depends on them.
- Operator Console: dashboard, project detail with a derived workflow strip and
  next action, Moment review, Story review/building, Edit Brief/EDL/rough-cut
  generation, platform variants, exports, batch detail, and a system health page.
- Diagnostic tooling: first-run `init_project`, structured `doctor`, `release_check`,
  and a real-match `smoke_real_match` harness with stage timing and reuse flags.
- Secret sanitization for operator-facing errors, rotating local logs, and a
  single application version source.

## Known Limitations

- Real long-media validation is PENDING (requires an operator-provided match and
  model credentials); automated workflow smoke is PASS.
- Live OORT / Airtable / Slack verification is PENDING (no credentials);
  adapter contract tests pass.
- Packaging is repository/venv + editable install; no bundled executable yet.
- FFmpeg/ffprobe are external system dependencies (not bundled).
- faster-whisper model weights download on first use; not bundled.
- No direct publishing, OAuth, analytics ingestion, or queue/worker system.