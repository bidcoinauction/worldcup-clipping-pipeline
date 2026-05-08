import csv
from pathlib import Path
from pipeline.utils import ROOT

def num(v):
    try:
        return float(v or 0)
    except ValueError:
        return 0.0

def main():
    perf = ROOT / "TRACKING/performance.csv"
    if not perf.exists():
        raise SystemExit("No performance.csv found.")

    with perf.open("r", encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    if not rows:
        raise SystemExit("Performance file is empty.")

    for row in rows:
        row["score"] = (
            num(row.get("completion_rate")) * 2.0
            + num(row.get("shares")) * 4.0
            + num(row.get("saves")) * 3.0
            + num(row.get("comments")) * 2.5
            + num(row.get("profile_visits")) * 1.5
            + (num(row.get("views")) / 1000)
        )

    rows.sort(key=lambda r: r["score"], reverse=True)

    out = ROOT / "TOP_PERFORMERS/top_performers.csv"
    out.parent.mkdir(exist_ok=True)

    fieldnames = list(rows[0].keys())
    with out.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    print(f"Top performers written: {out}")
    for row in rows[:10]:
        print(row["clip_id"], row["platform"], row.get("views"), row.get("completion_rate"), row["score"])

if __name__ == "__main__":
    main()
