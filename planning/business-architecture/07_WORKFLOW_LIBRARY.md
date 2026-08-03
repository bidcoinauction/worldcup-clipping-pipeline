# Workflow Library

## Purpose

The workflow library defines repeatable business workflows. These should be business templates before they become software abstractions.

## Workflow Structure

A workflow should eventually define:

- Name.
- Customer type.
- Source requirements.
- Metadata requirements.
- Transcription rules.
- Analysis goals.
- Review rules.
- Export profiles.
- Delivery rules.
- Reporting rules.

## Current Implicit Workflows

The repository already contains implicit templates:

### World Cup Match Workflow

Current support:

- `scripts/process_scheduled_match.py`.
- `data/worldcup_2026_schedule.csv`.
- `MATCH_RESEARCH/WORLD_CUP`.

Status:

- Case-study-specific, not generic.

### Manifest-Based Match Workflow

Current support:

- `scripts/create_match_manifest.py`.
- `scripts/process_from_manifest.py`.

Status:

- Useful pattern for multi-source recordings.

### Research Window Export Workflow

Current support:

- `scripts/export_research_windows.py`.

Status:

- Most reusable export path today.

### Live Segment Workflow

Current support:

- `scripts/record_live.py --mode segment`.
- `scripts/live_watch.py`.

Status:

- Experimental.

## Candidate Business Workflows

### Game Highlight Production

Inputs:

- Full game file.
- Teams.
- Date.
- Optional event notes.

Outputs:

- 5-10 clips.
- Vertical exports.
- Captions.

Best first technical target:

- Yes.

### Podcast Quote Clips

Inputs:

- Episode file.
- Host and guest names.
- Topic priorities.

Outputs:

- Quote clips.
- Captions.
- Social copy.

Best first technical target:

- After sports pilot.

### Conference Speaker Highlights

Inputs:

- Session file.
- Speaker metadata.
- Agenda.

Outputs:

- Speaker clips.
- LinkedIn-ready clips.

Best first technical target:

- After source and speaker metadata are modeled.

### Streamer VOD Clips

Inputs:

- VOD file.
- Optional chat export.
- Creator preferences.

Outputs:

- Funny moments.
- Reactions.
- Wins/losses.

Best first technical target:

- Later, because chat/source adapters are missing.

## Workflow Rule

Do not fork code per client. If a client needs a variation, decide whether it is:

- A configuration value.
- A prompt template.
- A source adapter.
- An export profile.
- A manual operating step.
