import argparse
import subprocess
from pathlib import Path
from pipeline.config import get_default_clip_mode, get_leagues, get_model
from pipeline.utils import ROOT, slugify

def run(cmd):
    print("\n$ " + " ".join(str(x) for x in cmd))
    subprocess.run(cmd, check=True)

def main():
    parser = argparse.ArgumentParser(description="One-command match processing pipeline.")
    parser.add_argument("--input", required=True)
    parser.add_argument("--league", required=True, choices=get_leagues())
    parser.add_argument("--match-name", required=True)
    parser.add_argument("--model", default=get_model("transcription"))
    parser.add_argument("--mode", default=None, choices=("story", "micro", "package"),
                        help="Clip mode (default: config value)")
    parser.add_argument("--run-detection", action="store_true", help="Run clip detection (uses provider from config)")
    parser.add_argument("--research", default=None,
                        help="Path to match_research.json with known events")
    args = parser.parse_args()

    match_slug = slugify(Path(args.input).stem)
    name_slug = slugify(args.match_name)

    run(["python", "scripts/transcribe_match.py", "--input", args.input, "--league", args.league, "--model", args.model])

    transcript = ROOT / "TRANSCRIPTS" / args.league / match_slug / "transcript.txt"
    research = args.research or ROOT / "MATCH_RESEARCH" / args.league / match_slug / "match_research.json"
    research = research if Path(research).exists() else None

    prompt_args = ["python", "scripts/generate_claude_prompt.py", "--transcript", transcript, "--match-name", args.match_name]
    if args.mode:
        prompt_args += ["--mode", args.mode]
    if research:
        prompt_args += ["--research", str(research)]
    run(prompt_args)

    prompt = ROOT / "PROMPTS" / f"{name_slug}_claude_prompt.txt"
    analysis = ROOT / "MATCH_ANALYSIS" / args.league / match_slug / "gpt_clips.json"

    if args.run_detection:
        run(["python", "scripts/run_gpt_detection.py", "--prompt", prompt, "--output", analysis])
        run([
            "python", "scripts/build_clip_manifest.py",
            "--analysis", analysis,
            "--league", args.league,
            "--match-name", args.match_name,
            "--source-video", args.input
        ])
        manifest = ROOT / "CLIP_MANIFESTS" / f"{name_slug}_manifest.csv"
        run(["python", "scripts/generate_asset_prompts.py", "--manifest", manifest])
    else:
        print("\nGPT prompt is ready.")
        print(f"Prompt: {prompt}")
        print("Paste it into ChatGPT, save JSON to:")
        print(analysis)
        print("Then run build_clip_manifest.py.")

if __name__ == "__main__":
    main()
