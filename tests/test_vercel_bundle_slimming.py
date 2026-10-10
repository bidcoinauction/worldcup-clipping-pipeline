from __future__ import annotations

import subprocess
import sys


def test_hosted_app_starts_without_local_media_dependencies():
    script = r'''
import importlib.abc
import os
import sys
from io import BytesIO

blocked = {
    "av",
    "cv2",
    "ctranslate2",
    "faster_whisper",
    "imageio_ffmpeg",
    "numpy",
    "onnxruntime",
    "pandas",
    "scipy",
    "torch",
}

class BlockHeavy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] in blocked:
            raise ImportError(f"blocked hosted dependency: {fullname}")
        return None

sys.meta_path.insert(0, BlockHeavy())
os.environ["CLIPPER_ENV"] = "demo"
os.environ["CLIPPER_DEMO_RESET"] = "1"

from main import app

def request(path):
    status = []
    headers = []
    body = b"".join(app({
        "REQUEST_METHOD": "GET",
        "PATH_INFO": path,
        "QUERY_STRING": "",
        "wsgi.input": BytesIO(b""),
        "CONTENT_LENGTH": "0",
    }, lambda s, h: (status.append(s), headers.extend(h))))
    return status[0], body

for path in ("/", "/style.css", "/health", "/projects/demo_argentina_croatia_2022_semifinal"):
    status, body = request(path)
    assert status == "200 OK", (path, status, body[:200])
    assert b"ImportError" not in body

assert b"Argentina vs Croatia" in request("/")[1]
assert not any(name in sys.modules for name in blocked)
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=60)

    assert result.returncode == 0, result.stderr or result.stdout


def test_pipeline_package_does_not_eagerly_import_whisper_transcriber():
    script = r'''
import importlib.abc
import sys

class BlockNumpy(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".", 1)[0] == "numpy":
            raise ImportError("numpy should not be needed for package import")
        return None

sys.meta_path.insert(0, BlockNumpy())
import pipeline
assert "pipeline.whisper_transcriber" not in sys.modules
'''
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)

    assert result.returncode == 0, result.stderr or result.stdout


def test_local_whisper_transcriber_remains_importable_when_installed():
    result = subprocess.run(
        [sys.executable, "-c", "from pipeline import whisper_transcriber; print(whisper_transcriber.last_model_load_seconds())"],
        capture_output=True,
        text=True,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr or result.stdout
