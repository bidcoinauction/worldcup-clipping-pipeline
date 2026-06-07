import json
from unittest.mock import patch
from pathlib import Path

from scripts.repair_package import _build_repair_header

ORIGINAL_PROMPT = (
    "You are an elite short-form football clipping strategist.\n"
    "\n"
    "Goal:\n"
    "Build a complete story package.\n"
    "\n"
    'Timestamped transcript (video duration: 120s):\n'
    '"""\n'
    "[0s - 10s] Referee explains the penalty rules.\n"
    "[10s - 20s] Both goalkeepers listen carefully.\n"
    '"""\n'
)

VALID_CLIPS = [
    {
        "clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
        "category": "EMOTION", "start_time": "0", "end_time": "10",
        "description": "Opening tension",
    },
    {
        "clip_id": "002", "sequence_order": 2, "narrative_role": "tension_builder",
        "category": "CHAOS", "start_time": "10", "end_time": "20",
        "description": "Pressure builds",
    },
    {
        "clip_id": "003", "sequence_order": 3, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "20", "end_time": "30",
        "description": "Goal scored",
    },
    {
        "clip_id": "004", "sequence_order": 4, "narrative_role": "reaction",
        "category": "EMOTION", "start_time": "30", "end_time": "40",
        "description": "Crowd erupts in the stands as fans go wild",
    },
    {
        "clip_id": "005", "sequence_order": 5, "narrative_role": "reaction",
        "category": "AURA", "start_time": "40", "end_time": "50",
        "description": "Manager paces nervously on the sideline",
    },
    {
        "clip_id": "006", "sequence_order": 6, "narrative_role": "aftermath",
        "category": "EMOTION", "start_time": "50", "end_time": "60",
        "description": "Players reflect after the final whistle",
    },
    {
        "clip_id": "007", "sequence_order": 7, "narrative_role": "setup",
        "category": "AURA", "start_time": "60", "end_time": "70",
        "description": "Post-match analysis",
    },
    {
        "clip_id": "008", "sequence_order": 8, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "70", "end_time": "80",
        "description": "Replay of key moment",
    },
]

FAILED_CLIPS = [
    {
        "clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
        "category": "CHAOS", "start_time": "0", "end_time": "25",
        "description": "History context",
    },
    {
        "clip_id": "002", "sequence_order": 2, "narrative_role": "tension_builder",
        "category": "CHAOS", "start_time": "17", "end_time": "35",
        "description": "Referee speaks",
    },
    {
        "clip_id": "003", "sequence_order": 3, "narrative_role": "tension_builder",
        "category": "CHAOS", "start_time": "47", "end_time": "56",
        "description": "Ramos reacts",
    },
    {
        "clip_id": "004", "sequence_order": 4, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "51", "end_time": "63",
        "description": "Pressure builds",
    },
    {
        "clip_id": "005", "sequence_order": 5, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "72", "end_time": "82",
        "description": "Stakes discussed",
    },
    {
        "clip_id": "006", "sequence_order": 6, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "83", "end_time": "102",
        "description": "Ramos shoots",
    },
    {
        "clip_id": "007", "sequence_order": 7, "narrative_role": "climax",
        "category": "CHAOS", "start_time": "103", "end_time": "120",
        "description": "Crowd goes wild",
    },
]


def _write_files(tmp_path, prompt_text=None, clips=None):
    prompt_dir = tmp_path / "PROMPTS"
    prompt_dir.mkdir(parents=True, exist_ok=True)
    prompt_file = prompt_dir / "prompt.txt"
    prompt_file.write_text(prompt_text or ORIGINAL_PROMPT, encoding="utf-8")

    detect_dir = tmp_path / "DETECTION"
    detect_dir.mkdir(parents=True, exist_ok=True)
    detect_file = detect_dir / "detection.json"
    detect_file.write_text(json.dumps(clips or FAILED_CLIPS), encoding="utf-8")

    output_dir = tmp_path / "OUTPUT"
    output_dir.mkdir(parents=True, exist_ok=True)

    return prompt_file, detect_file, output_dir


def test_build_repair_header_includes_warnings():
    warnings = ["Clip count 7 is below minimum 8", "No aftermath clip found"]
    clips = [{"clip_id": "001"}]
    header = _build_repair_header(warnings, clips)
    assert "PREVIOUS PACKAGE FAILED VALIDATION" in header
    assert "Clip count 7 is below minimum 8" in header
    assert "No aftermath clip found" in header
    assert "001" in header
    assert "Fix" in header or "fix" in header or "fixes" in header


def test_repair_prompt_contains_original_context(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    assert prompt_path.exists()
    content = prompt_path.read_text(encoding="utf-8")
    assert "elite short-form" in content
    assert "PREVIOUS PACKAGE FAILED VALIDATION" in content
    assert "transcript" in content or "Referee" in content


def test_repair_prompt_contains_failed_clips(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    content = prompt_path.read_text(encoding="utf-8")
    assert '"clip_id": "001"' in content
    assert '"narrative_role": "setup"' in content


def test_repair_prompt_contains_validation_warnings(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    content = prompt_path.read_text(encoding="utf-8")
    assert "Clip count 7 is below minimum 8" in content
    assert "No crowd reaction" in content or "crowd reaction" in content
    assert "No aftermath" in content or "aftermath" in content


def test_dry_run_skips_detection(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    assert prompt_path.exists()
    repaired_path = output_dir / "repaired.json"
    assert not repaired_path.exists()


def test_skips_when_already_valid(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path, clips=VALID_CLIPS)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
    ]):
        from scripts.repair_package import main
        try:
            main()
        except SystemExit as e:
            assert e.code == 0

    # Should not create repair prompt or output for valid packages
    prompt_path = output_dir / "repaired_repair.txt"
    assert not prompt_path.exists()
    repaired_path = output_dir / "repaired.json"
    assert not repaired_path.exists()


def test_repair_prompt_instructs_fix(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    content = prompt_path.read_text(encoding="utf-8")
    assert "NEW JSON" in content or "new JSON" in content or "Produce a" in content or "fixes ALL" in content


def test_custom_repair_prompt_path(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)
    custom_prompt = tmp_path / "CUSTOM" / "my_repair.txt"

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--repair-prompt-path", str(custom_prompt),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    assert custom_prompt.exists()
    content = custom_prompt.read_text(encoding="utf-8")
    assert "PREVIOUS PACKAGE FAILED VALIDATION" in content


def test_uses_external_validation_report(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path, clips=FAILED_CLIPS[:2])
    report_path = tmp_path / "report.json"
    report = {
        "valid": False,
        "warnings": ["Custom warning from external report"],
    }
    report_path.write_text(json.dumps(report), encoding="utf-8")

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--validation-report", str(report_path),
        "--dry-run",
    ]):
        from scripts.repair_package import main
        main()

    prompt_path = output_dir / "repaired_repair.txt"
    content = prompt_path.read_text(encoding="utf-8")
    assert "Custom warning from external report" in content


def test_non_dry_run_calls_detection(tmp_path):
    prompt_file, detect_file, output_dir = _write_files(tmp_path)

    with patch("sys.argv", [
        "repair_package",
        "--original-prompt", str(prompt_file),
        "--failed-detection", str(detect_file),
        "--output", str(output_dir / "repaired.json"),
        "--provider", "ollama",
    ]), patch("pipeline.ollama_detector.run_ollama_detection") as mock_detect:
        from scripts.repair_package import main
        main()

    mock_detect.assert_called_once()
    call_args = mock_detect.call_args
    assert Path(call_args[0][0]).name.endswith("_repair.txt")
    assert str(call_args[0][1]).endswith("repaired.json")
