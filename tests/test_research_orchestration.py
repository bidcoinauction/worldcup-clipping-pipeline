from __future__ import annotations

import json

from pipeline.match_identification_service import MatchIdentificationService, identify_match_from_text
from pipeline.research_first_orchestrator import ResearchFirstOrchestrator, SourceCoverage
from pipeline.match_identification_service import MatchCandidate
from pipeline.research_provider import ExternalResearchProvider, FixtureResearchProvider, normalize_event_type, normalize_research_fixture
from pipeline.runtime_service import index_existing_project, register_artifact, list_project_research, list_project_moments
from pipeline.operator_console import create_project
from tests.test_pilot_intake import build_intake


def test_filename_candidate_extraction_high_and_partial():
    candidate = identify_match_from_text("italy_germany_2006_part1.mp4", user_hint="Italy vs Germany 2006")
    assert candidate.team_a == "Italy"
    assert candidate.team_b == "Germany"
    assert candidate.season == "2006"
    assert candidate.confidence == "HIGH"
    assert {e["type"] for e in candidate.evidence} >= {"filename_or_metadata", "user_hint", "source_scope"}


def test_low_confidence_requires_hint():
    candidate = identify_match_from_text("unknown_source.mp4")
    assert candidate.confidence == "LOW"


def test_research_normalization_uses_canonical_taxonomy():
    assert normalize_event_type("goal") == "SCORE"
    assert normalize_event_type("send-off") == "CARD"
    assert normalize_event_type("confrontation") == "CONFRONTATION"


def test_orchestrator_idempotent_research_and_source_specific_alignment(tmp_path, monkeypatch):
    jobs = tmp_path / "jobs"
    monkeypatch.setenv("STADIUM_PILOT_JOBS_DIR", str(jobs))
    source = tmp_path / "portugal_netherlands_2006.mp4"
    source.write_bytes(b"fake")
    intake = build_intake(str(source), overrides={"pilot": {"pilot_id": "portugal_netherlands_2006"}, "media": {"source_id": "source"}, "rights": {"status": "CONFIRMED", "permitted_uses": ["review"], "confirmation_statement": "test", "confirmed_by": "test", "confirmation_date": "2026-10-05"}})
    job = create_project(intake, jobs_dir=jobs)
    project = index_existing_project(job["job_id"], jobs_dir=jobs)
    artifact = register_artifact(project_id=project.project_id, artifact_type="source_media", path=source, metadata={"duration_seconds": 7000})
    fixture = tmp_path / "research.json"
    fixture.write_text(json.dumps({
        "sport": "football", "competition": "2006 FIFA World Cup", "match_date": "2006-06-25",
        "home_team": "Portugal", "away_team": "Netherlands", "home_score": 1, "away_score": 0,
        "events": [{"match_minute": 23, "sport_event_type": "goal", "team": "Portugal", "headline": "Maniche scores", "participants": [{"name": "Maniche"}]}],
    }), encoding="utf-8")
    orchestrator = ResearchFirstOrchestrator(provider=FixtureResearchProvider([fixture]))
    coverage = SourceCoverage(source_duration_seconds=7000, kickoff_media_offset_seconds=420, coverage_confidence="MEDIUM")
    result1 = orchestrator.run(project.project_id, artifact.artifact_id, user_hint="Portugal vs Netherlands 2006", coverage=coverage)
    result2 = orchestrator.run(project.project_id, artifact.artifact_id, user_hint="Portugal vs Netherlands 2006", coverage=coverage)
    assert result1["ok"] is True
    assert result2["ok"] is True
    assert len(list_project_research(project.project_id)) == 1
    assert len(list_project_moments(project.project_id)) == 1
    assert result1["full_match_transcription_used"] is False
    assert result1["full_match_detection_used"] is False


