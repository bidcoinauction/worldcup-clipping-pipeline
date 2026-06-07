import json
from unittest.mock import patch
from pathlib import Path


TRANSCRIPT_TEXT = (
    "Referee explains the penalty rules to both goalkeepers "
    "before the shootout begins."
)

TIMESTAMPS = [
    {"start": 0, "end": 10, "text": "Referee explains the penalty rules."},
    {"start": 10, "end": 20, "text": "Both goalkeepers listen carefully."},
]

RESEARCH_EVENTS = [
    {
        "minute_raw": "12",
        "type": "goal",
        "description": "Messi scores a free kick",
        "player": "Messi",
        "team": "home",
        "importance": "high",
    },
    {
        "minute_raw": "34",
        "type": "yellow_card",
        "description": "Saliba booked for tactical foul",
        "player": "Saliba",
        "team": "away",
    },
    {
        "minute_raw": "45+3",
        "type": "penalty_save",
        "description": "Ramsdale saves Mbappe's penalty",
        "importance": "high",
    },
]


def _write_transcript(tmp_path, transcript_text=TRANSCRIPT_TEXT, timestamps=None):
    d = tmp_path / "TRANSCRIPTS" / "WORLD_CUP" / "psg_arsenal_2min"
    d.mkdir(parents=True, exist_ok=True)
    (d / "transcript.txt").write_text(transcript_text, encoding="utf-8")
    ts = timestamps or TIMESTAMPS
    (d / "timestamps.json").write_text(json.dumps(ts), encoding="utf-8")
    return d / "transcript.txt"


def _mock_root(mock, tmp_path):
    mock.__truediv__ = lambda self, other: tmp_path / other


def _research_file(tmp_path, events=None):
    d = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_2min"
    d.mkdir(parents=True, exist_ok=True)
    data = {"events": RESEARCH_EVENTS if events is None else events}
    path = d / "match_research.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _run_main(tmp_path, research_arg=None):
    transcript = _write_transcript(tmp_path)
    argv = [
        "prog",
        "--transcript", str(transcript),
        "--match-name", "psg_arsenal_2min",
    ]
    if research_arg:
        argv += ["--research", str(research_arg)]
    with patch("sys.argv", argv):
        from scripts.generate_claude_prompt import main
        main()


def test_prompt_without_research(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    assert out.exists()
    content = out.read_text(encoding="utf-8")
    assert "Match events" not in content
    assert "psg_arsenal_2min" in content
    assert "Referee explains" in content


def test_prompt_with_research_injects_block(tmp_path):
    research = _research_file(tmp_path)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[12' GOAL]" in content
    assert "[34' YELLOW CARD]" in content
    assert "[45+3' PENALTY SAVE]" in content
    assert "Messi" in content
    assert "Saliba" in content
    assert "Ramsdale" in content
    assert "Match events" in content


def test_research_block_omitted_when_no_file(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "Match events" not in content
    assert "GOAL" not in content


def test_research_empty_events_list(tmp_path):
    research = _research_file(tmp_path, events=[])
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "Match events" not in content
    assert "GOAL" not in content


def test_research_partial_fields(tmp_path):
    partial_events = [
        {"minute_raw": "90", "type": "goal", "description": "Last minute winner"},
        {"minute_raw": "120", "type": "shootout_miss", "description": "Penalty saved"},
    ]
    research = _research_file(tmp_path, events=partial_events)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[90' GOAL] Last minute winner" in content
    assert "[120' SHOOTOUT MISS] Penalty saved" in content
    assert "Match events" in content
