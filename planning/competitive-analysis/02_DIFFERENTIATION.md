# Differentiation

This document directly compares ChatCut's apparent offer with our potential offer. Classifications are grounded in repository evidence. We do not present generic AI editing capabilities as differentiation.

## Comparison Matrix

| Dimension | ChatCut (apparent) | Us (potential) | Classification |
|---|---|---|---|
| Individual creator vs organization | Individual creator-facing | Organization-facing managed service | OUR VERIFIED STRENGTH (business model documented in `00_VISION.md`, `03_OFFERINGS.md`) |
| One video vs recurring production | One-piece edits (vlog teaser, podcast short) | Recurring production workflows (leagues, schedules, weekly output) | OUR VERIFIED STRENGTH |
| General editing vs defined workflows | General conversational editing | Defined, repeatable workflows per client/event | OUR VERIFIED STRENGTH |
| Manual conversation vs scheduled jobs | Manual conversational tasks | Schedule-driven processing (World Cup schedule CSV, `process_scheduled_match.py`) | OUR VERIFIED STRENGTH (partially; schedule is World Cup-specific) |
| Single-user editing vs agency operation | Single-user editing in ChatGPT | Agency/managed operation with review and approval | OUR VERIFIED STRENGTH (documented, not yet productized) |
| Highlight generation | Highlight selection advertised | Analysis + extraction present (`run_gpt_detection.py`, research windows) | SHARED CATEGORY FEATURE |
| Sports/event research | Not advertised | Match research windows, event taxonomy, mythology scoring | OUR VERIFIED STRENGTH |
| Event taxonomy | Not advertised | Categories + series/emotions (`config/emotions.yml`, `config/series.yml`) | OUR VERIFIED STRENGTH |
| Brand consistency | Not advertised | Brand positioning in config; brand intake documented | FUTURE OPPORTUNITY (config exists; brand system is doc-only) |
| Human approval | Not advertised | Static review dashboard, CSV statuses | OUR VERIFIED STRENGTH (basic) |
| Rights confirmation | Not advertised | Rights boundary in docs (`05_SECURITY_AND_RIGHTS_CHECK.md`) | FUTURE OPPORTUNITY (documented, not a workflow yet) |
| Job history | Not advertised | Match manifests track pipeline flags | FUTURE OPPORTUNITY (basic via manifests) |
| Source provenance | Not advertised | Manifest/source model, archive conventions | FUTURE OPPORTUNITY |
| Archive structure | Not advertised | `FOOTBALL_ARCHIVE_ROOT`, `FootballArchive/`, league dirs | OUR VERIFIED STRENGTH |
| Multi-project operations | Not advertised | Documented Org->Project->Workflow hierarchy | FUTURE OPPORTUNITY (doc-only) |
| Delivery | Final file + editable project (ChatCut) | Shared-folder/manual delivery documented | SHARED CATEGORY FEATURE (delivery differs in mode) |
| Publishing | Not advertised | Not offered yet | NOT A PRIORITY |
| Reporting | Not advertised | Manifests + future metrics | FUTURE OPPORTUNITY |
| Editable timeline | ChatCut advertises editable timeline projects | Not offered | COMPETITOR APPARENT STRENGTH |
| Motion graphics | ChatCut advertises motion graphics | Not offered | COMPETITOR APPARENT STRENGTH |
| Captions | ChatCut advertises captions | Caption prompt text generated (`generate_asset_prompts.py`); no burned-in captions | SHARED CATEGORY FEATURE (commodity) |
| Filler-word removal | ChatCut advertises | Not offered | COMPETITOR APPARENT STRENGTH |
| Dead-air removal | ChatCut advertises | Not offered | COMPETITOR APPARENT STRENGTH |
| AI voiceovers | ChatCut advertises | Not offered | COMPETITOR APPARENT STRENGTH |
| Noise removal | ChatCut advertises | Not offered | COMPETITOR APPARENT STRENGTH |
| Long-to-short conversion | ChatCut advertises | Present via analysis + vertical export | SHARED CATEGORY FEATURE |
| Reliability and repeatability | UNKNOWN | Local-first scripts with dry-runs, validation | OUR VERIFIED STRENGTH |
| Current implementation maturity | UNKNOWN (no repo evidence) | World Cup reference deployment (tested, 541 passing) | OUR VERIFIED STRENGTH (for sports workflow) |

## Commodity Capabilities

These are already becoming commodity and must not be presented as differentiation:

- Captions.
- Filler-word removal.
- Basic highlight selection.
- Aspect-ratio conversion.
- Generic short-form exports.
- Basic motion graphics.
- Conversational edit requests.

The category map (`01_CATEGORY_MAP.md`) confirms the center of the market is commoditizing.

## More Defensible Workflow And Operational Capabilities

The capabilities that may be more defensible are operational and organizational rather than purely editorial:

- **Recurring production cadence** — weekly game/episode/event workflows, not one-off edits.
- **Sports/event research and taxonomy** — event-aware selection with football/sports semantics.
- **Brand consistency as data** — brand rules captured per client (doc-level today, config path planned).
- **Human review and approval gating** — review dashboard and status tracking as part of the service.
- **Rights confirmation** — source-level rights recording before commercial processing.
- **Source provenance and job history** — manifests and future job logs that show how a clip was chosen.
- **Archive and asset organization** — `FOOTBALL_ARCHIVE_ROOT` and structured league/event folders.
- **Agency operation** — the business model of managing many recurring jobs for organizations.

## Positioning Statement

We do not compete with ChatCut by copying per-piece creator editing. We occupy managed media operations for organizations producing recurring live or long-form content, using commodity editing (captions, clipping, vertical export) as replaceable components while owning the operational layer: schedules, research, brand, review, rights, provenance, delivery, and reporting.

## Guardrail

No ChatCut capabilities are claimed as existing in this repository unless a file proves them. Where a capability is documented but not implemented (e.g., rights intake workflow, job log), it is classified as FUTURE OPPORTUNITY, not OUR VERIFIED STRENGTH.