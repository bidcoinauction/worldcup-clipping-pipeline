# Rights Confirmation

## Operational disclaimer

**This document is not legal advice.** It records operational rights
confirmation for processing. The software does not provide legal approval, and
a confirmation record is an operational record, not a legal opinion. Confirm
any open legal questions with qualified counsel.

## What the client must affirm

The client (rights holder, or someone authorized by them) must **affirmatively
confirm** that the operator may:

- Store the supplied source file for a defined scope.
- Clip (trim / select moments) from that source.
- Review internal outputs.
- Deliver the approved outputs through the agreed channel.

Affirmative confirmation cannot be inferred from:

- Public availability of the media
- Stream accessibility
- Platform URLs
- File possession
- Prior clipping
- Any client statement that does not affirmatively grant the above uses

## Permitted uses

Record which specific uses are permitted (`permitted_uses`), for example:
`["clip", "store", "review", "delivery"]`. If publishing is intended, the
list must also include `publish` / `public_distribution`.

## Distribution limits

Record any limits on distribution (`distribution_limitations`), for example
"no broadcast without client approval", "approved social channels only",
"no resale". If publishing is enabled, distribution limitations **must** be
recorded.

## Publishing distinction

Publishing is included only when the intake explicitly sets
`publishing_included: true` and the permitted uses allow it. The default is
`false`. A publishing request without corresponding permission is rejected by
the validator.

## Expiration

If the confirmation expires (an `expiration_date`), the rights are treated as
expired once that date passes, and the job must not be run or delivered.
Expired confirmations no longer pass the rights gate.

## Takedown or revocation

If the client revokes rights, stop processing and delivery immediately. Record
the revocation in the job event log and cancel or quarantine the job. Do not
deliver anything new after revocation.

## Requirements to be execution-ready

Only these are combined with a valid source, and confirmed rights:

- `status: CONFIRMED`
- a confirmation statement
- a confirmer identity and confirmation date
- at least one permitted use
- confirmation not expired
- If `publishing_included` is true: publish is a permitted use and
  distribution limitations are recorded.

Every other status (`UNCONFIRMED`, `RESTRICTED`, `EXPIRED`, `REJECTED`) does
not pass the gate. `RESTRICTED` validate structurally but require an explicit
supported-use check before execution.

## Never store

Do not record government IDs, payment information, authentication
credentials, or unnecessary personal information in a rights record.