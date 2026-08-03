# Minimum Viable Paid Pilot Implementation Plan

## Principle

Build the smallest operational layer needed to sell and deliver one managed paid pilot. Preserve the World Cup implementation as the current reference deployment.

Do not build dashboard, authentication, billing, multi-tenancy, publishing integrations, or broad product abstractions in the next phase.

## Recommended Next Build Phase

Phase 1: Managed local-file paid pilot readiness.

## Pilot Boundary

- One client.
- One project.
- Local client-supplied media files only.
- One workflow: sports/game highlight production.
- Manual review and shared-folder delivery.
- Explicit rights confirmation before processing.

## Workstream 1: Operator Runbook

Create `docs/pilot_runbook.md` covering:

- Intake checklist.
- Environment check.
- Source file placement.
- Dry-run commands.
- Processing commands.
- Review steps.
- Delivery steps.
- Failure handling.

## Workstream 2: Intake Templates

Create templates before code abstractions:

- `docs/source_intake_template.md`
- `docs/brand_intake_template.md`
- `docs/rights_checklist.md`

These should be plain Markdown so the first pilot can be operated manually.

## Workstream 3: Pilot Job Log

Add a simple local-first job log format, likely JSON or CSV, to record:

- Job ID.
- Client/project label.
- Source file path.
- Rights confirmation status.
- Commands run.
- Start and finish timestamps.
- Status.
- Errors.
- Outputs.
- Delivery notes.

Keep it additive and avoid a database.

## Workstream 4: Local-File Source Manifest

Add a minimal source manifest for local files that can coexist with existing World Cup match manifests.

Required fields should include:

- Source ID.
- Local path or archive-relative filename.
- Source type.
- Duration if known.
- Rights status.
- Notes.

Do not remove current World Cup manifest behavior.

## Workstream 5: Reliability And Safety

Prioritize small fixes that reduce operator failure during paid work:

- Replace remaining generated-command `shell=True` FFmpeg execution with safer list-form execution or strict parsing.
- Add clearer subprocess error messages with stderr where available.
- Add clearer hosted API error messages.
- Validate that expected output files exist before marking a job complete.

## Workstream 6: Pilot Configuration

Add only the config needed for one pilot:

- Client/project display name.
- Brand notes or link to brand intake.
- Workflow selection.
- Output directory.
- Default clip/export profile.

Avoid organization/project/workflow platform models until repeated pilots prove the shape.

## Definition Of Done

- A new operator can run a documented local-file pilot from intake through delivery.
- Existing World Cup validation and tests still pass.
- The job log records source-to-output provenance.
- Rights status is recorded before processing.
- Failure modes are documented or surfaced clearly.
- No broad product features have been added.
