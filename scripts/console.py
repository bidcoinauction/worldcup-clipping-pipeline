"""Start the Operator Console web server.

Usage:
    python scripts/console.py
    python scripts/console.py --port 8420
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stadium Signal Operator Console")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    args = parser.parse_args(argv)

    try:
        from pipeline.console_server import run_server
        run_server(host=args.host, port=args.port)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
