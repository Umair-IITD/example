"""
tests/test_sprint223_audit.py

Sprint 2.23: AuditLogger Sprint 2.23 method tests.

Coverage (8 new methods):
  - log_execution_started
  - log_execution_completed
  - log_verification_started
  - log_verification_completed
  - log_recovery_started
  - log_recovery_completed
  - log_resolution_started
  - log_resolution_completed

For each method:
  - Called with no supabase client: no crash (log-only mode)
  - Called with mock supabase: inserts to correct table
  - action_type field in action_detail matches expected AuditEventType
  - outcome field set correctly
  - Exception in _write does not propagate
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case


# ── Helpers ───────────────────────────────────────────────────────────────────

def _case(case_id: str = "case-001") -> Case:
    c = Case()
    c.case_id = case_id
    c.ticket_id = "ticket-001"
    c.client = "test"
    return c


def _logger_no_sb() -> AuditLogger:
    return AuditLogger(supabase_client=None)


def _mock_sb() -> MagicMock:
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = None
    return sb


def _logger_with_sb() -> tuple[AuditLogger, MagicMock]:
    sb = _mock_sb()
    return AuditLogger(supabase_client=sb), sb


# ── log_execution_started ─────────────────────────────────────────────────────

class TestLogExecutionStarted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_execution_started(_case(), "resend_otp", "b-001")

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_execution_started(_case(), "resend_otp", "b-001")
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_execution_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_execution_started(_case(), "resend_otp", "b-001")
        assert rows[0]["action_type"] == AuditEventType.EXECUTION_STARTED.value

    def test_outcome_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_execution_started(_case(), "resend_otp", "b-001")
        assert rows[0]["outcome"] == "STARTED"

    def test_exception_in_write_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        logger = AuditLogger(supabase_client=sb)
        logger.log_execution_started(_case(), "resend_otp", "b-001")


# ── log_execution_completed ───────────────────────────────────────────────────

class TestLogExecutionCompleted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_execution_completed(
            _case(), "resend_otp", "b-001", "SUCCESS", True
        )

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_execution_completed(_case(), "resend_otp", "b-001", "SUCCESS", True)
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_execution_completed(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_execution_completed(_case(), "resend_otp", "b-001", "SUCCESS", True)
        assert rows[0]["action_type"] == AuditEventType.EXECUTION_COMPLETED.value

    def test_outcome_success(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_execution_completed(_case(), "resend_otp", "b-001", "SUCCESS", True)
        assert rows[0]["outcome"] == "SUCCESS"

    def test_outcome_failed_when_not_success(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_execution_completed(_case(), "resend_otp", "b-001", "FAILED", False)
        assert rows[0]["outcome"] == "FAILED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_execution_completed(
            _case(), "resend_otp", "b-001", "SUCCESS", True
        )


# ── log_verification_started ──────────────────────────────────────────────────

class TestLogVerificationStarted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_verification_started(_case(), "resend_otp", "b-001")

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_verification_started(_case(), "resend_otp", "b-001")
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_verification_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_verification_started(_case(), "resend_otp", "b-001")
        assert rows[0]["action_type"] == AuditEventType.VERIFICATION_STARTED.value

    def test_outcome_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_verification_started(_case(), "resend_otp", "b-001")
        assert rows[0]["outcome"] == "STARTED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_verification_started(_case(), "t", "b")


# ── log_verification_completed ────────────────────────────────────────────────

class TestLogVerificationCompleted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_verification_completed(
            _case(), "resend_otp", "b-001", "VERIFIED_SUCCESS", True
        )

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_verification_completed(_case(), "t", "b", "VERIFIED_SUCCESS", True)
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_verification_completed(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_verification_completed(_case(), "t", "b", "VERIFIED_SUCCESS", True)
        assert rows[0]["action_type"] == AuditEventType.VERIFICATION_COMPLETED.value

    def test_outcome_verified_on_success(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_verification_completed(_case(), "t", "b", "VERIFIED_SUCCESS", True)
        assert rows[0]["outcome"] == "VERIFIED"

    def test_outcome_unverified_on_failure(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_verification_completed(_case(), "t", "b", "VERIFIED_FAILURE", False)
        assert rows[0]["outcome"] == "UNVERIFIED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_verification_completed(
            _case(), "t", "b", "VERIFIED_SUCCESS", True
        )


# ── log_recovery_started ──────────────────────────────────────────────────────

class TestLogRecoveryStarted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_recovery_started(_case(), "resend_otp", "b-001")

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_recovery_started(_case(), "resend_otp", "b-001")
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_recovery_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_recovery_started(_case(), "t", "b")
        assert rows[0]["action_type"] == AuditEventType.RECOVERY_STARTED.value

    def test_outcome_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_recovery_started(_case(), "t", "b")
        assert rows[0]["outcome"] == "STARTED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_recovery_started(_case(), "t", "b")


# ── log_recovery_completed ────────────────────────────────────────────────────

class TestLogRecoveryCompleted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_recovery_completed(
            _case(), "resend_otp", "b-001", "RETRY", "RECOVERY_PENDING"
        )

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_recovery_completed(_case(), "t", "b", "RETRY", "RECOVERY_PENDING")
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_recovery_completed(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_recovery_completed(_case(), "t", "b", "RETRY", "RECOVERY_PENDING")
        assert rows[0]["action_type"] == AuditEventType.RECOVERY_COMPLETED.value

    def test_outcome_is_status(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_recovery_completed(_case(), "t", "b", "RETRY", "RECOVERY_PENDING")
        assert rows[0]["outcome"] == "RECOVERY_PENDING"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_recovery_completed(
            _case(), "t", "b", "RETRY", "RECOVERY_PENDING"
        )


# ── log_resolution_started ────────────────────────────────────────────────────

class TestLogResolutionStarted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_resolution_started(_case(), "resend_otp", "b-001")

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_resolution_started(_case(), "t", "b")
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_resolution_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_started(_case(), "t", "b")
        assert rows[0]["action_type"] == AuditEventType.RESOLUTION_STARTED.value

    def test_outcome_started(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_started(_case(), "t", "b")
        assert rows[0]["outcome"] == "STARTED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_resolution_started(_case(), "t", "b")


# ── log_resolution_completed ──────────────────────────────────────────────────

class TestLogResolutionCompleted:
    def test_no_supabase_no_crash(self):
        _logger_no_sb().log_resolution_completed(
            _case(), "resend_otp", "b-001", "RESOLVED", True
        )

    def test_insert_called(self):
        logger, sb = _logger_with_sb()
        logger.log_resolution_completed(_case(), "t", "b", "RESOLVED", True)
        sb.table.assert_called_with("case_audit_log")

    def test_event_type_resolution_completed(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_completed(_case(), "t", "b", "RESOLVED", True)
        assert rows[0]["action_type"] == AuditEventType.RESOLUTION_COMPLETED.value

    def test_outcome_resolved_when_resolved(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_completed(_case(), "t", "b", "RESOLVED", True)
        assert rows[0]["outcome"] == "RESOLVED"

    def test_outcome_status_when_unresolved(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_completed(_case(), "t", "b", "UNRESOLVED", False)
        assert rows[0]["outcome"] == "UNRESOLVED"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("DB down")
        AuditLogger(supabase_client=sb).log_resolution_completed(
            _case(), "t", "b", "RESOLVED", True
        )

    def test_workflow_context_passed(self):
        rows = []
        logger, sb = _logger_with_sb()
        sb.table.return_value.insert.side_effect = lambda row: rows.append(row) or MagicMock()
        logger.log_resolution_completed(
            _case(), "t", "b", "RESOLVED", True,
            workflow_id="wf-001", step_id="step-001"
        )
        detail = rows[0]["action_detail"]
        assert detail.get("workflow_id") == "wf-001"
        assert detail.get("step_id") == "step-001"
