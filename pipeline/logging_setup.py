"""Minimal application logging setup.

Local rotating logs only. Operator-facing errors remain concise; technical logs
may contain stack traces. Secrets are sanitized at the source.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

from .utils import ROOT

_DEFAULT_LOG_DIR = ROOT / "LOGS"


def configure_logging(level: int = logging.INFO, log_dir: str | Path | None = None) -> None:
    """Configure a root logger with timestamp/severity/component/message format.

    Idempotent. If the LOGS directory is not writable, falls back to stderr-only.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    root.setLevel(level)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")

    stream = logging.StreamHandler()
    stream.setFormatter(fmt)
    root.addHandler(stream)

    try:
        directory = Path(log_dir) if log_dir is not None else _DEFAULT_LOG_DIR
        directory.mkdir(parents=True, exist_ok=True)
        rotating = logging.handlers.RotatingFileHandler(
            directory / "clipper.log", maxBytes=1_000_000, backupCount=3, encoding="utf-8"
        )
        rotating.setFormatter(fmt)
        root.addHandler(rotating)
    except OSError:
        pass