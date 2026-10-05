from __future__ import annotations

import json

import pytest

from pipeline.moment_adapter import adapt_detection_to_moments
from pipeline.operator_console import analyze_project, get_project, review_moment
from pipeline.runtime_service import (
    get_project_runtime_summary,
    index_existing_project,
    list_project_moments,
    register_artifact,
    update_moment_review_state,
    update_project_moment_review_state,
    upsert_moment,
)
from pipeline.console_server import ConsoleHandler
from tests.test_operator_console import _FakeHandler, _html_body, _json_body
from tests.test_runtime_managed_analysis import _make_job, _patch_success


def _moment(tmp_path, monkeypatch, *, category="GOAL", clip_id="clip_001", start=1, end=5):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    moment = adapt_detection_to_moments(
        [{"clip_id": clip_id, "category": category, "start_time": start, "end_time": end}],
        project_id=project.project_id,
        source_artifact_id=artifact.artifact_id,
    )[0]
    return job, jobs_dir, project, upsert_moment(moment)


def test_review_state_transitions_and_metadata(tmp_path, monkeypatch):
    _job, _jobs_dir, _project, moment = _moment(tmp_path, monkeypatch)

    keep = update_moment_review_state(moment.moment_id, "KEEP", reviewed_by="operator")
    strong = update_moment_review_state(moment.moment_id, "STRONG", reviewed_by="operator")
    must = update_moment_review_state(moment.moment_id, "MUST_USE", reviewed_by="operator")
    reject = update_moment_review_state(moment.moment_id, "REJECT", reviewed_by="operator")
    unreviewed = update_moment_review_state(moment.moment_id, "UNREVIEWED", reviewed_by="operator")

    assert keep.review_state == "KEEP"
    assert keep.reviewed_at
    assert keep.reviewed_by == "operator"
    assert strong.review_state == "STRONG"
    assert must.review_state == "MUST_USE"
    assert reject.review_state == "REJECT"
    assert unreviewed.review_state == "UNREVIEWED"
    assert unreviewed.reviewed_at is None
    assert unreviewed.reviewed_by is None


def test_invalid_review_state_and_wrong_project_rejected(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moment = _moment(tmp_path, monkeypatch)

    with pytest.raises(ValueError, match="review_state"):
        update_moment_review_state(moment.moment_id, "MAYBE")
    with pytest.raises(ValueError, match="does not belong"):
        update_project_moment_review_state("other_project", moment.moment_id, "KEEP")
    assert update_project_moment_review_state(project.project_id, moment.moment_id, "KEEP").review_state == "KEEP"


def test_review_state_survives_moment_upsert_renormalization(tmp_path, monkeypatch):
    _job, _jobs_dir, project, moment = _moment(tmp_path, monkeypatch)
    reviewed = update_moment_review_state(moment.moment_id, "MUST_USE", reviewed_by="operator")

    renormalized = adapt_detection_to_moments(
        [{"clip_id": "clip_001", "category": "GOAL", "start_time": 1, "end_time": 5, "virality_score": "75"}],
        project_id=project.project_id,
        source_artifact_id=moment.source_artifact_id,
    )[0]
    upsert_moment(renormalized)

    after = list_project_moments(project.project_id)[0]
    assert after.moment_id == reviewed.moment_id
    assert after.review_state == "MUST_USE"
    assert after.reviewed_at == reviewed.reviewed_at


def test_filtering_counts_and_chronological_order(tmp_path, monkeypatch):
    job, jobs_dir, project, first = _moment(tmp_path, monkeypatch, category="GOAL", clip_id="clip_001", start=20, end=25)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "more.json")
    for row in [
        {"clip_id": "clip_002", "category": "SAVE", "start_time": 10, "end_time": 12},
        {"clip_id": "clip_003", "category": "FOUL", "start_time": 20, "end_time": 22},
        {"clip_id": "clip_004", "category": "RED_CARD", "start_time": 30, "end_time": 35},
    ]:
        upsert_moment(adapt_detection_to_moments([row], project_id=project.project_id, source_artifact_id=artifact.artifact_id)[0])

    moments = list_project_moments(project.project_id)
    update_moment_review_state(moments[0].moment_id, "KEEP")
    update_moment_review_state(moments[1].moment_id, "REJECT")
    update_moment_review_state(moments[2].moment_id, "STRONG")
    update_moment_review_state(moments[3].moment_id, "MUST_USE")
    summary = get_project_runtime_summary(job["job_id"], jobs_dir=jobs_dir)["moment_summary"]

    assert [m.start_seconds for m in moments] == [10.0, 20.0, 20.0, 30.0]
    assert len(list_project_moments(project.project_id, review_state="KEEP")) == 1
    assert len(list_project_moments(project.project_id, review_state="REJECT")) == 1
    assert len(list_project_moments(project.project_id, review_state="STRONG")) == 1
    assert len(list_project_moments(project.project_id, review_state="MUST_USE")) == 1
    assert summary["moment_count"] == 4
    assert summary["reviewed_count"] == 4
    assert summary["unreviewed_count"] == 0
    assert summary["keep_count"] == 1
    assert summary["reject_count"] == 1
    assert summary["strong_count"] == 1
    assert summary["must_use_count"] == 1


