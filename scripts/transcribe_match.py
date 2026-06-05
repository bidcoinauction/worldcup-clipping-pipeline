import argparse
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from pipeline.api import make_openai_client
from pipeline.config import get_leagues, get_model as _get_model
from pipeline.utils import ROOT, slugify

load_dotenv()

def extract_audio(video_path: Path, out_audio: Path) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_path),
        "-vn",
        "-acodec", "aac",
        "-b:a", "192k",
        str(out_audio),
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    return out_audio

def transcribe_with_openai(audio_path: Path, model: str) -> tuple[str, list[dict]]:
    client = make_openai_client()

    with audio_path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="verbose_json",
        )

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

def main():
    parser = argparse.ArgumentParser(description="Transcribe a match using OpenAI API.")
    parser.add_argument("--input", required=True, help="Path to match video/audio file")
    parser.add_argument("--league", required=True, choices=get_leagues())
    parser.add_argument("--model", default=os.getenv("DEFAULT_TRANSCRIBE_MODEL") or _get_model("transcription"))
    parser.add_argument("--dry-run", action="store_true",
                        help="Print actions without executing.")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    match_slug = slugify(input_path.stem)
    out_dir = ROOT / "TRANSCRIPTS" / args.league / match_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / f"{match_slug}_audio.m4a"

    print(f"Extracting audio with ffmpeg: {audio_path}")
    if not args.dry_run:
        extract_audio(input_path, audio_path)
    else:
        print(f"[dry-run] Would extract audio")

    print(f"Transcribing with OpenAI model: {args.model}")
    transcript, segments = "", []
    if not args.dry_run:
        transcript, segments = transcribe_with_openai(audio_path, args.model)

    transcript_txt = out_dir / "transcript.txt"
    timestamps_json = out_dir / "timestamps.json"
    meta_json = out_dir / "metadata.json"

    if not args.dry_run:
        transcript_txt.write_text(transcript.strip(), encoding="utf-8")
        timestamps_json.write_text(json.dumps(segments, indent=2), encoding="utf-8")
        meta_json.write_text(json.dumps({
            "input": str(input_path),
            "audio": str(audio_path),
            "league": args.league,
            "match_slug": match_slug,
            "model": args.model,
            "created_at": datetime.utcnow().isoformat() + "Z"
        }, indent=2), encoding="utf-8")
        print(f"Transcript written: {transcript_txt}")
        print(f"Timestamps written: {timestamps_json} ({len(segments)} segments)")
    else:
        print(f"[dry-run] Would write: {transcript_txt}")
        print(f"[dry-run] Would write: {timestamps_json}")
        print(f"[dry-run] Would write: {meta_json}")

if __name__ == "__main__":
    main()
