# Dependency Audit

## Declared Dependencies

`requirements.txt` now declares:

- `openai`
- `anthropic`
- `python-dotenv`
- `tqdm`
- `rich`
- `pytest`
- `faster-whisper`
- `requests`
- `urllib3`
- `icalendar`

## Third-Party Imports Found

- `openai`: imported dynamically in `pipeline/api.py`.
- `anthropic`: imported dynamically in `pipeline/claude_client.py`.
- `python-dotenv`: imported by OpenAI/Claude/transcription scripts.
- `requests`: imported by Ollama, LiveTV, and showvideo resolvers.
- `urllib3`: imported directly by `pipeline/showvideo_resolver.py`.
- `faster-whisper`: imported by `pipeline/whisper_transcriber.py` when local Whisper transcription runs.
- `icalendar`: imported by `scripts/export_calendar.py`.
- `pytest`: imported by tests.

## Missing Declarations Fixed

- Added `anthropic` because `pipeline/claude_client.py` imports it.
- Added `urllib3` because `pipeline/showvideo_resolver.py` imports it directly.

## Optional Or Environment-Sensitive Dependencies

- `faster-whisper` is required only for local Whisper transcription. The current Python 3.9 pytest environment did not have it installed, so `pipeline.whisper_transcriber` now imports cleanly and raises a clear `SystemExit` only when the local transcription function is invoked without the dependency.
- FFmpeg, ffprobe, curl, Ace Stream, Ollama, OpenAI, and Anthropic are not Python package dependencies but are operational dependencies for specific workflows.

## Not Fully Resolved In Phase 0

- Phase 0 did not remove potentially unused Python packages such as `rich` or `tqdm`; removal is not required to verify or operate the current repository.
- Phase 0 did not install missing packages into alternate Python interpreters.
