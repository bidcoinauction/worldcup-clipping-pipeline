from __future__ import annotations

import pytest

from pipeline.moment_models import Moment, MomentRelation
from pipeline.runtime_service import (
    delete_moment_relation,
    get_moment_relation,
    list_moment_incoming_relations,
    list_moment_outgoing_relations,
    list_project_moment_relations,
    register_artifact,
    upsert_moment,
    upsert_moment_relation,
    upsert_project,
)
from tests.test_pilot_intake import build_intake


@pytest.fixture(autouse=True)
def _isolated_runtime_db(tmp_path, monkeypatch):
    monkeypatch.setenv("STADIUM_RUNTIME_DB", str(tmp_path / "runtime.sqlite3"))


def _moments(tmp_path, monkeypatch, *, project_id="p1"):
    upsert_project(project_id=project_id, job_id=project_id, profile="football", sport="football", display_name="A", status="READY")
    artifact = register_artifact(project_id=project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moment_rows = [
        {"clip_id": "001", "category": "GOAL", "start_time": 1, "end_time": 5},
        {"clip_id": "002", "category": "SAVE", "start_time": 6, "end_time": 10},
        {"clip_id": "003", "category": "RED_CARD", "start_time": 11, "end_time": 15},
    ]
    moments = []
    for row in moment_rows:
        moment = Moment(
            moment_id=f"m_{row['clip_id']}", project_id=project_id,
            source_artifact_id=artifact.artifact_id, sport="football",
            universal_event_type="OTHER", sport_event_type=row["category"],
            start_seconds=row["start_time"], peak_seconds=None, end_seconds=row["end_time"],
        )
        moments.append(upsert_moment(moment))
    return moments


# ── Model ────────────────────────────────────────────────────────────────────


def test_relation_model_valid_and_invalid():
    rel = MomentRelation(relation_id="rel1", project_id="p1", source_moment_id="m_1", target_moment_id="m_2", relation_type="ESCALATES")
    assert rel.to_dict()["relation_type"] == "ESCALATES"
    with pytest.raises(ValueError, match="relation type"):
        MomentRelation("r2", "p1", "m_1", "m_2", "NOPE")
    with pytest.raises(ValueError, match="self-relations"):
        MomentRelation("r3", "p1", "m_1", "m_1", "CAUSES")
    with pytest.raises(ValueError, match="weight"):
        MomentRelation("r4", "p1", "m_1", "m_2", "CAUSES", weight=1.5)
    with pytest.raises(ValueError, match="confidence"):
        MomentRelation("r5", "p1", "m_1", "m_2", "CAUSES", confidence=-0.1)


# ── Persistence ──────────────────────────────────────────────────────────────


def test_relation_persistence_and_directed_behavior(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)

    rel = upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id,
                                 target_moment_id=moments[1].moment_id, relation_type="ESCALATES", weight=0.8, confidence=0.9)
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id,
                           target_moment_id=moments[2].moment_id, relation_type="CONTRASTS")

    fetched = get_moment_relation(rel.relation_id)
    assert fetched.relation_type == "ESCALATES"
    assert fetched.weight == 0.8
    assert len(list_project_moment_relations("p1")) == 2
    assert len(list_moment_outgoing_relations(moments[0].moment_id)) == 2
    assert len(list_moment_incoming_relations(moments[1].moment_id)) == 1
    assert len(list_moment_incoming_relations(moments[2].moment_id)) == 1
    assert len(list_moment_incoming_relations(moments[0].moment_id)) == 0


def test_relation_upsert_idempotent(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id,
                           relation_type="CAUSES", weight=0.5)
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id,
                           relation_type="CAUSES", weight=0.9)
    relations = list_project_moment_relations("p1")
    assert len(relations) == 1
    assert relations[0].weight == 0.9


def test_relation_delete(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)
    rel = upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id,
                                 relation_type="CAUSES")
    assert delete_moment_relation(rel.relation_id) is True
    assert get_moment_relation(rel.relation_id) is None
    assert delete_moment_relation(rel.relation_id) is False


# ── Relationship safety ──────────────────────────────────────────────────────


def test_relation_rejects_cross_project_and_missing_moments(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)
    upsert_project(project_id="p2", job_id="p2", profile="football", sport="football", display_name="B", status="READY")

    with pytest.raises(ValueError, match="cross projects"):
        upsert_moment_relation(project_id="p2", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id,
                               relation_type="CAUSES")
    with pytest.raises(ValueError, match="source moment"):
        upsert_moment_relation(project_id="p1", source_moment_id="missing", target_moment_id=moments[1].moment_id,
                               relation_type="CAUSES")


# ── Multiple relations on one moment ────────────────────────────────────────


def test_one_moment_with_multiple_relations(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id, relation_type="ESCALATES")
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[2].moment_id, relation_type="CONTRASTS")
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id, relation_type="CALLBACK_TO")

    outgoing = list_moment_outgoing_relations(moments[0].moment_id)
    assert {r.relation_type for r in outgoing} == {"ESCALATES", "CONTRASTS", "CALLBACK_TO"}


# ── Reverse not implied ──────────────────────────────────────────────────────


def test_directed_relations_are_not_symmetric(tmp_path, monkeypatch):
    moments = _moments(tmp_path, monkeypatch)
    upsert_moment_relation(project_id="p1", source_moment_id=moments[0].moment_id, target_moment_id=moments[1].moment_id, relation_type="ESCALATES")

    assert len(list_moment_outgoing_relations(moments[1].moment_id)) == 0
    assert len(list_moment_incoming_relations(moments[0].moment_id)) == 0