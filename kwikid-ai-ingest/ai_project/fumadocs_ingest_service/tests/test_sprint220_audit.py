"""
tests/test_sprint220_audit.py

Sprint 2.20: Knowledge Layer audit event tests.

Coverage:
  - AuditEventType values exist: KNOWLEDGE_SEARCH_STARTED, KNOWLEDGE_SEARCH_COMPLETED,
    SOP_MATCH_FOUND, SOP_MATCH_NOT_FOUND
  - log_knowledge_search_started: writes to Supabase, action_type correct,
    detail fields correct, outcome=STARTED, no-op without Supabase, never raises
  - log_knowledge_search_completed: writes, action_type, outcome reflects match_found,
    no-op without Supabase, never raises
  - log_sop_match_found: writes, action_type=SOP_MATCH_FOUND, detail has entry_id/score,
    outcome=FOUND, never raises
  - log_sop_match_not_found: writes, action_type=SOP_MATCH_NOT_FOUND, outcome=NOT_FOUND,
    never raises
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case


def _make_case() -> Case:
    return Case(case_id="audit-kb-001", ticket_id="T-001", client="test")


class TestAuditEventTypeKnowledge:
    def test_knowledge_search_started_exists(self):
        assert hasattr(AuditEventType, "KNOWLEDGE_SEARCH_STARTED")

    def test_knowledge_search_started_value(self):
        assert AuditEventType.KNOWLEDGE_SEARCH_STARTED.value == "KNOWLEDGE_SEARCH_STARTED"

    def test_knowledge_search_completed_exists(self):
        assert hasattr(AuditEventType, "KNOWLEDGE_SEARCH_COMPLETED")

    def test_knowledge_search_completed_value(self):
        assert AuditEventType.KNOWLEDGE_SEARCH_COMPLETED.value == "KNOWLEDGE_SEARCH_COMPLETED"

    def test_sop_match_found_exists(self):
        assert hasattr(AuditEventType, "SOP_MATCH_FOUND")

    def test_sop_match_found_value(self):
        assert AuditEventType.SOP_MATCH_FOUND.value == "SOP_MATCH_FOUND"

    def test_sop_match_not_found_exists(self):
        assert hasattr(AuditEventType, "SOP_MATCH_NOT_FOUND")

    def test_sop_match_not_found_value(self):
        assert AuditEventType.SOP_MATCH_NOT_FOUND.value == "SOP_MATCH_NOT_FOUND"


class TestLogKnowledgeSearchStarted:
    def test_writes_to_supabase(self):
        sb = MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")
        assert sb.table().insert().execute.called

    def test_action_type_is_knowledge_search_started(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")
        assert captured[0]["action_type"] == "KNOWLEDGE_SEARCH_STARTED"

    def test_detail_has_topic(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")
        assert captured[0]["action_detail"]["topic"] == "VKYC_Session_Failure"

    def test_outcome_is_started(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")
        assert captured[0]["outcome"] == "STARTED"

    def test_no_op_without_supabase(self):
        logger = AuditLogger(None)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")

    def test_never_raises_on_supabase_error(self):
        sb = MagicMock()
        sb.table().insert().execute.side_effect = Exception("DB down")
        logger = AuditLogger(sb)
        logger.log_knowledge_search_started(_make_case(), topic="VKYC_Session_Failure")


class TestLogKnowledgeSearchCompleted:
    def test_writes_to_supabase(self):
        sb = MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_completed(
            _make_case(), topic="VKYC_Session_Failure",
            result_id="kr-001", sop_match_found=True, top_score=0.85,
        )
        assert sb.table().insert().execute.called

    def test_outcome_match_found(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_completed(
            _make_case(), topic="X", result_id="r", sop_match_found=True, top_score=0.8,
        )
        assert captured[0]["outcome"] == "MATCH_FOUND"

    def test_outcome_no_match(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_knowledge_search_completed(
            _make_case(), topic="X", result_id="r", sop_match_found=False, top_score=0.0,
        )
        assert captured[0]["outcome"] == "NO_MATCH"

    def test_no_op_without_supabase(self):
        AuditLogger(None).log_knowledge_search_completed(
            _make_case(), topic="X", result_id="r", sop_match_found=False, top_score=0.0,
        )

    def test_never_raises(self):
        sb = MagicMock()
        sb.table().insert().execute.side_effect = RuntimeError("fail")
        AuditLogger(sb).log_knowledge_search_completed(
            _make_case(), topic="X", result_id="r", sop_match_found=False, top_score=0.0,
        )


class TestLogSOPMatchFound:
    def test_writes_to_supabase(self):
        sb = MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_found(
            _make_case(), topic="VKYC_Session_Failure",
            entry_id="e-001", entry_title="Reset SOP", relevance_score=0.85,
        )
        assert sb.table().insert().execute.called

    def test_action_type_is_sop_match_found(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_found(
            _make_case(), topic="X", entry_id="e", entry_title="T", relevance_score=0.5,
        )
        assert captured[0]["action_type"] == "SOP_MATCH_FOUND"

    def test_outcome_is_found(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_found(
            _make_case(), topic="X", entry_id="e", entry_title="T", relevance_score=0.5,
        )
        assert captured[0]["outcome"] == "FOUND"

    def test_detail_has_entry_id(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_found(
            _make_case(), topic="X", entry_id="e-123", entry_title="T", relevance_score=0.5,
        )
        assert captured[0]["action_detail"]["entry_id"] == "e-123"

    def test_no_op_without_supabase(self):
        AuditLogger(None).log_sop_match_found(
            _make_case(), topic="X", entry_id="e", entry_title="T", relevance_score=0.5,
        )

    def test_never_raises(self):
        sb = MagicMock()
        sb.table().insert().execute.side_effect = Exception("fail")
        AuditLogger(sb).log_sop_match_found(
            _make_case(), topic="X", entry_id="e", entry_title="T", relevance_score=0.5,
        )


class TestLogSOPMatchNotFound:
    def test_writes_to_supabase(self):
        sb = MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_not_found(_make_case(), topic="VKYC_Session_Failure")
        assert sb.table().insert().execute.called

    def test_action_type_is_sop_match_not_found(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_not_found(_make_case(), topic="VKYC_Session_Failure")
        assert captured[0]["action_type"] == "SOP_MATCH_NOT_FOUND"

    def test_outcome_is_not_found(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_not_found(_make_case(), topic="X")
        assert captured[0]["outcome"] == "NOT_FOUND"

    def test_with_root_cause_category(self):
        captured = []
        sb = MagicMock()
        sb.table().insert.side_effect = lambda row: captured.append(row) or MagicMock()
        logger = AuditLogger(sb)
        logger.log_sop_match_not_found(
            _make_case(), topic="X", root_cause_category="EXPIRED_SESSION"
        )
        assert captured[0]["action_detail"]["root_cause_category"] == "EXPIRED_SESSION"

    def test_no_op_without_supabase(self):
        AuditLogger(None).log_sop_match_not_found(_make_case(), topic="X")

    def test_never_raises(self):
        sb = MagicMock()
        sb.table().insert().execute.side_effect = Exception("fail")
        AuditLogger(sb).log_sop_match_not_found(_make_case(), topic="X")
