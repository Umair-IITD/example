"""
tests/test_sprint224_audit.py

Sprint 2.24: AuditLogger — 4 new reasoning audit methods.

Coverage for each of the 4 methods:
  - No supabase (log-only mode) — doesn't raise
  - With supabase insert — called once with correct table
  - event_type set correctly
  - outcome set correctly
  - Exception in supabase write — swallowed, doesn't propagate
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case


def _make_case() -> Case:
    return Case(
        case_id="case-123",
        ticket_id="ticket-456",
        client="test-client",
    )


def _make_sb() -> MagicMock:
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = None
    return sb


# ── log_reasoning_started ─────────────────────────────────────────────────────

class TestLogReasoningStarted:
    def test_no_supabase_does_not_raise(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK_FAILURE")

    def test_with_supabase_calls_insert(self):
        sb     = _make_sb()
        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK_FAILURE")
        sb.table.assert_called_once_with("case_audit_log")
        sb.table().insert.assert_called_once()

    def test_event_type_is_reasoning_started(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK_FAILURE")
        assert len(inserted_rows) == 1
        assert inserted_rows[0]["action_type"] == AuditEventType.REASONING_STARTED.value

    def test_outcome_is_started(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK_FAILURE")
        assert inserted_rows[0]["outcome"] == "STARTED"

    def test_exception_in_supabase_is_swallowed(self):
        sb = _make_sb()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db error")
        logger = AuditLogger(supabase_client=sb)
        # Must not raise
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK_FAILURE")

    def test_workflow_id_in_action_detail(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_started(_make_case(), topic="VKYC", root_cause_category="NETWORK", workflow_id="wf-1", step_id="s-1")
        detail = inserted_rows[0]["action_detail"]
        assert detail["workflow_id"] == "wf-1"
        assert detail["step_id"] == "s-1"


# ── log_reasoning_completed ───────────────────────────────────────────────────

class TestLogReasoningCompleted:
    def test_no_supabase_does_not_raise(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_reasoning_completed(
            _make_case(),
            topic="VKYC",
            result_id="r-1",
            outcome="RECOMMEND_ACTION",
            recommended_action="RESET_SESSION",
            should_escalate=False,
            confidence=0.90,
        )

    def test_with_supabase_calls_insert(self):
        sb     = _make_sb()
        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_completed(
            _make_case(), topic="VKYC", result_id="r-1",
            outcome="RECOMMEND_ACTION", recommended_action="RESET_SESSION",
            should_escalate=False, confidence=0.90,
        )
        sb.table.assert_called_once_with("case_audit_log")
        sb.table().insert.assert_called_once()

    def test_event_type_is_reasoning_completed(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_completed(
            _make_case(), topic="VKYC", result_id="r-1",
            outcome="RECOMMEND_ACTION", recommended_action="RESET_SESSION",
            should_escalate=False, confidence=0.90,
        )
        assert inserted_rows[0]["action_type"] == AuditEventType.REASONING_COMPLETED.value

    def test_outcome_matches_passed_outcome(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_completed(
            _make_case(), topic="VKYC", result_id="r-1",
            outcome="ESCALATE", recommended_action="ESCALATE_L2",
            should_escalate=True, confidence=0.70,
        )
        assert inserted_rows[0]["outcome"] == "ESCALATE"

    def test_exception_in_supabase_is_swallowed(self):
        sb = _make_sb()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db error")
        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_completed(
            _make_case(), topic="VKYC", result_id="r-1",
            outcome="RECOMMEND_ACTION", recommended_action="RESET_SESSION",
            should_escalate=False, confidence=0.90,
        )

    def test_action_detail_has_recommended_action(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_reasoning_completed(
            _make_case(), topic="VKYC", result_id="r-1",
            outcome="RECOMMEND_ACTION", recommended_action="RESET_SESSION",
            should_escalate=False, confidence=0.90,
        )
        detail = inserted_rows[0]["action_detail"]
        assert detail["recommended_action"] == "RESET_SESSION"


# ── log_workflow_reasoning_started ────────────────────────────────────────────

class TestLogWorkflowReasoningStarted:
    def test_no_supabase_does_not_raise(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-1", step_id="s-1")

    def test_with_supabase_calls_insert(self):
        sb     = _make_sb()
        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-1", step_id="s-1")
        sb.table.assert_called_once_with("case_audit_log")
        sb.table().insert.assert_called_once()

    def test_event_type_is_workflow_reasoning_started(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-1", step_id="s-1")
        assert inserted_rows[0]["action_type"] == AuditEventType.WORKFLOW_REASONING_STARTED.value

    def test_outcome_is_started(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-1", step_id="s-1")
        assert inserted_rows[0]["outcome"] == "STARTED"

    def test_exception_in_supabase_is_swallowed(self):
        sb = _make_sb()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db error")
        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-1", step_id="s-1")

    def test_action_detail_has_workflow_id(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_started(_make_case(), workflow_id="wf-abc", step_id="s-xyz")
        detail = inserted_rows[0]["action_detail"]
        assert detail["workflow_id"] == "wf-abc"
        assert detail["step_id"] == "s-xyz"


# ── log_workflow_reasoning_completed ──────────────────────────────────────────

class TestLogWorkflowReasoningCompleted:
    def test_no_supabase_does_not_raise(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_COMPLETED", recommended_action="RESET_SESSION",
        )

    def test_with_supabase_calls_insert(self):
        sb     = _make_sb()
        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_COMPLETED", recommended_action="RESET_SESSION",
        )
        sb.table.assert_called_once_with("case_audit_log")
        sb.table().insert.assert_called_once()

    def test_event_type_is_workflow_reasoning_completed(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_COMPLETED", recommended_action="RESET_SESSION",
        )
        assert inserted_rows[0]["action_type"] == AuditEventType.WORKFLOW_REASONING_COMPLETED.value

    def test_outcome_matches_passed_outcome(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_ESCALATE", recommended_action="ESCALATE_L2",
        )
        assert inserted_rows[0]["outcome"] == "REASONING_ESCALATE"

    def test_exception_in_supabase_is_swallowed(self):
        sb = _make_sb()
        sb.table.return_value.insert.return_value.execute.side_effect = RuntimeError("db error")
        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_COMPLETED", recommended_action="RESET_SESSION",
        )

    def test_action_detail_has_recommended_action(self):
        inserted_rows = []
        sb = _make_sb()
        sb.table.return_value.insert.side_effect = lambda row: (inserted_rows.append(row), sb.table().insert.return_value)[1]

        logger = AuditLogger(supabase_client=sb)
        logger.log_workflow_reasoning_completed(
            _make_case(), workflow_id="wf-1", step_id="s-1",
            outcome="REASONING_COMPLETED", recommended_action="RESEND_OTP",
        )
        detail = inserted_rows[0]["action_detail"]
        assert detail["recommended_action"] == "RESEND_OTP"
