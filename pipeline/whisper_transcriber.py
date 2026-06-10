from pathlib import Path
from faster_whisper import WhisperModel


def transcribe(audio_path: Path, model_size: str = "base", initial_prompt: str = "") -> tuple[str, list[dict]]:
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
