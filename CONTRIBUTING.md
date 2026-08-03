# Contributing

Thanks for helping improve the Stadium Signal World Cup clipping pipeline.

## How to contribute

- **Preserve the World Cup reference deployment.** Keep existing match, manifest, archive, and validation workflows working unless a change explicitly migrates them.
- **Keep changes scoped.** Prefer small script, config, data, or test changes over broad rewrites.
- **Document intent, inputs, and outputs.** New scripts and data files should be understandable without reading a conversation log.
- **Avoid sensitive data.** Do not commit API keys, access tokens, private media credentials, session files, or client-provided secrets.
- **Separate generated artifacts from source.** Large video/audio outputs belong in `FootballArchive/` or another ignored archive root, not in Git.

## Suggested workflow

1. Review `AGENTS.md` and `README.md` for the current operating workflow.
2. Run `python3 scripts/validate_data.py` and `pytest` before proposing a commit.
3. If a script requires local media, ffmpeg, API credentials, or network access, document that requirement in the change.
4. Keep business architecture and pilot planning grounded in the verified repository state.

## Data integrity

When updating CSVs, manifests, or research files, preserve required columns and references. Run the data validator after changes and explain any intentional schema or fixture changes.
