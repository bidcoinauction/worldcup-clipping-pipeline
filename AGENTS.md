# Stadium Signal Agent Instructions

## Mission

Stadium Signal is a football mythology archive. Prioritize emotional narrative, historical context, and cinematic structure over raw highlight clipping.

## Rules

- Treat full matches as primary source material.
- Do not reduce clips to goals only.
- Every match should include emotional metadata.
- Every moment should include narrative function.
- Every output should support short-form, medium-form, or long-form editing.
- Prefer CSV/JSON/YAML files that can later migrate to Supabase.
- Keep scripts local-first and Windows-compatible.
- Use `FOOTBALL_ARCHIVE_ROOT` when available.
- Default Windows archive root: `C:\FootballArchive`.
- Default local/macOS archive root: `FootballArchive/`.

## Required Validation

After code changes, run:

```bash
python scripts/validate_data.py
pytest
```
