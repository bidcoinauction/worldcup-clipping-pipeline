import argparse
import importlib.util
import os
import sys
from pathlib import Path

from pipeline.paths import PROJECT_DIRS
from pipeline.utils import ROOT, ffmpeg_executable


REQUIRED_FILES = [
    "config/pipeline_config.json",
    "prompts/thumbnail_prompt_template.txt",
    "scripts/process_match.py",
    "scripts/transcribe_match.py",
    "scripts/build_clip_manifest.py",
    "scripts/export_clips_ffmpeg.py",
]

OPTIONAL_IMPORTS = [
    ("openai", "OpenAI API detection"),
    ("dotenv", ".env loading"),
    ("faster_whisper", "local transcription"),
    ("imageio_ffmpeg", "bundled FFmpeg fallback"),
]


def check(name: str, ok: bool, detail: str = "", required: bool = True) -> bool:
    marker = "OK" if ok else ("FAIL" if required else "WARN")
    suffix = f" - {detail}" if detail else ""
    print(f"[{marker}] {name}{suffix}")
    return ok or not required


def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Preflight check for the clipping pipeline.")
    parser.add_argument("--strict", action="store_true", help="Exit non-zero on warnings as well as failures.")
    args = parser.parse_args()

    load_env_file(ROOT / ".env")
    results = []
    warnings = []

    version_ok = (3, 11) <= sys.version_info[:2] <= (3, 12)
    results.append(check(
        "Python version",
        version_ok,
        f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}; use 3.11 or 3.12 for faster-whisper",
    ))

    for rel_path in REQUIRED_FILES:
        path = ROOT / rel_path
        results.append(check(f"Required file: {rel_path}", path.exists()))

    for rel_dir in PROJECT_DIRS:
        path = ROOT / rel_dir
        ok = path.exists() and path.is_dir()
        results.append(check(f"Project dir: {rel_dir}", ok, "run scripts/init_project.py" if not ok else ""))

    try:
        ffmpeg_path = ffmpeg_executable()
        results.append(check("FFmpeg", bool(ffmpeg_path), ffmpeg_path))
    except (RuntimeError, SystemExit) as exc:
        results.append(check("FFmpeg", False, str(exc)))

    for module, purpose in OPTIONAL_IMPORTS:
        ok = importlib.util.find_spec(module) is not None
        required = module in {"dotenv", "imageio_ffmpeg"}
        if not ok and not required:
            warnings.append(False)
        results.append(check(f"Python package: {module}", ok, purpose, required=required))

    api_key_present = bool(os.getenv("OPENAI_API_KEY"))
    warnings.append(check(
        "OPENAI_API_KEY",
        api_key_present,
        "required only for --run-gpt",
        required=False,
    ))

    model = os.getenv("DEFAULT_OPENAI_MODEL", "gpt-4.1")
    check("DEFAULT_OPENAI_MODEL", bool(model), model, required=False)

    failed = not all(results)
    warned = not all(warnings) if warnings else False
    if failed or (args.strict and warned):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
