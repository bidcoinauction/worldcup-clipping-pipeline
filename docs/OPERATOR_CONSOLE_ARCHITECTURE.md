# Operator Console Architecture

Updated: 2026-10-05. Describes the implementation on `main`; future extensions are marked explicitly.

## Purpose and current implementation

The Operator Console is a working local browser control surface for the sports story engine. Start it with `python3 scripts/console.py` and open `http://127.0.0.1:8420`. The launcher supports `--host` and `--port`; the default is loopback. The server uses Python's standard-library HTTP server, HTML templates, and CSS, without React or FastAPI.

Operators can create/select projects, provide local media paths, confirm rights, run supported analysis, inspect moments, generate story suggestions and edit briefs, build timelines, and request rough cuts. This is an operator workflow over engine artifacts, not a general-purpose video editor or hosted multi-tenant product. Runtime dependencies and model configuration still need to be installed/configured outside the UI.

## Application / service boundary

Both CLI adapters and the console use reusable Python services. The browser calls `pipeline.console_server`, which delegates to `pipeline.operator_console`; that facade calls the engine and pilot services rather than shelling into CLI scripts.

| Module | Responsibility |
| --- | --- |
| `pipeline.configurator` | Registered profiles, configuration, brands, taxonomy, templates, and portable archive/output paths |
| `pipeline.pilot` | Intake/source/rights validation, durable jobs and events, transitions, readiness, run/plan/output/delivery records |
| `pipeline.transcription` | Transcript discovery, reuse, and source transcription |
| `pipeline.prompt_generation`, `pipeline.detection`, `pipeline.clip_manifest` | Profile-aware prompts, analysis orchestration, moments JSON, and compatible clip-manifest CSV |
| `pipeline.story_engine` | Story suggestions grounded in detected moment identifiers |
| `pipeline.edit_brief` | Editorial briefs for `SHORT`, `MEDIUM`, and `LONG` treatments |
| `pipeline.edl` | Deterministic source windows and contiguous timeline construction from briefs and moments |
| `pipeline.rendering` | FFmpeg rough cuts and render capability/status records |

Services return structured results, artifact references, status, and validation issues. New capabilities should extend these services first, receive direct tests, and then expose thin CLI/console adapters.

## Operator language

| Engine term | Operator term |
| --- | --- |
| Job | Project |
| Intake manifest | Source |
| Detection | Analyze |
| Clip-manifest rows / moments JSON | Moments |
| Mythology/archetype | Story |
| Edit brief / Edit Decision List | Edit / Timeline |
| Execution plan | Processing |
| Output manifest | Videos |
| Delivery package | Export |

These are naming guidelines, not a claim that every technical detail is hidden today. The current UI still exposes identifiers and local media paths. Advanced details should retain provenance without requiring operators to interpret raw engine files for routine actions.

## Sport registry and capability gates

The explicit registry in `pipeline.configurator` is intentionally small, not a plugin framework.

| Profile | Default | Production-capable registry flag | Console analysis |
| --- | --- | --- | --- |
| `football` | Yes | Yes | Supported |
| `basketball` | No | Yes | Not implemented (`analysis_supported=False`) |
| `basketball_sandbox` | No | No | Not supported |

Basketball can be selected for supported configuration, intake, prompt-generation, and execution-planning surfaces. Its registered profile references `config/examples/basketball.json`, the basketball brand/taxonomy, and its registered detection template. Registration does not establish basketball detection or an end-to-end validated basketball workflow. Non-production profiles are rejected by production prompt/planning surfaces.

Add future sports through registered profiles and minimal sport-specific configuration, keeping the shared services rather than cloning script trees.

## Analysis, transcription, and recovery

Analysis checks the profile capability and performs preflight before entering `RUNNING`: readable intake, current execution readiness including source and rights, and a readable local source file.

If a valid existing transcript is found, it is reused. Otherwise preflight checks FFmpeg on `PATH` and checks `faster-whisper` importability when that transcription provider is selected. The service then transcribes, generates the profile-aware prompt, calls the configured detector, builds clip-manifest rows, and persists moments JSON plus CSV artifacts.

Operator stages are Preparing Source, Transcribing, Understanding Game, Finding Moments, and Preparing Results. Analysis states include `WAITING`, `RUNNING`, `COMPLETE`, `NEEDS ATTENTION`, and `FAILED`. An orphaned in-process analysis is recovered as failed when project/status reads detect it, enabling an explicit retry; recovery does not resume partial processing automatically.

The HTTP handler catches broken-pipe, aborted-connection, and reset-connection errors so a disconnected browser does not prevent subsequent requests. This does not provide a durable background-worker queue, resumable distributed jobs, or multi-user concurrency guarantees.

## Story, edit, and render artifacts

The implemented workflow is source → transcript → moments → story suggestions → edit brief → EDL → rough cut → human review.

Story suggestions reference detected moments. Edit briefs describe narrative beats, pacing, intensity, transitions, audio strategy, and text intent for short-, medium-, or long-form treatment. EDL construction deterministically derives source windows and contiguous timeline segments, validates moment references and timeline bounds, and checks source bounds when duration is supplied. EDLs are implemented; they are not a future-only design.

The renderer supports two modes:

- `REFERENCE`: clean assembly, cuts, ordering, concatenation, and source audio.
- `EDITORIAL`: supported effects such as flash cuts, fades, audio drops, and silence, with partial freeze-push and audio-bridge support.

The capability registry in `pipeline.rendering` distinguishes applied, partially applied, and deferred features. Music selection, animated hook text, audio-stem separation, shot-aware crowd/reaction selection, and other deferred effects are not promised as executed merely because a brief requests them. Rough cuts require editorial review before delivery.

## Persistence and operator lifecycle

Durable pilot job records and append-only events live under the gitignored `data/pilot/jobs/` root by default. Analysis, story, brief, EDL, and render services retain generated artifacts and status references. These artifacts complement the existing match manifests, schedule CSV, and clip manifests rather than replacing them.

Job lifecycle state and stage-specific analysis/story/brief/EDL/render status are separate. A completed analysis or render does not itself mean an output is approved, delivered, or published. Pilot output review, delivery package/checklist creation, and delivery confirmation remain explicit operator steps; see [the pilot runbook](pilot/PILOT_RUNBOOK.md). The `pilot_job.py` CLI records and validates operations; its execution plans and manual run records do not execute media processing.

## Future extensions and boundaries

A universal, sport-normalized event manifest is still future work. Today's moments JSON and clip-manifest CSV are implemented compatibility artifacts, not a complete universal event contract.

A full channel package covering channel/series identity, approved videos, captions, thumbnails, metadata, and publishing instructions is also future work. Existing pilot delivery packages are implemented handoff records and should not be confused with autonomous channel packaging or publishing.

Authentication, billing, multi-tenancy, hosted operation, direct publishing, durable workers, and complete basketball analysis are outside the current implementation. The next changes should preserve local operation, Windows compatibility, provenance, explicit review, and tested service boundaries.

## Documentation authority

Use [README.md](../README.md) for setup and operator overview, [AGENTS.md](../AGENTS.md) for development/agent rules and capture operations, this document for console architecture, and [RELEASE_READINESS.md](../RELEASE_READINESS.md) for release scope and remaining gates. Runtime behavior is governed by code and `config/`; match state/provenance lives in `data/manifests/`, and media belongs in the external archive. Historical `planning/` evidence describes its dated phase rather than overriding current implementation.
