"""
tests/test_sprint220_service.py

Sprint 2.20: KnowledgeService unit tests.

Coverage:
  - search() returns a dict
  - search() result_id is populated
  - search() topic preserved in result
  - search() with investigation_result extracts root cause
  - search() without investigation_result still runs
  - search() sop_match_found reflects match
  - search() recommendation always present
  - search() result is JSON-serializable
  - search() never raises
  - build_knowledge_service() factory creates a ready service
  - build_knowledge_service() with seed_entries seeds repository
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from case_engine.knowledge import build_knowledge_service
from case_engine.knowledge.matcher import SOPMatcher
from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
)
from case_engine.knowledge.recommendation import ResolutionRecommendationEngine
from case_engine.knowledge.repository import InMemoryKnowledgeRepository
from case_engine.knowledge.retriever import HybridRetriever
from case_engine.knowledge.service import KnowledgeService


def _make_entry(
    entry_id: str = "e-001",
    topic_keys: tuple = ("VKYC_Session_Failure",),
    root_cause_categories: tuple = ("EXPIRED_SESSION",),
    recommended_actions: tuple = ("SESSION_RESET",),
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title="Reset VKYC Session SOP",
        body="1. Navigate to Admin Portal.\n2. Reset session.",
        entry_type=KnowledgeEntryType.SOP,
        tags=("vkyc", "session_reset", "expired_session"),
        topic_keys=topic_keys,
        root_cause_categories=root_cause_categories,
        recommended_actions=recommended_actions,
        resolution_steps=("1. Navigate to Admin Portal.", "2. Reset session."),
        source="manual",
        source_id=None,
        accepted_answer=True,
        score=10,
        created_at="2024-01-01T00:00:00Z",
        status=KnowledgeEntryStatus.ACTIVE,
    )


def _make_service(entries: list | None = None) -> KnowledgeService:
    repo = InMemoryKnowledgeRepository()
    if entries:
        repo.bulk_store(entries)
    retriever  = HybridRetriever()
    matcher    = SOPMatcher(retriever, repo)
    rec_engine = ResolutionRecommendationEngine()
    return KnowledgeService(matcher=matcher, recommendation_engine=rec_engine)


_INVESTIGATION_RESULT = {
    "result_id":   "inv-001",
    "root_cause": {
        "category":           "EXPIRED_SESSION",
        "confidence":         0.90,
        "recommended_action": "SESSION_RESET",
        "escalate":           False,
    },
    "observation": "VKYC session expired.",
}


class TestKnowledgeServiceReturnShape:
    def setup_method(self):
        self.svc = _make_service([_make_entry()])

    def test_returns_dict(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert isinstance(result, dict)

    def test_result_id_present(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result.get("result_id")

    def test_topic_preserved(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["topic"] == "VKYC_Session_Failure"

    def test_sop_match_found_is_bool(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert isinstance(result["sop_match_found"], bool)

    def test_recommendation_present(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert "recommendation" in result
        assert result["recommendation"] is not None

    def test_search_result_present(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert "search_result" in result

    def test_result_json_serializable(self):
        result = self.svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        serialized = json.dumps(result)
        assert len(serialized) > 0


class TestKnowledgeServiceMatchBehavior:
    def test_sop_match_found_when_entry_matches(self):
        svc = _make_service([_make_entry()])
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["sop_match_found"] is True

    def test_sop_match_none_when_no_entries(self):
        svc = _make_service()
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["sop_match_found"] is False

    def test_sop_match_has_entry_when_found(self):
        svc = _make_service([_make_entry("e-001")])
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        if result["sop_match_found"]:
            assert result["sop_match"]["entry"]["entry_id"] == "e-001"

    def test_search_without_investigation_result(self):
        svc = _make_service([_make_entry()])
        result = svc.search("VKYC_Session_Failure", None)
        assert isinstance(result, dict)
        assert "recommendation" in result

    def test_search_empty_investigation_result(self):
        svc = _make_service()
        result = svc.search("VKYC_Session_Failure", {})
        assert isinstance(result, dict)


class TestKnowledgeServiceRobustness:
    def test_never_raises_on_crash(self):
        bad_matcher = MagicMock()
        bad_matcher.match.side_effect = RuntimeError("matcher crashed")
        svc = KnowledgeService(
            matcher=bad_matcher,
            recommendation_engine=ResolutionRecommendationEngine(),
        )
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert isinstance(result, dict)

    def test_error_result_has_escalation(self):
        bad_matcher = MagicMock()
        bad_matcher.match.side_effect = RuntimeError("crash")
        svc = KnowledgeService(
            matcher=bad_matcher,
            recommendation_engine=ResolutionRecommendationEngine(),
        )
        result = svc.search("VKYC_Session_Failure", None)
        assert result["recommendation"]["escalation_required"] is True

    def test_search_with_case_no_audit_does_not_raise(self):
        svc = _make_service([_make_entry()])
        case = MagicMock()
        case.case_id = "case-svc-001"
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT, case=case)
        assert result is not None

    def test_audit_logger_called_on_match(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry())
        retriever  = HybridRetriever()
        matcher    = SOPMatcher(retriever, repo)
        rec_engine = ResolutionRecommendationEngine()
        audit = MagicMock()
        svc = KnowledgeService(matcher=matcher, recommendation_engine=rec_engine, audit_logger=audit)
        case = MagicMock()
        case.case_id = "case-audit"
        svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT, case=case)
        assert audit.log_sop_match_found.called or audit.log_sop_match_not_found.called


class TestBuildKnowledgeServiceFactory:
    def test_factory_returns_service(self):
        svc = build_knowledge_service()
        assert isinstance(svc, KnowledgeService)

    def test_factory_with_seed_entries(self):
        entries = [_make_entry("e-seed")]
        svc = build_knowledge_service(seed_entries=entries)
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["sop_match_found"] is True

    def test_factory_empty_by_default(self):
        svc = build_knowledge_service()
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["sop_match_found"] is False

    def test_factory_with_custom_repository(self):
        repo = InMemoryKnowledgeRepository()
        repo.store(_make_entry("e-custom"))
        svc = build_knowledge_service(repository=repo)
        result = svc.search("VKYC_Session_Failure", _INVESTIGATION_RESULT)
        assert result["sop_match_found"] is True
