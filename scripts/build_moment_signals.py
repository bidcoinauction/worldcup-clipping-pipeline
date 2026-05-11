import argparse
import json
from pathlib import Path
from pipeline.utils import ROOT, seconds_to_timestamp, timestamp_to_seconds


KEYWORDS = {
    "CHAOS": ["var", "penalty", "handball", "red card", "yellow card", "foul", "referee", "offside", "post", "bar"],
    "EMOTION": ["crowd", "stadium", "atmosphere", "roar", "fans", "noise", "sold out"],
    "AURA": ["messi", "suarez", "busquets", "alba", "star", "goat"],
    "AMERICA": ["world cup", "america", "united states", "mls", "2026"],
}


def read_json(path: Path, fallback):
    if not path.exists():
        return fallback
    return json.loads(path.read_text(encoding="utf-8"))


def transcript_hits(timestamps_path: Path) -> list[dict]:
    rows = read_json(timestamps_path, [])
    hits = []
    for row in rows:
        text = str(row.get("text", "")).lower()
        categories = []
        matched = []
        for category, keywords in KEYWORDS.items():
            for keyword in keywords:
                if keyword in text:
                    categories.append(category)
                    matched.append(keyword)
        if not matched:
            continue
        start = float(row.get("start", timestamp_to_seconds(row.get("start_time", 0))))
        end = float(row.get("end", timestamp_to_seconds(row.get("end_time", start + 10))))
        hits.append({
            "type": "transcript_keyword",
            "start": max(0, start - 4),
            "end": end + 6,
            "center": (start + end) / 2,
            "categories": sorted(set(categories)),
            "matched_keywords": sorted(set(matched)),
            "text": row.get("text", ""),
            "score": min(9, 4 + len(set(matched))),
        })
    return hits


def audio_hits(audio_path: Path) -> list[dict]:
    data = read_json(audio_path, {})
    hits = []
    for spike in data.get("spikes", []):
        hits.append({
            "type": "audio_spike",
            "start": spike["start"],
            "end": spike["end"],
            "center": spike["center"],
            "categories": ["EMOTION", "CHAOS"],
            "matched_keywords": [],
            "text": spike.get("reason", ""),
            "score": min(10, 5 + float(spike.get("z_score", 0))),
            "z_score": spike.get("z_score"),
        })
    return hits


def ocr_hits(scoreboard_path: Path) -> list[dict]:
    data = read_json(scoreboard_path, {})
    previous = ""
    hits = []
    for sample in data.get("samples", []):
        text = sample.get("ocr_text", "")
        if not text or text == previous:
            continue
        timestamp = float(sample.get("timestamp", 0))
        hits.append({
            "type": "scoreboard_ocr_change",
            "start": max(0, timestamp - 8),
            "end": timestamp + 10,
            "center": timestamp,
            "categories": ["CHAOS"],
            "matched_keywords": ["scoreboard_change"],
            "text": text,
            "score": 6,
        })
        previous = text
    return hits


def merge_hits(hits: list[dict], merge_gap_seconds: float) -> list[dict]:
    merged = []
    for hit in sorted(hits, key=lambda row: row["center"]):
        if not merged or hit["start"] - merged[-1]["end"] > merge_gap_seconds:
            merged.append({
                "start": hit["start"],
                "end": hit["end"],
                "sources": [hit],
            })
            continue
        merged[-1]["start"] = min(merged[-1]["start"], hit["start"])
        merged[-1]["end"] = max(merged[-1]["end"], hit["end"])
        merged[-1]["sources"].append(hit)

    moments = []
    for index, group in enumerate(merged, start=1):
        sources = group["sources"]
        categories = sorted({category for source in sources for category in source.get("categories", [])})
        score = sum(float(source.get("score", 0)) for source in sources)
        source_types = sorted({source["type"] for source in sources})
        start = max(0, group["start"])
        end = max(start + 8, group["end"])
        moments.append({
            "moment_id": f"signal_{index:03d}",
            "start": round(start, 2),
            "end": round(end, 2),
            "start_time": seconds_to_timestamp(start),
            "end_time": seconds_to_timestamp(end),
            "score": round(score, 2),
            "categories": categories,
            "source_types": source_types,
            "evidence": [
                {
                    "type": source["type"],
                    "time": seconds_to_timestamp(source["center"]),
                    "text": source.get("text", ""),
                    "score": round(float(source.get("score", 0)), 2),
                }
                for source in sources
            ],
        })

    return sorted(moments, key=lambda row: row["score"], reverse=True)


def main():
    parser = argparse.ArgumentParser(description="Merge transcript, audio, and scoreboard signals into ranked clip moments.")
    parser.add_argument("--timestamps", required=True)
    parser.add_argument("--audio-spikes", required=True)
    parser.add_argument("--scoreboard-samples", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--merge-gap-seconds", type=float, default=8)
    args = parser.parse_args()

    hits = []
    hits.extend(transcript_hits(Path(args.timestamps)))
    hits.extend(audio_hits(Path(args.audio_spikes)))
    if args.scoreboard_samples:
        hits.extend(ocr_hits(Path(args.scoreboard_samples)))

    moments = merge_hits(hits, args.merge_gap_seconds)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({"moments": moments}, indent=2), encoding="utf-8")
    print(f"Moment signals written: {output}")
    print(f"Ranked moments: {len(moments)}")


if __name__ == "__main__":
    main()