class _FakeExternalProvider(ExternalResearchProvider):
    def __init__(self, *, results, page):
        super().__init__(enabled=True, timeout_seconds=1, user_agent="test")
        self.queries = []
        self._results = results
        self._page_data = page

    def _search(self, query: str):
        self.queries.append(query)
        return self._results

    def _page(self, title: str):
        return self._page_data


def test_external_provider_configuration_and_unavailable(monkeypatch):
    monkeypatch.delenv("RESEARCH_PROVIDER", raising=False)
    result = ExternalResearchProvider().research(MatchCandidate(team_a="A", team_b="B", season="2000"))
    assert result.ok is False
    assert result.readiness == "NOT_CONFIGURED"


def test_external_provider_request_construction_and_portugal_normalization():
    page = {
        "title": "Battle of Nuremberg (2006 FIFA World Cup)",
        "extract": "The Battle of Nuremberg was Portugal vs Netherlands. Maniche scored. Costinha, Boulahrouz, Deco and Giovanni van Bronckhorst were sent off.",
        "revisions": [{"slots": {"main": {"content": "| event = [[2006 FIFA World Cup]]<br> Round of 16\n| team1 = [[Portugal national football team|Portugal]]\n| team1score = 1\n| team2 = [[Netherlands national football team|Netherlands]]\n| team2score = 0\n| date = 25 June 2006\n| stadium = [[Max-Morlock-Stadion|Frankenstadion]]"}}}],
    }
    provider = _FakeExternalProvider(results=[{"title": page["title"], "snippet": "Portugal Netherlands 2006 World Cup"}], page=page)
    result = provider.research(MatchCandidate(team_a="Portugal", team_b="Netherlands", season="2006", competition="2006 FIFA World Cup", confidence="HIGH"))
    assert result.ok is True
    assert "Portugal Netherlands 2006" in provider.queries[0]
    fixture = result.fixture
    assert fixture["competition"] == "2006 FIFA World Cup"
    assert fixture["stage"] == "Round of 16"
    assert fixture["home_score"] == 1 and fixture["away_score"] == 0
    assert any(event["universal_event_type"] == "SCORE" for event in fixture["events"])
    assert any((event["metadata"]["event_position"]["display_label"] == "45+1") for event in fixture["events"])
    assert fixture["metadata"]["corroboration_state"] == "single-source"


def test_external_provider_ambiguous_and_partial_behavior():
    candidate = MatchCandidate(team_a="Italy", team_b="Germany", season="2006", competition="2006 FIFA World Cup", confidence="HIGH")
    ambiguous = _FakeExternalProvider(results=[{"title": "Italy national football team", "snippet": "Italy Germany 2006 World Cup"}, {"title": "Germany national football team", "snippet": "Italy Germany 2006 World Cup"}], page={"title": "x", "extract": ""}).research(candidate)
    assert ambiguous.ok is False
    assert ambiguous.readiness == "AMBIGUOUS"
    partial = _FakeExternalProvider(results=[{"title": "Italy Germany", "snippet": "Italy Germany 2006"}], page={"title": "Italy Germany", "extract": "Italy and Germany played.", "revisions": []}).research(candidate)
    assert partial.ok is False
    assert partial.readiness == "PARTIAL"


def test_basketball_event_position_and_native_type_survive_normalization():
    fixture = normalize_research_fixture({
        "sport": "basketball", "competition": "NBA Finals", "season": "2010", "home_team": "Lakers", "away_team": "Celtics",
        "events": [{"match_minute": 0, "sport_event_type": "three_pointer", "native_event_type": "three_pointer", "headline": "Late three", "metadata": {"event_position": {"sport": "basketball", "period": "Q4", "clock": "01:24", "display_label": "Q4 01:24"}}}],
    }, provider="fixture", cache_key="basketball|lakers|celtics|2010")
    event = fixture["events"][0]
    assert event["native_event_type"] == "three_pointer"
    assert event["metadata"]["event_position"]["display_label"] == "Q4 01:24"
    assert event["metadata"]["semantic_kind"] == "historical_fact"
