import argparse
import json
import os
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv
from openai import OpenAI
from pipeline.utils import ROOT, slugify

load_dotenv()

def extract_audio(video_path: Path, out_audio: Path) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "afconvert",
        str(video_path),
        str(out_audio),
        "-f", "mp4f",
        "-d", "aac"
    ]
    subprocess.run(cmd, check=True)
    return out_audio

def transcribe_with_openai(audio_path: Path, model: str) -> str:
    client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    if not os.getenv("OPENAI_API_KEY"):
        raise SystemExit("OPENAI_API_KEY missing. Add it to .env first.")

    with audio_path.open("rb") as f:
        result = client.audio.transcriptions.create(
            model=model,
            file=f,
            response_format="text",
        )

    return str(result)

def main():
    parser = argparse.ArgumentParser(description="Transcribe a match using OpenAI API.")
    parser.add_argument("--input", required=True, help="Path to match video/audio file")
    parser.add_argument("--league", required=True, choices=["PREMIER_LEAGUE", "UCL", "MLS", "LIGA_MX"])
    parser.add_argument("--model", default=os.getenv("DEFAULT_TRANSCRIBE_MODEL", "gpt-4o-transcribe"))
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    match_slug = slugify(input_path.stem)
    out_dir = ROOT / "TRANSCRIPTS" / args.league / match_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / f"{match_slug}_audio.m4a"

    print(f"Extracting audio with macOS afconvert: {audio_path}")
    extract_audio(input_path, audio_path)

    print(f"Transcribing with OpenAI model: {args.model}")
    transcript = transcribe_with_openai(audio_path, args.model)

    transcript_txt = out_dir / "transcript.txt"
    transcript_txt.write_text(transcript.strip(), encoding="utf-8")

    # Timestamp placeholder. OpenAI text mode does not return segment timestamps.
    timestamps_json = out_dir / "timestamps.json"
    timestamps_json.write_text(json.dumps([], indent=2), encoding="utf-8")

    meta_json = out_dir / "metadata.json"
    meta_json.write_text(json.dumps({
        "input": str(input_path),
        "audio": str(audio_path),
        "league": args.league,
        "match_slug": match_slug,
        "model": args.model,
        "created_at": datetime.utcnow().isoformat() + "Z"
    }, indent=2), encoding="utf-8")

    print(f"Transcript written: {transcript_txt}")
    print("Note: This API transcription mode creates transcript text, not segment-level timestamps.")

if __name__ == "__main__":
    main()
