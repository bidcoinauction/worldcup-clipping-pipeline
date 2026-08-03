# Roadmap Implications

This document reviews the current roadmap and determines whether ChatCut changes any priorities. The reference documents are `planning/business-architecture/09_PRODUCT_ROADMAP.md`, `planning/phase-0-verification/07_IMPLEMENTATION_PLAN.md`, and `planning/competitive-analysis/*`.

## Overall Read

ChatCut's presence does not change the core direction. It confirms that per-piece editing and generic clipping are commodity and crowded, which reinforces the managed-operations strategy already recorded. It does not create a new requirement to build editing features; it reinforces deferral of them.

## Immediate Priority Items

| Item | Verdict | Rationale |
|---|---|---|
| Reference deployment preservation | KEEP | Remains the compatibility contract; steady baseline. |
| Configuration extraction | KEEP | Enables brand/taxonomy/workflow configurability, the operational moat. |
| Taxonomy selection | KEEP | Supports per-deployment metadata consistent with sports/events focus. |
| Prompt selection | KEEP | Enables per-client brand/voice rules. |
| Path and archive configuration | KEEP | Foundation for archives and multi-project operations. |
| Safer FFmpeg execution | KEEP | Reduces paid-pilot failure risk (documented in `RELEASE_READINESS.md`). |
| Operator-facing errors | KEEP | Required for a reliable managed service. |
| Pilot runbook | KEEP | Enables the first paid pilot. |
| Rights intake | KEEP | Legal and trust boundary before commercial work. |
| Brand intake | KEEP | Brand-consistency is a defensible differentiator. |
| Job log | KEEP | Provenance/audit, key to operational trust. |
| Human review | KEEP | Approval is core service value. |
| Manual delivery | KEEP | Shared-folder delivery is the pilot handoff mode. |

## Deferred Items

| Decision | Verdict | Rationale |
|---|---|---|
| General talking-head editor | **REMOVE** from near-term; not building an NLE | Out of scope; commodity; guardrail. |
| Full timeline editor | **REMOVE** | Not our product; guardrail. |
| Motion-graphics library | **MOVE LATER / PARTNER** | Not needed for pilot; integrate later. |
| AI voiceovers | **MOVE LATER / PARTNER** | Not needed for sports pilot. |
| Music marketplace | **MOVE LATER** | Not needed for pilot. |
| Creator self-service | **MOVE LATER** | Post-Phase 3; not now. |
| ChatGPT plugin | **DO NOT PURSUE** | Competing for the tool we aim not to be; guardrail. |
| Billing | **MOVE LATER** | Agency invoices manually first. |
| Multi-tenancy | **MOVE LATER** | Phase 3/4 concern, not Phase 1. |
| Direct publishing | **MOVE LATER / INTEGRATE** | After review, rights, account access are mature. |
| Broad SaaS features | **MOVE LATER** | Guardrail: build only validated, pilot-demanded features. |

## What ChatCut Does NOT Change

- The first paid pilot scope (managed, one client, sports/highlights, manual review, shared-folder) remains unchanged.
- The World Cup reference deployment remains the compatibility contract.
- Commodity editing features remain deferred, not built to match ChatCut.

## What ChatCut Reinforces

- Note that product-for-product editing competition is a poor wedge.
- The defensible position is managed media operations; ChatCut strengthens, not weakens, that thesis.

## Recommend Positioning Correction

The business-architecture market/roadmap documents already describe this. ChatCut adds no new strategic direction; it confirms the current one. Where the update occurs, we record the competitive validation, not a pivot.

## No Unnecessary Change

Do not add ChatCut-specific features, interfaces, or workflows. Where the roadmap already aligns, no change is applied.