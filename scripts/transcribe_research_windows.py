import argparse
import json
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from pipeline.api import make_openai_client
from pipeline.config import get_leagues, get_model as _get_model, get_provider
from pipeline.condense_transcript import _merge_windows
from pipeline.utils import ROOT, get_video_duration, slugify

load_dotenv()


def compute_windows(
    events: list[dict[str, Any]],
    padding: float = 30.0,
    duration: float = 0.0,
) -> list[tuple[float, float]]:
    raw: list[tuple[float, float]] = []
    for ev in events:
        ts = ev.get("video_time_seconds")
        if ts is None:
            continue
        t = float(ts)
        start = max(0.0, t - padding)
        end = t + padding
        if duration > 0:
            end = min(duration, end)
        raw.append((start, end))
    if not raw:
        return []
    return _merge_windows(raw)


def build_event_context(events: list[dict[str, Any]], win_start: float, win_end: float) -> str:
    parts: list[str] = []
    for ev in events:
        ts = ev.get("video_time_seconds")
        if ts is None:
            continue
        if win_start <= float(ts) <= win_end:
            desc = ev.get("description", "")
            ev_type = ev.get("type", "")
            player = ev.get("player", "")
            suffix = f" ({player})" if player else ""
            parts.append(f"[{ev_type.upper()}] {desc}{suffix}")
    return "\n".join(parts)


def extract_audio_window(video_path: Path, out_audio: Path, start: float, end: float) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    duration = end - start
    cmd = [
        "ffmpeg", "-y",
        "-ss", str(start),
        "-i", str(video_path),
        "-t", str(duration),
        "-vn",
        "-acodec", "aac",
        "-b:a", "192k",
        str(out_audio),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_audio


def transcribe_window_openai(audio_path: Path, model: str, context: str = "") -> tuple[str, list[dict]]:
    client = make_openai_client()
    with audio_path.open("rb") as f:
        kwargs: dict[str, Any] = {
            "model": model,
            "file": f,
            "response_format": "verbose_json",
        }
        if context:
            kwargs["prompt"] = context
        result = client.audio.transcriptions.create(**kwargs)
    transcript = str(result.text)
    segments = []
    if hasattr(result, "segments") and result.segments:
        for seg in result.segments:
            segments.append({
                "start": seg.start,
                "end": seg.end,
                "text": seg.text,
            })
    return transcript, segments


def transcribe_window_whisper(audio_path: Path, model_size: str, context: str = "") -> tuple[str, list[dict]]:
    from pipeline.whisper_transcriber import transcribe
    return transcribe(audio_path, model_size, initial_prompt=context)


def offset_segments(segments: list[dict], offset: float) -> list[dict]:
    return [
        {"start": s["start"] + offset, "end": s["end"] + offset, "text": s["text"]}
        for s in segments
    ]


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Transcribe research-anchored windows instead of full match. "
                    "Reduces a 2h30m match to ~10-30 minutes of event-centered transcription."
    )
    parser.add_argument("--research", required=True,
                        help="Path to match_research.json with video_time_seconds")
    parser.add_argument("--source-video", required=True,
                        help="Path to source video file")
    parser.add_argument("--league", required=True, choices=get_leagues())
    parser.add_argument("--padding", type=int, default=30,
                        help="Seconds each side of anchor (default: 30)")
    parser.add_argument("--provider", default=get_provider("transcription"),
                        choices=["openai", "faster-whisper"],
                        help="Transcription provider")
    parser.add_argument("--model", default=os.getenv("DEFAULT_TRANSCRIBE_MODEL") or _get_model("transcription"),
                        help="Transcription model")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print planned windows without executing")
    args = parser.parse_args()

    research_path = Path(args.research)
    if not research_path.exists():
        sys.exit(f"Research file not found: {research_path}")

    video_path = Path(args.source_video)
    if not video_path.exists():
        sys.exit(f"Source video not found: {video_path}")

    data = json.loads(research_path.read_text(encoding="utf-8"))
    events = data.get("events", [])
    if not events:
        sys.exit("No events in research file.")

    duration = get_video_duration(video_path)
    windows = compute_windows(events, padding=float(args.padding), duration=duration)

    if not windows:
        sys.exit(
            "No events with video_time_seconds found. "
            "Run map_research_timestamps.py first."
        )

    total_window_seconds = sum(e - s for s, e in windows)
    print(f"Video duration: {duration:.0f}s ({duration/60:.1f} min)")
    print(f"Research windows: {len(windows)} window(s), "
          f"{total_window_seconds:.0f}s ({total_window_seconds/60:.1f} min) total")
    pct = total_window_seconds / duration * 100
    print(f"Reduction: {pct:.1f}% of original")

    if args.dry_run:
        for i, (s, e) in enumerate(windows, 1):
            print(f"  Window {i}: {s:.1f}s - {e:.1f}s ({e - s:.1f}s)")
            context = build_event_context(events, s, e)
            if context:
                for line in context.split("\n"):
                    print(f"    {line}")
        print(f"[dry-run] Would extract and transcribe {len(windows)} window(s)")
        return

    match_slug = slugify(video_path.stem)
    out_dir = ROOT / "TRANSCRIPTS" / args.league / match_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    all_segments: list[dict] = []
    all_transcript_parts: list[str] = []

    for i, (win_start, win_end) in enumerate(windows):
        context = build_event_context(events, win_start, win_end)
        print(f"Window {i + 1}/{len(windows)}: {win_start:.1f}s - {win_end:.1f}s "
              f"({win_end - win_start:.1f}s)")

        audio_path = out_dir / f"window_{i + 1:03d}.m4a"
        extract_audio_window(video_path, audio_path, win_start, win_end)

        if args.provider == "openai":
            _, segments = transcribe_window_openai(audio_path, args.model, context)
        else:
            _, segments = transcribe_window_whisper(audio_path, args.model, context)

        offset_segs = offset_segments(segments, win_start)
        all_segments.extend(offset_segs)

        for seg in segments:
            all_transcript_parts.append(seg.get("text", ""))
        print(f"  -> {len(segments)} segment(s)")

        audio_path.unlink(missing_ok=True)

    all_segments.sort(key=lambda s: s["start"])

    transcript_txt = out_dir / "transcript.txt"
    timestamps_json = out_dir / "timestamps.json"
    meta_json = out_dir / "metadata.json"

    transcript_txt.write_text(" ".join(all_transcript_parts).strip(), encoding="utf-8")
    timestamps_json.write_text(json.dumps(all_segments, indent=2), encoding="utf-8")
    meta_json.write_text(json.dumps({
        "input": str(video_path),
        "method": "research_windows",
        "windows": len(windows),
        "window_padding": args.padding,
        "league": args.league,
        "match_slug": match_slug,
        "model": args.model,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }, indent=2), encoding="utf-8")

    print(f"\nTranscript written: {transcript_txt}")
    print(f"Timestamps written: {timestamps_json} ({len(all_segments)} segments)")
    print(f"Metadata written: {meta_json}")


if __name__ == "__main__":
    main()
