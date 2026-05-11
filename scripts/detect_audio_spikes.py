import argparse
import json
import math
import wave
from pathlib import Path
from pipeline.utils import ROOT, seconds_to_timestamp, slugify


def rms_int16(raw: bytes) -> float:
    if not raw:
        return 0.0
    total = 0
    count = len(raw) // 2
    for i in range(0, len(raw) - 1, 2):
        sample = int.from_bytes(raw[i:i + 2], byteorder="little", signed=True)
        total += sample * sample
    return math.sqrt(total / max(1, count))


def read_windows(audio_path: Path, window_seconds: float) -> list[dict]:
    with wave.open(str(audio_path), "rb") as wav:
        channels = wav.getnchannels()
        sample_width = wav.getsampwidth()
        sample_rate = wav.getframerate()
        if sample_width != 2:
            raise SystemExit("Audio spike detection expects 16-bit PCM WAV audio.")

        frames_per_window = max(1, int(sample_rate * window_seconds))
        windows = []
        index = 0
        while True:
            raw = wav.readframes(frames_per_window)
            if not raw:
                break

            if channels > 1:
                mono = bytearray()
                frame_size = sample_width * channels
                for offset in range(0, len(raw), frame_size):
                    mono.extend(raw[offset:offset + sample_width])
                raw = bytes(mono)

            start = index * window_seconds
            windows.append({
                "start": start,
                "end": start + window_seconds,
                "rms": rms_int16(raw),
            })
            index += 1

    return windows


def detect_spikes(windows: list[dict], threshold_z: float, min_gap_seconds: float) -> list[dict]:
    if not windows:
        return []

    values = [w["rms"] for w in windows]
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / max(1, len(values) - 1)
    std = math.sqrt(variance) or 1.0

    candidates = []
    for window in windows:
        z_score = (window["rms"] - mean) / std
        if z_score >= threshold_z:
            candidates.append({**window, "z_score": z_score})

    selected = []
    for candidate in sorted(candidates, key=lambda row: row["z_score"], reverse=True):
        center = (candidate["start"] + candidate["end"]) / 2
        if any(abs(center - existing["center"]) < min_gap_seconds for existing in selected):
            continue
        start = max(0, candidate["start"] - 4)
        end = candidate["end"] + 8
        selected.append({
            "type": "audio_spike",
            "start": start,
            "end": end,
            "center": center,
            "start_time": seconds_to_timestamp(start),
            "end_time": seconds_to_timestamp(end),
            "center_time": seconds_to_timestamp(center),
            "rms": round(candidate["rms"], 2),
            "z_score": round(candidate["z_score"], 2),
            "reason": "Crowd/commentary loudness spike",
        })

    return sorted(selected, key=lambda row: row["center"])


def main():
    parser = argparse.ArgumentParser(description="Detect crowd/commentary audio spikes from extracted WAV audio.")
    parser.add_argument("--audio", required=True)
    parser.add_argument("--league", required=True)
    parser.add_argument("--match-slug", default="")
    parser.add_argument("--window-seconds", type=float, default=1.0)
    parser.add_argument("--threshold-z", type=float, default=1.8)
    parser.add_argument("--min-gap-seconds", type=float, default=12.0)
    parser.add_argument("--output", default="")
    args = parser.parse_args()

    audio_path = Path(args.audio)
    match_slug = args.match_slug or slugify(audio_path.stem.replace("_audio", ""))
    output = Path(args.output) if args.output else ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "audio_spikes.json"

    windows = read_windows(audio_path, args.window_seconds)
    spikes = detect_spikes(windows, args.threshold_z, args.min_gap_seconds)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps({
        "audio": str(audio_path),
        "window_seconds": args.window_seconds,
        "threshold_z": args.threshold_z,
        "spikes": spikes,
    }, indent=2), encoding="utf-8")
    print(f"Audio spikes written: {output}")
    print(f"Detected spikes: {len(spikes)}")


if __name__ == "__main__":
    main()
