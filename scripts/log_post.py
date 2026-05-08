import argparse, csv
from pathlib import Path
from datetime import datetime
from pipeline.utils import ROOT

def main():
    parser = argparse.ArgumentParser(description="Log a posted clip.")
    parser.add_argument("--platform", required=True, choices=["TikTok", "Reels", "Shorts"])
    parser.add_argument("--clip-id", required=True)
    parser.add_argument("--url", required=True)
    parser.add_argument("--angle", required=True)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    out = ROOT / "TRACKING/posted.csv"
    exists = out.exists()
    with out.open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=["clip_id", "platform", "url", "angle", "posted_at", "notes"])
        if not exists:
            writer.writeheader()
        writer.writerow({
            "clip_id": args.clip_id,
            "platform": args.platform,
            "url": args.url,
            "angle": args.angle,
            "posted_at": datetime.now().isoformat(timespec="seconds"),
            "notes": args.notes,
        })
    print(f"Logged post: {args.clip_id} on {args.platform}")

if __name__ == "__main__":
    main()
