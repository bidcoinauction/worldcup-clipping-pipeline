from pathlib import Path

try:
    from faster_whisper import WhisperModel
except ImportError:  # pragma: no cover - exercised only when dependency is absent.
    WhisperModel = None


def transcribe(audio_path: Path, model_size: str = "base", initial_prompt: str = "") -> tuple[str, list[dict]]:
    if WhisperModel is None:
        raise SystemExit("Missing dependency. Run: pip install faster-whisper")

    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    kwargs = {}
    if initial_prompt:
        kwargs["initial_prompt"] = initial_prompt
    segments, _info = model.transcribe(str(audio_path), **kwargs)

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
