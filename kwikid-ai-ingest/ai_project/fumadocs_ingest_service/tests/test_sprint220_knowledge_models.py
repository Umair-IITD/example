"""
tests/test_sprint220_knowledge_models.py

Sprint 2.20: Knowledge Layer domain model tests.

Coverage:
  - KnowledgeEntryType enum values
  - KnowledgeEntryStatus enum values
  - KnowledgeEntry construction and to_dict()
  - KnowledgeSearchQuery construction and to_dict()
  - SOPMatch construction and to_dict()
  - KnowledgeSearchResult construction and to_dict()
  - ResolutionRecommendation construction and to_dict()
  - KnowledgeResult construction and to_dict()
  - JSON round-trip for all models
  - WorkflowStepType.KNOWLEDGE_LOOKUP exists
  - WorkflowExecutionResult.knowledge_result defaults to None
  - WorkflowExecutionResult.to_dict() includes knowledge_result
  - WorkflowExecutionResult.from_dict() restores knowledge_result
"""
from __future__ import annotations

import json
import pytest

from case_engine.knowledge.models import (
    KnowledgeEntry,
    KnowledgeEntryStatus,
    KnowledgeEntryType,
    KnowledgeResult,
    KnowledgeSearchQuery,
    KnowledgeSearchResult,
    ResolutionRecommendation,
    SOPMatch,
)
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowStepType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_entry(
    entry_id: str = "e-001",
    title: str = "Reset VKYC Session",
    topic_keys: tuple = ("VKYC_Session_Failure",),
    root_cause_categories: tuple = ("EXPIRED_SESSION",),
    recommended_actions: tuple = ("SESSION_RESET",),
) -> KnowledgeEntry:
    return KnowledgeEntry(
        entry_id=entry_id,
        title=title,
        body="1. Navigate to Admin Portal\n2. Find session\n3. Reset",
        entry_type=KnowledgeEntryType.SOP,
        tags=("vkyc", "session_reset"),
        topic_keys=topic_keys,
        root_cause_categories=root_cause_categories,
        recommended_actions=recommended_actions,
        resolution_steps=("1. Navigate to Admin Portal.", "2. Reset session."),
        source="stackoverflow_teams",
        source_id="12345",
        accepted_answer=True,
        score=10,
        created_at="2024-01-15T10:00:00Z",
        status=KnowledgeEntryStatus.ACTIVE,
    )


def _make_query() -> KnowledgeSearchQuery:
    return KnowledgeSearchQuery(
        query_id="q-001",
        topic="VKYC_Session_Failure",
        root_cause_category="EXPIRED_SESSION",
        recommended_action="SESSION_RESET",
        keywords=("vkyc", "session", "expired"),
        created_at="2024-01-15T10:00:00Z",
    )


def _make_sop_match(entry: KnowledgeEntry | None = None) -> SOPMatch:
    return SOPMatch(
        match_id="m-001",
        entry=entry or _make_entry(),
        relevance_score=0.85,
        match_reason="root_cause_category:EXPIRED_SESSION; topic_key:VKYC_Session_Failure",
        matched_on=("root_cause_category:EXPIRED_SESSION", "topic_key:VKYC_Session_Failure"),
    )


def _make_search_result(top_match: SOPMatch | None = None) -> KnowledgeSearchResult:
    m = top_match or _make_sop_match()
    return KnowledgeSearchResult(
        result_id="sr-001",
        query=_make_query(),
        matches=(m,),
        top_match=m,
        total_found=1,
        searched_at="2024-01-15T10:00:00Z",
    )


def _make_recommendation() -> ResolutionRecommendation:
    return ResolutionRecommendation(
        recommendation_id="rec-001",
        topic="VKYC_Session_Failure",
        root_cause_category="EXPIRED_SESSION",
        recommended_action="SESSION_RESET",
        confidence=0.90,
        explanation="Root cause matched SOP.",
        sop_steps=("1. Navigate to Admin Portal.", "2. Reset session."),
        escalation_required=False,
        source_entry_ids=("e-001",),
        created_at="2024-01-15T10:00:00Z",
    )


def _make_knowledge_result() -> KnowledgeResult:
    sr = _make_search_result()
    return KnowledgeResult(
        result_id="kr-001",
        topic="VKYC_Session_Failure",
        search_result=sr,
        sop_match=sr.top_match,
        recommendation=_make_recommendation(),
        sop_match_found=True,
        completed_at="2024-01-15T10:01:00Z",
    )


# ── KnowledgeEntryType ────────────────────────────────────────────────────────

