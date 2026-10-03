# Operator Console Architecture

## Purpose

The future Operator Console is a non-technical control surface for the sports
story engine. It should let an operator create projects, select a sport and
channel, provide source media, start analysis, review moments and stories,
approve outputs, and export packages without editing JSON, running Python,
using Terminal, knowing environment variables, reading execution-plan manifests,
or manually locating generated files.

The console is not an editing program. It is an operator workflow over the same
engine artifacts the CLI uses today.

## Conceptual Architecture

```text
Operator Console
      ↓
Application / Service Layer
      ↓
Job + Story Engine
      ↓
Sport Adapter
      ↓
Detection / Research / Editing
      ↓
Artifact Store
```

The CLI and the future frontend should be two clients of the same application
layer. CLI scripts remain supported, but new pipeline capabilities should expose
reusable Python functions first, then adapt those functions to CLI commands.

## UI Language

The UI should translate engine terms into operator terms:

| Engine term | Operator term |
| --- | --- |
| Job | Project |
| Intake manifest | Source |
| Detection | Analyze |
| Event manifest | Moments |
| Mythology/archetype | Story |
| Edit Decision List | Edit |
| Execution plan | Processing |
| Output manifest | Videos |
| Delivery package | Export |

Technical identifiers such as `JOB_ID`, `PLAN_ID`, stage IDs, manifest paths,
environment variable names, and generated file locations should be hidden by
default. They can appear in an advanced details/logs drawer for technical review.

## Application / Service Boundary

Reusable pipeline behavior should live under `pipeline/` modules. CLI scripts
under `scripts/` should parse arguments, call service functions, print concise
results, and exit.

Current service examples:

- `pipeline.configurator` resolves registered sport/project profiles, brand
  profiles, editorial taxonomies, templates, export profiles, and output roots.
- `pipeline.pilot` manages intake validation, job records, execution-plan
  manifests, run records, output manifests, delivery manifests, and readiness.
- `pipeline.prompt_generation` builds and writes profile-aware detection prompts.
- `pipeline.clip_manifest` builds and writes the existing clip manifest CSV
  artifact from detected clip candidates and returns structured status for UI
  callers.

Future console-facing services should return structured dictionaries containing
operator-safe status, artifact references, validation issues, and next actions.
They should not require callers to shell into individual scripts.

## Sport Registry

The sport/project registry is explicit and intentionally small. It is not a
plugin framework. A registered profile describes:

- profile identifier
- sport
- production-capable flag
- default flag
- profile/configuration references
- brand reference
- editorial taxonomy reference
- export profile references
- command-preview league label

Football remains the default reference deployment. Basketball is the first
explicitly selectable additional production-capable profile. Registered
non-production profiles are allowed for validation/modeling, but production
execution-plan and prompt-generation surfaces reject them.

Future sports should be added by registering another profile and adding the
minimum sport-specific configuration needed by the shared services. Do not clone
the pipeline into sport-specific script trees.

## Future Universal Event Manifest

Not implemented yet.

The future event manifest should be the sport-normalized output of detection.
It should allow downstream scoring and story selection to consume consistent
moment data regardless of sport. A future basketball detector could emit dunks,
blocks, turnovers, runs, buzzer beaters, and technical fouls; a football detector
could emit goals, saves, cards, penalties, fouls, and crowd spikes. The shared
story engine should receive normalized moments rather than raw sport-specific
detector output.

## Future Edit Decision List

Not implemented yet.

The future Edit Decision List should sit downstream of moments and upstream of
export. It should describe an operator-readable story structure such as hook,
setup, escalation, climax, and aftermath. FFmpeg or a later editing renderer
should render the edit; the story engine should decide the structure.

## Future Channel Package

Not implemented yet.

The channel package should separate sport identity from channel identity. The
same source moment may become a different treatment depending on brand, channel,
series, format, and target platform. Channel packages should collect approved
videos, captions, thumbnails, metadata, rights notes, and delivery/export
instructions into an operator-safe artifact.

## Artifact And Status Model

The console should present high-level status while preserving provenance:

- Project created
- Source validated
- Rights confirmed
- Processing planned
- Analysis running
- Moments found
- Stories suggested
- Edit ready
- Rough cut generated
- Review required
- Approved
- Export ready
- Delivered

Underneath, the engine may store intake manifests, readiness reports,
execution-plan manifests, pipeline-run records, transcripts, research files,
moment/event manifests, clip manifests, edit plans, output manifests, and
delivery packages. The UI should not require operators to understand or locate
those files.

Today, clip manifests remain CSV artifacts for compatibility with review and
export workflows. The application service returns profile, sport, input path,
output path, clip-window count, field names, coverage derived from available
timestamps, warnings, and rows so a future console can show progress without
parsing terminal text.

## Current Boundary

This repository still has substantial CLI-first behavior. The current direction
is to move reusable logic behind service functions incrementally while preserving
existing CLI workflows and tests.

The next capabilities should follow this rule:

1. Add or extend a `pipeline/` service function.
2. Cover it with tests directly.
3. Keep or add a thin CLI adapter.
4. Let a future API/frontend call the service function, not the CLI script.

Do not build React, FastAPI, authentication, publishing APIs, autonomous
execution, basketball detection, universal event manifests, or EDLs until the
service boundary for the relevant slice exists and is tested.
