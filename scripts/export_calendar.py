import argparse
import csv
import sys
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.utils import ROOT

try:
    from icalendar import Calendar, Event
    HAS_ICAL = True
except ImportError:
    HAS_ICAL = False

UTC = ZoneInfo("UTC")


def _build_rows(reader, duration_hours):
    rows = list(reader)
    if not rows:
        print("Error: CSV is empty")
        raise SystemExit(1)

    required = {"match_no", "date", "time", "timezone", "home", "away", "venue", "notes", "stream_url", "pipeline_status"}
    missing = required - set(rows[0].keys())
    if missing:
        print(f"Error: missing CSV columns: {', '.join(sorted(missing))}")
        raise SystemExit(1)

    out = []
    for row in rows:
        date_str = row["date"]
        time_str = row["time"]
        tz_id = row["timezone"]
        home = row["home"].strip() or "TBD"
        away = row["away"].strip() or "TBD"
        venue = row["venue"].strip() or ""
        notes = row["notes"].strip() or ""
        stream_url = (row.get("stream_url") or "").strip()
        pipeline_status = (row.get("pipeline_status") or "").strip()

        dt_local = datetime.strptime(f"{date_str} {time_str}", "%Y-%m-%d %H:%M")
        dt_local = dt_local.replace(tzinfo=ZoneInfo(tz_id))
        dt_start_utc = dt_local.astimezone(UTC)
        dt_end_utc = dt_start_utc + timedelta(hours=duration_hours)

        desc_parts = []
        if notes:
            desc_parts.append(notes)
        desc_parts.append(f"Local: {date_str} {time_str} {tz_id}")
        if stream_url:
            desc_parts.append(f"Stream: {stream_url}")
        if pipeline_status:
            desc_parts.append(f"Pipeline: {pipeline_status}")

        out.append({
            "home": home,
            "away": away,
            "venue": venue,
            "desc": " / ".join(desc_parts),
            "dt_start_utc": dt_start_utc,
            "dt_end_utc": dt_end_utc,
            "stream_url": stream_url,
            "pipeline_status": pipeline_status,
        })
    return out


def _write_csv(out_path, rows, args):
    GCSV = ["Subject", "Start Date", "Start Time", "End Date", "End Time",
            "All Day Event", "Description", "Location", "Private"]
    with out_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=GCSV)
        w.writeheader()
        for r in rows:
            s = r["dt_start_utc"]
            e = r["dt_end_utc"]
            w.writerow({
                "Subject": f"{r['home']} vs {r['away']}",
                "Start Date": s.strftime("%m/%d/%Y"),
                "Start Time": s.strftime("%I:%M %p").lstrip("0"),
                "End Date": e.strftime("%m/%d/%Y"),
                "End Time": e.strftime("%I:%M %p").lstrip("0"),
                "All Day Event": "False",
                "Description": r["desc"],
                "Location": r["venue"],
                "Private": "False",
            })
    print(f"Calendar CSV written: {out_path} ({len(rows)} events)")


def _write_ics(out_path, rows, args):
    if not HAS_ICAL:
        print("Error: icalendar not installed. Run: pip install icalendar")
        raise SystemExit(1)

    cal = Calendar()
    cal.add("prodid", "-//Stadium Signal//World Cup 2026//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("x-wr-calname", "World Cup 2026")

    now = datetime.now(UTC)
    for r in rows:
        e = Event()
        e.add("uid", f"wc2026-match-{id(r)}@stadiumsignal")
        e.add("dtstamp", now)
        e.add("dtstart", r["dt_start_utc"])
        e.add("dtend", r["dt_end_utc"])
        e.add("summary", f"{r['home']} vs {r['away']}")
        e.add("location", r["venue"])
        e.add("description", r["desc"])
        if r["stream_url"]:
            e.add("X-STREAM-URL", r["stream_url"])
        if r["pipeline_status"]:
            e.add("X-PIPELINE-STATUS", r["pipeline_status"])
        cal.add_component(e)

    with out_path.open("wb") as f:
        f.write(cal.to_ical())
    print(f"Calendar ICS written: {out_path} ({len(rows)} events)")


def main():
    parser = argparse.ArgumentParser(
        description="Convert World Cup 2026 schedule to calendar file."
    )
    parser.add_argument("--input", required=True, help="Path to CSV schedule")
    parser.add_argument("--output", default=None, help="Output path")
    parser.add_argument("--format", choices=["csv", "ics"], default="csv",
                        help="Output format (default: csv for Google Calendar)")
    parser.add_argument("--duration", type=float, default=3.5,
                        help="Event duration in hours (default: 3.5)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: input file not found: {input_path}")
        raise SystemExit(1)

    ext = args.format
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = ROOT / "outputs" / f"worldcup_2026.{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with input_path.open("r", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = _build_rows(reader, args.duration)

    if args.format == "csv":
        _write_csv(out_path, rows, args)
    else:
        _write_ics(out_path, rows, args)


if __name__ == "__main__":
    main()
