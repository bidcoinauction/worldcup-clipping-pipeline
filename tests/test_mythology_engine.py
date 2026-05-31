from pipeline.stadium_signal import classification_for_tier, mythology_for_match, tier_for_score


def test_mythology_score_tiering():
    assert tier_for_score(100) == "S"
    assert tier_for_score(95) == "S"
    assert tier_for_score(94) == "A"
    assert tier_for_score(85) == "A"
    assert tier_for_score(84) == "B"
    assert tier_for_score(60) == "C"
    assert tier_for_score(59) == "Archive"
    assert classification_for_tier("S") == "Football Mythology"


def test_mythology_engine_reads_seeded_score():
    result = mythology_for_match("brazil_germany_2014")

    assert result == {
        "match_id": "brazil_germany_2014",
        "total_score": 99,
        "tier": "S",
        "classification": "Football Mythology",
        "recommended_series": ["The Collapse", "National Trauma", "Football Cinema"],
    }
