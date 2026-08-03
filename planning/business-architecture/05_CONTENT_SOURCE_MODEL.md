# Content Source Model

## Purpose

Content sources define where media comes from and what constraints apply before processing begins.

The current repository can process local files well enough for a pilot. It has narrower support for Ace Stream and LiveTV workflows. It does not yet have a general content-source abstraction.

## Source Types

### Uploaded Or Transferred Files

Examples:

- MP4 game recording.
- MOV conference session.
- WAV podcast recording.
- TS livestream capture.

Current support:

- Strongest current path. Most scripts can operate on local files.

Commercial use:

- Best initial pilot source model.

### Live Capture

Examples:

- Sports broadcast capture.
- Event livestream capture.
- Conference stream recording.

Current support:

- Ace Stream recording exists through `scripts/record_live.py`.
- This is specific to Windows and Ace Stream.

Commercial use:

- Should be treated carefully because rights, stream stability, and platform terms vary.

### VOD URL

Examples:

- Twitch VOD.
- YouTube livestream replay.
- Vimeo event recording.

Current support:

- Not implemented as a generic adapter.

Commercial use:

- High-value future source type.

### RSS Or Podcast Feed

Examples:

- Podcast RSS feed.
- Audio episode feed.

Current support:

- Missing.

Commercial use:

- Useful for repeatable podcast production.

### Cloud Folder

Examples:

- Google Drive folder.
- Dropbox folder.

Current support:

- Missing.

Commercial use:

- Practical agency delivery mechanism before deeper integrations.

### Archive Directory

Examples:

- Client drive of historical games.
- Conference recording archive.

Current support:

- Partial. The repository has a `FootballArchive` convention and `FOOTBALL_ARCHIVE_ROOT`.

Commercial use:

- Useful for archive processing services.

## Required Source Metadata

Every source should eventually track:

- Source ID.
- Organization.
- Project.
- Source type.
- Original location.
- Local path.
- Duration.
- Format and codec.
- Rights status.
- Capture or upload time.
- Processing eligibility.
- Notes.

## Rights And Permissions

No source should be processed commercially unless the client has confirmed rights or permission.

Minimum agency checklist:

- Who owns the footage?
- Is clipping allowed?
- Where may outputs be published?
- Are sponsor marks or broadcast graphics allowed?
- Are athletes/speakers/guests cleared?
- Are there takedown requirements?

## Recommended First Implementation Boundary

For the first paid pilot, support local files only.

Do not promise:

- Automatic livestream capture.
- Platform login integrations.
- Direct VOD downloads.
- Continuous ingest.

Those can become adapters after repeated demand.
