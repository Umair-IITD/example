"""
tests/test_sprint221_audit.py

Sprint 2.21: AuditLogger action proposal event tests.

Coverage:
  - log_action_proposal_started: correct event type, outcome=STARTED, fields
  - log_action_proposal_completed: correct event type, outcome=COMPLETED, fields
  - log_action_proposal_blocked: correct event type, outcome=BLOCKED, fields
  - log_risk_assessment_completed: correct event type, outcome=risk_level, fields
  - All 4 methods: log-only mode when supabase_client=None (never raises)
  - All 4 methods: never raises even on supabase exceptions
  - AuditEventType enum has all 4 new values
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch

import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case


# ── Helpers ───────────────────────────────────────────────────────────────────

def _case() -> Case:
    return Case(
        case_id="case-001",
        ticket_id="ticket-001",
        client="test_client",
    )


def _logger(sb=None) -> AuditLogger:
    return AuditLogger(supabase_client=sb)


def _mock_sb():
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
    return sb


# ── AuditEventType enum values ────────────────────────────────────────────────

class TestAuditEventTypeValues:
    def test_action_proposal_started_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_STARTED")

    def test_action_proposal_started_value(self):
        assert AuditEventType.ACTION_PROPOSAL_STARTED.value == "ACTION_PROPOSAL_STARTED"

    def test_action_proposal_completed_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_COMPLETED")

    def test_action_proposal_completed_value(self):
        assert AuditEventType.ACTION_PROPOSAL_COMPLETED.value == "ACTION_PROPOSAL_COMPLETED"

    def test_action_proposal_blocked_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_BLOCKED")

    def test_action_proposal_blocked_value(self):
        assert AuditEventType.ACTION_PROPOSAL_BLOCKED.value == "ACTION_PROPOSAL_BLOCKED"

    def test_risk_assessment_completed_exists(self):
        assert hasattr(AuditEventType, "RISK_ASSESSMENT_COMPLETED")

    def test_risk_assessment_completed_value(self):
        assert AuditEventType.RISK_ASSESSMENT_COMPLETED.value == "RISK_ASSESSMENT_COMPLETED"


# ── log_action_proposal_started ───────────────────────────────────────────────

class TestLogActionProposalStarted:
    def test_no_raise_without_supabase(self):
        logger = _logger()
        logger.log_action_proposal_started(_case(), topic="T", root_cause_category="EXPIRED_SESSION")

    def test_calls_supabase_insert(self):
        sb = _mock_sb()
        logger = _logger(sb)
        logger.log_action_proposal_started(
            _case(), topic="T", root_cause_category="EXPIRED_SESSION",
            workflow_id="wf-001", step_id="step-001",
        )
        sb.table.assert_called_once_with("case_audit_log")

    def test_outcome_is_started(self):
        sb = _mock_sb()
        logger = _logger(sb)
        inserted_row = None

        def capture_insert(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture_insert
        logger.log_action_proposal_started(_case(), topic="T", root_cause_category="EXPIRED_SESSION")
        assert inserted_row["outcome"] == "STARTED"

    def test_event_type_is_action_proposal_started(self):
        sb = _mock_sb()
        logger = _logger(sb)
        inserted_row = None

        def capture_insert(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture_insert
        logger.log_action_proposal_started(_case(), topic="T", root_cause_category="EXPIRED_SESSION")
        assert inserted_row["action_type"] == "ACTION_PROPOSAL_STARTED"

    def test_no_raise_even_if_supabase_throws(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db boom")
        logger = _logger(sb)
        logger.log_action_proposal_started(_case(), topic="T", root_cause_category="EXPIRED_SESSION")


# ── log_action_proposal_completed ─────────────────────────────────────────────

class TestLogActionProposalCompleted:
    def test_no_raise_without_supabase(self):
        logger = _logger()
        logger.log_action_proposal_completed(
            _case(), topic="T", bundle_id="b-001",
            proposal_count=2, top_action="RESET_SESSION",
            risk_level="REVERSIBLE", requires_approval=True,
        )

    def test_calls_supabase_insert(self):
        sb = _mock_sb()
        logger = _logger(sb)
        logger.log_action_proposal_completed(
            _case(), topic="T", bundle_id="b-001",
            proposal_count=2, top_action="RESET_SESSION",
            risk_level="REVERSIBLE", requires_approval=True,
        )
        sb.table.assert_called_once_with("case_audit_log")

    def test_outcome_is_completed(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        logger = _logger(sb)
        logger.log_action_proposal_completed(
            _case(), topic="T", bundle_id="b-001",
            proposal_count=2, top_action="RESET_SESSION",
            risk_level="REVERSIBLE", requires_approval=True,
        )
        assert inserted_row["outcome"] == "COMPLETED"

    def test_event_type_is_action_proposal_completed(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        _logger(sb).log_action_proposal_completed(
            _case(), topic="T", bundle_id="b-001",
            proposal_count=1, top_action="RESET_SESSION",
            risk_level="REVERSIBLE", requires_approval=True,
        )
        assert inserted_row["action_type"] == "ACTION_PROPOSAL_COMPLETED"

    def test_no_raise_even_if_supabase_throws(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db boom")
        _logger(sb).log_action_proposal_completed(
            _case(), topic="T", bundle_id="b-001",
            proposal_count=1, top_action="RESET_SESSION",
            risk_level="REVERSIBLE", requires_approval=True,
        )


# ── log_action_proposal_blocked ───────────────────────────────────────────────

class TestLogActionProposalBlocked:
    def test_no_raise_without_supabase(self):
        _logger().log_action_proposal_blocked(
            _case(), topic="T", block_reason="NO_INVESTIGATION_RESULT",
        )

    def test_calls_supabase_insert(self):
        sb = _mock_sb()
        _logger(sb).log_action_proposal_blocked(
            _case(), topic="T", block_reason="NO_INVESTIGATION_RESULT",
        )
        sb.table.assert_called_once_with("case_audit_log")

    def test_outcome_is_blocked(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        _logger(sb).log_action_proposal_blocked(
            _case(), topic="T", block_reason="NO_INVESTIGATION_RESULT",
        )
        assert inserted_row["outcome"] == "BLOCKED"

    def test_event_type_is_action_proposal_blocked(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        _logger(sb).log_action_proposal_blocked(
            _case(), topic="T", block_reason="NO_INVESTIGATION_RESULT",
        )
        assert inserted_row["action_type"] == "ACTION_PROPOSAL_BLOCKED"

    def test_no_raise_even_if_supabase_throws(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db boom")
        _logger(sb).log_action_proposal_blocked(
            _case(), topic="T", block_reason="NO_INVESTIGATION_RESULT",
        )


# ── log_risk_assessment_completed ─────────────────────────────────────────────

class TestLogRiskAssessmentCompleted:
    def test_no_raise_without_supabase(self):
        _logger().log_risk_assessment_completed(
            _case(), topic="T", bundle_id="b-001",
            action_type="RESET_SESSION", risk_level="REVERSIBLE",
            requires_approval=True,
        )

    def test_calls_supabase_insert(self):
        sb = _mock_sb()
        _logger(sb).log_risk_assessment_completed(
            _case(), topic="T", bundle_id="b-001",
            action_type="RESET_SESSION", risk_level="REVERSIBLE",
            requires_approval=True,
        )
        sb.table.assert_called_once_with("case_audit_log")

    def test_outcome_is_risk_level(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        _logger(sb).log_risk_assessment_completed(
            _case(), topic="T", bundle_id="b-001",
            action_type="RESET_SESSION", risk_level="REVERSIBLE",
            requires_approval=True,
        )
        assert inserted_row["outcome"] == "REVERSIBLE"

    def test_event_type_is_risk_assessment_completed(self):
        sb = _mock_sb()
        inserted_row = None

        def capture(row):
            nonlocal inserted_row
            inserted_row = row
            return MagicMock(execute=MagicMock(return_value=MagicMock()))

        sb.table.return_value.insert.side_effect = capture
        _logger(sb).log_risk_assessment_completed(
            _case(), topic="T", bundle_id="b-001",
            action_type="RESET_SESSION", risk_level="REVERSIBLE",
            requires_approval=True,
        )
        assert inserted_row["action_type"] == "RISK_ASSESSMENT_COMPLETED"

    def test_no_raise_even_if_supabase_throws(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db boom")
        _logger(sb).log_risk_assessment_completed(
            _case(), topic="T", bundle_id="b-001",
            action_type="RESET_SESSION", risk_level="REVERSIBLE",
            requires_approval=True,
        )
