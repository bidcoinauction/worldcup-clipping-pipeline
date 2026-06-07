# Stadium Signal Release Readiness Audit

**Date:** 2026-06-05
**Branch:** `main` (ahead of `origin/main` by 4 commits)
**Baseline:** 45 passing tests, 12 test files, 4 clean checkpoints

---

## 1. Scores

### Reliability — 6/10

**What works:**
- OpenAI SDK built-in retry mechanism with `max_retries=4` configured in `pipeline/api.py:17`. SDK automatically retries HTTP 408, 429, 500, 502, 503, 504 with exponential backoff via `httpx`/`tenacity`.
- Verified by test `test_make_openai_client_creates_client_with_retries` in `tests/test_api.py`.
- JSON parse fallback in `pipeline/openai_client.py:41-49` recovers GPT output with text wrapped around a JSON array (e.g., `"Here are the clips:\n[{...}]"`).
- Dry-run safety flag on all expensive operations (transcribe, export, detection).

**What is missing:**
- **No `try/except` around any OpenAI API call.** `client.responses.create()` at `openai_client.py:28` and `client.audio.transcriptions.create()` at `transcribe_match.py:31` both propagate raw exceptions after SDK retries are exhausted. Exceptions like `openai.RateLimitError`, `openai.APIStatusError`, `openai.AuthenticationError`, `openai.APITimeoutError`, `openai.APIConnectionError` are never caught.
- **No `try/except` around any `subprocess.run` call.** All 5 ffmpeg subprocess calls (`transcribe_match.py:24`, `export_clips_ffmpeg.py:27`, `export_vertical_blur.py:74`, `stadium_signal.py:717`, `process_match.py:9`) use `check=True` without exception handling. ffmpeg failures crash the script with a raw traceback.
- **No timeout configuration.** The `OpenAI()` constructor receives no explicit `timeout` parameter (SDK defaults to ~600s). `subprocess.run` calls have no `timeout` parameter. A corrupt input file could hang ffmpeg indefinitely.
- **No validation that transcripts are non-empty.** `transcribe_match.py:85` writes `transcript.strip()` to disk without checking if it's empty. Silent empty outputs flow downstream.
- **JSON fallback only handles arrays.** The parser at `openai_client.py:44-48` searches for `[` and `]` only. If GPT returns a JSON object embedded in text (`{"clips": [...]}`), the fallback misses it. Multiple arrays (`[1,2] [3,4]`) produce an invalid extraction that crashes with an unhandled `JSONDecodeError`.
- **No logging.** All output is via `print()`. Failed retries, API errors, and ffmpeg stderr are invisible.
- **No idempotency.** If a retry succeeds after the caller has timed out, there is no mechanism to detect the duplicate. Transcription has no resume/checkpoint; failure after audio extraction loses all progress.

---

### Cross-Platform — 7/10

**What works:**
- `pathlib.Path` used exclusively across all 25 Python files. Zero `os.path` calls (confirmed by grep).
- `pipeline/stadium_signal.py:9` explicitly imports `PureWindowsPath` for Windows path handling.
- `archive_root()` at `stadium_signal.py:639-640` detects `os.name == "nt"` to default to `C:\FootballArchive` on Windows vs `FootballArchive` on Unix.
- `quote_path()` at `stadium_signal.py:698-699` uses double-quoting on Windows, `shlex.quote` on Unix.
- `ffmpeg` and `python` invoked by name (available on PATH on all platforms).
- Config path `"prompts/thumbnail_prompt_template.txt"` uses forward slashes; `pathlib.Path` normalizes on all platforms.

**What is missing:**
- `execute_ffmpeg_commands()` at `stadium_signal.py:717` uses `subprocess.run(command, shell=True)` with a joined string. This breaks the list-form pattern used everywhere else, introduces shell injection risk, and behaves differently on Windows (cmd.exe) vs Unix (sh). This is the single cross-platform liability.
- No CI/CD matrix testing across Windows/Linux/macOS.

---

### Configuration — 8/10

**What works:**
- Centralized singleton config loader in `pipeline/config.py` with `load_config()`, `get_leagues()`, `get_model(name)`, `get_path(name)`, `reload_config()`.
- Caching: first call reads from disk, subsequent calls return the cached dict (verified by test `test_load_config_caches` in `tests/test_config.py`).
- Env-var override pattern: `os.getenv("DEFAULT_OPENAI_MODEL") or _get_model("detection")` at `openai_client.py:17`. Env wins, config is fallback.
- Config structure in `config/pipeline_config.json`: leagues, categories, platforms, daily targets, clip rules, scoring weights, models, paths.
- 5 dedicated tests in `tests/test_config.py` covering load, cache, leagues, model lookup, path lookup.
- `.env.example` documents all expected environment variables.

