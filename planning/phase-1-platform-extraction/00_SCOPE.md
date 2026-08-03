# Phase 1 — Platform Extraction — Scope

## Mission

Keep the World Cup implementation as the **current reference deployment** while separating reusable platform concepts so the operational platform can serve a second deployment (modeled as a regional basketball league, documentation only).

Phase 1 is **documentation and planning only**. No production code, tests, config values, scripts, or repository names are modified in this phase.

## In Scope

- A guardrail document describing the reference deployment: `docs/REFERENCE_DEPLOYMENT.md`.
- This planning track: `planning/phase-1-platform-extraction/`.
- Identification of concepts that are **extractable** (reusable across deployments) vs **specialized** (football/World Cup-specific).
- A proposed **first implementation slice** for a later phase (approved before any code change).
- Compatibility requirements the World Cup deployment must keep meeting.

## Out of Scope (Phase 1)

- Renaming any repository directory, script, config file, or taxonomy name.
- Modifying `pipeline/config.py`, `pipeline/paths.py`, `pipeline/utils.py`, `pipeline/stadium_signal.py`, any `config/*`, any script, or any test.
- Adding a database, queue, auth, billing, publishing, or multi-tenant system.
- Building new dashboards or new automation.
- Choosing repository names, company names, or product names.
- Building the basketball deployment itself (modeled only to validate the extraction).

## Definitions Used in This Track

- **Reference deployment**: the World Cup workflow currently implemented and preserved.
- **Second deployment (model)**: a regional basketball league used to test whether extracted concepts are truly reusable. Documentation only.
- **Extraction**: separating reusable concepts from the World Cup implementation.
- **Specialized**: football or World Cup-specific behavior kept on the reference deployment.

## Decision Rule

Use the **Extraction Decision Test** in `docs/REFERENCE_DEPLOYMENT.md`. If a concept is meaningful to a second company/deployment without changing meaning, extract. If it is football/tournament-only, keep it specialized.

## Deliverables

1. `docs/REFERENCE_DEPLOYMENT.md` — the guardrail (principles, flow, preserved behaviors, decision test, anti-patterns).
2. `planning/phase-1-platform-extraction/00_SCOPE.md` — this file.
3. `planning/phase-1-platform-extraction/01_ASSUMPTION_REGISTER.md` — the EXTRACT NOW / KEEP SPECIALIZED register.
4. `planning/phase-1-platform-extraction/02_BOUNDARIES.md` — explicit boundaries for Phase 1 and later phases.
5. `planning/phase-1-platform-extraction/03_COMPATIBILITY.md` — World Cup compatibility contract.
6. `planning/phase-1-platform-extraction/04_CONFIGURATION_MODEL.md` — proposed config model.
7. `planning/phase-1-platform-extraction/05_TEMPLATE_TAXONOMY_MODEL.md` — template and taxonomy model.
8. `planning/phase-1-platform-extraction/06_ADAPTER_MODEL.md` — source/path adapter model.
9. `planning/phase-1-platform-extraction/07_MIGRATION_SEQUENCE.md` — staged migration.
10. `planning/phase-1-platform-extraction/08_TEST_STRATEGY.md` — test strategy.
11. `planning/phase-1-platform-extraction/09_IMPLEMENTATION_PLAN.md` — the proposed first implementation slice and plan.

## Success Criteria for Phase 1

- The World Cup reference deployment is fully documented and preserved.
- Every future change has a documented decision record for what is extracted vs specialized.
- The proposed first implementation slice is concrete, repo-grounded, and does not disturb the reference deployment.
- `git diff --check` passes; no production code, tests, or config values changed.

## Competitive Boundary (ChatCut)

The competitive analysis (`planning/competitive-analysis/`) confirms Phase 1 scope:

- Phase 1 extracts **operational and configurable** concepts (identity, taxonomy, prompt selection, paths) — not editing features.
- Editing-product capabilities (talking-head editing, timeline editor, motion graphics, ChatGPT-plugin editing) are out of scope for the platform and are not extracted into config.
- The second-deployment model (basketball) validates operational reuse; it is not a ChatCut-style editor.

This boundary does not change the Phase 1 deliverables or the proposed first slice.