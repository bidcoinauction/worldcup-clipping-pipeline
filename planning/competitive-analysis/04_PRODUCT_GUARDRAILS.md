# Product Guardrails

These guardrails prevent competitor-driven scope drift. They are the product boundary that ChatCut must not push us past.

## Operating Principles For The Product

1. **Do not rebuild a full nonlinear video editor.** We are not building an NLE; we build an operational layer over media.
2. **Do not compete feature-for-feature with creator editing products.** Per-piece editing is commodity and crowded.
3. **Do not add features solely because a competitor advertises them.** Features must map to paying pilot demand.
4. **Prefer recurring operational workflows over isolated editing commands.** Optimize for repeatable jobs (leagues, events, weekly output), not one-off edits.
5. **Treat commodity editing capabilities as replaceable components.** Captions, clipping, and export are inputs, not the moat.
6. **Preserve export interoperability.** Output standard playable formats so external editors and delivery partners can consume our work.
7. **Keep human review available.** Accuracy, brand safety, and approval are part of the service, not an afterthought.
8. **Make brand, taxonomy, workflow, and destination choices configurable.** No client-specific hardcoding.
9. **Optimize for repeated jobs, not only one video.** Cadence and reliability over novelty.
10. **Build features required by paying pilots.** Defer anything no pilot has paid for.
11. **Integrate where mature tools already solve the problem well.** Prefer integration/handoff over rebuilding.
12. **Preserve source and selection provenance.** Show how every clip was chosen, from source to final asset.
13. **Rights confirmation is part of the workflow.** No commercial processing without rights record.
14. **The World Cup implementation remains the reference deployment.** Do not disturb it while building the platform layer.
15. **Do not introduce ChatCut branding, language, or copied interface patterns.** This analysis is competitive evaluation, not adoption.

## What The Product Is Not

- **Not a general-purpose desktop editor.**
- **Not an Adobe Premiere replacement.**
- **Not only a ChatGPT plugin.**
- **Not only a talking-head editor.**
- **Not only an automatic caption generator.**
- **Not a motion-graphics marketplace.**
- **Not a self-serve creator SaaS at this stage.**

## Scope Guardrails For The First Offering

- Managed pilot only (one client, one project, local client-supplied files, one workflow, manual review, shared-folder delivery). See `RELEASE_READINESS.md` and `phase-0-verification/07_IMPLEMENTATION_PLAN.md`.
- No general editing features in the pilot.
- Defer editable timeline, motion graphics, AI voiceover, music marketplace, podcast auto-editing, direct publishing, billing, multi-tenancy, creator self-service.

## Decision Practices

- Before building any feature, ask: "Does a paying pilot need this to deliver and succeed?" If not, defer.
- Before integrating/buying, ask: "Is a mature tool already good at this?" If yes, integrate, do not build.
- Before adopting competitor capability, ask: "Is this a commodity or a defensible operational feature?" If commodity, do not lead with it.

## Guardrail Guard

- Do not rename the repository or choose company/product names in this process.
- Keep ChatCut only as a benchmark, never as a design source.
- Report competitor strength classifications precisely (OUR VERIFIED STRENGTH / COMPETITOR APPARENT STRENGTH / SHARED CATEGORY FEATURE / FUTURE OPPORTUNITY / NOT A PRIORITY / UNKNOWN), not assumed capabilities.