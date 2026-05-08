from pathlib import Path
from pipeline.paths import PROJECT_DIRS
from pipeline.utils import ROOT, ensure_dirs

def touch_csv(path: Path, header: str):
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(header, encoding="utf-8")

def main():
    ensure_dirs(PROJECT_DIRS)

    touch_csv(
        ROOT / "TRACKING/performance.csv",
        "clip_id,platform,url,angle,views,avg_watch_time,completion_rate,shares,saves,comments,profile_visits,notes\n"
    )
    touch_csv(
        ROOT / "TRACKING/posted.csv",
        "clip_id,platform,url,angle,posted_at,notes\n"
    )
    touch_csv(
        ROOT / "TRACKING/content_calendar.csv",
        "date,slot,clip_id,platform,angle,status,notes\n"
    )

    print("Connected project folders and tracking files created.")

if __name__ == "__main__":
    main()
