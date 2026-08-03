# Security And Rights Check

## Secret Scan

Pattern scan found no tracked real API keys, access keys, Slack webhooks, GitHub tokens, or similar secrets.

Observed benign matches:

- Placeholder example in `docs/SETUP_WINDOWS.md`: `OPENAI_API_KEY=sk-...`.
- Stale historical mention in `RELEASE_READINESS.md` was corrected.
- Code constants containing words such as `KEYWORDS` are not secrets.

## Ignored Secret Paths

- `.env` is ignored.
- `secrets/` is ignored.
- `*.session` is ignored.

## Workspace Secret Files

- `.env`: not present.
- `secrets/`: not present.

## Security Risks Remaining

Critical:

- Commercial processing requires rights verification for every source file, livestream, archive, or broadcast recording.

High:

- `pipeline/stadium_signal.py` still executes generated FFmpeg command strings with `shell=True`.
- Hosted model calls and FFmpeg subprocesses still have inconsistent error handling and timeout behavior.
- External websites and downloads are used by LiveTV/showvideo workflows and may have rights, terms, availability, or trust risks.

Medium:

- No formal client offboarding or media deletion procedure exists.
- No audit log exists for who processed what media and when.
- No cost tracking exists for hosted API usage.

## Rights Boundary For Paid Pilot

Only process client-supplied local media where the client confirms:

- They own or control the footage or have permission to provide it.
- Clipping and derivative edits are allowed.
- Storage location and retention period are acceptable.
- Review and delivery destinations are approved.
- Athlete, speaker, guest, sponsor, and broadcast restrictions are understood.
