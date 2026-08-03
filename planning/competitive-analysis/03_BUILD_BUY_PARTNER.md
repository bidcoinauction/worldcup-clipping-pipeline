# Build / Buy / Partner

This document gives a recommendation for each adjacent capability: BUILD, BUY, PARTNER, INTEGRATE, DEFER, or DO NOT PURSUE. Recommendations are grounded in the customer value, strategic importance, current repository support, complexity, commodity risk, and first-pilot value. We do not recommend rebuilding mature commodity editing tools without evidence and customer need.

## Decision Key

- BUILD — own it in our repository/workflow.
- BUY — license/acquire a mature tool or service.
- PARTNER — rely on an external editor/studio as a destination partner.
- INTEGRATE — hand off to an external tool that already does it well.
- DEFER — postpone until a paying pilot proves demand.
- DO NOT PURSUE — disagree to build or integrate at this stage.

## Recommendations

| Capability | Decision | Customer value | Strategic importance | Current repo support | Complexity | Commodity risk | Helps first pilot | Timing |
|---|---|---|---|---|---|---|---|---|
| Filler-word removal | INTEGRATE · DEFER | Reduce dead time in talking content | Low | None in repo | Medium | High | No | Later, only if a podcast client asks |
| Dead-air removal | INTEGRATE · DEFER | Same as above | Low | None | Medium | High | No | Later |
| Captions | INTEGRATE | Platform-readiness and accessibility | Medium | Caption prompt text only (`generate_asset_prompts.py`); no burned-in captions | Medium | High | Sometimes | Integrate a mature caption tool; don't build |
| Motion graphics / title cards | PARTNER · DEFER | Branded polish | Medium | None | High | High | No | Later; partner not build |
| Editable timeline | DO NOT PURSUE (compete directly) | Full editorial control | Low vs our model | None | Very high | Low (mature) | No | Not building a full NLE |
| Music beds | DEFER · PARTNER | Adds polish to clips | Low | None | Low-complexity (library) | Medium | No | Later |
| Audio cleanup / noise removal | INTEGRATE · DEFER | Cleaner audio for voice/talking-head | Low | None | Medium | Medium | No | Later |
| AI voiceover | DEFER · PARTNER | Voice overs for ads/captions | Low | None | Medium | Medium | No | Later |
| Hook generation | BUILD (prompt-based, cheap) | Stronger openers | Medium | Detection prompt generation exists; captions/hook text partially generated | Low | Medium | Maybe | Optional, low effort |
| General talking-head editing | DO NOT PURSUE | Editing talking heads | Low vs our model | None | High | High (commodity) | No | Defer |
| Podcast clipping | INTEGRATE | Clip quotes from episodes | Medium | Transcription + export reusable; detection templates missing | Medium | High | After sports pilot | Later |
| Sports-event detection | BUILD (deepen) | Core game highlight value | High | Football event detection works (config `emotions.yml`, detection script) | Medium | Low-mid (specialized) | Yes | **Leverage now** |
| Schedule-driven processing | BUILD | Recurrent content without manual work | High | `data/worldcup_2026_schedule.csv`, `process_scheduled_match.py` | Medium | Low (org-own schedule is unique) | Yes | **Priority** |
| Research-assisted clipping | BUILD | Better, defensible selection | Medium-high | Research windows exist (`export_research_windows.py`) | Medium | Low | Yes (sports) | **Priority** |
| Brand templates | BUILD (config-driven) | Brand-safe uniform output | Medium-high | Config `account_positioning`, hashtags; brand system doc-only | Medium | Low | Brand intake | **Priority** |
| Human approval | BUILD (lightweight) | Trusted accuracy before delivery | High | Static review dashboard, CSV statuses | Low | Low | Yes | **Priority** |
| Job logging | BUILD (minimal) | provenance, audit, ops | High | Match manifests track pipeline flags | Low | Low | Yes | **Priority** |
| Rights intake | BUILD (workflow, doc/config) | Legal safety | High | Documented in security/rights doc; no intake template | Low | Low | Yes | **Priority** |
| Shared-folder delivery | BUILD (manual, doc + manifest) | Client handoff mode | Medium | No generic delivery manifest | Low | Low | Yes | **Priority** |
| Publishing integrations | DEFER / INTEGRATE later | Posting directly to platforms | Medium | None | High | High risk (API maturity) | No | Defer |
| Performance reporting | BUILD (manual for pilot; later metrics) | Show delivered value | Medium | None | Low | Low | Only after pilot | Later |

## Principles

1. **Do not rebuild mature commodity editing tools** unless repository evidence and a paying pilot justify it.
2. **Where mature tools solve the problem well, integrate** — treat external editors as downstream destinations, not competitors we must replicate.
3. **Own the operational layer, not the editor.** Our defensible value is in schedule, research, brand, review, rights, provenance, delivery, and reporting.
4. **Prioritize first-pilot value.** Build the thin set that lets one sports/highlight pilot run end to end; defer everything else.

## External Tools As Destinations

A relevant structural view: external editing tools (including ChatCut and other editors) can become **downstream destinations** rather than competitors. We produce well-selected, metadata-rich clip candidates with openings, research notes, and provenance; an editor (human or tool) can finish polish. This lets us integrate instead of copying their features.

## First-Pilot Build Priority

For the first paid pilot, build or document only:

- Pilot runbook (`docs/pilot_runbook.md`).
- Source intake template.
- Rights checklist.
- Brand intake template.
- Job log.
- Local-file source manifest.
- Safer FFmpeg execution and clearer operator-facing errors.

These map directly to `phase-0-verification/07_IMPLEMENTATION_PLAN.md`. Do not build editing features for the pilot.

## Note On Confidence

Where repository evidence is absent, the recommendation is deliberately conservative (defer/integrate). ChatCut's features are NOT assumed to exist in this repository.