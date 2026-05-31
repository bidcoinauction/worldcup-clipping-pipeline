import csv
from pathlib import Path

from pipeline.stadium_signal import DATASETS, MATCH_FIELDS, MYTHOLOGY_FIELDS, validate_data, write_csv


def seed_empty_files(root: Path) -> None:
    for rel_path, fields in DATASETS.values():
        path = root / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(",".join(fields) + "\n", encoding="utf-8")


def test_csv_validation_passes_for_required_columns(tmp_path):
    seed_empty_files(tmp_path)
    write_csv(
        tmp_path / "data/matches.csv",
        [{"match_id": "brazil_germany_2014", "title": "Brazil 1-7 Germany"}],
        MATCH_FIELDS,
    )

    result = validate_data(tmp_path)

    assert result.ok
    assert result.errors == []
    assert result.dataset_status["matches"]


def test_missing_match_reference_fails(tmp_path):
    seed_empty_files(tmp_path)
    with (tmp_path / "data/moments.csv").open("a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=DATASETS["moments"][1])
        writer.writerow(
            {
                "moment_id": "ghost_moment",
                "match_id": "missing_match",
                "video_timestamp": "00:12:00",
                "importance_score": "80",
            }
        )

    result = validate_data(tmp_path)

    assert not result.ok
    assert any("moments.csv references unknown match_id" in error for error in result.errors)


def test_invalid_score_range_fails(tmp_path):
    seed_empty_files(tmp_path)
    write_csv(
        tmp_path / "data/matches.csv",
        [{"match_id": "brazil_germany_2014", "title": "Brazil 1-7 Germany"}],
        MATCH_FIELDS,
    )
    write_csv(
        tmp_path / "data/mythology_scores.csv",
        [{"match_id": "brazil_germany_2014", "total_score": "101", "tier": "S"}],
        MYTHOLOGY_FIELDS,
    )

    result = validate_data(tmp_path)

    assert not result.ok
    assert any("out-of-range total_score" in error for error in result.errors)
