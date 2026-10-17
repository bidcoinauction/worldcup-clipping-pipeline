"""Optional integration boundary with a real OORT Storage service.

An *optional* storage layer that connects the local Clipper artifact system to
OORT Storage (a decentralized, S3-compatible object store) without making the
local pipeline depend on it. When credentials or connectivity are absent the
caller gets an honest ``unavailable`` result and the local workflow continues.

Design posture:

* **Stdlib only.** No ``boto3``/``s3fs`` dependency is introduced. Requests are
  signed with AWS Signature Version 4 (``AWS4-HMAC-SHA256``) implemented here
  on top of :mod:`hmac` / :mod:`hashlib` / :mod:`urllib`, matching how OORT's
  own AWS CLI and rclone (Minio path-style) guides configure the endpoint.
* **Offline-testable.** The HTTP call is a swappable ``transport`` callable, so
  every code path is exercised by deterministic mocked tests without a network.
* **Private by default.** No public objects and no presigned URLs are ever
  generated. Object references are ``s3://`` URIs that are meaningless outside
  the authenticated account; secrets are never logged or returned.
* **Fail safe.** Uploads are opt-in; verifying the local artifact never depends
  on a successful upload, and a failed upload never deletes a local file.

Provider facts (verified against ``docs.oortech.com`` at implementation time):

* Standard endpoint ``https://s3-standard.oortech.com`` (also
  ``https://s3-archive.oortech.com``); HTTPS-only on port 443.
* Auth: AWS SigV4 (``AWS4-HMAC-SHA256``), region ``us-east-1``, service ``s3``.
* Path-style addressing (as rclone's Minio provider config uses).
* Rate limit: 100 requests/second.

Public surface:

* :data:`DEFAULT_OORT_ENDPOINT` / :data:`DEFAULT_OORT_REGION`.
* :class:`OORTConfig` / :class:`StorageResult` / :class:`StorageIntegrationError`.
* :func:`storage_config_from_env` — read credentials/config from the environment.
* :func:`build_storage_adapter` — construct an adapter, or ``None`` when unconfigured.
* :class:`OORTAdapter` — real health check, upload, head, download, verify.
* :func:`integration_health_report` — capability-level health for the creator
  platform's optional OORT, Airtable, and Slack boundaries.

The real Phase 7.1 OORT service and the creator-platform capability registry
coexist deliberately. Core runtime, analysis, Moments, Stories, Render, and
Export behavior never depends on either surface.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import hmac
import os
import tempfile
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as _ET
from pathlib import Path
from typing import Any, Callable

from .integration_models import AdapterHealth, IntegrationResult

try:  # pragma: no cover - dotenv is a declared dependency but stay resilient
    from dotenv import load_dotenv

    load_dotenv()
except Exception:  # pragma: no cover
    pass

# ── Provider identity / defaults ─────────────────────────────────────────────

OORT_STORAGE_PROVIDER = "oort"
DEFAULT_OORT_ENDPOINT = "https://s3-standard.oortech.com"
DEFAULT_OORT_REGION = "us-east-1"
OORT_SIGNING_SERVICE = "s3"
OORT_SIGNING_ALGORITHM = "AWS4-HMAC-SHA256"

# Read-only credential probe. OORT's account-level ListBuckets (GET /) was
# observed to hang upstream at implementation time (verified: anonymous GET /
# returns 403 in <1s, but a correctly signed GET / never returns), while a
# bucket-scoped ListObjectsV2 responds immediately. We therefore validate
# credentials with a bucket-scoped probe: 401/403 => bad credentials,
# 404/NoSuchBucket => credentials accepted. The probe bucket need not exist and
# no object is read or written.
CREDENTIAL_PROBE_BUCKET = "stadium-signal-credential-probe"

# Environment variable names (documented in .env.example).
ENV_ACCESS_KEY_ID = "OORT_ACCESS_KEY_ID"
ENV_SECRET_ACCESS_KEY = "OORT_SECRET_ACCESS_KEY"
ENV_ENDPOINT = "OORT_ENDPOINT"
ENV_REGION = "OORT_REGION"
ENV_BUCKET = "OORT_BUCKET"
ENV_STORAGE_CLASS = "OORT_STORAGE_CLASS"

# Storage block status vocabulary (mirrors the contract's honest reporting).
STORAGE_STATUS_UPLOADED = "uploaded"
STORAGE_STATUS_SKIPPED = "skipped"
STORAGE_STATUS_UNAVAILABLE = "unavailable"
STORAGE_STATUS_FAILED = "failed"

_EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()

# A transport is ``(method, url, headers, body, timeout) -> (status, headers, body)``.
Transport = Callable[[str, str, dict, bytes, float], "tuple[int, dict, bytes]"]


class StorageIntegrationError(Exception):
    """Raised for expected OORT storage failures (auth, network, protocol)."""


# ── Configuration ────────────────────────────────────────────────────────────


@dataclasses.dataclass(frozen=True)
class OORTConfig:
    """Resolved OORT Storage connection settings (secret is never serialized)."""

    access_key_id: str
    secret_access_key: str
    endpoint: str = DEFAULT_OORT_ENDPOINT
    region: str = DEFAULT_OORT_REGION
    bucket: str | None = None
    storage_class: str | None = None

    @property
    def host(self) -> str:
        return urllib.parse.urlsplit(self.endpoint).netloc

    def redacted(self) -> dict:
        """A safe-to-log view: presence flags only, never the secret itself."""
        return {
            "endpoint": self.endpoint,
            "region": self.region,
            "bucket": self.bucket,
            "storage_class": self.storage_class,
            "access_key_id_set": bool(self.access_key_id),
            "secret_access_key_set": bool(self.secret_access_key),
        }


@dataclasses.dataclass
class StorageResult:
    """Outcome of an upload/verification, shaped for the integration contract."""

    status: str
    provider: str = OORT_STORAGE_PROVIDER
    endpoint: str | None = None
    region: str | None = None
    bucket: str | None = None
    object_key: str | None = None
    object_uri: str | None = None
    etag: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    storage_class: str | None = None
    uploaded_at: str | None = None
    verified: bool = False
    read_back_verified: bool = False
    checks: dict | None = None
    error: str | None = None

    def to_block(self) -> dict:
        """Render the contract ``storage`` block (stable key set)."""
        return {
            "enabled": self.status in (STORAGE_STATUS_UPLOADED, STORAGE_STATUS_FAILED),
            "provider": self.provider,
            "status": self.status,
            "endpoint": self.endpoint,
            "region": self.region,
            "bucket": self.bucket,
            "object_key": self.object_key,
            "object_uri": self.object_uri,
            "etag": self.etag,
            "sha256": self.sha256,
            "size_bytes": self.size_bytes,
            "storage_class": self.storage_class,
            "uploaded_at": self.uploaded_at,
            "verified": self.verified,
            "read_back_verified": self.read_back_verified,
            "checks": self.checks or {},
            "error": self.error,
        }


def storage_config_from_env(env: dict | None = None) -> OORTConfig | None:
    """Build an :class:`OORTConfig` from the environment, or ``None`` if unset.

    Credentials are required; ``OORT_ENDPOINT``/``OORT_REGION`` fall back to the
    verified OORT defaults. ``OORT_BUCKET`` is optional but required before an
    upload (not for a credential health check).
    """
    source = env if env is not None else os.environ
    access = (source.get(ENV_ACCESS_KEY_ID) or "").strip()
    secret = (source.get(ENV_SECRET_ACCESS_KEY) or "").strip()
    if not access or not secret:
        return None
    return OORTConfig(
        access_key_id=access,
        secret_access_key=secret,
        endpoint=(source.get(ENV_ENDPOINT) or DEFAULT_OORT_ENDPOINT).strip().rstrip("/"),
        region=(source.get(ENV_REGION) or DEFAULT_OORT_REGION).strip() or DEFAULT_OORT_REGION,
        bucket=(source.get(ENV_BUCKET) or "").strip() or None,
        storage_class=(source.get(ENV_STORAGE_CLASS) or "").strip() or None,
    )


def build_storage_adapter(
    *,
    env: dict | None = None,
    transport: Transport | None = None,
    timeout: float = 30.0,
) -> "OORTAdapter | None":
    """Construct an :class:`OORTAdapter` from the environment, or ``None``."""
    config = storage_config_from_env(env)
    if config is None:
        return None
    return OORTAdapter(config, transport=transport, timeout=timeout)


# ── SigV4 primitives ─────────────────────────────────────────────────────────


def _sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _signing_key(secret: str, datestamp: str, region: str, service: str) -> bytes:
    k_date = _hmac(("AWS4" + secret).encode("utf-8"), datestamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def _canonical_uri(bucket: str | None, key: str | None) -> str:
    segments = [s for s in (bucket, key) if s]
    if not segments:
        return "/"
    # Encode each segment, preserving the separating "/" in the object key.
    return "/" + urllib.parse.quote("/".join(segments), safe="/-_.~")


def _canonical_query(query: dict | None) -> str:
    if not query:
        return ""
    parts = [
        f"{urllib.parse.quote(str(k), safe='-_.~')}={urllib.parse.quote(str(v), safe='-_.~')}"
        for k, v in sorted(query.items())
    ]
    return "&".join(parts)


def _canonical_headers(headers: dict) -> tuple[str, str]:
    normalized = {k.lower().strip(): str(v).strip() for k, v in headers.items()}
    signed = ";".join(sorted(normalized))
    block = "".join(f"{name}:{normalized[name]}\n" for name in sorted(normalized))
    return block, signed


# ── Default transport ────────────────────────────────────────────────────────


def _urllib_transport(method: str, url: str, headers: dict, body: bytes, timeout: float) -> tuple[int, dict, bytes]:
    """HTTPS transport used in production. Swapped out in tests."""
    request = urllib.request.Request(url, data=body if body else None, method=method)
    for name, value in headers.items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, {k.lower(): v for k, v in response.headers.items()}, response.read()
    except urllib.error.HTTPError as exc:  # non-2xx still carries a body we want
        return exc.code, {k.lower(): v for k, v in exc.headers.items()}, exc.read()


def _parse_error(body: bytes) -> dict:
    if not body:
        return {"code": None, "message": None}
    try:
        root = _ET.fromstring(body)
        return {"code": root.findtext("Code"), "message": root.findtext("Message")}
    except _ET.ParseError:
        return {"code": None, "message": body[:200].decode("utf-8", "replace")}


# ── Adapter ──────────────────────────────────────────────────────────────────


class OORTAdapter:
    """Minimal, private-by-default S3 client for OORT Storage.

    Only the surface the integration needs is implemented: a credential/bucket
    health check, ``PutObject``, ``HeadObject``, ``GetObject``, and an upload
    verification that can re-read the object to prove byte-for-byte integrity.
    """

    provider = OORT_STORAGE_PROVIDER

    def __init__(
        self,
        config: OORTConfig,
        *,
        transport: Transport | None = None,
        timeout: float = 30.0,
        now: Callable[[], "_dt.datetime"] | None = None,
    ) -> None:
        self.config = config
        self._transport = transport or _urllib_transport
        self._timeout = float(timeout)
        self._now = now or (lambda: _dt.datetime.now(_dt.timezone.utc))

    # -- low level -----------------------------------------------------------

    def _signed_headers(
        self, method: str, bucket: str | None, key: str | None, *,
        query: dict | None = None, body: bytes = b"", extra_headers: dict | None = None,
    ) -> tuple[str, dict]:
        now = self._now()
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        datestamp = now.strftime("%Y%m%d")
        payload_hash = _sha256_hex(body) if body else _EMPTY_SHA256

        headers: dict[str, str] = {
            "Host": self.config.host,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
        }
        if extra_headers:
            headers.update(extra_headers)

        canonical_uri = _canonical_uri(bucket, key)
        canonical_query = _canonical_query(query)
        header_block, signed_headers = _canonical_headers(headers)
        canonical_request = "\n".join(
            [method.upper(), canonical_uri, canonical_query, header_block, signed_headers, payload_hash]
        )

        scope = f"{datestamp}/{self.config.region}/{OORT_SIGNING_SERVICE}/aws4_request"
        string_to_sign = "\n".join(
            [OORT_SIGNING_ALGORITHM, amz_date, scope, _sha256_hex(canonical_request.encode("utf-8"))]
        )
        signature = hmac.new(
            _signing_key(self.config.secret_access_key, datestamp, self.config.region, OORT_SIGNING_SERVICE),
            string_to_sign.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        headers["Authorization"] = (
            f"{OORT_SIGNING_ALGORITHM} Credential={self.config.access_key_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        )
        return canonical_uri, headers

    def _request(
        self, method: str, bucket: str | None, key: str | None, *,
        query: dict | None = None, body: bytes = b"", extra_headers: dict | None = None,
    ) -> tuple[int, dict, bytes]:
        canonical_uri, headers = self._signed_headers(
            method, bucket, key, query=query, body=body, extra_headers=extra_headers,
        )
        query_string = _canonical_query(query)
        url = self.config.endpoint + canonical_uri + (f"?{query_string}" if query_string else "")
        return self._transport(method.upper(), url, headers, body, self._timeout)

    # -- public surface ------------------------------------------------------

    def with_bucket(self, bucket: str) -> "OORTAdapter":
        """Return an equivalent adapter pointed at a different bucket."""
        return OORTAdapter(
            dataclasses.replace(self.config, bucket=bucket),
            transport=self._transport, timeout=self._timeout, now=self._now,
        )

    def object_uri(self, object_key: str) -> str:
        """A private ``s3://`` object reference (never a public URL)."""
        return f"s3://{self.config.bucket}/{object_key.lstrip('/')}"

    def health_check(self, *, probe_bucket: str | None = None) -> dict:
        """Real connectivity + credential check against the OORT endpoint.

        Uses a bucket-scoped ``ListObjectsV2`` (``max-keys=1``) because OORT's
        account-level ``ListBuckets`` was observed to hang; see
        :data:`CREDENTIAL_PROBE_BUCKET`. No object is read or written and no
        secret is echoed back. When a bucket is configured it is probed for
        access; otherwise a read-only probe bucket verifies the credentials.
        """
        report: dict = {
            "ok": False,
            "provider": self.provider,
            "endpoint": self.config.endpoint,
            "region": self.config.region,
            "bucket": self.config.bucket,
            "bucket_configured": bool(self.config.bucket),
            "checks": {},
        }
        bucket = probe_bucket or self.config.bucket or CREDENTIAL_PROBE_BUCKET
        try:
            status, _headers, body = self._request(
                "GET", bucket, None, query={"list-type": "2", "max-keys": "1"},
            )
        except Exception as exc:  # network/DNS/TLS failure
            report["reason"] = "network_error"
            report["error"] = f"{type(exc).__name__}: {exc}"
            report["checks"]["endpoint_reachable"] = False
            return report

        report["checks"]["endpoint_reachable"] = True
        err = _parse_error(body)

        if status == 200:
            report["checks"]["credentials_valid"] = True
            report["checks"]["bucket_accessible"] = True
            report["ok"] = True
            report["reason"] = "ok"
            return report

        if status in (401, 403):
            report["checks"]["credentials_valid"] = False
            report["reason"] = "auth_failed"
            report["http_status"] = status
            report["error"] = err.get("message") or f"HTTP {status}"
            report["error_code"] = err.get("code")
            return report

        if status == 404:
            # Credentials were accepted; the probe/configured bucket is missing.
            report["checks"]["credentials_valid"] = True
            report["checks"]["bucket_accessible"] = False
            report["http_status"] = 404
            report["error_code"] = err.get("code") or "NoSuchBucket"
            if self.config.bucket:
                report["reason"] = "bucket_unavailable"
                report["error"] = (
                    f"bucket {self.config.bucket!r} is not accessible: "
                    f"{err.get('code') or 'NoSuchBucket'}"
                )
            else:
                report["reason"] = "credentials_valid"
                report["ok"] = True
                report["probe_bucket"] = bucket
                report["note"] = "credentials verified via a read-only bucket probe; no OORT_BUCKET configured"
            return report

        report["reason"] = "http_error"
        report["http_status"] = status
        report["error"] = err.get("message") or f"HTTP {status}"
        report["error_code"] = err.get("code")
        return report

    def create_bucket(self, bucket: str) -> dict:
        """Create a private bucket via ``CreateBucket`` (``PUT /<bucket>``).

        Idempotent: a bucket already owned by the caller is reported as existing
        rather than raising. Raises on any other failure.
        """
        status, _headers, body = self._request("PUT", bucket, None)
        if status in (200, 201):
            return {"created": True, "bucket": bucket, "http_status": status}
        if status == 409:
            return {"created": False, "exists": True, "bucket": bucket, "http_status": status}
        err = _parse_error(body)
        raise StorageIntegrationError(
            f"CreateBucket failed for {bucket!r}: HTTP {status} "
            f"{err.get('code') or ''} {err.get('message') or ''}".strip()
        )

    def upload_file(
        self,
        local_path: str | Path,
        object_key: str,
        *,
        storage_class: str | None = None,
        content_type: str = "application/octet-stream",
        metadata: dict | None = None,
    ) -> StorageResult:
        """Upload one local file via ``PutObject``. Raises on failure."""
        if not self.config.bucket:
            raise StorageIntegrationError("no OORT bucket configured (set OORT_BUCKET)")
        source = Path(local_path)
        if not source.is_file():
            raise StorageIntegrationError(f"local file not found for upload: {source}")

        data = source.read_bytes()
        sha256 = _sha256_hex(data)
        sclass = storage_class or self.config.storage_class

        extra_headers = {"Content-Type": content_type}
        if sclass:
            extra_headers["x-amz-storage-class"] = sclass
        meta = dict(metadata or {})
        meta.setdefault("sha256", sha256)
        for name, value in meta.items():
            extra_headers[f"x-amz-meta-{str(name).lower()}"] = str(value)

        status, headers, body = self._request(
            "PUT", self.config.bucket, object_key, body=data, extra_headers=extra_headers,
        )
        if status not in (200, 201):
            err = _parse_error(body)
            raise StorageIntegrationError(
                f"PutObject failed for s3://{self.config.bucket}/{object_key}: "
                f"HTTP {status} {err.get('code') or ''} {err.get('message') or ''}".strip()
            )

        return StorageResult(
            status=STORAGE_STATUS_UPLOADED,
            endpoint=self.config.endpoint,
            region=self.config.region,
            bucket=self.config.bucket,
            object_key=object_key,
            object_uri=self.object_uri(object_key),
            etag=(headers.get("etag") or "").strip('"') or None,
            sha256=sha256,
            size_bytes=len(data),
            storage_class=sclass,
            uploaded_at=self._now().isoformat(),
        )

    def head_object(self, object_key: str) -> dict:
        """``HeadObject`` metadata. Raises :class:`StorageIntegrationError`."""
        status, headers, body = self._request("HEAD", self.config.bucket, object_key)
        if status != 200:
            err = _parse_error(body)
            raise StorageIntegrationError(
                f"HeadObject failed for s3://{self.config.bucket}/{object_key}: "
                f"HTTP {status} {err.get('message') or ''}".strip()
            )
        length = headers.get("content-length")
        return {
            "etag": (headers.get("etag") or "").strip('"') or None,
            "size_bytes": int(length) if length not in (None, "") else None,
            "sha256": headers.get("x-amz-meta-sha256"),
            "storage_class": headers.get("x-amz-storage-class"),
            "last_modified": headers.get("last-modified"),
        }

    def download_object(self, object_key: str, dest_path: str | Path) -> dict:
        """``GetObject`` into *dest_path* (private read-back). Returns hashes."""
        status, _headers, body = self._request("GET", self.config.bucket, object_key)
        if status != 200:
            err = _parse_error(body)
            raise StorageIntegrationError(
                f"GetObject failed for s3://{self.config.bucket}/{object_key}: "
                f"HTTP {status} {err.get('message') or ''}".strip()
            )
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return {"path": str(dest), "sha256": _sha256_hex(body), "size_bytes": len(body)}

    def verify_object(
        self, object_key: str, *,
        expected_sha256: str, expected_size: int | None = None,
        read_back: bool = False, scratch_dir: str | Path | None = None,
    ) -> dict:
        """Verify an uploaded object by metadata, optionally re-reading bytes.

        OORT does not reliably round-trip user metadata (``x-amz-meta-*``) on
        ``HeadObject``, so content integrity is proven by a ``GetObject``
        read-back when requested. Head metadata is checked only when present; a
        metadata-only verification is reported unverified when the provider did
        not return it.
        """
        checks: dict[str, bool] = {}
        head = self.head_object(object_key)
        if expected_size is not None:
            checks["head_size_matches"] = head.get("size_bytes") == expected_size

        head_sha = head.get("sha256")
        metadata_available = head_sha is not None
        if metadata_available:
            checks["head_sha256_metadata_matches"] = head_sha == expected_sha256

        if read_back:
            scratch = Path(scratch_dir) if scratch_dir is not None else None
            with tempfile.TemporaryDirectory() as tmp:
                local = (scratch / Path(object_key).name) if scratch is not None else (Path(tmp) / Path(object_key).name)
                got = self.download_object(object_key, local)
            checks["read_back_sha256_matches"] = got["sha256"] == expected_sha256
            checks["read_back_size_matches"] = (
                got["size_bytes"] == expected_size if expected_size is not None else True
            )

        verified = all(checks.values()) if checks else False
        if not read_back and not metadata_available:
            checks["content_verifiable"] = False
            verified = False

        return {"verified": verified, "checks": checks, "head": head}

    def verify_upload(
        self, result: StorageResult, *,
        expected_sha256: str | None = None, expected_size: int | None = None,
        read_back: bool = False, scratch_dir: str | Path | None = None,
    ) -> StorageResult:
        """Verify a completed :class:`StorageResult` in place and return it."""
        digest = expected_sha256 or result.sha256
        verification = self.verify_object(
            result.object_key, expected_sha256=digest,
            expected_size=expected_size if expected_size is not None else result.size_bytes,
            read_back=read_back, scratch_dir=scratch_dir,
        )
        result.checks = verification["checks"]
        result.verified = verification["verified"]
        result.read_back_verified = bool(verification["checks"].get("read_back_sha256_matches"))
        result.etag = result.etag or verification["head"].get("etag")
        return result
