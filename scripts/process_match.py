import argparse
import json
import subprocess
import sys
from pathlib import Path
from datetime import datetime
from pipeline.utils import ROOT, slugify

def run(cmd):
    print("\n$ " + " ".join(str(x) for x in cmd))
    subprocess.run(cmd, check=True)

def main():
    parser = argparse.ArgumentParser(description="One-command match processing pipeline.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--league", required=True, choices=["PREMIER_LEAGUE", "UCL", "MLS", "LIGA_MX", "WORLD_CUP"])
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--model", default="small", help="Local faster-whisper model size/name")
    parser.add_argument("--source", default="local_file", help="Where the video came from, e.g. footballia")
    parser.add_argument("--source-url", default="", help="Optional source page URL for your own records")
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--compute-type", default="int8")
    parser.add_argument("--run-signals", action="store_true", help="Run fast local transcript/audio signal detection")
    parser.add_argument("--run-scoreboard", action="store_true", help="Also sample scoreboard frames for OCR signals")
    parser.add_argument("--scoreboard-crop", default="0:0:620:140", help="x:y:w:h crop for scoreboard sampling")
    parser.add_argument("--scoreboard-interval-seconds", type=int, default=20)
    parser.add_argument("--scoreboard-duration-seconds", type=int, default=180, help="0 scans the full video")
    parser.add_argument("--run-gpt", action="store_true", help="Requires OPENAI_API_KEY")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input file not found: {input_path}")
    if args.run_scoreboard and not args.run_signals:
        parser.error("--run-scoreboard requires --run-signals")

    match_slug = slugify(input_path.stem)
    name_slug = slugify(args.match_name)

    source_meta = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "source_metadata.json"
    source_meta.parent.mkdir(parents=True, exist_ok=True)
    source_meta.write_text(json.dumps({
        "source": args.source,
        "source_url": args.source_url,
        "input": args.input,
        "match_name": args.match_name,
        "league": args.league,
        "created_at": datetime.utcnow().isoformat() + "Z",
    }, indent=2), encoding="utf-8")

    run([
        sys.executable, "scripts/transcribe_match.py",
        "--input", args.input,
        "--league", args.league,
        "--model", args.model,
        "--device", args.device,
        "--compute-type", args.compute_type,
    ])

    transcript = ROOT / "TRANSCRIPTS" / args.league / match_slug / "transcript.txt"
    timestamps = ROOT / "TRANSCRIPTS" / args.league / match_slug / "timestamps.json"
    audio = ROOT / "TRANSCRIPTS" / args.league / match_slug / f"{match_slug}_audio.wav"
    signals = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "moment_signals.json"

    prompt_cmd = [sys.executable, "scripts/generate_claude_prompt.py", "--transcript", transcript, "--match-name", args.match_name]

    if args.run_signals:
        audio_spikes = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "audio_spikes.json"
        scoreboard_samples = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "scoreboard_samples.json"
        run([
            sys.executable, "scripts/detect_audio_spikes.py",
            "--audio", audio,
            "--league", args.league,
            "--match-slug", match_slug,
        ])
        build_signal_cmd = [
            sys.executable, "scripts/build_moment_signals.py",
            "--timestamps", timestamps,
            "--audio-spikes", audio_spikes,
            "--output", signals,
        ]
        if args.run_scoreboard:
            scoreboard_cmd = [
                sys.executable, "scripts/sample_scoreboard.py",
                "--input", args.input,
                "--league", args.league,
                "--match-slug", match_slug,
                "--crop", args.scoreboard_crop,
                "--interval-seconds", str(args.scoreboard_interval_seconds),
            ]
            if args.scoreboard_duration_seconds:
                scoreboard_cmd.extend(["--duration-seconds", str(args.scoreboard_duration_seconds)])
            run(scoreboard_cmd)
            if scoreboard_samples.exists():
                build_signal_cmd.extend(["--scoreboard-samples", scoreboard_samples])
        run(build_signal_cmd)
        prompt_cmd.extend(["--signals", signals])

    run(prompt_cmd)

    prompt = ROOT / "PROMPTS" / f"{name_slug}_claude_prompt.txt"
    analysis = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "gpt_clips.json"

    if args.run_gpt:
        run([sys.executable, "scripts/run_gpt_detection.py", "--prompt", prompt, "--output", analysis])
        run([
            sys.executable, "scripts/build_clip_manifest.py",
            "--analysis", analysis,
            "--league", args.league,
            "--match-name", args.match_name,
            "--source-video", args.input
        ])
        manifest = ROOT / "CLIP_MANIFESTS" / f"{name_slug}_manifest.csv"
        run([sys.executable, "scripts/generate_asset_prompts.py", "--manifest", manifest])
    else:
        print("\nGPT prompt is ready.")
        print(f"Prompt: {prompt}")
        print("Paste it into ChatGPT, save JSON to:")
        print(analysis)
        print("Then run build_clip_manifest.py.")

if __name__ == "__main__":
    main()
