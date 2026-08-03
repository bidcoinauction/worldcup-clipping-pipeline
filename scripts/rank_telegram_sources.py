from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.stadium_signal import ROOT, read_csv
from pipeline.utils import timestamp_to_seconds


def main() -> None:
    parser = argparse.ArgumentParser(description="Rank Telegram source leads by archive usefulness and emotional signal.")
    parser.add_argument("--input", default=ROOT / "data/telegram_sources.csv")
    parser.add_argument("--limit", type=int, default=25)
    args = parser.parse_args()

    rows = read_csv(args.input)
    ranked = sorted(rows, key=source_score, reverse=True)[: args.limit]
    for row in ranked:
        print(f"{source_score(row):05.2f} {row.get('title') or row.get('message_id')} {row.get('url', '')}")


def source_score(row: dict[str, str]) -> float:
    emotion = _float(row.get("emotion_score"))
    views = _float(row.get("views"))
    duration = _duration_score(row.get("duration", ""))
    source_bonus = 2.0 if row.get("has_video", "").lower() in {"1", "true", "yes"} else 0.0
    return emotion * 3.0 + min(views / 10000.0, 5.0) + duration + source_bonus


def _float(value: str | None) -> float:
    try:
        return float(value or 0)
    except ValueError:
        return 0.0


def _duration_score(value: str) -> float:
    if not value:
        return 0.0
    try:
        seconds = timestamp_to_seconds(value)
    except ValueError:
        return 0.0
    if 20 <= seconds <= 180:
        return 2.0
    if seconds > 180:
        return 1.0
    return 0.5


if __name__ == "__main__":
    main()
