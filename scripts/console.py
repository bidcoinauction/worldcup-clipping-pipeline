"""Start the Operator Console web server.

Runs a lightweight core preflight (runtime DB initialize/migrate with safe
backup, writable storage, critical imports) before binding. Startup failures
produce concise operator-safe messages with a pointer to the Doctor.

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


def _preflight() -> str | None:
    """Return an operator-safe error message on failure, else None."""
    from pipeline import runtime_db
    from pipeline.safety import safe_operator_message

    try:
        db_path = runtime_db.initialize()
        if not runtime_db.schema_is_current():
            return "Runtime database schema is out of date. Run: python scripts/doctor.py"
    except Exception as exc:  # noqa: BLE001
        return safe_operator_message(
            str(exc),
            "Runtime database migration failed. Backup created at: <backups dir>. Run: python scripts/doctor.py",
        )
    try:
        from pipeline import console_server  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return safe_operator_message(str(exc), "Operator Console dependencies could not be imported.")
    try:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        probe = db_path.parent / ".clipper_write_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        return f"Runtime directory is not writable: {db_path.parent}"
    return None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stadium Signal Operator Console")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=8420, help="Port (default: 8420)")
    args = parser.parse_args(argv)

    preflight_error = _preflight()
    if preflight_error:
        print("Clipper could not start.")
        print(preflight_error)
        print("Run: python scripts/doctor.py")
        return 1

    from pipeline.console_server import run_server
    try:
        run_server(host=args.host, port=args.port)
    except OSError as exc:
        message = str(exc)
        if "WinError 10048" in message or "address already in use" in message.lower():
            print(f"Port {args.port} is already in use. Choose another port with --port.")
        else:
            print(f"Clipper could not start: {message}")
        return 1
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())