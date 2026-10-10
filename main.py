"""Vercel entrypoint for the existing Clipper console."""

from __future__ import annotations

import os

os.environ.setdefault("CLIPPER_ENV", "demo")

from pipeline.deployment import setup_demo_environment
from pipeline.vercel_wsgi import application


setup_demo_environment()

app = application
