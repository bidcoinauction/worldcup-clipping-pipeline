"""First-run initialization for the local Clipper/Stadium Signal application.

Idempotent. Creates required directories, initializes the runtime database
(including safe additive migrations with pre-upgrade backups), verifies
writable storage, and validates the required configuration shape.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.paths import PROJECT_DIRS
from pipeline.utils import ROOT, ensure_dirs
from pipeline import runtime_db


def _writeable(root: Path) -> bool:
    try:
        probe = root / ".clipper_init_probe"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def _touch_csv(path: Path, header: str) -> None:
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")


def main() -> int:
    ensure_dirs(PROJECT_DIRS)
    runtime_dir = runtime_db.default_runtime_db_path().parent
    backups_dir = runtime_db.default_backups_dir()
    for path in (runtime_dir, backups_dir, ROOT / "LOGS", ROOT / "TRACKING"):
        path.mkdir(parents=True, exist_ok=True)

    _touch_csv(ROOT / "TRACKING/performance.csv",
               "clip_id,platform,url,angle,views,avg_watch_time,completion_rate,shares,saves,comments,profile_visits,notes\n")
    _touch_csv(ROOT / "TRACKING/posted.csv",
               "clip_id,platform,url,angle,posted_at,notes\n")
    _touch_csv(ROOT / "TRACKING/content_calendar.csv",
               "date,slot,clip_id,platform,angle,status,notes\n")

    db_path = runtime_db.initialize()
    version = runtime_db.current_schema_version()

    config_path = ROOT / "config" / "pipeline_config.json"
    if not config_path.exists():
        print("WARNING: config/pipeline_config.json is missing. Create it or restore from the repository.")
    else:
        try:
            from pipeline.config import load_config
            load_config()
        except Exception as exc:  # noqa: BLE001
            print(f"WARNING: configuration could not be loaded: {exc}")

    if not _writeable(runtime_dir):
        print(f"ERROR: runtime directory is not writable: {runtime_dir}")
        return 1

    print(f"Runtime database ready (schema v{version}): {db_path}")
    print("Required project folders created.")
    print("Next steps:")
    print("  python scripts/doctor.py")
    print("  python scripts/console.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())