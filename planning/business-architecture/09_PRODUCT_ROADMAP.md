# Product Roadmap

## Roadmap Philosophy

The roadmap should productize repeated agency needs. It should not force the business into pure SaaS before the operating model is proven.

## Phase 0 — Commercial Validation

Goal:

- Confirm what exists, what is reusable, and what blocks commercial use.

Status:

- Commercial-readiness audit completed in conversation.

Priority tasks:

- Save audit documents.
- Run validation and tests.
- Confirm no secrets are present.
- Confirm README and docs gaps.

Definition of done:

- Current state is documented.
- Test baseline is known.
- Major risks are visible.

## Phase 0.5 — Business Architecture

Goal:

- Define the business operating model before implementation.

Priority tasks:

- Define market.
- Define customer types.
- Define offerings.
- Define organization/project/workflow/job/output/asset hierarchy.
- Define content source model.
- Define brand system.
- Define workflow library.
- Define agency operating playbook.
- Translate business concepts into future technical changes.

Definition of done:

- Future implementation work can be evaluated against business needs.

## Phase 1 — Pilot Delivery

Goal:

- Support one paying pilot client without pretending the system is a platform.

Likely scope:

- Local file input.
- One customer/project config.
- One workflow template.
- One brand profile.
- Manual review and shared folder delivery.

Priority tasks:

- Create pilot runbook. **(built: `docs/pilot/PILOT_RUNBOOK.md`)**
- Make configuration explicit for one client. **(built: validated intake + rights gate in `pipeline/pilot.py`)**
- Remove the most harmful hardcoded assumptions from the pilot path. **(done in Phase 1 platform extraction)**
- Add validation for inputs and outputs. **(built: read-only source validation + intake validation)**
- Add basic job log. **(built: `scripts/pilot_job.py` job records + append-only event log)**

Status:

- Managed pilot intake, rights confirmation, source validation, and job
  records are implemented. Media processing, review, approval, and delivery
  remain manual operator steps (per the runbook).

Definition of done:

- One real client can be served repeatably with limited manual steps.

## Phase 2 — Repeatable Agency

Goal:

- Onboard several managed clients without rebuilding the workflow each time.

Priority tasks:

- Organization/project folder conventions.
- Workflow template config files.
- Brand intake to config process.
- Source manifest standard.
- Review status standard.
- Delivery manifest standard.
- Basic reporting.

Definition of done:

- Five managed clients can be operated with documented runbooks and minimal source changes.

## Phase 3 — Operational Platform

Goal:

- Productize internal operations where manual work has repeated.

Priority tasks:

- Job tracking.
- Review queue.
- Asset registry.
- Cost tracking.
- Error monitoring.
- Team/operator dashboard.
- Source and output provenance.

Definition of done:

- Operators can manage multiple clients from a shared internal system.

## Phase 4 — Selective Productization

Goal:

- Expose only validated workflows to clients or self-serve users.

Possible tasks:

- Client-facing review portal.
- Upload portal.
- Reusable integration adapters.
- Publishing handoff integrations.
- Usage-based reporting.

Definition of done:

- Productized features reduce agency labor and match repeated customer demand.

## Roadmap Guardrail

Do not build platform features only because they are standard in software products. Build them when they remove repeated operational pain or unlock paid work.

## Competitive Validation (ChatCut)

ChatCut does not change roadmap priorities. It confirms them.

- Per-piece conversational editing (a general talking-head editor, full timeline editor, ChatGPT-plugin editing) is **not prioritized**. See `planning/competitive-analysis/05_ROADMAP_IMPLICATIONS.md`.
- The roadmap remains anchored on the managed-pilot path (Phase 1-2) and operational productization (Phase 3-4), not on editing-product features.
- Immediate items (reference-deployment preservation, configuration extraction, taxonomy/prompt selection, path/archive config, safer FFmpeg, operator errors, pilot runbook, rights/brand intake, job log, human review, manual delivery) all remain KEEP.
- Chat-specific editing features are moved later or removed (do not pursue), consistent with the product guardrails in `planning/competitive-analysis/04_PRODUCT_GUARDRAILS.md`.

No roadmap pivot is required. Rationale for every item is in `planning/competitive-analysis/05_ROADMAP_IMPLICATIONS.md`.
