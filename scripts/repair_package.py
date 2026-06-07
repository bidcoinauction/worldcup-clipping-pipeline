import argparse
import json
import sys
from pathlib import Path

from pipeline.config import get_provider
from pipeline.stadium_signal import validate_package


def _build_repair_header(warnings: list[str], failed_clips: list[dict]) -> str:
    lines: list[str] = []
    lines.append("PREVIOUS PACKAGE FAILED VALIDATION.\n")
    lines.append("Your previous clip package had the following issues:")
    for w in warnings:
        lines.append(f"  - {w}")
    lines.append("")
    lines.append("Your previous clips were:")
    lines.append(json.dumps(failed_clips, indent=2))
    lines.append("")
    lines.append("Produce a NEW JSON array that fixes ALL of the issues listed above. "
                 "Follow the same JSON schema and rules as before.")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Repair a failed story package by asking the model to fix validation issues."
    )
    parser.add_argument("--original-prompt", required=True,
                        help="Path to the original prompt file")
    parser.add_argument("--failed-detection", required=True,
                        help="Path to the failed detection JSON")
    parser.add_argument("--output", required=True,
                        help="Path to write the repaired detection JSON")
    parser.add_argument("--validation-report", default=None,
                        help="Pre-computed validation report (otherwise computed)")
    parser.add_argument("--repair-prompt-path", default=None,
                        help="Path to write the intermediate repair prompt")
    parser.add_argument("--provider", default=get_provider("detection"),
                        choices=["openai", "ollama"])
    parser.add_argument("--model", default=None,
                        help="Model name (default from provider)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Write repair prompt but skip API call")
    args = parser.parse_args()

    # Read inputs
    original = Path(args.original_prompt).read_text(encoding="utf-8")
    failed_clips = json.loads(Path(args.failed_detection).read_text(encoding="utf-8"))
    if isinstance(failed_clips, dict) and "clips" in failed_clips:
        failed_clips = failed_clips["clips"]

    # Validate
    if args.validation_report:
        report = json.loads(Path(args.validation_report).read_text(encoding="utf-8"))
    else:
        report = validate_package(failed_clips)

    if report["valid"]:
        print("Package is already valid \u2014 nothing to repair.")
        sys.exit(0)

    # Build repair prompt
    header = _build_repair_header(report["warnings"], failed_clips)
    repair_prompt = header + "\n\n" + original

    # Write repair prompt
    if args.repair_prompt_path:
        prompt_path = Path(args.repair_prompt_path)
    else:
        prompt_path = Path(args.output).with_name(
            Path(args.output).stem + "_repair.txt"
        )
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text(repair_prompt, encoding="utf-8")
    print(f"Repair prompt written: {prompt_path}")

    if args.dry_run:
        return

    # Call detection
    if args.provider == "openai":
        from pipeline.openai_client import run_gpt_detection
        run_gpt_detection(prompt_path, args.output)
    else:
        from pipeline.ollama_detector import run_ollama_detection
        kwargs: dict = {}
        if args.model:
            kwargs["model"] = args.model
        run_ollama_detection(prompt_path, args.output, **kwargs)

    print(f"Repaired detection saved: {args.output}")


if __name__ == "__main__":
    main()
