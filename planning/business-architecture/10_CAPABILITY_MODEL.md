# Capability Model

## Purpose

The capability model defines what the company does independent of any specific software feature, customer type, or content format.

Every future offering, workflow, and product feature should map to one or more capabilities.

## Capability Map

```text
Capture
  -> Organize
    -> Analyze
      -> Extract
        -> Transform
          -> Review
            -> Approve
              -> Deliver
                -> Publish
                  -> Measure
                    -> Archive
```

This is not a required linear process for every client. It is a map of reusable operating capabilities.

## Capture

Acquire or receive source media.

Examples:

- Receive uploaded files.
- Record a live stream.
- Import a VOD.
- Ingest an archive folder.

Current repo support:

- Local file processing.
- Ace Stream recording.
- Partial LiveTV-related tooling.

Tradeoff:

- Live capture creates more operational risk than file-based intake. First pilots should prefer client-supplied files.

## Organize

Structure source media, metadata, rights notes, and project context.

Examples:

- Project folders.
- Source manifests.
- Match or session metadata.
- Brand assets.

Current repo support:

- CSV and JSON manifests.
- Football archive conventions.

Tradeoff:

- Manual organization is acceptable early. Poor organization becomes expensive once multiple clients are active.

## Analyze

Understand source media and identify useful moments, topics, or sections.

Examples:

- Transcription.
- Event detection.
- Quote extraction.
- Speaker or participant context.

Current repo support:

- Transcription.
- Prompt-based football moment detection.

Tradeoff:

- Analysis should assist operators, not replace review during early agency delivery.

## Extract

Select useful time ranges, quotes, segments, or metadata from source media.

Examples:

- Clip windows.
- Quote ranges.
- Highlight sections.
- Archive markers.

Current repo support:

- Clip manifests and research windows.

Tradeoff:

- Extraction rules differ heavily by customer type. Avoid over-generalizing before pilots.

## Transform

Convert selected material into usable outputs.

Examples:

- Vertical clips.
- Captions.
- Thumbnails.
- Social copy.
- Localized versions.
- Metadata packages.

Current repo support:

- FFmpeg exports.
- Caption text files.
- Thumbnail prompts.

Tradeoff:

- Transformation is where brand rules matter most. Brand should become data before heavy export automation.

## Review

Evaluate outputs before delivery or publishing.

Examples:

- Operator review.
- Client review.
- Crop checks.
- Rights checks.
- Caption checks.

Current repo support:

- Static local review dashboard.
- CSV status fields.

Tradeoff:

- Human review is not a weakness. It is part of the service value until automation is proven safe.

## Approve

Record a decision that an output is ready for delivery or publishing.

Examples:

- Approved.
- Needs trim.
- Needs crop.
- Rejected.
- Legal review required.

Current repo support:

- Partial through manifest and status conventions.

Tradeoff:

- Approval can be manual at first, but decisions must become traceable for repeatable operations.

## Deliver

Move approved outputs to the client or publishing team.

Examples:

- Shared folder.
- Export package.
- Delivery manifest.
- Notification.

Current repo support:

- Manual only.

Tradeoff:

- Shared-folder delivery is enough for early pilots. Direct integrations can wait.

## Publish

Send approved outputs to public or internal destinations.

Examples:

- Social platform upload.
- CMS handoff.
- Internal asset library.

Current repo support:

- Missing.

Tradeoff:

- Do not promise direct publishing until review, rights, and account-access workflows are mature.

## Measure

Track operational and content performance.

Examples:

- Turnaround time.
- Output volume.
- Review pass rate.
- Cost per job.
- Client feedback.
- Platform performance if provided.

Current repo support:

- Missing.

Tradeoff:

- Operational metrics should come before marketing analytics.

## Archive

Preserve source provenance, outputs, and reusable metadata.

Examples:

- Source-to-output lineage.
- Rights notes.
- Approved outputs.
- Reusable clips.
- Searchable archive metadata.

Current repo support:

- Partial through archive folders and CSV/JSON files.

Tradeoff:

- Archive quality becomes a long-term advantage if provenance is preserved from the start.

## Design Principles To Carry Forward

- Configuration over customization.
- Workflows over forks.
- Human approval before high-risk automation.
- Preserve provenance.
- Every output should be reproducible.
- Customer branding is data, not code.
- Workflows should be composable.
- Case studies are deployments, not products.