class IntegrationAdapter:
    """Minimal adapter interface. Subclasses declare capabilities."""

    adapter_id: str = ""
    display_name: str = ""
    capabilities: tuple[str, ...] = ()
    _config_keys: tuple[str, ...] = ()

    def is_configured(self) -> bool:
        return all(os.environ.get(key) for key in self._config_keys)

    def _provider_available(self) -> bool:
        return False

    def health_check(self) -> AdapterHealth:
        configured = self.is_configured()
        if not configured:
            return AdapterHealth(self.adapter_id, configured=False, available=False,
                                 status="NOT_CONFIGURED", message="adapter is not configured",
                                 capabilities=self.capabilities)
        if not self._provider_available():
            return AdapterHealth(self.adapter_id, configured=True, available=False,
                                 status="UNAVAILABLE", message="provider is not available",
                                 capabilities=self.capabilities)
        return AdapterHealth(self.adapter_id, configured=True, available=True,
                             status="READY", capabilities=self.capabilities)

    def upload_artifact(self, path: str, **kwargs: Any) -> IntegrationResult:
        return self._unavailable("upload_artifact")

    def publish_record(self, payload: dict[str, Any], **kwargs: Any) -> IntegrationResult:
        return self._unavailable("publish_record")

    def send_notification(self, message: str, **kwargs: Any) -> IntegrationResult:
        return self._unavailable("send_notification")

    def _unavailable(self, operation: str) -> IntegrationResult:
        return IntegrationResult(self.adapter_id, operation, ok=False, status="NOT_AVAILABLE",
                                 message="adapter is not configured")


