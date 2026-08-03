# Security Policy

## Secret handling

Do not commit real API keys, tokens, access keys, webhook URLs, session files, or client credentials. Use `.env` for local development only; it is ignored by Git. Keep any additional secret files under `secrets/`, which is also ignored.

Use `.env.example` for variable names and safe placeholders only.

If a secret is accidentally committed or exposed, rotate it immediately before relying on deletion from Git history or a follow-up commit.

## Media and rights

Do not process or distribute client, broadcast, livestream, or archive media commercially unless the rights holder has confirmed clipping, storage, review, and delivery permissions.

## Reporting a vulnerability

If you discover a security issue in this repository (for example, accidental exposure of sensitive data), please open a GitHub issue with a clear description of the problem. Avoid sharing private keys, seed phrases, or other secrets in the report.

If the issue is sensitive, keep the report minimal and ask a maintainer to provide a secure channel for follow-up.
