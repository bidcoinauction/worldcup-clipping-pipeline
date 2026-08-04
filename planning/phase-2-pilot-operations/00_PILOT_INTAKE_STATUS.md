# Phase 2 — Managed Pilot Operations: Intake + Job Records (Status)

## Objective

Implement the smallest operational slice for one managed local-file sports
pilot: a validated pilot intake manifest, an explicit rights gate, read-only
source validation, and a durable local job record.

## Built

- `pipeline/pilot.py` — intake validation (structural / configuration /
  rights / source), the rights gate, read-only source validation, and the job
  record API (atomic writes, append-only event log).
- `scripts/pilot_job.py` — operator CLI: `validate`, `create`, `show`, `list`.
- `docs/pilot/` — runbook and intake templates.
- `docs/pilot/examples/` — a tracked non-production World Cup example plus
  invalid fixtures (unconfirmed rights, missing source, bad reference).

## Boundaries

No database, queue, auth, users, billing, publishing, dashboard, media
processing, model calls, or network access. Existing World Cup manifests,
schedule, and clip manifests are unchanged; the pilot intake is an operational
wrapper, not a replacement.

## Manifest / Job Relationship

- **Pilot intake** (`intake JSON`) — the client-facing intake our operator.
- **Existing match manifest** (`data/manifests/*.json`) — records the World Cup
  recorded sources + pipeline flags; unchanged.
- **Existing schedule** (`data/worldcup_2026_schedule.csv`) — unchanged.
- **Existing clip manifest** (`CLIP_MANIFESTS/*.csv`) — unchanged.
- **Job record** (`data/pilot/jobs/<job_id>.json` + `.events.json`) — the
  durable provenance record for one pilot intake; gitignored.

```text
Client intake -> rights gate -> source validation -> job record (READY) ->
  existing pipeline (manual) -> review -> approval -> delivery
```

## Verification

- `python3 scripts/validate_data.py` passes.
- `pytest` 660 baseline preserved (plus new tests added).
- `python3 scripts/validate_config.py config/pipeline_config.json`,
  `config/examples/basketball.json`, `config/brands/world_cup.json`,
  `config/export/world_cup.json` all pass.
- `git diff --check` clean.
- Manual demonstrations confirm execution-ready, awaiting-rights,
  missing-source, durable READY job creation, deterministic duplicates, and
  no network / FFmpeg / media mutation.

## Remaining pilot-operation gaps

- Job-state transitions beyond creation (`RUNNING`, `REVIEW_REQUIRED`,
  `APPROVED`, `DELIVERY_READY`, `DELIVERED`, `FAILED`, `CANCELLED`) are
  documented but not implemented as commands.
- No supported-use check is automated for `RESTRICTED` rights (documented as a
  manual step).
- Delivery/billing/reporting remain out of scope.