**What is missing:**
- No schema validation. Malformed config produces a `KeyError` at first use with no helpful message.
- No TTL on config cache. Singleton is never refreshed within a long-running process unless `reload_config()` is called explicitly.
- `claude_client.py:22` uses a hardcoded fallback `"claude-3-5-sonnet-latest"` instead of reading from config or `.env`.

---

### Testing — 7/10

**What works:**
- **45 tests, all passing.** Distributed across 12 test files:

| Test file | Tests | What it covers |
|-----------|-------|----------------|
| `tests/test_api.py` | 3 | `require_api_key`, `make_openai_client` with retries |
| `tests/test_config.py` | 5 | Config load, cache, leagues, model, path |
| `tests/test_utils.py` | 13 | `slugify` (5), `timestamp_to_seconds` (4), `seconds_to_timestamp` (4) |
| `tests/test_transcribe_match.py` | 4 | ffmpeg command args, verbose_json usage, empty segments |
| `tests/test_export_clips_ffmpeg.py` | 4 | Command construction, min duration clamp, parent dir creation, dry-run |
| `tests/test_openai_client.py` | 4 | Dry-run skip, JSON output, text-before-array fallback, non-JSON SystemExit |
| `tests/test_generate_asset_prompts.py` | 2 | Thumbnail prompt file, caption file output |
| `tests/test_build_clip_manifest.py` | 3 | JSON list to CSV, wrapped clips key, status default |
| `tests/test_mythology_engine.py` | 2 | Score tiering, engine reads seeded score |
| `tests/test_story_arcs.py` | 2 | Arc generation, writes expected JSON |
| `tests/test_validate_data.py` | 3 | Required columns, missing match ref, invalid score range |

- **Bug found by test:** Test `test_dry_run_skips_api_call` exposed `output_path.with_suffix()` called on a `str` before `Path()` conversion in `openai_client.py:23`. Fixed by moving `Path(output_path)` before the dry-run check.

**What is missing:**
- **No tests for `pipeline/stadium_signal.py`** — the largest file (741 lines, ~30 functions) has zero direct tests. Only `mythology_for_match`, `generate_story_arc`, `write_story_arc`, and `validate_data` are indirectly tested via their script callers.
- **No ffmpeg failure tests.** All subprocess calls are mocked to succeed; failure paths are untested.
- **No integration tests.** All tests use isolated mocking; no end-to-end pipeline test exists.
- **No config validation tests.** Schema checks, missing keys, corrupt JSON files are untested.

---

### Security — 4/10

**What works:**
- `pipeline/api.py:require_api_key()` provides a consistent pattern: read from env, fail cleanly with `SystemExit` if missing.
- `make_openai_client()` uses `require_api_key()` — no secrets are hardcoded in source code.
- `.env` is listed in `.gitignore` (line 8). `secrets/` is also gitignored (line 10).
- No code writes API keys or secrets to disk (confirmed by grep).

**Critical issues:**
- **Real secrets in `.env` on disk:** Contains a live OpenAI API key (`sk-proj-...`), OORT S3 access and secret keys, Slack webhook URL, Airtable personal access token, and Telegram API credentials. Despite `.gitignore`, these are exposed to anyone with filesystem access to the repo.
- **Real secrets in `secrets/stadium_signal_integrations.env`:** Same set of OORT keys, Slack webhook, and Airtable token.
- **`subprocess.run(command, shell=True)` at `stadium_signal.py:717`:** Commands are built from CSV data fields (`clip.get("start_time")`, etc.). If CSV data were maliciously crafted, shell injection is possible. The `quote_path()` function only quotes input/output paths, not time values.
- **`claude_client.py:13`** uses `os.getenv("ANTHROPIC_API_KEY")` directly instead of `require_api_key()`, creating an inconsistent validation pattern.

---

### Documentation — 5/10

**What works:**
- `AGENTS.md` — clear mission, rules, validation commands.
- `docs/STADIUM_SIGNAL_OS.md` — 89-line canonical workflow reference with CSV schemas.
- CLI help strings — all 18 argparser-based scripts have descriptive `description` and argument help.
- `pipeline/openai_client.py` and `claude_client.py` have docstrings on their main functions.

**What is missing:**
- **`README.md` deleted from working tree.** No project-level README exists. New contributors have no entry point.
- **`CONTRIBUTING.md` references GSMG.io 5 BTC puzzle** (line 3: `"Thanks for helping with the GSMG.io 5 BTC puzzle effort!"`). Entirely wrong project — not Stadium Signal.
- **`SECURITY.md`** is only 7 lines, covering vulnerability reporting but no secret management guidance.
- **Code docstrings:** `pipeline/stadium_signal.py` (741 lines) has zero docstrings on its ~30 functions.
- **No architecture diagram or data flow documentation.**

