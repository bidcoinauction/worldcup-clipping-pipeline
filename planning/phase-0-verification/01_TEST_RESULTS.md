# Test Results

## Commands Executed

```bash
git status --short --branch
git branch --show-current
python scripts/validate_data.py
pytest
python3 --version
python3 scripts/validate_data.py
python3 -m pytest
python3.12 --version
python3.12 scripts/validate_data.py
python3.12 -m pytest
pytest --version
python3 scripts/validate_data.py
pytest
```

## Initial Results

### `python scripts/validate_data.py`

Failed before validation started:

```text
zsh:1: command not found: python
```

Cause: this macOS workspace does not have a `python` alias.

### `pytest`

Initial collection failed under Python 3.9.6:

```text
24 errors during collection
TypeError: unsupported operand type(s) for |: 'type' and 'type'
TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
```

Cause: the installed `pytest` runner uses Python 3.9.6 while parts of the code used Python 3.10-style union annotations without `from __future__ import annotations`.

### `python3 -m pytest` and `python3.12 -m pytest`

Both failed because pytest was not installed into those interpreters:

```text
No module named pytest
```

## Post-Fix Results

### `python3 scripts/validate_data.py`

Passed:

```text
matches.csv valid
moments.csv valid
emotional_timelines.csv valid
clip_windows.csv valid
mythology_scores.csv valid
Stadium Signal data validation passed
```

### `pytest`

Passed with one skip and two warnings:

```text
542 items collected
541 passed, 1 skipped, 2 warnings in 2.90s
```

## Skipped Tests

- `tests/test_export_clips_ffmpeg.py::test_get_video_duration_returns_float`
- Reason: local media sample missing at `FootballArchive/SAMPLES/psg_arsenal_2min.mp4`.

## Warnings

- `PytestUnknownMarkWarning` for `pytest.mark.network` was observed before registering the marker in `pytest.ini`; the marker is now configured.
- `urllib3.exceptions.NotOpenSSLWarning`: active Python 3.9 ssl module is compiled with LibreSSL 2.8.3 while urllib3 v2 expects OpenSSL 1.1.1+.

## Configured Checks Not Run

No lint, format, type-check, tox, Makefile, or pre-commit commands are configured in this repository.
