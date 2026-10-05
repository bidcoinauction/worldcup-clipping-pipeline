from __future__ import annotations

import pytest

from pipeline.moment_adapter import adapt_detection_to_moments, football_universal_event_type, parse_timestamp_seconds
from pipeline.moment_models import Moment, Participant
from pipeline.runtime_service import (
    get_moment,
    index_existing_project,
    list_project_artifacts,
    list_project_moments,
    register_artifact,
    update_moment_review_state,
    upsert_moment,
)
from tests.test_runtime_managed_analysis import _make_job


def _project_and_artifact(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    return project, artifact


def test_valid_moment_creation():
    moment = Moment(
        moment_id="mom_1",
        project_id="project_1",
        source_artifact_id="art_1",
        sport="football",
        universal_event_type="SCORE",
        sport_event_type="GOAL",
        start_seconds=10.0,
        peak_seconds=None,
        end_seconds=15.0,
        participants=[Participant(participant_type="PLAYER", name="Cristiano Ronaldo", role="SCORER", team="Portugal")],
        emotion=["CELEBRATION"],
    )

    assert moment.review_state == "UNREVIEWED"
    assert moment.participants[0].name == "Cristiano Ronaldo"


def test_moment_rejects_invalid_times_and_review_state():
    with pytest.raises(ValueError, match="negative"):
        Moment("m", "p", None, "football", "SCORE", "GOAL", -1, None, 2)
    with pytest.raises(ValueError, match="greater than or equal"):
        Moment("m", "p", None, "football", "SCORE", "GOAL", 5, None, 4)
    with pytest.raises(ValueError, match="review_state"):
        Moment("m", "p", None, "football", "SCORE", "GOAL", 1, None, 2, review_state="MAYBE")


@pytest.mark.parametrize(
    ("sport_type", "universal"),
    [("GOAL", "SCORE"), ("SAVE", "SAVE"), ("FOUL", "FOUL"), ("RED_CARD", "CARD"), ("WEIRD", "OTHER")],
)
def test_football_event_mapping_preserves_sport_type(sport_type, universal):
    assert football_universal_event_type(sport_type) == universal


@pytest.mark.parametrize(("raw", "seconds"), [(12, 12.0), ("01:02", 62.0), ("01:02:03", 3723.0)])
def test_parse_timestamp_seconds(raw, seconds):
    assert parse_timestamp_seconds(raw) == seconds


def test_parse_timestamp_seconds_rejects_invalid():
    with pytest.raises(ValueError):
        parse_timestamp_seconds("bad")


def test_adapt_detection_to_moments_idempotent_and_preserves_provenance(tmp_path, monkeypatch):
    project, artifact = _project_and_artifact(tmp_path, monkeypatch)
    rows = [{"clip_id": "clip_001", "category": "GOAL", "start_time": "00:01", "end_time": "00:05", "virality_score": "90"}]

    first = adapt_detection_to_moments(rows, project_id=project.project_id, source_artifact_id=artifact.artifact_id)
    second = adapt_detection_to_moments(rows, project_id=project.project_id, source_artifact_id=artifact.artifact_id)

    assert first[0].moment_id == second[0].moment_id
    assert first[0].universal_event_type == "SCORE"
    assert first[0].sport_event_type == "GOAL"
    assert first[0].importance == 0.9
    assert first[0].metadata["original_event_id"] == "clip_001"
    assert first[0].source_artifact_id == artifact.artifact_id


def test_runtime_moment_persistence_listing_and_review(tmp_path, monkeypatch):
    project, artifact = _project_and_artifact(tmp_path, monkeypatch)
    moment = adapt_detection_to_moments(
        [{"clip_id": "clip_001", "category": "SAVE", "start_time": 1, "end_time": 4}],
        project_id=project.project_id,
        source_artifact_id=artifact.artifact_id,
    )[0]

    stored = upsert_moment(moment)
    fetched = get_moment(stored.moment_id)
    by_project = list_project_moments(project.project_id)
    reviewed = update_moment_review_state(stored.moment_id, "STRONG")

    assert fetched.moment_id == stored.moment_id
    assert by_project[0].universal_event_type == "SAVE"
    assert reviewed.review_state == "STRONG"
    assert list_project_moments(project.project_id, review_state="STRONG")[0].moment_id == stored.moment_id
    with pytest.raises(ValueError, match="review_state"):
        update_moment_review_state(stored.moment_id, "BAD")


def test_runtime_moment_upsert_does_not_duplicate(tmp_path, monkeypatch):
    project, artifact = _project_and_artifact(tmp_path, monkeypatch)
    rows = [{"clip_id": "clip_001", "category": "FOUL", "start_time": 1, "end_time": 3}]
    moment = adapt_detection_to_moments(rows, project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0]

    upsert_moment(moment)
    upsert_moment(moment)

    assert len(list_project_moments(project.project_id)) == 1


def test_moment_foreign_key_requires_project(tmp_path, monkeypatch):
    project, artifact = _project_and_artifact(tmp_path, monkeypatch)
    moment = adapt_detection_to_moments(
        [{"clip_id": "clip_001", "category": "GOAL", "start_time": 1, "end_time": 2}],
        project_id="missing_project",
        source_artifact_id=artifact.artifact_id,
    )[0]

    with pytest.raises(Exception):
        upsert_moment(moment)

    assert list_project_artifacts(project.project_id, artifact_type="analysis_moments")