def test_console_project_detail_displays_review_surface_and_empty_state(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    fake = _FakeHandler()

    ConsoleHandler._render_project_detail(fake, job["job_id"])
    empty_html = _html_body(fake)
    assert "No canonical moments are available for review yet." in empty_html

    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    artifact = register_artifact(project_id=project.project_id, artifact_type="analysis_moments", path=tmp_path / "moments.json")
    upsert_moment(adapt_detection_to_moments(
        [{"clip_id": "clip_001", "category": "GOAL", "start_time": 1, "end_time": 5}],
        project_id=project.project_id,
        source_artifact_id=artifact.artifact_id,
    )[0])
    fake = _FakeHandler()
    ConsoleHandler._render_project_detail(fake, job["job_id"])
    html = _html_body(fake)
    assert "Review Moments" in html
    assert "Must Use" in html
    assert "SCORE" in html
    assert "GOAL" in html


def test_console_review_action_updates_runtime_state(tmp_path, monkeypatch):
    job, jobs_dir, _project, moment = _moment(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    body = json.dumps({"review_state": "KEEP", "reviewed_by": "operator"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/moments/{moment.moment_id}/review")

    ConsoleHandler.do_POST(fake)

    response = _json_body(fake)
    assert fake.status == 200
    assert response["ok"] is True
    assert response["moment"]["review_state"] == "KEEP"
    detail = get_project(job["job_id"], jobs_dir=jobs_dir)
    assert detail["runtime"]["moment_summary"]["keep_count"] == 1


def test_console_invalid_review_action_fails_safely(tmp_path, monkeypatch):
    job, jobs_dir, _project, moment = _moment(tmp_path, monkeypatch)
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs_dir))
    body = json.dumps({"review_state": "BAD"}).encode("utf-8")
    fake = _FakeHandler(body, path=f"/api/projects/{job['job_id']}/moments/{moment.moment_id}/review")

    ConsoleHandler.do_POST(fake)

    assert fake.status == 400
    assert _json_body(fake)["ok"] is False


def test_review_flow_after_managed_analysis_preserves_raw_artifact(tmp_path, monkeypatch):
    job, jobs_dir, _source, _db_path = _make_job(tmp_path, monkeypatch)
    _patch_success(monkeypatch, tmp_path, transcript_reused=True)
    analyze_project(job["job_id"], jobs_dir=jobs_dir, dry_run=True)
    project = index_existing_project(job["job_id"], jobs_dir=jobs_dir)
    moments = list_project_moments(project.project_id)
    raw_path = next(a["path"] for a in get_project(job["job_id"], jobs_dir=jobs_dir)["runtime"]["artifacts"] if a["artifact_type"] == "analysis_moments")
    before = open(raw_path, "rb").read()

    review_moment(job["job_id"], moments[0].moment_id, "KEEP", reviewed_by="operator", jobs_dir=jobs_dir)
    if len(moments) == 1:
        review_moment(job["job_id"], moments[0].moment_id, "MUST_USE", reviewed_by="operator", jobs_dir=jobs_dir)
    summary = get_project(job["job_id"], jobs_dir=jobs_dir)["runtime"]["moment_summary"]
    after = open(raw_path, "rb").read()

    assert summary["reviewed_count"] == 1
    assert summary["unreviewed_count"] == 0
    assert summary["must_use_count"] in (0, 1)
    assert before == after
