from pathlib import Path
from unittest.mock import MagicMock, patch

from scripts.transcribe_match import extract_audio, transcribe_with_openai, transcribe_with_whisper


def test_extract_audio_calls_ffmpeg(tmp_path, monkeypatch):
    video = tmp_path / "match.mp4"
    video.write_text("fake video data")
    out_audio = tmp_path / "match_audio.m4a"

    commands = []
    monkeypatch.setattr("subprocess.run", lambda cmd, **kw: commands.append(cmd))

    result = extract_audio(video, out_audio)
    assert result == out_audio
    assert len(commands) == 1

    cmd = commands[0]
    assert cmd[0] == "ffmpeg"
    assert "-y" in cmd
    assert "-i" in cmd
    assert str(video) in cmd
    assert "-vn" in cmd
    assert "-acodec" in cmd
    assert "aac" in cmd
    assert str(out_audio) in cmd


@patch("scripts.transcribe_match.subprocess.run")
def test_extract_audio_returns_output_path(mock_run, tmp_path):
    video = tmp_path / "match.mp4"
    video.write_text("")
    out_audio = tmp_path / "out.m4a"

    result = extract_audio(video, out_audio)
    assert result == out_audio


def test_transcribe_with_openai_uses_verbose_json(tmp_path):
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_text("fake audio data")

    mock_segment = MagicMock()
    mock_segment.start = 0.0
    mock_segment.end = 4.5
    mock_segment.text = "The match begins."

    mock_response = MagicMock()
    mock_response.text = "The match begins."
    mock_response.segments = [mock_segment]

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = mock_response

    with patch("scripts.transcribe_match.make_openai_client", return_value=mock_client):
        transcript, segments = transcribe_with_openai(audio_file, "gpt-4o-transcribe")

    assert transcript == "The match begins."
    assert len(segments) == 1
    assert segments[0]["start"] == 0.0
    assert segments[0]["end"] == 4.5
    assert segments[0]["text"] == "The match begins."

    call_kwargs = mock_client.audio.transcriptions.create.call_args[1]
    assert call_kwargs["response_format"] == "verbose_json"
    assert call_kwargs["model"] == "gpt-4o-transcribe"


def test_transcribe_with_openai_handles_no_segments(tmp_path):
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_text("fake audio data")

    mock_response = MagicMock()
    mock_response.text = "Some transcript text."
    mock_response.segments = []

    mock_client = MagicMock()
    mock_client.audio.transcriptions.create.return_value = mock_response

    with patch("scripts.transcribe_match.make_openai_client", return_value=mock_client):
        transcript, segments = transcribe_with_openai(audio_file, "gpt-4o-transcribe")

    assert transcript == "Some transcript text."
    assert segments == []


@patch("pipeline.whisper_transcriber.WhisperModel")
def test_transcribe_with_whisper_returns_same_contract(mock_model_cls, tmp_path):
    audio_file = tmp_path / "audio.m4a"
    audio_file.write_text("fake audio data")

    mock_seg_1 = MagicMock()
    mock_seg_1.start = 0.0
    mock_seg_1.end = 3.0
    mock_seg_1.text = "Hello world"
    mock_seg_2 = MagicMock()
    mock_seg_2.start = 3.5
    mock_seg_2.end = 6.0
    mock_seg_2.text = " Goodbye "

    mock_model = MagicMock()
    mock_model.transcribe.return_value = ([mock_seg_1, mock_seg_2], MagicMock())
    mock_model_cls.return_value = mock_model

    transcript, segments = transcribe_with_whisper(audio_file, "base")

    assert transcript == "Hello world Goodbye"
    assert len(segments) == 2
    assert segments[0] == {"start": 0.0, "end": 3.0, "text": "Hello world"}
    assert segments[1] == {"start": 3.5, "end": 6.0, "text": "Goodbye"}


@patch("scripts.transcribe_match.extract_audio")
@patch("scripts.transcribe_match.transcribe_with_whisper")
def test_main_faster_whisper_dispatches_correctly(mock_whisper, mock_extract, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_whisper.return_value = ("transcript", [{"start": 0.0, "end": 1.0, "text": "test"}])
    mock_extract.return_value = tmp_path / "out.m4a"

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE", "--provider", "faster-whisper",
        "--model", "base",
    ]):
        from scripts.transcribe_match import main
        main()

    mock_whisper.assert_called_once()


@patch("scripts.transcribe_match.extract_audio")
@patch("scripts.transcribe_match.transcribe_with_openai")
def test_main_openai_still_works(mock_openai, mock_extract, tmp_path):
    input_video = tmp_path / "match.mp4"
    input_video.touch()
    mock_openai.return_value = ("transcript", [{"start": 0.0, "end": 1.0, "text": "test"}])
    mock_extract.return_value = tmp_path / "out.m4a"

    with patch("sys.argv", [
        "transcribe_match", "--input", str(input_video),
        "--league", "PREMIER_LEAGUE", "--provider", "openai",
    ]):
        from scripts.transcribe_match import main
        main()

    mock_openai.assert_called_once()
