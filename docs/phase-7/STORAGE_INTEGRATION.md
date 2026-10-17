# Phase 7.1 — Optional OORT Storage Integration

## Purpose

Connect the verified local-ingest artifact system to **OORT Storage**, a
decentralized, S3-compatible object store, **without** making the local pipeline
depend on it. Uploads are opt-in; when OORT is unconfigured or unreachable the
local run continues and reports `storage.status = "unavailable"`.

It builds directly on Phase 7 ([`LOCAL_INGESTION.md`](LOCAL_INGESTION.md)): the
same one-source → one-project → one-verified-artifact loop, now with an optional
remote copy and an honest storage record.

## Provider Facts (verified at implementation time)

Sources: `docs.oortech.com` — *Use S3-Compatible API*, *Access Keys*,
*Use AWS CLI*, rclone remote configuration.

| Property | Value |
| --- | --- |
| Endpoints | `https://s3-standard.oortech.com` (default), `https://s3-archive.oortech.com`, `https://s3-storj.oortech.com` |
| Protocol | HTTPS-only, port 443 |
| Auth | AWS Signature V4 (`AWS4-HMAC-SHA256`); v2 also accepted |
| Region | `us-east-1` |
| Addressing | Path-style (matches OORT's rclone "Minio" provider guidance) |
| Rate limit | 100 requests/second |
| Supported ops | `ListBuckets`, `ListObjectsV2`, `GetObject`, `HeadObject`, `PutObject`, `CopyObject`, `DeleteObject(s)`, multipart ops, `GetSignedUrl` |
| Credentials | Access key id + secret, created in the OORT Console (Access Keys, max 10) |
| Bucket names | 3–63 chars, lowercase letters/numbers/dashes, globally unique |

### Observed quirks

1. **Account-level `ListBuckets` hangs.** At implementation time a correctly
   signed `GET /` (ListBuckets) **never returned** against
   `s3-standard.oortech.com`, while anonymous `GET /` returned `403` in <1s and a
   bucket-scoped `ListObjectsV2` returned immediately. Status mapping was
   confirmed live: bogus credentials → `401`, valid credentials + missing bucket
   → `404 NoSuchBucket`. The health check therefore validates credentials with a
   **bucket-scoped `ListObjectsV2` (`max-keys=1`)** instead of `ListBuckets`; a
   `404` means *credentials accepted* (no object is read or written).
2. **`HeadObject` does not round-trip user metadata.** Metadata set on upload
   (`x-amz-meta-*`, including our `sha256`) was **not** returned by `HeadObject`
   on a live object, so content integrity is proven by a `GetObject` read-back
   rather than by metadata. Metadata is still checked when the provider returns
   it, and a metadata-only verification is reported unverified when it does not.

## Security Posture

- **Credentials come only from the environment** (`OORT_ACCESS_KEY_ID`,
  `OORT_SECRET_ACCESS_KEY`, …). Nothing is logged or serialized: `OORTConfig`
  exposes presence flags only, never the secret.
- **Private by default.** No public objects and **no presigned/public URLs** are
  ever generated. Object references are private `s3://` URIs.
- **Opt-in uploads.** An upload happens only when explicitly requested
  (`--upload` / `upload=True`).
- **Local artifacts are never deleted or modified** by the storage layer. A
  failed upload leaves the local artifact untouched.
- **Never auto-upload full matches or third-party media.** Only the locally
  owned, rights-cleared, verified artifact is uploaded, and only on request.

## Environment Variables

Documented with safe placeholders in `.env.example`. Real values belong in a
local `.env` (gitignored) or the process environment.

| Variable | Required | Default | Notes |
| --- | --- | --- | --- |
| `OORT_ACCESS_KEY_ID` | for upload/health | — | Console → Access Keys |
| `OORT_SECRET_ACCESS_KEY` | for upload/health | — | Console → Access Keys |
| `OORT_ENDPOINT` | no | `https://s3-standard.oortech.com` | standard/archive/storj |
| `OORT_REGION` | no | `us-east-1` | SigV4 region |
| `OORT_BUCKET` | for upload | — | target bucket |
| `OORT_STORAGE_CLASS` | no | provider default | optional storage class header |

## Module And CLI

- `pipeline/integration_service.py` — `OORTAdapter` (stdlib SigV4 S3 client),
  `OORTConfig`, `StorageResult`, `storage_config_from_env`,
  `build_storage_adapter`.
- `pipeline/local_ingest.py` — `upload_artifact_to_storage` and the additive
  `storage` contract block.

```bash
# health check only — no writes
python3 scripts/local_ingest.py storage-health

# opt-in upload + read-back verification
python3 scripts/local_ingest.py run \
    --source ~/FootballArchive/TESTS/capture_test.mp4 \
    --pilot-id ss_lite_local_001 --upload \
    --out data/pilot/contracts/ss_lite_local_001.contract.json

# fail the run if OORT is unconfigured or the upload fails
python3 scripts/local_ingest.py run --source ... --pilot-id ... --upload --require-storage
```

By default the upload is verified by `HeadObject` metadata **and** a `GetObject`
read-back (SHA-256 compared). Use `--no-read-back` for metadata-only
verification.

## Contract Additions (backward compatible)

`contract_version` stays `1`. Two additive, optional fields are added:

- `storage` — `enabled`, `provider`, `status`
  (`skipped`/`unavailable`/`uploaded`/`failed`), `endpoint`, `region`, `bucket`,
  `object_key`, `object_uri` (private `s3://`), `etag`, `sha256`, `size_bytes`,
  `storage_class`, `uploaded_at`, `verified`, `read_back_verified`, `checks`,
  `error`.
- `provenance.storage_object_uri` and `provenance.storage_object_sha256`.

Older contracts remain valid, and consumers that ignore `storage` (including
Stadium Signal Lite's `clipper_bridge`) are unaffected.

## Verification Status

- **Live connection: verified.** `storage-health` returned `ok: true`,
  `credentials_valid: true`, `endpoint_reachable: true` against
  `https://s3-standard.oortech.com`.
- **Live upload: verified.** A dedicated private test bucket
  `stadium-signal-test-4c7eed7a` was created, a small synthetic text file was
  uploaded and read-back-verified, then the Phase 7 reference render of the
  operator-owned synthetic asset was ingested and uploaded end-to-end:

  ```text
  run: source -> project -> render -> verify -> upload -> contract
  artifact: reference.mp4  (750,308 bytes, 2.0s)
  artifact sha256: b583ce0a47008971e18b9724b26f1bcc6397360db9c02ef0eb1b58d50e060c5f
  object: s3://stadium-signal-test-4c7eed7a/stadium-signal/local-ingest/ss_lite_local_oort_001_capture_test/reference.mp4
  storage.status: uploaded | verified: true | read_back_verified: true
  ```

  The local artifact was preserved and the contract passed
  `local_ingest.py verify`.
- **SigV4 correctness: proven** against Amazon's published "GET Object"
  reference vector (signature
  `f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41`), and all
  network paths are covered by an in-memory mocked S3 transport.

## Known Limitations

- `ListBuckets` is not used (hangs upstream, see above); health relies on a
  bucket-scoped probe.
- `HeadObject` metadata is not trusted as the sole integrity proof (not
  round-tripped); a `GetObject` read-back is the default.
- Single-request `PutObject` only (no multipart); adequate for the small
  reference render, and the verified artifact for this workflow.
- No presigned/public URL support by design.

## Tests

```bash
.venv/bin/python -m pytest tests/test_integration_service.py tests/test_local_ingest.py -q
```

Covers: the SigV4 reference vector, config resolution, health-check
ok/auth-failure/bucket-unavailable/network-error paths, path-style upload URL,
payload hashing, private `s3://` references, upload failure handling, read-back
tamper detection, graceful degradation, `--require-storage` behavior, and local
artifact preservation. A live test is available under the `network` marker and
runs only when `OORT_LIVE_TEST=1` and credentials are present.
