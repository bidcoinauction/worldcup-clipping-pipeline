"""WSGI adapter for the framework-free Clipper console."""

from __future__ import annotations

from email.message import Message
from io import BytesIO
from types import MethodType
from typing import Callable, Iterable

from pipeline.console_server import ConsoleHandler


def application(environ: dict, start_response: Callable) -> Iterable[bytes]:
    method = (environ.get("REQUEST_METHOD") or "GET").upper()
    path = environ.get("PATH_INFO") or "/"
    query = environ.get("QUERY_STRING") or ""
    body = environ.get("wsgi.input").read(int(environ.get("CONTENT_LENGTH") or 0)) if environ.get("wsgi.input") else b""
    handler = _build_handler(method, path + (("?" + query) if query else ""), environ, body)
    if method == "POST":
        handler.do_POST()
    else:
        handler.do_GET()
    status = f"{handler._status} {handler._reason}"
    start_response(status, handler._headers)
    return [handler.wfile.getvalue()]


def _build_handler(method: str, path: str, environ: dict, body: bytes):
    handler = object.__new__(ConsoleHandler)
    handler.command = method
    handler.path = path
    handler.request_version = "HTTP/1.1"
    handler.rfile = BytesIO(body)
    handler.wfile = BytesIO()
    handler.headers = _headers_from_environ(environ)
    handler._status = 200
    handler._reason = "OK"
    handler._headers = []

    def send_response(self, code: int, message: str | None = None):
        self._status = code
        self._reason = message or _reason(code)

    def send_header(self, key: str, value: object):
        self._headers.append((key, str(value)))

    def end_headers(self):
        return None

    handler.send_response = MethodType(send_response, handler)
    handler.send_header = MethodType(send_header, handler)
    handler.end_headers = MethodType(end_headers, handler)
    return handler


def _headers_from_environ(environ: dict) -> Message:
    headers = Message()
    for key, value in environ.items():
        if key.startswith("HTTP_"):
            name = key[5:].replace("_", "-").title()
            headers[name] = str(value)
    if environ.get("CONTENT_TYPE"):
        headers["Content-Type"] = str(environ["CONTENT_TYPE"])
    if environ.get("CONTENT_LENGTH"):
        headers["Content-Length"] = str(environ["CONTENT_LENGTH"])
    return headers


def _reason(code: int) -> str:
    return {
        200: "OK",
        206: "Partial Content",
        400: "Bad Request",
        403: "Forbidden",
        404: "Not Found",
        409: "Conflict",
        500: "Internal Server Error",
    }.get(code, "OK")
