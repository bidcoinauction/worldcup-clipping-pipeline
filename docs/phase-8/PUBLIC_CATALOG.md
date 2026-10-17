# Phase 8 Public Catalog

The public Library is a reviewed metadata publication, not a connection to the
Mac worker.

## Publish explicitly

Build the private catalog and opt into a public projection in the same command:

```bash
python scripts/media_catalog.py build \
  --contracts-dir "$JOBS/contracts" \
  --sessions-dir "$JOBS/sessions" \
  --jobs-dir "$JOBS" \
  --out "$JOBS/media.catalog.json" \
  --public-out "/path/to/stadium-signal-lite/public/catalog/media_catalog.v1.json"
```

The `--public-out` projection removes filesystem paths, OORT object URIs,
bucket and region details, and playback sources. It keeps stable asset IDs,
media facts, processing state, rights status, and provenance hashes. The
operator reviews the generated diff and commits it to the Lite repository;
nothing is uploaded automatically.

## Boundary

- Vercel serves the committed projection through `/api/library`.
- The endpoint is read-only metadata and marks playback as unauthorized.
- Local `/api/library` and `/preview` remain the only paths that can resolve
  configured local media roots.
- Recording, rendering, worker control, OORT credentials, and remote object
  access are not exposed to the browser.
- A future authorized playback flow must be designed separately from catalog
  publication, using short-lived authorization rather than catalog fields.
