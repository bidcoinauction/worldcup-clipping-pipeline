"""Regression tests for the real-media whisper path fix.

faster-whisper 1.2.1 calls ``av.open(..., metadata_errors=...)`` which newer
PyAV versions reject. ``whisper_transcriber`` now decodes audio itself with
ffmpeg and passes a float32 numpy array, bypassing PyAV entirely.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

from pipeline import whisper_transcriber


def test_load_audio_float32_decodes_with_ffmpeg(tmp_path):
    audio = tmp_path / "tone.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ac", "1", "-ar", "16000", str(audio)], check=True)

    samples = whisper_transcriber._load_audio_float32(audio)
    assert isinstance(samples, np.ndarray)
    assert samples.dtype == np.float32
    assert samples.ndim == 1
    assert len(samples) > 0
    assert np.max(np.abs(samples)) <= 1.0


def test_transcribe_passes_numpy_array_to_whisper(monkeypatch, tmp_path):
    import faster_whisper
    audio = tmp_path / "tone.wav"
    import subprocess
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                    "-ac", "1", "-ar", "16000", str(audio)], check=True)

    captured = {}

    class _FakeSegments:
        def __init__(self):
            self.items = []
        def __iter__(self):
            return iter(self.items)
        def __next__(self):
            return next(iter(self.items))

    class _FakeModel:
        def __init__(self, *a, **k):
            pass

        def transcribe(self, audio, **kwargs):
            captured["audio"] = audio
            captured["kwargs"] = kwargs
            seg = type("S", (), {"start": 0.0, "end": 1.0, "text": "hello"})()
            return [seg], None

    monkeypatch.setattr(whisper_transcriber, "WhisperModel", _FakeModel)

    text, segments = whisper_transcriber.transcribe(audio, model_size="base", initial_prompt="prompt")

    assert isinstance(captured["audio"], np.ndarray)
    assert captured["audio"].dtype == np.float32
    assert captured["kwargs"].get("initial_prompt") == "prompt"
    assert text == "hello"
    assert segments == [{"start": 0.0, "end": 1.0, "text": "hello"}]