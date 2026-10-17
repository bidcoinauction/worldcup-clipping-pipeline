from __future__ import annotations

from io import BytesIO


def _request(app, method: str, path: str, body: bytes = b""):
    status: list[str] = []
    headers: list[tuple[str, str]] = []
    payload = b"".join(app({
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.input": BytesIO(body),
        "CONTENT_LENGTH": str(len(body)),
    }, lambda s, h: (status.append(s), headers.extend(h))))
    return status[0], headers, payload


def test_vercel_demo_health_and_seed(monkeypatch):
    monkeypatch.setenv("CLIPPER_ENV", "demo")
    monkeypatch.setenv("CLIPPER_DEMO_RESET", "1")

    from api.index import app

    status, _headers, body = _request(app, "GET", "/health")

    assert status == "200 OK"
    assert b'"environment": "demo"' in body

    status, _headers, body = _request(app, "GET", "/")

    assert status == "200 OK"
    assert b"Hosted Demo" in body
    assert b"Argentina vs Croatia" in body


def test_vercel_demo_blocks_processing_actions(monkeypatch):
    monkeypatch.setenv("CLIPPER_ENV", "demo")

    from api.index import app

    status, _headers, body = _request(app, "POST", "/api/projects/demo_argentina_croatia_2022_semifinal/actions/render_rough_cut")

    assert status == "403 Forbidden"
    assert b"HOSTED_DEMO_READ_ONLY" in body
    assert b"C:\\FootballArchive" not in body
