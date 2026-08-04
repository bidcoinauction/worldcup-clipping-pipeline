# Security Policy

## Secret handling

Do not commit real API keys, tokens, access keys, webhook URLs, session files, or client credentials. Use `.env` for local development only; it is ignored by Git. Keep any additional secret files under `secrets/`, which is also ignored.

Use `.env.example` for variable names and safe placeholders only.

If a secret is accidentally committed or exposed, rotate it immediately before relying on deletion from Git history or a follow-up commit.

## Media and rights

Do not process or distribute client, broadcast, livestream, or archive media commercially unless the rights holder has confirmed clipping, storage, review, and delivery permissions.

## Managed pilot intake and job records

Pilot intake manifests and job records under `data/pilot/` are runtime files
ignored by Git; they are never committed.

- Intake manifests and job records **must not contain** secrets, API keys,
  tokens, passwords, payment information, authentication credentials,
  government IDs, or unnecessary personal information. The intake validator
  rejects keys/values that look like credentials or payment data.
- Rights records contain only operationally necessary confirmation (status,
  statement, confirmer, date, permitted uses, distribution limits, expiration).
  The `show` command and the job record never surface intake confirmation or
  personal fields.
- A rights confirmation is an operational record, not legal approval. Only
  `CONFIRMED`, unexpired rights pass the execution-ready gate. No permission
  is inferred from public availability, stream access, platform URLs, file
  possession, or prior clipping.
- Source-file validation is read-only (existence, type, readability, checksum,
  optional duration). It never modifies, moves, copies, or transcodes media,
  and it never makes network requests.
- Job transition metadata and artifact references must not contain credentials,
  API keys, social tokens, payment information, source-media secrets, or path
  traversal. Transitions record manual operator state only; they do not copy,
  upload, publish, delete, or process media. `history` output remains
  privacy-safe and omits sensitive intake confirmation text.
- Output manifests link existing local files to a job for review. They must not
  contain tokens, passwords, cookies, payment details, credential URLs, shell
  commands, base64 media, environment dumps, or unnecessary personal
  information. Output registration and review never copy, move, edit, delete,
  upload, publish, or process the referenced files.

## Reporting a vulnerability

If you discover a security issue in this repository (for example, accidental exposure of sensitive data), please open a GitHub issue with a clear description of the problem. Avoid sharing private keys, seed phrases, or other secrets in the report.

If the issue is sensitive, keep the report minimal and ask a maintainer to provide a secure channel for follow-up.
