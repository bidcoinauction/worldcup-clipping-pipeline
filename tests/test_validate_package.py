from pipeline.stadium_signal import validate_package


def _clip(overrides=None):
    base = {
        "clip_id": "001",
        "sequence_order": 1,
        "narrative_role": "setup",
        "category": "EMOTION",
        "start_time": "0",
        "end_time": "10",
        "description": "Opening tension builds before the match",
        "caption": "Tense atmosphere",
        "hook_text": "",
        "manual_scrub_note": "",
    }
    if overrides:
        base.update(overrides)
    return base


def _package(*clips):
    return list(clips) if clips else []


def test_valid_package_passes():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
               "start_time": "0", "end_time": "10", "category": "EMOTION"}),
        _clip({"clip_id": "002", "sequence_order": 2, "narrative_role": "tension_builder",
               "start_time": "10", "end_time": "20", "category": "CHAOS"}),
        _clip({"clip_id": "003", "sequence_order": 3, "narrative_role": "climax",
               "start_time": "20", "end_time": "30", "category": "CHAOS"}),
        _clip({"clip_id": "004", "sequence_order": 4, "narrative_role": "reaction",
               "start_time": "30", "end_time": "40", "category": "EMOTION",
               "description": "Crowd erupts in the stands as fans cheer wildly"}),
        _clip({"clip_id": "005", "sequence_order": 5, "narrative_role": "reaction",
               "start_time": "40", "end_time": "50", "category": "AURA",
               "description": "Manager paces on the sideline, coach looks stunned"}),  # noqa: E501
        _clip({"clip_id": "006", "sequence_order": 6, "narrative_role": "aftermath",
               "start_time": "50", "end_time": "60", "category": "EMOTION"}),
        _clip({"clip_id": "007", "sequence_order": 7, "narrative_role": "setup",
               "start_time": "60", "end_time": "70", "category": "AURA"}),
        _clip({"clip_id": "008", "sequence_order": 8, "narrative_role": "climax",
               "start_time": "70", "end_time": "80", "category": "CHAOS"}),
    ]
    report = validate_package(clips)
    assert report["valid"] is True
    assert report["warnings"] == []


def test_too_few_clips():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "start_time": "0", "end_time": "10"}),
        _clip({"clip_id": "002", "sequence_order": 2, "start_time": "10", "end_time": "20"}),
        _clip({"clip_id": "003", "sequence_order": 3, "start_time": "20", "end_time": "30"}),
    ]
    report = validate_package(clips, min_clips=8)
    assert report["valid"] is False
    assert report["clip_count"] == 3
    assert report["clip_count_ok"] is False
    assert any("below minimum" in w for w in report["warnings"])


def test_too_many_clips():
    clips = [_clip({"clip_id": f"{i:03d}", "sequence_order": i,
                    "narrative_role": "climax", "start_time": str(i * 10),
                    "end_time": str(i * 10 + 9),
                    "description": "match action"}) for i in range(1, 18)]
    report = validate_package(clips, max_clips=15)
    assert report["valid"] is False
    assert report["clip_count"] == 17
    assert report["clip_count_ok"] is False
    assert any("exceeds maximum" in w for w in report["warnings"])


def test_overlap_detected():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "start_time": "0", "end_time": "25"}),
        _clip({"clip_id": "002", "sequence_order": 2, "start_time": "17", "end_time": "35"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=10)
    assert report["valid"] is False
    assert report["overlap_count"] == 1
    assert report["overlaps"][0]["clip_a"] == "001"
    assert report["overlaps"][0]["clip_b"] == "002"
    assert any("Overlap" in w for w in report["warnings"])


def test_no_overlap_ok():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "start_time": "0", "end_time": "25",
               "narrative_role": "aftermath",
               "description": "Manager paces on the sideline, coach looks stunned"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["valid"] is True
    assert report["overlap_count"] == 0


def test_missing_crowd_reaction():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
               "start_time": "0", "end_time": "10", "description": "match action"}),
        _clip({"clip_id": "002", "sequence_order": 2, "narrative_role": "aftermath",
               "start_time": "10", "end_time": "20",
               "description": "players walk off the pitch"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_crowd_reaction"] is False
    assert any("crowd reaction" in w.lower() for w in report["warnings"])


def test_has_crowd_reaction():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "reaction",
               "start_time": "0", "end_time": "10",
               "description": "Crowd erupts in the stands as fans go wild"}),
        _clip({"clip_id": "002", "sequence_order": 2, "narrative_role": "climax",
               "start_time": "10", "end_time": "20", "description": "match action"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_crowd_reaction"] is True
    assert not any("crowd reaction" in w.lower() for w in report["warnings"])


def test_missing_manager_reaction():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "reaction",
               "start_time": "0", "end_time": "10",
               "description": "Crowd erupts in celebration"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_manager_reaction"] is False
    assert any("manager" in w.lower() for w in report["warnings"])


def test_has_manager_reaction():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "reaction",
               "start_time": "0", "end_time": "10",
               "description": "Manager paces nervously on the sideline"}),  # noqa: E501
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_manager_reaction"] is True
    assert not any("manager" in w.lower() for w in report["warnings"])


