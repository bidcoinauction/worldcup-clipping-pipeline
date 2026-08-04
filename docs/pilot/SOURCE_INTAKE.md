# Source Intake

## Accepted local-file expectations

This pilot accepts **local files only** on the operator machine (or an
explicitly configured intake root). Network URLs, stream addresses, platform
links, and remote paths are **rejected** by the intake validator.

## File naming

- Keep the client's original filename in the intake (`original_filename`).
- Refer to the file by its absolute local path in `local_file_path`.
- Give the source a stable, slug-safe identifier (`media.source_id`).
- Never rename or copy the client's file as part of intake.

## Supported types

The validator accepts these media extensions:

`mp4, mov, m4v, mkv, webm, ts, avi, mpg, mpeg` (video)
`wav, mp3, m4a, aac, flac` (audio)

Anything else is `SOURCE_UNSUPPORTED_EXTENSION`. Source files must exist, be a
regular file, be readable, and be non-empty.

## Source-quality guidance

Prefer the highest available resolution and the original (untranscoded)
recording. A clean file with intact audio and full event coverage produces the
most usable clips. Note the approximate duration and any client-provided
checksum when available.

## Event metadata

Record the optional match or event name and event date so the intake is
self-describing. This does not replace the World Cup schedule or recording
manifests; it is the pilot's operational wrapper.

## Delivery method to the operator

Client supplies the file through an agreed channel: shared folder, direct
transfer, or local handoff. The operator records where it was received.

## Prohibited credentials or private information

Never include in an intake manifest:

- API keys, tokens, passwords, secret strings
- Payment / card data
- Authentication credentials
- Government IDs
- Unnecessary personal information

The validator rejects keys whose names match credential/payment patterns and
values that look like credentials.

## Validation process

```bash
python3 scripts/pilot_job.py validate path/to/intake.json
```

Exit zero means the intake is **structurally valid**. The report shows whether
it is source-ready and execution-ready.