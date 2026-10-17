# Phase 7.3 — Unified Read-Only Media Library

## Purpose

Present a single, honest inventory of everything the Clipper holds — registered
sources, live recordings, and processed artifacts — as a read-only catalog that
Stadium Signal Lite can list, and (optionally) preview locally, without Lite
ever downloading, proxying, or uploading media.

```text
Clipper runtime records
  (Phase 7 contracts, Phase 7.2 session manifests, managed pilot jobs)
  -> pipeline.media_catalog normalizes + deduplicates into stable asset IDs
  -> stadium_signal.media_catalog/v1 catalog file (local, read-only)
  -> Stadium Signal Lite media_library.py validates + sanitizes
  -> GET /api/library (metadata only, no paths, no object URIs)
  -> optional loopback-only GET /preview?path=... (Range-capable, allow-listed)
```

## Scope And Guardrails

- **Read-only.** The catalog is a projection of existing runtime records. It
  never mutates, moves, renames, or deletes a source, recording, or artifact.
- **Additive.** No existing service changes. The World Cup reference
  deployment, Phase 7/7.1/7.2 behaviour, and both repositories are preserved.
- **No private paths emitted.** The source file's local path
  (`source.local_file_path`) is never written into a catalog, and the Lite-side
  API payload never carries artifact or source filesystem paths, nor a raw
  storage `object_uri`.
- **No media movement.** Lite never fetches, copies, transcodes, or re-uploads
  media. Preview (when explicitly enabled) streams bytes from the local disk
  over loopback only.
- **Local-only by default.** Lite's `/api/library` and `/preview` routes live in
  the loopback development server; hosted (Vercel) deployments expose neither.
- **Optional and explicit preview.** Preview is off unless both
  `STADIUM_SIGNAL_PREVIEW_ENABLED=1` and `STADIUM_SIGNAL_PREVIEW_ROOTS` are set.

## Catalog Contract

Schema: `docs/contracts/media_catalog.v1.json`
(`stadium_signal.media_catalog/v1`, version `1`).

A catalog is:

```jsonc
{
  "catalog_schema": "stadium_signal.media_catalog/v1",
  "catalog_version": 1,
  "generated_at": "2026-10-17T12:00:00+00:00",
  "producer": { "application": "worldcup-clipping-pipeline", "component": "pipeline.media_catalog" },
  "reference_deployment": "stadium_signal_lite_local",
  "asset_count": 3,
  "assets": [ /* one entry per source / recording / artifact */ ]
}
```

Every asset carries: `asset_id`, `asset_type`, `title`, `sport`, `event_name`,
`event_date`, `duration_seconds`, `created_at`, `project`, `source`, `artifact`,
`storage`, `processing`, `playback`, `provenance`, and `rights`.

- **`asset_id`** — stable, deterministic:
  `clipper:<asset_type>:<sha256(identity)[:24]>`. Rebuilding a catalog from the
  same records yields the same IDs; a new record yields a new ID.
- **`asset_type`** — `processed_artifact`, `live_recording`, or
  `registered_source`.
- **`playback.visibility`** — `local_only`, `private_remote` (set when the
  storage block reports an uploaded *and verified* remote copy), or `public`
  (never emitted today).
- **`processing.state`** — `complete`, `in_progress`, or `failed`.
- **`artifact.path`** — an absolute local path (or `null`). Network URLs are
  rejected by both the producer and the Lite consumer.

## Module And CLI

- `pipeline/media_catalog.py` — normalization, stable IDs, dedupe, catalog
  build/validate/write. Sources are read through existing pipes:
  `pipeline.pilot.list_jobs`/`show_job` (registered sources),
  Phase 7.2 session manifests (live recordings), and Phase 7/7.2
  `*.contract.json` records (processed artifacts).
- `scripts/media_catalog.py` — the operator CLI.

```bash
# Build from any combination of runtime records.
python3 scripts/media_catalog.py build \
  --contracts-dir data/pilot/contracts \
  --sessions-dir  data/live/segments \
  --jobs-dir      data/pilot/jobs \
  --out           data/library.catalog.json

# Inspect without writing.
python3 scripts/media_catalog.py build --contracts-dir data/pilot/contracts --dry-run

# Validate an existing catalog file.
python3 scripts/media_catalog.py validate data/library.catalog.json
```

Assets loaded from multiple record kinds are deduplicated by `asset_id`. Paths
in the catalog are absolute local paths under the runtime roots
(`resolve_archive_root()`); keep the catalog out of version control (Lite's
`.gitignore` already ignores `data/` and `catalogs/`).

## Stadium Signal Lite Consumption

- `media_library.py` validates the schema/version, rejects duplicate IDs,
  network URLs, traversal/relative paths, and any leaked source path, then
  serves a sanitized `GET /api/library`. It has no catalog-path awareness in the
  payload: absolute local paths are never serialized.
- `media_preview.py` serves `GET /preview?path=<abs path>` from the loopback
  server with HTTP `Range` support. A path is accepted only when it is absolute,
  traversal-free, resolves inside a configured root, exists, and has an allowed
  media extension.
- The `Library` view lists assets with type/visibility badges, processing and
  storage state, and a `Preview` action when a local preview is available.
  Playback uses the existing in-app HTML5 player.

### Enable Preview Locally (Lite)

```bash
export STADIUM_SIGNAL_MEDIA_CATALOG="$HOME/FootballArchive/ESPN/data/library.catalog.json"
export STADIUM_SIGNAL_PREVIEW_ENABLED=1
export STADIUM_SIGNAL_PREVIEW_ROOTS="$HOME/FootballArchive/ESPN/data"
python3 server.py
```

## Tests And Validation

- Clipper: `tests/test_media_catalog.py` (stable IDs, dedupe, normalization from
  all three record kinds, schema validation, private-path rejection).
- Lite: `tests/test_media_library.py` (schema/version rejection, duplicate IDs,
  traversal/network path rejection, payload sanitization, preview allow-listing,
  HTTP `Range` serving, hosted unavailability) and `tests/test_exports.js`
  (Library render/empty/unavailable/error states).
- Offline by default: no live broadcast is recorded or fetched. Validation uses
  the synthetic asset `~/FootballArchive/TESTS/capture_test.mp4`.
