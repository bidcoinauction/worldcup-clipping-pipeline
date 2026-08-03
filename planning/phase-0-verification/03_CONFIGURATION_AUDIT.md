# Configuration Audit

## `.env.example`

`.env.example` now documents environment variables used by the code:

- `OPENAI_API_KEY`
- `ANTHROPIC_API_KEY`
- `FOOTBALL_ARCHIVE_ROOT`
- `DEFAULT_OPENAI_MODEL`
- `DEFAULT_CLAUDE_MODEL`
- `DEFAULT_TRANSCRIBE_MODEL`
- `DEFAULT_WHISPER_MODEL`
- `OLLAMA_URL`
- `OLLAMA_MODEL`
- `ACCOUNT_POSITIONING`

## Environment Variables Found In Code

- `OPENAI_API_KEY`: required by OpenAI client creation.
- `ANTHROPIC_API_KEY`: required by Claude detection.
- `DEFAULT_OPENAI_MODEL`: optional hosted detection model override.
- `DEFAULT_CLAUDE_MODEL`: optional Claude model override.
- `DEFAULT_TRANSCRIBE_MODEL`: optional transcription model override.
- `OLLAMA_URL`: optional local Ollama endpoint override.
- `OLLAMA_MODEL`: optional model name for Ollama detection CLI default.
- `FOOTBALL_ARCHIVE_ROOT`: optional archive root override.

## Config Files Reviewed

- `config/pipeline_config.json`: leagues, categories, platforms, clip modes, scoring weights, model defaults, paths, providers.
- `config/series.yml`: editorial series promises and arc roles.
- `config/emotions.yml`: emotional category descriptions and score definitions.
- `config/match_urls.json`: currently empty match URL list.

## Current Defaults

- Archive root defaults to `C:\FootballArchive` on Windows and `FootballArchive/` on macOS/Linux when `FOOTBALL_ARCHIVE_ROOT` is unset.
- Config default providers are `faster-whisper` for transcription and `ollama` for detection.
- `pipeline_config.json` currently uses World Cup/football-specific positioning and league/category assumptions.

## Configuration Gaps

- No schema validation exists for `config/pipeline_config.json`.
- No first-class organization, project, client, brand, workflow, source, or job config exists yet.
- Business architecture recommends those concepts later, but Phase 0 intentionally did not implement them.
