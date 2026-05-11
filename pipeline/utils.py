from __future__ import annotations

from pathlib import Path
import re
import json
import os
import shutil
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "_", text)
    text = re.sub(r"_+", "_", text)
    return text.strip("_")

def ensure_dirs(paths: list[str]) -> None:
    for path in paths:
        (ROOT / path).mkdir(parents=True, exist_ok=True)

def read_json(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))

def write_json(path: str | Path, data: Any) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(data, indent=2), encoding="utf-8")

def timestamp_to_seconds(ts: str | int | float) -> float:
    if isinstance(ts, (int, float)):
        return float(ts)
    parts = str(ts).strip().split(":")
    if len(parts) == 3:
        h, m, s = parts
        return int(h) * 3600 + int(m) * 60 + float(s)
    if len(parts) == 2:
        m, s = parts
        return int(m) * 60 + float(s)
    return float(parts[0])

def seconds_to_timestamp(seconds: float) -> str:
    seconds = max(0, float(seconds))
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = seconds % 60
    return f"{h:02d}:{m:02d}:{s:05.2f}"

def ffmpeg_executable() -> str:
    configured = os.getenv("FFMPEG_BINARY")
    if configured:
        return configured

    system_ffmpeg = shutil.which("ffmpeg")
    if system_ffmpeg:
        return system_ffmpeg

    for candidate in [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
    ]:
        if Path(candidate).exists():
            return candidate

    try:
        import imageio_ffmpeg
    except ImportError:
        raise SystemExit("FFmpeg not found. Run: pip install -r requirements.txt")

    return imageio_ffmpeg.get_ffmpeg_exe()