class TestKnowledgeEntryType:
    def test_sop_value(self):
        assert KnowledgeEntryType.SOP.value == "SOP"

    def test_faq_value(self):
        assert KnowledgeEntryType.FAQ.value == "FAQ"

    def test_runbook_value(self):
        assert KnowledgeEntryType.RUNBOOK.value == "RUNBOOK"

    def test_known_issue_value(self):
        assert KnowledgeEntryType.KNOWN_ISSUE.value == "KNOWN_ISSUE"

    def test_engineering_fix_value(self):
        assert KnowledgeEntryType.ENGINEERING_FIX.value == "ENGINEERING_FIX"

    def test_historical_resolution_value(self):
        assert KnowledgeEntryType.HISTORICAL_RESOLUTION.value == "HISTORICAL_RESOLUTION"

    def test_six_entry_types(self):
        assert len(KnowledgeEntryType) == 6


# ── KnowledgeEntryStatus ──────────────────────────────────────────────────────

class TestKnowledgeEntryStatus:
    def test_active(self):
        assert KnowledgeEntryStatus.ACTIVE.value == "ACTIVE"

    def test_deprecated(self):
        assert KnowledgeEntryStatus.DEPRECATED.value == "DEPRECATED"

    def test_archived(self):
        assert KnowledgeEntryStatus.ARCHIVED.value == "ARCHIVED"


# ── KnowledgeEntry ────────────────────────────────────────────────────────────

