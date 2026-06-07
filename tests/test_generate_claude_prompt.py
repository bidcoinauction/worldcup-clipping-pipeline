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


def _run_main(tmp_path, research_arg=None, mode=None):
    transcript = _write_transcript(tmp_path)
    argv = [
        "prog",
        "--transcript", str(transcript),
        "--match-name", "psg_arsenal_2min",
    ]
    if mode:
        argv += ["--mode", mode]
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


def test_research_with_video_time_seconds(tmp_path):
    events = [
        {"minute_raw": "12", "video_time_seconds": 345, "type": "goal",
         "description": "Messi scores", "player": "Messi"},
        {"minute_raw": "45+3", "type": "penalty_save",
         "description": "Ramsdale saves", "player": "Ramsdale"},
    ]
    research = _research_file(tmp_path, events=events)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "[12' / 345s GOAL]" in content
    assert "[45+3' PENALTY SAVE]" in content
    assert "/ 345s" in content


def test_research_adds_category_rule(tmp_path):
    research = _research_file(tmp_path)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "concrete football events" in content
    assert "Do not default to AMERICA" in content


def test_no_research_omits_category_rule(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path)

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "concrete football events" not in content
    assert "Do not default to AMERICA" not in content


def _research_file_with_story_targets(tmp_path):
    d = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_2min"
    d.mkdir(parents=True, exist_ok=True)
    data = {
        "events": [{"minute_raw": "12", "type": "goal", "description": "Messi scores", "importance": "high"}],
        "story_targets": {
            "arc_type": "Legacy Arc",
            "acts": ["setup", "pressure", "rupture", "aftermath"],
            "required_coverage": {
                "types": ["goal", "penalty", "trophy_lift"],
                "diversity": ["crowd_reaction", "manager_reaction"]
            },
            "narrative_hook": "The greatest World Cup final of all time"
        },
    }
    path = d / "match_research.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def _research_file_no_story_targets(tmp_path):
    d = tmp_path / "MATCH_RESEARCH" / "WORLD_CUP" / "psg_arsenal_2min"
    d.mkdir(parents=True, exist_ok=True)
    data = {"events": [{"minute_raw": "12", "type": "goal", "description": "Messi scores"}]}
    path = d / "match_research.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_package_mode_uses_goal_package(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "complete story package" in content
    assert "full emotional narrative" in content


def test_package_mode_clip_schema_includes_package_fields(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "sequence_order" in content
    assert "narrative_role" in content
    assert "setup | tension_builder | climax | reaction | aftermath" in content


def test_package_mode_rules_include_diversity(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "crowd reaction" in content.lower()
    assert "manager/bench reaction" in content.lower()
    assert "setup" in content and "pressure" in content
    assert "rupture" in content and "aftermath" in content


def test_package_mode_with_story_targets_injects_block(tmp_path):
    research = _research_file_with_story_targets(tmp_path)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "Story package targets:" in content
    assert "Legacy Arc" in content
    assert "setup, pressure, rupture, aftermath" in content
    assert "goal, penalty, trophy_lift" in content
    assert "crowd_reaction, manager_reaction" in content
    assert "The greatest World Cup final of all time" in content


def test_package_mode_research_no_story_targets_omitted(tmp_path):
    research = _research_file_no_story_targets(tmp_path)
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, research_arg=research, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "Story package targets" not in content


def test_package_mode_no_research_omits_story_targets(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="package")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "Story package targets" not in content


def test_story_mode_no_package_fields(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="story")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "sequence_order" not in content
    assert "narrative_role" not in content


def test_micro_mode_no_package_fields(tmp_path):
    with patch("scripts.generate_claude_prompt.ROOT") as mock_root:
        _mock_root(mock_root, tmp_path)
        _run_main(tmp_path, mode="micro")

    out = tmp_path / "PROMPTS" / "psg_arsenal_2min_claude_prompt.txt"
    content = out.read_text(encoding="utf-8")
    assert "sequence_order" not in content
    assert "narrative_role" not in content
