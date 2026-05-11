import argparse
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from pipeline.utils import ROOT, ffmpeg_executable, seconds_to_timestamp, slugify

load_dotenv()
os.environ.setdefault("HF_HOME", str(ROOT / ".cache" / "huggingface"))

def extract_audio(video_path: Path, out_audio: Path) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        ffmpeg_executable(),
        "-y",
        "-i",
        str(video_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "pcm_s16le",
        str(out_audio),
    ]
    subprocess.run(cmd, check=True)
    return out_audio

def transcribe_with_faster_whisper(audio_path: Path, model: str, device: str, compute_type: str) -> tuple[str, list[dict], dict]:
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        raise SystemExit(
            "Missing dependency: faster-whisper. Use a Python 3.11/3.12 venv, then run: "
            "pip install -r requirements.txt"
        )

    whisper = WhisperModel(model, device=device, compute_type=compute_type)
    segments, info = whisper.transcribe(str(audio_path), vad_filter=True)

    lines = []
    segment_rows = []
    for segment in segments:
        row = {
            "start": segment.start,
            "end": segment.end,
            "start_time": seconds_to_timestamp(segment.start),
            "end_time": seconds_to_timestamp(segment.end),
            "text": segment.text.strip(),
        }
        segment_rows.append(row)
        lines.append(f"[{row['start_time']} - {row['end_time']}] {row['text']}")

    metadata = {
        "detected_language": info.language,
        "language_probability": info.language_probability,
    }
    return "\n".join(lines), segment_rows, metadata

def main():
    parser = argparse.ArgumentParser(description="Transcribe a match locally using faster-whisper.")
    parser.add_argument("--input", required=True, help="Path to match video/audio file")
    parser.add_argument("--league", required=True, choices=["PREMIER_LEAGUE", "UCL", "MLS", "LIGA_MX", "WORLD_CUP"])
    parser.add_argument("--model", default="small", help="faster-whisper model size/name, e.g. tiny, base, small, medium")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"], help="Device for faster-whisper")
    parser.add_argument("--compute-type", default="int8", help="faster-whisper compute type, e.g. int8 or float16")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    match_slug = slugify(input_path.stem)
    out_dir = ROOT / "TRANSCRIPTS" / args.league / match_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / f"{match_slug}_audio.wav"

    print(f"Extracting audio with FFmpeg: {audio_path}")
    extract_audio(input_path, audio_path)

    print(f"Transcribing locally with faster-whisper model: {args.model}")
    transcript, segments, whisper_metadata = transcribe_with_faster_whisper(
        audio_path,
        args.model,
        args.device,
        args.compute_type,
    )

    transcript_txt = out_dir / "transcript.txt"
    transcript_txt.write_text(transcript.strip(), encoding="utf-8")

    timestamps_json = out_dir / "timestamps.json"
    timestamps_json.write_text(json.dumps(segments, indent=2), encoding="utf-8")

    meta_json = out_dir / "metadata.json"
    meta_json.write_text(json.dumps({
        "input": str(input_path),
        "audio": str(audio_path),
        "league": args.league,
        "match_slug": match_slug,
        "model": args.model,
        "engine": "faster-whisper",
        "device": args.device,
        "compute_type": args.compute_type,
        **whisper_metadata,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }, indent=2), encoding="utf-8")

    print(f"Transcript written: {transcript_txt}")
    print(f"Timestamps written: {timestamps_json}")

if __name__ == "__main__":
    main()