class TestKnowledgeEntry:
    def test_construction(self):
        entry = _make_entry()
        assert entry.entry_id == "e-001"
        assert entry.title == "Reset VKYC Session"
        assert entry.status == KnowledgeEntryStatus.ACTIVE

    def test_to_dict_has_required_keys(self):
        d = _make_entry().to_dict()
        for key in ("entry_id", "title", "body", "entry_type", "tags",
                    "topic_keys", "root_cause_categories", "recommended_actions",
                    "resolution_steps", "source", "source_id", "accepted_answer",
                    "score", "created_at", "status"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_entry_type_is_string(self):
        assert _make_entry().to_dict()["entry_type"] == "SOP"

    def test_to_dict_status_is_string(self):
        assert _make_entry().to_dict()["status"] == "ACTIVE"

    def test_to_dict_tags_is_list(self):
        assert isinstance(_make_entry().to_dict()["tags"], list)

    def test_to_dict_topic_keys_is_list(self):
        assert isinstance(_make_entry().to_dict()["topic_keys"], list)

    def test_to_dict_json_serializable(self):
        serialized = json.dumps(_make_entry().to_dict())
        assert len(serialized) > 0

    def test_immutable(self):
        entry = _make_entry()
        with pytest.raises((AttributeError, TypeError)):
            entry.title = "Changed"  # type: ignore[misc]


# ── KnowledgeSearchQuery ──────────────────────────────────────────────────────

class TestKnowledgeSearchQuery:
    def test_construction(self):
        q = _make_query()
        assert q.topic == "VKYC_Session_Failure"
        assert q.root_cause_category == "EXPIRED_SESSION"

    def test_to_dict_keys(self):
        d = _make_query().to_dict()
        for key in ("query_id", "topic", "root_cause_category", "recommended_action",
                    "keywords", "created_at"):
            assert key in d

    def test_to_dict_keywords_is_list(self):
        assert isinstance(_make_query().to_dict()["keywords"], list)

    def test_none_root_cause_allowed(self):
        q = KnowledgeSearchQuery(
            query_id="q-x", topic="VKYC_Session_Failure",
            root_cause_category=None, recommended_action=None,
            keywords=(), created_at="2024-01-15T10:00:00Z",
        )
        assert q.root_cause_category is None
        assert q.to_dict()["root_cause_category"] is None


# ── SOPMatch ──────────────────────────────────────────────────────────────────

class TestSOPMatch:
    def test_construction(self):
        m = _make_sop_match()
        assert m.relevance_score == 0.85
        assert isinstance(m.entry, KnowledgeEntry)

    def test_to_dict_has_match_id(self):
        assert "match_id" in _make_sop_match().to_dict()

    def test_to_dict_has_entry(self):
        assert "entry" in _make_sop_match().to_dict()

    def test_to_dict_nested_entry_is_dict(self):
        assert isinstance(_make_sop_match().to_dict()["entry"], dict)

    def test_to_dict_json_serializable(self):
        serialized = json.dumps(_make_sop_match().to_dict())
        assert len(serialized) > 0

    def test_matched_on_is_list(self):
        assert isinstance(_make_sop_match().to_dict()["matched_on"], list)


# ── KnowledgeSearchResult ─────────────────────────────────────────────────────

class TestKnowledgeSearchResult:
    def test_construction(self):
        sr = _make_search_result()
        assert sr.total_found == 1
        assert sr.top_match is not None

    def test_to_dict_keys(self):
        d = _make_search_result().to_dict()
        for key in ("result_id", "query", "matches", "top_match", "total_found", "searched_at"):
            assert key in d

    def test_matches_is_list(self):
        assert isinstance(_make_search_result().to_dict()["matches"], list)

    def test_no_match_result(self):
        sr = KnowledgeSearchResult(
            result_id="sr-x", query=_make_query(), matches=(),
            top_match=None, total_found=0,
            searched_at="2024-01-15T10:00:00Z",
        )
        assert sr.to_dict()["top_match"] is None

    def test_json_serializable(self):
        serialized = json.dumps(_make_search_result().to_dict())
        assert len(serialized) > 0


# ── ResolutionRecommendation ──────────────────────────────────────────────────

class TestResolutionRecommendation:
    def test_construction(self):
        rec = _make_recommendation()
        assert rec.confidence == 0.90
        assert rec.escalation_required is False

    def test_to_dict_keys(self):
        d = _make_recommendation().to_dict()
        for key in ("recommendation_id", "topic", "root_cause_category",
                    "recommended_action", "confidence", "explanation",
                    "sop_steps", "escalation_required", "source_entry_ids", "created_at"):
            assert key in d

    def test_sop_steps_is_list(self):
        assert isinstance(_make_recommendation().to_dict()["sop_steps"], list)

    def test_json_serializable(self):
        serialized = json.dumps(_make_recommendation().to_dict())
        assert len(serialized) > 0


# ── KnowledgeResult ───────────────────────────────────────────────────────────

class TestKnowledgeResult:
    def test_construction(self):
        kr = _make_knowledge_result()
        assert kr.sop_match_found is True
        assert kr.sop_match is not None

    def test_to_dict_keys(self):
        d = _make_knowledge_result().to_dict()
        for key in ("result_id", "topic", "search_result", "sop_match",
                    "recommendation", "sop_match_found", "completed_at"):
            assert key in d

    def test_to_dict_json_serializable(self):
        d = _make_knowledge_result().to_dict()
        serialized = json.dumps(d)
        assert len(serialized) > 0

    def test_no_match_result(self):
        sr = KnowledgeSearchResult(
            result_id="sr-x", query=_make_query(), matches=(),
            top_match=None, total_found=0, searched_at="2024-01-15T10:00:00Z",
        )
        kr = KnowledgeResult(
            result_id="kr-x", topic="VKYC_Session_Failure",
            search_result=sr, sop_match=None,
            recommendation=_make_recommendation(),
            sop_match_found=False,
            completed_at="2024-01-15T10:01:00Z",
        )
        assert kr.to_dict()["sop_match"] is None
        assert kr.sop_match_found is False


# ── WorkflowStepType integration ──────────────────────────────────────────────

class TestWorkflowStepTypeKnowledgeLookup:
    def test_knowledge_lookup_exists(self):
        assert hasattr(WorkflowStepType, "KNOWLEDGE_LOOKUP")

    def test_knowledge_lookup_value(self):
        assert WorkflowStepType.KNOWLEDGE_LOOKUP.value == "KNOWLEDGE_LOOKUP"

    def test_eight_step_types(self):
        # Sprint 2.25 added CLARIFY — now 12 step types
        assert len(WorkflowStepType) == 12


# ── WorkflowExecutionResult integration ───────────────────────────────────────

class TestWorkflowExecutionResultKnowledgeResult:
    def test_knowledge_result_default_none(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        assert wer.knowledge_result is None

    def test_to_dict_includes_knowledge_result(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        assert "knowledge_result" in wer.to_dict()

    def test_to_dict_knowledge_result_none_by_default(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        assert wer.to_dict()["knowledge_result"] is None

    def test_to_dict_with_knowledge_result(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        wer.knowledge_result = {"result_id": "kr-001", "sop_match_found": True}
        assert wer.to_dict()["knowledge_result"]["result_id"] == "kr-001"

    def test_from_dict_restores_knowledge_result(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        wer.knowledge_result = {"result_id": "kr-002", "sop_match_found": False}
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.knowledge_result is not None
        assert restored.knowledge_result["result_id"] == "kr-002"

    def test_from_dict_knowledge_result_none_when_missing(self):
        d = {"workflow_id": "wf-x", "workflow_state": "PENDING"}
        restored = WorkflowExecutionResult.from_dict(d)
        assert restored.knowledge_result is None

    def test_full_round_trip_json(self):
        wer = WorkflowExecutionResult(workflow_id="wf-x")
        wer.knowledge_result = _make_knowledge_result().to_dict()
        encoded = json.dumps(wer.to_dict())
        decoded = json.loads(encoded)
        restored = WorkflowExecutionResult.from_dict(decoded)
        assert restored.knowledge_result is not None
        assert restored.knowledge_result["sop_match_found"] is True