def test_missing_aftermath():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
               "start_time": "0", "end_time": "10"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_aftermath"] is False
    assert any("aftermath" in w.lower() for w in report["warnings"])


def test_has_aftermath():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "aftermath",
               "start_time": "0", "end_time": "10"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["has_aftermath"] is True


def test_category_overuse():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "category": "CHAOS",
               "narrative_role": "setup", "start_time": "0", "end_time": "10",
               "description": "action"}),
        _clip({"clip_id": "002", "sequence_order": 2, "category": "CHAOS",
               "narrative_role": "climax", "start_time": "10", "end_time": "20",
               "description": "action"}),
        _clip({"clip_id": "003", "sequence_order": 3, "category": "CHAOS",
               "narrative_role": "climax", "start_time": "20", "end_time": "30",
               "description": "action"}),
        _clip({"clip_id": "004", "sequence_order": 4, "category": "EMOTION",
               "narrative_role": "climax", "start_time": "30", "end_time": "40",
               "description": "action"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=10)
    assert report["category_concentration"]["overused"] is True
    assert report["category_concentration"]["dominant"] == "CHAOS"
    assert report["category_concentration"]["percentage"] == 75.0
    assert any("dominates" in w for w in report["warnings"])


def test_category_balanced():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "category": "EMOTION",
               "narrative_role": "setup", "start_time": "0", "end_time": "10",
               "description": "Anthem"}),
        _clip({"clip_id": "002", "sequence_order": 2, "category": "CHAOS",
               "narrative_role": "climax", "start_time": "10", "end_time": "20",
               "description": "Goal"}),
        _clip({"clip_id": "003", "sequence_order": 3, "category": "AURA",
               "narrative_role": "aftermath",
               "start_time": "20", "end_time": "30",
               "description": "Manager paces on the sideline"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert report["category_concentration"]["overused"] is False
    assert not any("dominates" in w for w in report["warnings"])


def test_missing_narrative_roles():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
               "start_time": "0", "end_time": "10"}),
        _clip({"clip_id": "002", "sequence_order": 2, "narrative_role": "climax",
               "start_time": "10", "end_time": "20"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=5)
    assert "tension_builder" in report["narrative_roles"]["missing"]
    assert "reaction" in report["narrative_roles"]["missing"]
    assert "aftermath" in report["narrative_roles"]["missing"]
    assert any("Missing narrative roles" in w for w in report["warnings"])


def test_all_narrative_roles_present():
    clips = [
        _clip({"clip_id": "001", "sequence_order": 1, "narrative_role": "setup",
               "start_time": "0", "end_time": "10", "description": "Anthem"}),
        _clip({"clip_id": "002", "sequence_order": 2, "narrative_role": "tension_builder",
               "start_time": "10", "end_time": "20", "description": "Pressure builds"}),
        _clip({"clip_id": "003", "sequence_order": 3, "narrative_role": "climax",
               "start_time": "20", "end_time": "30", "description": "Goal scored"}),
        _clip({"clip_id": "004", "sequence_order": 4, "narrative_role": "reaction",
               "start_time": "30", "end_time": "40",
               "description": "Crowd erupts in the stands"}),
        _clip({"clip_id": "005", "sequence_order": 5, "narrative_role": "aftermath",
               "start_time": "40", "end_time": "50",
               "description": "Manager paces on the sideline"}),
    ]
    report = validate_package(clips, min_clips=1, max_clips=10)
    assert report["narrative_roles"]["missing"] == []
    assert not any("Missing narrative roles" in w for w in report["warnings"])


def test_empty_clips():
    report = validate_package([])
    assert report["valid"] is False
    assert report["clip_count"] == 0


def test_package_with_wrapped_clips_key():
    data = {"clips": [{
        "clip_id": "001", "sequence_order": 1, "narrative_role": "aftermath",
        "category": "CHAOS", "start_time": "0", "end_time": "10",
        "description": "Manager paces on the sideline",
    }]}
    report = validate_package(data["clips"], min_clips=1, max_clips=5)
    assert report["valid"] is True
