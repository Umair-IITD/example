"""
tests/test_sprint220_matcher_recommendation.py

Sprint 2.20: SOPMatcher + ResolutionRecommendationEngine unit tests.

Coverage (SOPMatcher):
  - returns None when no entries in repository
  - returns SOPMatch when matching entry exists
  - returns None when match score is below threshold
  - returns full KnowledgeSearchResult always
  - extracts keywords from topic + root cause
  - match_reason populated on returned match

Coverage (ResolutionRecommendationEngine):
  - with SOP match: confidence is elevated
  - without SOP match: confidence is reduced
  - escalation_required inherits from investigation_escalate=True
  - escalation_required True when low confidence and no SOP match
  - recommended_action preserved
  - sop_steps from SOP match used when available
  - default steps used when no SOP match
  - never raises on any input
  - fallback recommendation has escalation_required=True
"""
from __future__ import annotations

import pytest

from case_engine.knowledge.matcher import SOPMatcher
from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
    SOPMatch,
)
from case_engine.knowledge.recommendation import ResolutionRecommendationEngine
from case_engine.knowledge.repository import InMemoryKnowledgeRepository
from case_engine.knowledge.retriever import HybridRetriever


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(
    entry_id: str = "e-001",
    topic_keys: tuple = ("VKYC_Session_Failure",),
    root_cause_categories: tuple = ("EXPIRED_SESSION",),
    recommended_actions: tuple = ("SESSION_RESET",),
    resolution_steps: tuple = ("1. Reset session.", "2. Verify."),
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title="Reset VKYC Session",
        body="Steps to reset a failed VKYC session with expired credentials",
        entry_type=KnowledgeEntryType.SOP,
        tags=("vkyc", "session_reset", "expired_session"),
        topic_keys=topic_keys,
        root_cause_categories=root_cause_categories,
        recommended_actions=recommended_actions,
        resolution_steps=resolution_steps,
        source="manual",
        source_id=None,
        accepted_answer=True,
        score=10,
        created_at="2024-01-01T00:00:00Z",
        status=KnowledgeEntryStatus.ACTIVE,
    )


def _make_matcher(entries: list | None = None) -> SOPMatcher:
    repo = InMemoryKnowledgeRepository()
    if entries:
        repo.bulk_store(entries)
    return SOPMatcher(HybridRetriever(), repo)


def _make_sop_match_for_rec(entry: KnowledgeEntry | None = None) -> SOPMatch:
    return SOPMatch(
        match_id="m-001",
        entry=entry or _make_entry(),
        relevance_score=0.85,
        match_reason="category match",
        matched_on=("root_cause_category:EXPIRED_SESSION",),
    )


# ── SOPMatcher Tests ──────────────────────────────────────────────────────────

class TestSOPMatcherNoEntries:
    def test_returns_none_when_empty(self):
        matcher = _make_matcher()
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop is None

    def test_returns_search_result_when_empty(self):
        matcher = _make_matcher()
        _, result = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert result is not None

    def test_search_result_has_zero_matches_when_empty(self):
        matcher = _make_matcher()
        _, result = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert result.total_found == 0


class TestSOPMatcherWithEntries:
    def test_returns_sop_match_for_matching_entry(self):
        matcher = _make_matcher([_make_entry()])
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop is not None

    def test_sop_match_has_correct_entry(self):
        matcher = _make_matcher([_make_entry("e-123")])
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop.entry.entry_id == "e-123"

    def test_sop_match_has_relevance_score(self):
        matcher = _make_matcher([_make_entry()])
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop.relevance_score > 0

    def test_sop_match_reason_not_empty(self):
        matcher = _make_matcher([_make_entry()])
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop.match_reason != ""

    def test_returns_none_for_non_matching_topic(self):
        matcher = _make_matcher([_make_entry(
            topic_keys=("OTP_Delivery_Failure",),
            root_cause_categories=("SMS_DELIVERY_FAILURE",),
            recommended_actions=("OTP_RESEND",),
        )])
        sop, _ = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert sop is None

    def test_match_with_recommended_action(self):
        matcher = _make_matcher([_make_entry()])
        sop, _ = matcher.match(
            "VKYC_Session_Failure", "EXPIRED_SESSION", "SESSION_RESET"
        )
        assert sop is not None

    def test_search_result_total_found(self):
        matcher = _make_matcher([_make_entry("e-1"), _make_entry("e-2")])
        _, result = matcher.match("VKYC_Session_Failure", "EXPIRED_SESSION")
        assert result.total_found == 2


# ── ResolutionRecommendationEngine Tests ──────────────────────────────────────

class TestResolutionRecommendationWithMatch:
    def setup_method(self):
        self.engine = ResolutionRecommendationEngine()
        self.sop_match = _make_sop_match_for_rec()

    def test_returns_recommendation(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=self.sop_match,
        )
        assert rec is not None

    def test_confidence_is_elevated_with_sop_match(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=self.sop_match,
        )
        assert rec.confidence > 0.85 * 0.7  # higher than no-match case

    def test_sop_steps_from_match_used(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=self.sop_match,
        )
        assert len(rec.sop_steps) > 0

    def test_source_entry_ids_populated(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=self.sop_match,
        )
        assert len(rec.source_entry_ids) > 0

    def test_no_escalation_when_investigation_not_escalated(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=self.sop_match,
        )
        assert rec.escalation_required is False

    def test_escalation_when_investigation_escalated(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=True,
            sop_match=self.sop_match,
        )
        assert rec.escalation_required is True


class TestResolutionRecommendationNoMatch:
    def setup_method(self):
        self.engine = ResolutionRecommendationEngine()

    def test_confidence_reduced_without_sop(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=None,
        )
        assert rec.confidence < 0.85  # reduced without SOP backing

    def test_default_steps_used_for_session_reset(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=None,
        )
        assert len(rec.sop_steps) > 0

    def test_escalation_when_very_low_confidence_no_sop(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="UNKNOWN",
            recommended_action="MANUAL_REVIEW",
            investigation_confidence=0.10,
            investigation_escalate=False,
            sop_match=None,
        )
        assert rec.escalation_required is True

    def test_no_source_entry_ids_without_sop(self):
        rec = self.engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.85,
            investigation_escalate=False,
            sop_match=None,
        )
        assert len(rec.source_entry_ids) == 0

    def test_recommended_action_preserved(self):
        rec = self.engine.recommend(
            topic="OTP_Delivery_Failure",
            root_cause_category="SMS_DELIVERY_FAILURE",
            recommended_action="OTP_RESEND",
            investigation_confidence=0.80,
            investigation_escalate=False,
            sop_match=None,
        )
        assert rec.recommended_action == "OTP_RESEND"


class TestResolutionRecommendationRobustness:
    def test_never_raises(self):
        engine = ResolutionRecommendationEngine()
        rec = engine.recommend(
            topic="",
            root_cause_category="",
            recommended_action="",
            investigation_confidence=0.0,
            investigation_escalate=False,
            sop_match=None,
        )
        assert rec is not None

    def test_to_dict_json_serializable(self):
        import json
        engine = ResolutionRecommendationEngine()
        rec = engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.9,
            investigation_escalate=False,
            sop_match=_make_sop_match_for_rec(),
        )
        serialized = json.dumps(rec.to_dict())
        assert len(serialized) > 0
