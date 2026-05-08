import argparse, csv
from pipeline.utils import ROOT

def main():
    parser = argparse.ArgumentParser(description="Append performance metrics for a clip.")
    parser.add_argument("--clip-id", required=True)
    parser.add_argument("--platform", required=True, choices=["TikTok", "Reels", "Shorts"])
    parser.add_argument("--url", default="")
    parser.add_argument("--angle", default="")
    parser.add_argument("--views", type=int, required=True)
    parser.add_argument("--avg-watch-time", type=float, required=True)
    parser.add_argument("--completion-rate", type=float, required=True)
    parser.add_argument("--shares", type=int, default=0)
    parser.add_argument("--saves", type=int, default=0)
    parser.add_argument("--comments", type=int, default=0)
    parser.add_argument("--profile-visits", type=int, default=0)
    parser.add_argument("--notes", default="")
    args = parser.parse_args()

    out = ROOT / "TRACKING/performance.csv"
    exists = out.exists()
    with out.open("a", newline="", encoding="utf-8") as f:
        fieldnames = ["clip_id", "platform", "url", "angle", "views", "avg_watch_time", "completion_rate", "shares", "saves", "comments", "profile_visits", "notes"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        if not exists:
            writer.writeheader()
        writer.writerow(vars(args).copy() | {
            "clip_id": args.clip_id,
            "avg_watch_time": args.avg_watch_time,
            "completion_rate": args.completion_rate,
            "profile_visits": args.profile_visits,
        })
    print(f"Performance updated for: {args.clip_id}")

if __name__ == "__main__":
    main()
