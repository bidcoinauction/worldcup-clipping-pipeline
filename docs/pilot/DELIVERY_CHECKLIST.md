# Delivery Checklist

Confirm every item before delivering pilot outputs.

## Job and source

- [ ] Correct job (`show JOB_ID`) — matches the intended pilot and source.
- [ ] Current job revision noted before delivery transition.
- [ ] Correct original source file confirmed (no mixed-up sessions).
- [ ] Correct brand profile referenced.

## Output configuration

- [ ] Correct export profile used (vertical / platform profile).
- [ ] Output naming verified (expected `clip_id_<suffix>.<ext>` patterns).
- [ ] Naming matches the agreed convention.
- [ ] Expected clip count matches the intake (`requested_clip_count` range).

## Rights and review

- [ ] Rights are still valid (`CONFIRMED`, not expired, not revoked).
- [ ] Human review completed on every deliverable.
- [ ] Approval recorded.
- [ ] `APPROVED -> DELIVERY_READY` transition recorded before handoff.
- [ ] Publishing not performed unless explicitly permitted.

## Destination and privacy

- [ ] Shared-folder / local-directory destination verified.
- [ ] No accidental source-media exposure (only approved deliverables leave).
- [ ] Delivery recorded in the job event log.
- [ ] `DELIVERED` transition recorded only after manual handoff is complete.
- [ ] `history JOB_ID` shows the expected append-only sequence.

## Post-delivery

- [ ] Intake manifest and job record archived under `data/pilot/`.
- [ ] Client confirmed receipt (if applicable).
