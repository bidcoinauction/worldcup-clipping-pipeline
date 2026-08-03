# Customer Types

## Evaluation Dimensions

Every customer type should be evaluated by:

- Source format.
- Content cadence.
- Content taxonomy.
- Brand complexity.
- Review requirements.
- Delivery destination.
- Rights clarity.
- Operational complexity.

## Sports Leagues

Typical need:

- Game highlights.
- Player moments.
- Weekly social content.
- Sponsor-safe outputs.

What they buy:

- Reliable clip production across many games.
- Consistent branding.
- Fast turnaround after matches.

Current repo fit:

- Strongest fit because the current implementation already understands matches, teams, events, emotional arcs, and vertical exports.

Main gaps:

- Non-soccer taxonomy.
- Client-specific schedules.
- Brand profiles.
- Approval and delivery workflow.

## Teams

Typical need:

- Team-controlled highlights.
- Player social assets.
- Behind-the-scenes clips.
- Sponsor deliverables.

What they buy:

- Branded assets their media team can post quickly.

Current repo fit:

- Good for match footage if source files are provided.

Main gaps:

- Player metadata.
- Team brand system.
- Sponsor constraints.

## Tournaments And Events

Typical need:

- Daily recap clips.
- Best moments packages.
- Participant highlights.

What they buy:

- Event-week production capacity.

Current repo fit:

- The World Cup schedule model is a case study, but it is currently hardcoded.

Main gaps:

- Generic event schedule model.
- Multi-venue handling.
- Operational monitoring.

## Esports Organizations

Typical need:

- Match highlights.
- Caster reactions.
- Player reactions.
- Clutch moments.

What they buy:

- Fast social outputs from streams and VODs.

Current repo fit:

- Transcription and export are reusable.

Main gaps:

- Game-specific event taxonomy.
- Stream/VOD ingest.
- Chat or scoreboard signals.

## Streamers And Creators

Typical need:

- Funny moments.
- Reactions.
- Wins and losses.
- Community moments.

What they buy:

- More output from long streams without hiring a full-time editor.

Current repo fit:

- Low to medium. The current media processing scripts can process VOD files, but the detection logic is football-oriented.

Main gaps:

- Twitch/YouTube source adapter.
- Chat signal ingestion.
- Creator-specific tone and format.

## Podcasts And Interview Shows

Typical need:

- Quotable moments.
- Topic clips.
- Guest highlights.
- Captioned vertical exports.

What they buy:

- Repeatable episode-to-social workflow.

Current repo fit:

- Medium. Transcription and export are reusable.

Main gaps:

- Speaker metadata.
- Quote detection templates.
- Caption styling.

## Conferences And Business Events

Typical need:

- Speaker highlights.
- Key quotes.
- Session summaries.
- LinkedIn-ready clips.

What they buy:

- Post-event content extraction and repackaging.

Current repo fit:

- Medium if recordings are supplied.

Main gaps:

- Speaker/session metadata.
- Brand and lower-third rules.
- Rights and speaker approval workflow.

## Media Archives

Typical need:

- Find valuable moments in old footage.
- Build evergreen packages.
- Organize source assets.

What they buy:

- Archive activation.

Current repo fit:

- Medium. The current football archive model is a useful precedent.

Main gaps:

- Generic archive metadata.
- Search and indexing.
- Rights provenance.