class OORTBoundaryAdapter(IntegrationAdapter):
    adapter_id = "oort"
    display_name = "OORT"
    capabilities = ("ARTIFACT_STORAGE",)
    _config_keys = ("OORT_API_KEY", "OORT_BUCKET")


class AirtableAdapter(IntegrationAdapter):
    adapter_id = "airtable"
    display_name = "Airtable"
    capabilities = ("RECORD_SYNC",)
    _config_keys = ("AIRTABLE_API_TOKEN", "AIRTABLE_BASE_ID")


class SlackAdapter(IntegrationAdapter):
    adapter_id = "slack"
    display_name = "Slack"
    capabilities = ("NOTIFICATION", "REVIEW_CARD")
    _config_keys = ("SLACK_WEBHOOK_URL",)


_ADAPTERS: dict[str, IntegrationAdapter] = {
    adapter.adapter_id: adapter for adapter in (OORTBoundaryAdapter(), AirtableAdapter(), SlackAdapter())
}


def registered_integration_adapters() -> list[IntegrationAdapter]:
    return list(_ADAPTERS.values())


def get_integration_adapter(adapter_id: str) -> IntegrationAdapter:
    adapter = _ADAPTERS.get(adapter_id)
    if adapter is None:
        raise ValueError(f"unknown integration adapter: {adapter_id}")
    return adapter


def integration_health_report() -> list[dict[str, Any]]:
    return [adapter.health_check().to_dict() for adapter in _ADAPTERS.values()]