---

### Operational Readiness — 6/10

**What works:**
- Dry-run flag on 3 expensive scripts (transcribe, export, detection) for safe iteration.
- Centralized config allows single-source-of-truth for league choices, models, and paths.
- Singleton config caching avoids repeated disk I/O.
- JSON parse fallback prevents total data loss when GPT wraps output in prose.
- `subprocess.run(capture_output=True)` suppresses noise during successful runs.
- Config-backed model selection with env-var override allows team-specific overrides.

**What is missing:**
- No retry resume for transcription. If a 5-minute match transcription fails after 4 retries, the entire audio must be re-uploaded and re-transcribed from scratch.
- No partial failure isolation in batch operations. In `export_clips_ffmpeg.py:47-55`, one bad clip in the manifest crashes the entire batch export. Previously exported clips are left on disk; remaining clips are skipped.
- No output file locking. If two processes run concurrently on the same match, they can corrupt each other's output.
- No audit log. There is no record of which operations succeeded, failed, or were retried.
- No metrics or instrumentation. Pipeline health is invisible without monitoring.

---

## 2. Top 5 Remaining Risks

| Rank | Risk | Severity | Location | Impact |
|------|------|----------|----------|--------|
| **1** | Secrets exposed in `.env` and `secrets/` | **Critical** | `.env`, `secrets/stadium_signal_integrations.env` | Credential theft, API abuse, Slack/S3 compromise |
| **2** | Shell injection via shell=True | **High** | `stadium_signal.py:717` (`execute_ffmpeg_commands`) | Arbitrary command execution via crafted CSV |
| **3** | No API error handling | **High** | `openai_client.py:28`, `transcribe_match.py:31` | Unhandled RateLimitError crashes pipeline; no recovery |
| **4** | No subprocess error handling | **High** | All 5 ffmpeg call sites | ffmpeg failure crashes with raw traceback; stderr lost in capture_output |
| **5** | No transcript validation | **Medium** | `transcribe_match.py:85` | Empty transcripts silently flow downstream, wasting detection API calls |

---

## 3. Top 5 Highest-ROI Improvements

| Rank | Improvement | Effort | Value | Category |
|------|-------------|--------|-------|----------|
| **1** | Rotate all exposed credentials immediately | 15 min | Prevents active account compromise | Security |
| **2** | Add `try/except` around API calls + error messages | 1-2 hours | Prevents raw traceback crashes; gives user actionable recovery steps | Reliability |
| **3** | Add `try/except` around subprocess calls + display stderr | 1-2 hours | Shows ffmpeg error output to user; prevents raw traceback | Reliability |
| **4** | Remove `shell=True`, use list-form subprocess | 30 min | Eliminates shell injection vector; matches existing pattern | Security |
| **5** | Add transcript emptiness check with warning | 15 min | Prevents wasted API calls on empty input | Reliability |

---

## 4. Current State Summary

| Metric | Value |
|--------|-------|
| Total tests | 45 |
| Test files | 12 |
| Production Python files | 10 (pipeline/) |
| Script files | 25 (scripts/) |
| Pipeline total LOC | ~1,800 (pipeline/) |
| Scripts total LOC | ~2,100 (scripts/) |
| Clean checkpoints | 4 |
| Unused dependencies | 2 (`rich`, `tqdm`) |
| Missing dependencies | 1 (`anthropic`) |
| Dead utility functions | 2 (`read_json`, `write_json` in `utils.py`) |
| Missing prompts/templates | 0 |
| Tests that found a real bug | 1 (`test_dry_run_skips_api_call`) |
| Config items centralized | 11 (leagues, models, paths, weights, etc.) |

---

## 5. Scores Summary

| Category | Score | Trend |
|----------|-------|-------|
| Reliability | 6/10 | Needs error handling around API and subprocess |
| Cross-Platform | 7/10 | One shell=True liability, otherwise solid |
| Configuration | 8/10 | Needs schema validation |
| Testing | 7/10 | Needs stadium_signal.py tests and integration tests |
| Security | 4/10 | **Critical:** exposed credentials, shell injection |
| Documentation | 5/10 | Needs README, fix CONTRIBUTING.md |
| Operational Readiness | 6/10 | Needs error isolation, retry resume, logging |

**Overall:** 6.1/10

---

## 6. Recommended Next Sprint

Given the scores above, the next sprint should be:

**B) Reliability** (with a Security hotfix before anything else)

The reliability score (6/10) is dragged down by the complete absence of error handling around API calls and subprocess calls — two 1-2 hour improvements that would move it to 8/10. The security hotfix (credential rotation + shell=True removal) is essential before any further development.

Dependencies and cleanup can wait until reliability and security reach 8/10.
