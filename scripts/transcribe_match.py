import argparse
import json
import logging
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any

from pipeline.utils import ROOT, slugify

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover
    WhisperModel = None


LOGGER = logging.getLogger("transcribe_match")


def configure_logging(verbose: bool = False) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def extract_audio(input_path: Path, out_audio: Path, dry_run: bool = False) -> Path:
    out_audio.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(input_path),
        "-vn",
        "-ac",
        "1",
        "-ar",
        "16000",
        "-c:a",
        "aac",
        str(out_audio),
    ]
    LOGGER.info("Extracting audio with ffmpeg")
    LOGGER.debug("Command: %s", " ".join(cmd))
    if not dry_run:
        subprocess.run(cmd, check=True)
    return out_audio


def choose_compute_type(device: str) -> str:
    # Apple Silicon MPS path currently works best with float16/float32.
    if device == "cpu":
        return "int8"
    return "float16"


def transcribe_with_faster_whisper(audio_path: Path, model_name: str, language: str | None, device: str) -> dict[str, Any]:
    if WhisperModel is None:
        raise SystemExit(
            "Missing dependency: faster-whisper. Install with `pip install -r requirements.txt`."
        )

    compute_type = choose_compute_type(device)
    LOGGER.info("Loading faster-whisper model=%s device=%s compute_type=%s", model_name, device, compute_type)
    model = WhisperModel(model_name, device=device, compute_type=compute_type)

    segments, info = model.transcribe(
        str(audio_path),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )

    segment_payload: list[dict[str, Any]] = []
    full_text_parts: list[str] = []

    for idx, segment in enumerate(segments):
        words = []
        for word in (segment.words or []):
            words.append(
                {
                    "word": word.word.strip(),
                    "start": float(word.start),
                    "end": float(word.end),
                    "probability": float(word.probability),
                }
            )
        seg_text = segment.text.strip()
        full_text_parts.append(seg_text)
        segment_payload.append(
            {
                "id": idx,
                "start": float(segment.start),
                "end": float(segment.end),
                "text": seg_text,
                "avg_logprob": float(segment.avg_logprob),
                "compression_ratio": float(segment.compression_ratio),
                "no_speech_prob": float(segment.no_speech_prob),
                "speaker_style": classify_speaker_style(seg_text),
                "words": words,
            }
        )

    return {
        "text": " ".join(part for part in full_text_parts if part).strip(),
        "language": info.language,
        "language_probability": float(info.language_probability),
        "duration": float(info.duration),
        "segments": segment_payload,
    }


def classify_speaker_style(text: str) -> str:
    lowered = text.lower()
    if any(token in lowered for token in ["crowd", "roar", "fans"]):
        return "CROWD"
    if "goal" in lowered or "what a" in lowered:
        return "COMMENTARY_PEAK"
    return "COMMENTARY"


def main() -> None:
    parser = argparse.ArgumentParser(description="Transcribe a match with faster-whisper and word timestamps.")
    parser.add_argument("--input", required=True, help="Path to match video/audio file")
    parser.add_argument("--league", required=True, choices=["PREMIER_LEAGUE", "UCL", "MLS", "LIGA_MX"])
    parser.add_argument("--model", default="large-v3", help="faster-whisper model name")
    parser.add_argument("--language", default=None, help="Optional ISO language code, e.g. en")
    parser.add_argument("--device", choices=["auto", "cpu"], default="auto", help="Use auto for Apple Silicon optimization")
    parser.add_argument("--keep-audio", action="store_true", help="Keep extracted intermediate audio file")
    parser.add_argument("--dry-run", action="store_true", help="Log actions without transcribing")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    args = parser.parse_args()

    configure_logging(args.verbose)

    input_path = Path(args.input)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    match_slug = slugify(input_path.stem)
    out_dir = ROOT / "TRANSCRIPTS" / args.league / match_slug
    out_dir.mkdir(parents=True, exist_ok=True)

    audio_path = out_dir / f"{match_slug}_audio.m4a"
    transcript_txt = out_dir / "transcript.txt"
    timestamps_json = out_dir / "timestamps.json"
    segments_json = out_dir / "segments.json"
    meta_json = out_dir / "metadata.json"

    extract_audio(input_path, audio_path, dry_run=args.dry_run)

    if args.dry_run:
        LOGGER.info("Dry run enabled; transcription skipped.")
        return

    result = transcribe_with_faster_whisper(audio_path, args.model, args.language, args.device)

    transcript_txt.write_text(result["text"].strip(), encoding="utf-8")
    timestamps_json.write_text(json.dumps(result["segments"], indent=2), encoding="utf-8")
    segments_json.write_text(
        json.dumps(
            {
                "schema_version": "2.0",
                "match_slug": match_slug,
                "league": args.league,
                "language": result["language"],
                "duration": result["duration"],
                "segments": result["segments"],
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    meta_json.write_text(
        json.dumps(
            {
                "input": str(input_path),
                "audio": str(audio_path),
                "league": args.league,
                "match_slug": match_slug,
                "transcription_engine": "faster-whisper",
                "model": args.model,
                "device": args.device,
                "created_at": datetime.utcnow().isoformat() + "Z",
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    if not args.keep_audio:
        audio_path.unlink(missing_ok=True)

    LOGGER.info("Transcript written: %s", transcript_txt)
    LOGGER.info("Timestamp segments written: %s", timestamps_json)


if __name__ == "__main__":
    main()
