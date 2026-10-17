# Phase 7.4 - Release Readiness

## Decision

**Ready for pull-request review; not approved for merge or deployment yet.**

The Phase 7 through 7.4 local media workflow is functionally complete across
the separate Clipper and Stadium Signal Lite repositories. No branch has been
merged to `main` and no deployment has been triggered. Review this report and
both repository diffs before approving the rollout sequence below.

## Scope Completed

- One Clipper command orchestrates registration, render, verification, optional
  storage, contract persistence, catalog generation, Lite import, and playback
  verification.
- Reruns reuse checksum-verified artifacts; interrupted jobs resume; catalog
  generation suppresses duplicate stable asset IDs.
- Reuse is bound to the pilot/source pair, trim/render settings, and effective
  storage destination. Multiple sources under one pilot retain separate intake,
  contract, and status records.
- OORT remains optional, explicit, private, and verified. Storage failure does
  not remove the local artifact.
- Lite loads the catalog at startup, renders Library state/counts, and uses the
  existing player for local previews.
- Preview URLs contain opaque asset IDs rather than encoded filesystem paths.
- A dependency-free cross-repository test exercises the actual loopback server
  and HTTP Range response.

## Real Versus Unavailable

| Stage | Assessment | Evidence |
| --- | --- | --- |
| Source registration and rights gate | Real | Managed pilot validation/job record. |
| Source/media probing | Real | Local `ffprobe`. |
| EDL and reference render | Real | Existing EDL service and local `ffmpeg`. |
| Artifact/provenance verification | Real | Existence, duration, source/artifact SHA-256. |
| Catalog build/import | Real | Versioned catalog consumed by Lite. |
| Local playback transport | Real | Loopback HTTP 206 Range response from rendered MP4. |
| OORT upload | Optional; not exercised in Phase 7.4 E2E | Existing Phase 7.1 upload/read-back path remains tested separately; retained remote test object is unchanged. |
| Transcription/moment detection/editorial render | Unavailable in this workflow | Explicitly outside ingestion/orchestration scope; not mocked. |
| Hosted Library/preview | Intentionally unavailable | Local-only routes are not Vercel functions. |

## End-To-End Evidence

The authorized synthetic source `~/FootballArchive/TESTS/capture_test.mp4` was
processed through the operator command with Lite preview enabled:

- Source: 28,357,847 bytes, 1280x720, 30 fps, H.264/AAC.
- Rendered artifact: 750,308 bytes, 2.0 seconds.
- Artifact SHA-256:
  `b583ce0a47008971e18b9724b26f1bcc6397360db9c02ef0eb1b58d50e060c5f`.
- Contract and catalog validation: passed.
- Catalog asset occurred once after deduplication.
- Lite import: available and expected asset found.
- Browser payload: no source path, artifact path, `object_uri`, or `s3://` value.
- Preview reference: opaque `/preview?asset=...`.
- Playback probe: HTTP 206, 1,024 bytes.
- Arbitrary filesystem path: HTTP 403.
- Identical rerun: verified contract/artifact reused; stable asset ID retained.
- Catalog count remained stable at two assets (artifact plus registered source)
  across the identical rerun; the catalog did not ingest its own output.

## Security Audit

### Passed

- The server binds to `127.0.0.1`; Library/preview are absent from hosted API
  functions and hosted mode reports the Library unavailable.
- Preview requires both explicit enablement and at least one allowed root.
- Asset resolution is server-side; paths must be absolute, traversal-free,
  existing files under an allowed root with an approved media extension.
- Unknown asset IDs and direct arbitrary path requests are rejected.
- Catalog validation rejects network artifact URLs, relative/traversal paths,
  duplicate asset IDs, and leaked source paths.
- `/api/library` strips artifact paths, source paths, and storage object URIs;
  invalid-catalog responses do not echo the configured path.
- Upload is opt-in; no credentials are serialized; full-length sources are not
  uploaded automatically; local sources/artifacts are never deleted.
- Subprocesses use argument arrays rather than a shell. Cross-repository
  verification has a bounded timeout.

### Hardening Applied During 7.4

- Replaced percent-encoded artifact paths in preview URLs with opaque asset IDs.
- Removed direct-path preview as a supported input.
- Removed a machine-specific Lite checkout path from the cross-repository test;
  the test now requires `STADIUM_SIGNAL_LITE_ROOT` and otherwise skips.
