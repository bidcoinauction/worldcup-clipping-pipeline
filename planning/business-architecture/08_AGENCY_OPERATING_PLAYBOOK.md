# Agency Operating Playbook

## Purpose

This playbook describes how the business can deliver value before the software is fully productized.

## Operating Sequence

### 1. Lead Qualification

Goal:

- Confirm the client has recurring long-form content and a real distribution need.

Questions:

- How often do you create source content?
- Who owns the rights?
- What platforms do you publish to?
- How quickly do clips need to be delivered?
- Who approves final assets?

Repo support:

- Missing.

### 2. Content Source Discovery

Goal:

- Identify source files, streams, archives, or feeds.

Questions:

- Are files available locally or in a shared folder?
- What format and duration?
- Are there captions, transcripts, or metadata?
- Are there rights constraints?

Repo support:

- Partial for local files and Ace Stream.

### 3. Client Onboarding

Goal:

- Create an organization/project record manually.

Minimum artifacts:

- Client brief.
- Source checklist.
- Brand intake.
- Approval contact.
- Pilot scope.

Repo support:

- Missing.

### 4. Brand Collection

Goal:

- Capture brand rules before outputs are generated.

Minimum artifacts:

- Logo files.
- Colors.
- Caption examples.
- Hashtags.
- Tone notes.
- Do-not-use rules.

Repo support:

- Missing except hardcoded case-study defaults.

### 5. Workflow Configuration

Goal:

- Select the closest workflow template and manually configure it.

Repo support:

- Partial through `config/pipeline_config.json` and CLI flags.

### 6. Test Run

Goal:

- Process one source file and validate outputs.

Repo support:

- Partial through dry-run flags and local scripts.

### 7. Client Approval

Goal:

- Let the client accept, reject, or request edits.

Repo support:

- Partial through the static review dashboard and CSV statuses.

### 8. Production Launch

Goal:

- Run the agreed workflow for the scoped pilot.

Repo support:

- Manual.

### 9. Review And Publishing

Goal:

- Deliver approved assets for client publishing.

Repo support:

- Manual delivery only.

### 10. Monitoring

Goal:

- Track job success, failure, cost, and turnaround.

Repo support:

- Missing.

### 11. Reporting

Goal:

- Show what was produced and delivered.

Repo support:

- Missing except manifests.

### 12. Billing

Goal:

- Invoice based on agreed service terms.

Repo support:

- Missing.

### 13. Support

Goal:

- Handle revisions, errors, and urgent delivery issues.

Repo support:

- Manual.

### 14. Offboarding And Data Export

Goal:

- Return client data and remove retained assets if required.

Repo support:

- Missing.

## What To Sell First

Sell a managed pilot, not a platform:

- Limited number of source files.
- Defined clip volume.
- Manual review.
- Shared folder delivery.
- Human QA.

## What Not To Promise Yet

- Fully automated publishing.
- Guaranteed viral clips.
- Real-time clipping.
- Self-serve dashboard.
- Multi-client portal.
- Platform analytics.
