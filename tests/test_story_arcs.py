import json

from pipeline.stadium_signal import CLIP_FIELDS, MATCH_FIELDS, generate_story_arc, write_csv, write_story_arc


def test_story_arc_generation_uses_match_emotion(tmp_path):
    write_csv(
        tmp_path / "data/matches.csv",
        [
            {
                "match_id": "brazil_germany_2014",
                "title": "Brazil 1-7 Germany",
                "primary_emotion": "National Trauma",
                "secondary_emotions": "Collapse; Shock",
                "mythology_score": "99",
            }
        ],
        MATCH_FIELDS,
    )
    write_csv(
        tmp_path / "data/clip_windows.csv",
        [{"clip_id": "brazil_germany_2014_collapse_001", "match_id": "brazil_germany_2014"}],
        CLIP_FIELDS,
    )

    arc = generate_story_arc("brazil_germany_2014", root=tmp_path)

    assert arc["match_id"] == "brazil_germany_2014"
    assert arc["arc_type"] == "Collapse Arc"
    assert arc["candidate_clip_ids"] == ["brazil_germany_2014_collapse_001"]


def test_story_arc_writes_expected_json(tmp_path):
    write_csv(
        tmp_path / "data/matches.csv",
        [{"match_id": "liverpool_milan_2005", "title": "Liverpool 3-3 Milan", "primary_emotion": "Miracle"}],
        MATCH_FIELDS,
    )
    write_csv(tmp_path / "data/clip_windows.csv", [], CLIP_FIELDS)

    out_path = write_story_arc("liverpool_milan_2005", root=tmp_path)
    payload = json.loads(out_path.read_text(encoding="utf-8"))

    assert out_path.name == "liverpool_milan_2005_story_arc.json"
    assert payload["arc_type"] == "Miracle Arc"
