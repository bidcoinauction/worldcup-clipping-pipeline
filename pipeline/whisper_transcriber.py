from pathlib import Path

import numpy as np

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - exercised only when dependency is absent.
    WhisperModel = None


def _load_audio_float32(audio_path: Path) -> np.ndarray:
    """Decode audio to 16 kHz mono float32 using ffmpeg.

    Bypasses faster-whisper's PyAV decode path, which is sensitive to the
    installed ``av`` version (``metadata_errors`` kwarg availability).
    """
    import subprocess

    cmd = ["ffmpeg", "-v", "error", "-i", str(audio_path),
           "-f", "f32le", "-ac", "1", "-ar", "16000", "-"]
    result = subprocess.run(cmd, capture_output=True)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg audio decode failed: {result.stderr[-300:]}")
    return np.frombuffer(result.stdout, dtype=np.float32).copy()


def transcribe(audio_path: Path, model_size: str = "base", initial_prompt: str = "") -> tuple[str, list[dict]]:
    if WhisperModel is None:
        raise SystemExit("Missing dependency. Run: pip install faster-whisper")

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    audio = _load_audio_float32(audio_path)
    kwargs = {}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, _info = model.transcribe(audio, **kwargs)

    full_text: list[str] = []
    result: list[dict] = []
    for seg in segments:
        text = (seg.text or "").strip()
        full_text.append(text)
        result.append({
            "start": seg.start,
            "end": seg.end,
            "text": text,
        })

    return " ".join(full_text), result