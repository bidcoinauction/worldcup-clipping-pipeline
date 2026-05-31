# Contributing

Thanks for helping with the GSMG.io 5 BTC puzzle effort!

## How to contribute

- **Share results clearly.** When adding new scripts or data, document the intent, inputs, and outputs.
- **Keep artifacts organized.** Prefer adding new analysis under descriptive filenames and update the relevant `.md` log if it changes the overall progress.
- **Avoid sensitive data.** Do **not** commit private keys, real seed phrases, or credentials. If you think something sensitive belongs in the repo, open an issue to discuss instead.
- **Prefer ambiguity-reducing facts.** For Level 5, prioritize authoritative clarifications and tests that rule out whole hypothesis branches before adding new brute-force variants.

## Suggested workflow

1. Review the latest status in `GSMG_Puzzle_README.md`.
2. For Level 5 work, review `LEVEL5_HYPOTHESIS_RESET.md` and state which ambiguity your change addresses.
3. Add or update scripts in the root (or under `manifest/` if you are curating a stable snapshot).
4. Summarize findings in the appropriate log file.

## Data integrity

When updating CSVs or logs, keep previous versions unless you are explicitly replacing a file. If replacing, note why in your commit message or in the related log.