- Sanitized invalid-catalog browser errors and verification-tool output.
- Selected directory catalogs by modification time rather than filename order.
- Bound resume to the original source checksum and a `READY` job; changed bytes,
  terminal jobs, trim settings, and storage destinations require new work.
- Keyed intake, contract, and workflow-status files by pilot plus source ID.
- Replaced shared atomic-write temp names with unique same-directory temp files.

## Browser Verification

Automated browser-surface checks completed:

- The local server returns the Library navigation and view markup.
- Startup JavaScript loads `/api/library` before the Library tab is opened, so
  the navigation count is populated immediately.
- The dependency-free JavaScript DOM harness renders available, empty,
  unavailable, and failed Library states; opens a Library preview in the
  existing player; sets the opaque preview URL; disables queue actions; and
  clears unrelated-player content.
- The real loopback server returns the sanitized catalog and a playable HTTP
  206 Range response for the same preview URL.

A fully automated Safari visual session was attempted but Safari WebDriver is
disabled on this machine (`Allow remote automation` is off). The installed
Chromium-derived applications did not provide a usable headless session.
Therefore a manual visual check in Safari or Chrome remains a pre-merge review
gate; this is not represented as completed automation.

### Residual Risks

- Local preview grants the local browser read access to explicitly cataloged
  media under explicitly configured roots. Keep roots narrow and do not expose
  the loopback server through a reverse proxy or tunnel.
- Browser codec support varies. HTTP playback transport is verified; operators
  must still visually confirm the Library layout and representative MP4
  playback in a supported browser before merge.
- Catalog/status files contain local runtime paths by design. They must remain
  in ignored local data directories and must not be committed or published.
- OORT availability and credentials are operational dependencies only when
  `--upload` is selected; Phase 7.4 did not create or delete remote objects.

## Test Gates

Required before merge:

```bash
# Clipper
.venv/bin/python -m pytest -q
.venv/bin/python scripts/validate_data.py

# Cross-repository case
STADIUM_SIGNAL_LITE_ROOT=/path/to/stadium-signal-lite \
  .venv/bin/python -m pytest tests/test_media_workflow.py -q

# Lite
python3 -m py_compile server.py media_library.py media_preview.py tools/verify_library.py
node --check public/app.js
node tests/test_exports.js
python3 -m unittest tests.test_clipper_bridge tests.test_media_library tests.test_verify_library
python3 -m unittest discover -s tests -p "test_smoke.py" -v
```

Record exact final counts and commit SHAs in the pull requests after commits are
created. Do not substitute optional live-provider tests for the offline gates.

Latest local results:

- Clipper standalone: 1242 passed, 3 skipped, 2 warnings.
- Clipper with `STADIUM_SIGNAL_LITE_ROOT`: 1243 passed, 2 skipped, 2 warnings.
- Lite media/bridge/verifier: 73 passed.
- Lite smoke: 25 passed.
- JavaScript export/player tests and Python/JavaScript syntax checks: passed.

## Rollout Order

1. Review both pull requests and this report without merging either branch.
2. Merge Stadium Signal Lite first. This installs the opaque asset preview
   endpoint and verification tool required by Clipper's cross-repository check.
3. Confirm Lite CI on `main`. No hosted Library deployment dependency exists;
   Vercel remains unchanged because these routes are local-only.
4. Merge Clipper second. Re-run the full Clipper suite and the cross-repository
   case against Lite `main`.
5. Run one local dry run, then one synthetic full workflow without `--upload`.
6. Enable OORT only in a separate operator action after local acceptance and a
   bucket-scoped health check. Never make upload a default.

## Rollback

- Before Clipper merge: reverting the Lite commit removes the verification tool
  and opaque preview behavior; Phase 7.3 catalog files remain read-only and no
  media is changed.
- After both merges: stop local servers, revert the Clipper Phase 7.4 commit,
  then revert the Lite Phase 7.4 commit. Do not delete source media, rendered
  artifacts, contracts, catalogs, or retained OORT objects during rollback.
- Runtime status/catalog files are projections and may be moved aside after a
  backup if an operator needs a clean rerun; source and artifact files remain
  authoritative.
- Hosted provider discovery is independent of this local workflow and should
  not be rolled back unless a separate regression is demonstrated.
