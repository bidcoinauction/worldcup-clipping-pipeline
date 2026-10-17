"""Phase 7.1 — OORT Storage integration service tests.

All network paths are exercised through an in-memory fake S3 transport, so the
suite is deterministic and offline. A live test is provided under the ``network``
marker and only runs when ``OORT_LIVE_TEST=1`` and real credentials are present.

The SigV4 signer is additionally checked against Amazon's published test vector
so the algorithm is proven, not merely self-consistent.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import os
import urllib.parse

import pytest

from pipeline import integration_service as svc

_AWS_ACCESS = "AKIAIOSFODNN7EXAMPLE"
_AWS_SECRET = "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY"
# Amazon "Example: GET Object" SigV4 reference signature.
_AWS_VECTOR_SIGNATURE = "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


# ── Fake S3 ──────────────────────────────────────────────────────────────────


class FakeOORT:
    """Minimal in-memory S3 that records requests and stores object bytes."""

    def __init__(self, *, fail_status: int | None = None, raise_on_call: bool = False,
                 list_status: int | None = None, create_status: int | None = None,
                 strip_metadata: bool = False) -> None:
        self.objects: dict[str, bytes] = {}
        self.requests: list[dict] = []
        self.fail_status = fail_status
        self.raise_on_call = raise_on_call
        self.list_status = list_status
        self.create_status = create_status
        self.strip_metadata = strip_metadata

    def __call__(self, method: str, url: str, headers: dict, body: bytes, timeout: float):
        self.requests.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        if self.raise_on_call:
            raise ConnectionError("simulated network failure")

        parts = urllib.parse.urlsplit(url)
        segments = [s for s in parts.path.split("/") if s]
        if self.fail_status:
            return self.fail_status, {}, b"<Error><Code>AccessDenied</Code><Message>denied</Message></Error>"

        if method == "GET" and "list-type" in parts.query:
            if self.list_status and self.list_status != 200:
                return self.list_status, {}, "<Error><Code>NoSuchBucket</Code><Message>no bucket</Message></Error>".encode()
            return 200, {}, b"<ListBucketResult/>"
        if method == "GET" and not segments:
            return 200, {}, b"<ListAllMyBucketsResult/>"
        if method == "PUT":
            if len(segments) == 1:
                if self.create_status:
                    return self.create_status, {}, b"<Error><Code>BucketAlreadyOwnedByYou</Code></Error>"
                return 200, {}, b""
            key = "/".join(segments[1:])
            self.objects[key] = body
            return 200, {"etag": '"%s"' % hashlib.md5(body).hexdigest()}, b""
        if method == "HEAD":
            key = "/".join(segments[1:])
            if key not in self.objects:
                return 404, {}, b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>"
            data = self.objects[key]
            headers = {
                "content-length": str(len(data)),
                "etag": '"%s"' % hashlib.md5(data).hexdigest(),
            }
            if not self.strip_metadata:
                headers["x-amz-meta-sha256"] = hashlib.sha256(data).hexdigest()
            return 200, headers, b""
        if method == "GET":
            key = "/".join(segments[1:])
            if key not in self.objects:
                return 404, {}, b"<Error><Code>NoSuchKey</Code><Message>missing</Message></Error>"
            return 200, {}, self.objects[key]
        return 200, {}, b""


def _adapter(fake, *, bucket="test-bucket", region="us-east-1", endpoint=svc.DEFAULT_OORT_ENDPOINT):
    config = svc.OORTConfig(
        access_key_id=_AWS_ACCESS, secret_access_key=_AWS_SECRET,
        endpoint=endpoint, region=region, bucket=bucket,
    )
    return svc.OORTAdapter(config, transport=fake, now=lambda: dt.datetime(2026, 10, 16, 12, 0, 0, tzinfo=dt.timezone.utc))


# ── SigV4 correctness ────────────────────────────────────────────────────────


def test_sigv4_matches_amazon_reference_vector():
    config = svc.OORTConfig(
        access_key_id=_AWS_ACCESS, secret_access_key=_AWS_SECRET,
        endpoint="https://examplebucket.s3.amazonaws.com", region="us-east-1", bucket=None,
    )
    adapter = svc.OORTAdapter(
        config, transport=FakeOORT(),
        now=lambda: dt.datetime(2013, 5, 24, 0, 0, 0, tzinfo=dt.timezone.utc),
    )
    _uri, headers = adapter._signed_headers("GET", None, "test.txt", extra_headers={"Range": "bytes=0-9"})
    auth = headers["Authorization"]
    assert auth.startswith(svc.OORT_SIGNING_ALGORITHM)
    assert "SignedHeaders=host;range;x-amz-content-sha256;x-amz-date" in auth
    assert auth.endswith(f"Signature={_AWS_VECTOR_SIGNATURE}")


def test_signed_request_includes_payload_hash_and_no_secret():
    fake = FakeOORT()
    adapter = _adapter(fake)
    adapter.upload_file(_write_tmp("hello world"), "key.txt")
    put = fake.requests[-1]
    assert put["method"] == "PUT"
    assert put["headers"]["x-amz-content-sha256"] == svc._sha256_hex(b"hello world")
    assert _AWS_SECRET not in put["headers"]["Authorization"]
    assert put["headers"]["Host"] == "s3-standard.oortech.com"


# ── Config resolution ────────────────────────────────────────────────────────


def test_config_from_env_returns_none_without_credentials():
    assert svc.storage_config_from_env({}) is None
    assert svc.storage_config_from_env({svc.ENV_ACCESS_KEY_ID: "x"}) is None
    assert svc.storage_config_from_env({svc.ENV_SECRET_ACCESS_KEY: "y"}) is None


def test_config_from_env_reads_values_with_defaults():
    config = svc.storage_config_from_env({
        svc.ENV_ACCESS_KEY_ID: "AKIA",
        svc.ENV_SECRET_ACCESS_KEY: "s3cr3t-value",
        svc.ENV_BUCKET: "bucket-1",
    })
    assert config is not None
    assert config.endpoint == svc.DEFAULT_OORT_ENDPOINT
    assert config.region == svc.DEFAULT_OORT_REGION
    assert config.bucket == "bucket-1"
    assert config.redacted()["secret_access_key_set"] is True
    assert "s3cr3t-value" not in str(config.redacted())
    assert "KAIA" not in str(config.redacted())


def test_build_storage_adapter_returns_none_when_unconfigured():
    assert svc.build_storage_adapter(env={}) is None
    adapter = svc.build_storage_adapter(env={svc.ENV_ACCESS_KEY_ID: "a", svc.ENV_SECRET_ACCESS_KEY: "b"})
    assert isinstance(adapter, svc.OORTAdapter)


# ── Health check ─────────────────────────────────────────────────────────────


def test_health_check_ok_with_bucket():
    report = _adapter(FakeOORT()).health_check()
    assert report["ok"] is True
    assert report["reason"] == "ok"
    assert report["checks"] == {
        "endpoint_reachable": True, "credentials_valid": True, "bucket_accessible": True,
    }
    assert report["bucket_configured"] is True


def test_health_check_credentials_valid_without_bucket():
    report = _adapter(FakeOORT(list_status=404), bucket=None).health_check()
    assert report["ok"] is True
    assert report["reason"] == "credentials_valid"
    assert report["checks"]["credentials_valid"] is True
    assert report["checks"]["bucket_accessible"] is False
    assert report["bucket_configured"] is False
    assert "probe_bucket" in report


def test_health_check_bucket_unavailable():
    report = _adapter(FakeOORT(list_status=404), bucket="missing-bucket").health_check()
    assert report["ok"] is False
    assert report["reason"] == "bucket_unavailable"
    assert report["checks"]["credentials_valid"] is True
    assert report["checks"]["bucket_accessible"] is False


def test_health_check_auth_failure():
    report = _adapter(FakeOORT(fail_status=403)).health_check()
    assert report["ok"] is False
    assert report["reason"] == "auth_failed"
    assert report["http_status"] == 403
    assert report["error_code"] == "AccessDenied"
    assert report["checks"]["credentials_valid"] is False


def test_health_check_network_error():
    report = _adapter(FakeOORT(raise_on_call=True)).health_check()
    assert report["ok"] is False
    assert report["reason"] == "network_error"
    assert report["checks"]["endpoint_reachable"] is False


# ── Upload + verification ────────────────────────────────────────────────────


def test_upload_and_verify_with_read_back(tmp_path):
    fake = FakeOORT()
    adapter = _adapter(fake)
    source = _write_tmp("the quick brown fox", tmp_path=tmp_path)

    result = adapter.upload_file(source, "stadium-signal/job/reference.mp4")
    assert result.status == svc.STORAGE_STATUS_UPLOADED
    assert result.object_uri == "s3://test-bucket/stadium-signal/job/reference.mp4"
    assert result.sha256 == svc._sha256_hex(b"the quick brown fox")
    assert result.size_bytes == len(b"the quick brown fox")

    verified = adapter.verify_upload(result, read_back=True, scratch_dir=tmp_path / "scratch")
    assert verified.verified is True
    assert verified.read_back_verified is True
    assert verified.checks["head_size_matches"] is True
    assert verified.checks["read_back_sha256_matches"] is True


def test_upload_is_path_style_and_never_public_url():
    fake = FakeOORT()
    adapter = _adapter(fake)
    adapter.upload_file(_write_tmp("x"), "a/b/c.bin")
    assert fake.requests[-1]["url"] == "https://s3-standard.oortech.com/test-bucket/a/b/c.bin"
    assert adapter.object_uri("a/b/c.bin").startswith("s3://")
    assert "http" not in adapter.object_uri("a/b/c.bin")


def test_upload_requires_bucket():
    adapter = _adapter(FakeOORT(), bucket=None)
    with pytest.raises(svc.StorageIntegrationError, match="no OORT bucket"):
        adapter.upload_file(_write_tmp("x"), "k")


def test_create_bucket_ok():
    fake = FakeOORT()
    result = _adapter(fake, bucket=None).create_bucket("brand-new")
    assert result["created"] is True
    assert fake.requests[-1]["url"] == "https://s3-standard.oortech.com/brand-new"


def test_create_bucket_already_owned_is_idempotent():
    result = _adapter(FakeOORT(create_status=409), bucket=None).create_bucket("mine")
    assert result["created"] is False
    assert result["exists"] is True


def test_upload_missing_local_file_raises(tmp_path):
    adapter = _adapter(FakeOORT())
    with pytest.raises(svc.StorageIntegrationError, match="local file not found"):
        adapter.upload_file(tmp_path / "nope.bin", "k")


def test_upload_http_failure_raises_with_code():
    adapter = _adapter(FakeOORT(fail_status=403))
    with pytest.raises(svc.StorageIntegrationError, match="AccessDenied"):
        adapter.upload_file(_write_tmp("x"), "k")


def test_verify_detects_tampered_object(tmp_path):
    fake = FakeOORT()
    adapter = _adapter(fake)
    result = adapter.upload_file(_write_tmp("original"), "k")
    fake.objects["k"] = b"tampered-bytes"
    verified = adapter.verify_upload(result, read_back=True, scratch_dir=tmp_path / "scratch")
    assert verified.verified is False
    assert verified.checks["head_sha256_metadata_matches"] is False
    assert verified.checks["read_back_sha256_matches"] is False


def test_verify_with_read_back_when_provider_drops_metadata(tmp_path):
    # OORT does not reliably round-trip x-amz-meta-* on HeadObject.
    fake = FakeOORT(strip_metadata=True)
    adapter = _adapter(fake)
    result = adapter.upload_file(_write_tmp("payload"), "k")
    verified = adapter.verify_upload(result, read_back=True, scratch_dir=tmp_path / "scratch")
    assert verified.verified is True
    assert verified.read_back_verified is True
    assert "head_sha256_metadata_matches" not in verified.checks
    assert verified.checks["read_back_sha256_matches"] is True


def test_verify_without_read_back_or_metadata_is_unverified(tmp_path):
    fake = FakeOORT(strip_metadata=True)
    adapter = _adapter(fake)
    result = adapter.upload_file(_write_tmp("payload"), "k")
    verified = adapter.verify_upload(result, read_back=False)
    assert verified.verified is False
    assert verified.checks["content_verifiable"] is False


def test_with_bucket_preserves_transport():
    fake = FakeOORT()
    adapter = _adapter(fake, bucket="one")
    other = adapter.with_bucket("two")
    assert other.config.bucket == "two"
    other.upload_file(_write_tmp("z"), "k")
    assert "/two/k" in fake.requests[-1]["url"]


# ── Live integration (opt-in) ────────────────────────────────────────────────


@pytest.mark.network
def test_live_oort_health_check():
    if os.environ.get("OORT_LIVE_TEST") != "1":
        pytest.skip("set OORT_LIVE_TEST=1 with real OORT_* credentials to run the live check")
    adapter = svc.build_storage_adapter()
    if adapter is None:
        pytest.skip("no OORT credentials present")
    report = adapter.health_check()
    assert report["ok"] is True, report


def _write_tmp(text: str, tmp_path=None):
    if tmp_path is None:
        import tempfile

        tmp_path = tempfile.mkdtemp()
    from pathlib import Path

    path = Path(tmp_path) / "payload.bin"
    path.write_bytes(text.encode("utf-8"))
    return path
