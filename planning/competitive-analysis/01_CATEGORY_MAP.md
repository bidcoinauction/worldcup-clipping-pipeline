# Category Map

This document maps the relevant market categories and locates both ChatCut and our current position within them. It grounds every "relationship to our repository" claim in the repository evidence.

## Categories At A Glance

| # | Category | Primary customer | Typical job | Purchase reason | Automation | Human involvement | Recurring vs one-time | Operational complexity | Competitive intensity | Relationship to our repo |
|---|---|---|---|---|---|---|---|---|---|---|
| 1 | Traditional video-editing software | Professional editors, studios | Complex timeline editing, polishing | Granular control | Low (manual tool) | High (skilled operator) | Project-based / one-off | High | High, mature | None; we are not an editor |
| 2 | AI-assisted video editors | Editors, prosumers | Fast assisted edits | Speed, fewer manual steps | Medium | Medium | Both | Medium | High | Adjacent commodity |
| 3 | Conversational editing tools | Non-editor individual creators | Edit video in natural language | No editing skill needed | Medium | Low | One-off | Low | Rising | ChatCut fits here |
| 4 | Automatic clipping tools | Creators, marketers | Auto-detect moments and generate clips | Automatic highlight generation | High | Low | Recurring batches | Low | High | Overlaps our analysis/extract stage, but commoditized |
| 5 | Creator-content tools | Individual creators | Turn long content into social posts | Repurposing + captions | Medium | Medium | Recurring | Medium | Medium | Overlaps our transform stage |
| 6 | Social repurposing tools | Marketers, social teams | Resize/form 1 source into many formats | Multi-platform output | Medium | Low | Recurring | Medium | High | Overlaps our export stage |
| 7 | Managed video-editing services | Organizations without edit staff | Outsourced editing of ongoing content | Capacity, consistency | Medium | Medium (service producer) | Recurring | High | Low-mid | Where our business model matches |
| 8 | Media-production agencies | Organizations with premium needs | Full campaign/quality production | Quality, strategy | Low | High | One-off + ongoing | Very high | High | We are not a traditional agency |
| 9 | Sports clipping systems | Leagues, teams, broadcasters | Game highlight production, rights-managed | Sports specificity, provenance | High | Medium | Highly recurring | High | Low-mid | This is our strongest repo fit |
| 10 | Media-operations platforms | Publishers, leagues, orgs | End-to-end managed content operations | Recurring reliability, ops | High | Medium | Recurring | High | Low | Our aspirational position |

## Where ChatCut Fits

ChatCut appears to fit best as a **conversational creator editorial tool** (#3) that also touches automatic clipping (#4) and social repurposing (#6). Its core is per-piece, creator-facing video editing delivered as a ChatGPT plugin.

## Where Our Strongest Defensible Position Exists

The strongest defensible space forms the overlap of:

- **Sports/events clipping (#9)** — because the repository already understands matches, events, emotional taxonomy, timelines, and vertical exports, and it is not generic editing.
- **Managed media operations (#7 / #10)** — recurring, multi-source, review-gated, provenance-preserving workflows, rather than one-off edits.
- **Agency operating model** — a human-in-the-loop service that guarantees brand safety and approvals.

These overlap positions are defensible because they depend on workflow discipline, source-and-selection provenance, recurring production cadence, and brand/review configuration — not on any single editing feature, which is commodity.

## Commoditized Middle

The center of the map (automatic clipping, captions, filler removal, aspect-ratio conversion, generic short-form export) is becoming commodity. Competing there product-for-product would put us in the least defensible territory.

## Category Detail (relationships to repo evidence)

- **Sports clipping systems (#9):** Our repository handles matches, teams, events, emotional arcs (`config/series.yml`, `config/emotions.yml`), sports research windows, and vertical export. This is the strongest reusable asset.
- **Media-operations platforms (#7/#10):** The business architecture documents an Organization->Project->Workflow->Job->Output->Asset hierarchy (`04_OPERATING_MODEL.md`); a capability chain from Capture to Archive (`10_CAPABILITY_MODEL.md`); and a managed-pilot offer in `RELEASE_READINESS.md` and `phase-0-verification/06_PILOT_READINESS_GAPS.md`. Today these are documented, not productized.
- **Automatic clipping (#4) and social repurposing (#6):** The repository can run detection (config `providers`, `scripts/run_gpt_detection.py`) and export vertical clips (`export_research_windows.py`, `export_clips_ffmpeg.py`), but these features are commodity. We should use them, not lead with them as differentiators.

## Takeaway For Roadmap

ChatCut confirms the gradient: individual editing and generic short-form clipping are crowded and commodity. Our defensible position is managed media operations with sports/events as the proven wedge, using commodity editing as replaceable components while owning the operational layer.