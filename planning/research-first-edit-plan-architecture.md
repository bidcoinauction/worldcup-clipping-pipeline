# Research-First Intelligence And Edit Plans

Clipper remains the source of truth for sports understanding, moment identity,
story selection, edit intent, provenance, review state, and performance learning.

## Architecture

Research = what happened.

Alignment = where it happened in media.

Media signals = how it felt.

Story = why it matters.

Edit Plan = how to tell it.

Renderer = how it gets executed.

## Strategies

Projects carry a runtime `analysis_strategy`:

- `TRANSCRIPT_FIRST`
- `RESEARCH_FIRST`
- `HYBRID`

Missing strategy remains backward compatible and behaves as `TRANSCRIPT_FIRST`.
Pipeline runs may record a one-run override, but all resolution goes through the
runtime helper `effective_analysis_strategy(project, run_override=None)`.

## Research-First Flow

Match Identity -> MatchResearch -> ResearchEvents -> Media Alignment -> Moment
Seeds -> Canonical Moments -> Story -> Edit Brief -> Edit Plan -> Renderer.

Research tells Clipper the factual sports timeline. Alignment estimates where
those facts occur in media. Transcript, audio, and visual evidence can enrich
the researched events but are not required to create them.

## Renderers

FFmpeg is infrastructure: ingestion, normalization, proxies, extraction, simple
deterministic cuts, and fallback rendering.

ChatCut is creative execution: composed edits, layered text, motion graphics,
freeze-frame treatments, and richer timeline handoff. No direct API is assumed;
Clipper exports an inspectable handoff package until a verified API/SDK exists.

Renderer adapters receive edit instructions only. They do not understand
football, basketball, F1, or any other sport.

## Performance Loop

EditPlan metadata preserves editorial choices such as hook style, hook duration,
climax position, freeze-frame usage, crowd reaction usage, subtitle density,
runtime, music style, and motion graphics usage. Future performance data can be
correlated with these choices without changing the canonical story or render
objects.
