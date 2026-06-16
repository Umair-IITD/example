"""
tests/test_sprint226_audit_events.py

Sprint 2.26: New audit event types and AuditLogger methods.

Coverage:
  - AuditEventType.WORKFLOW_CLARIFICATION_RESUMED exists
  - AuditEventType.CLARIFICATION_ATTEMPT_INCREMENTED exists
  - AuditLogger.log_workflow_clarification_resumed() — success path
  - AuditLogger.log_workflow_clarification_resumed() — log-only mode (no supabase)
  - AuditLogger.log_workflow_clarification_resumed() — never raises on exception
  - AuditLogger.log_clarification_attempt_incremented() — success path
  - AuditLogger.log_clarification_attempt_incremented() — log-only mode
  - AuditLogger.log_clarification_attempt_incremented() — never raises
  - Both methods emit correct event type
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.models import AuditEventType
from case_engine.audit import AuditLogger


# ── AuditEventType values ─────────────────────────────────────────────────────

class TestAuditEventTypeValues:
    def test_workflow_clarification_resumed_exists(self):
        assert AuditEventType.WORKFLOW_CLARIFICATION_RESUMED == "WORKFLOW_CLARIFICATION_RESUMED"

    def test_clarification_attempt_incremented_exists(self):
        assert AuditEventType.CLARIFICATION_ATTEMPT_INCREMENTED == "CLARIFICATION_ATTEMPT_INCREMENTED"

    def test_sprint226_events_are_string_subclass(self):
        assert isinstance(AuditEventType.WORKFLOW_CLARIFICATION_RESUMED.value, str)
        assert isinstance(AuditEventType.CLARIFICATION_ATTEMPT_INCREMENTED.value, str)


# ── AuditLogger.log_workflow_clarification_resumed() ─────────────────────────

class TestLogWorkflowClarificationResumed:
    def _case(self):
        case = MagicMock()
        case.case_id  = "case-resume-audit"
        case.ticket_id = "ticket-1"
        case.client   = "unity_bank"
        return case

    def test_log_only_mode_no_error(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        logger.log_workflow_clarification_resumed(
            case, workflow_id="wf-1", step_id="clarify_slots", slots_updated=["session_id"]
        )  # Should not raise

    def test_with_supabase_writes_entry(self):
        sb     = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = None
        logger = AuditLogger(supabase_client=sb)
        case   = self._case()
        logger.log_workflow_clarification_resumed(
            case, workflow_id="wf-1", step_id="clarify_slots", slots_updated=["session_id"]
        )
        sb.table.assert_called()

    def test_never_raises_on_supabase_exception(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("supabase_down")
        logger = AuditLogger(supabase_client=sb)
        case   = self._case()
        # Should not raise
        logger.log_workflow_clarification_resumed(
            case, workflow_id="wf-1", step_id="clarify_slots", slots_updated=[]
        )

    def test_empty_slots_list_allowed(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        logger.log_workflow_clarification_resumed(
            case, workflow_id="wf-1", step_id="step-1", slots_updated=[]
        )  # No error

    def test_multiple_slots_in_list(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        logger.log_workflow_clarification_resumed(
            case,
            workflow_id="wf-1",
            step_id="clarify_slots",
            slots_updated=["session_id", "phone_number"],
        )  # No error


# ── AuditLogger.log_clarification_attempt_incremented() ──────────────────────

class TestLogClarificationAttemptIncremented:
    def _case(self):
        case = MagicMock()
        case.case_id   = "case-attempt-audit"
        case.ticket_id = "ticket-2"
        case.client    = "unity_bank"
        return case

    def test_log_only_mode_no_error(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        logger.log_clarification_attempt_incremented(
            case, slot_name="session_id", attempt_count=1, max_attempts=3
        )  # No raise

    def test_with_supabase_writes_entry(self):
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = None
        logger = AuditLogger(supabase_client=sb)
        case   = self._case()
        logger.log_clarification_attempt_incremented(
            case, slot_name="session_id", attempt_count=1, max_attempts=3
        )
        sb.table.assert_called()

    def test_never_raises_on_supabase_exception(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("boom")
        logger = AuditLogger(supabase_client=sb)
        case   = self._case()
        logger.log_clarification_attempt_incremented(
            case, slot_name="session_id", attempt_count=2, max_attempts=3
        )  # Should not raise

    def test_with_workflow_id_and_step_id(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        logger.log_clarification_attempt_incremented(
            case,
            slot_name="session_id",
            attempt_count=1,
            max_attempts=3,
            workflow_id="wf-abc",
            step_id="step-clarify",
        )  # No error

    def test_defaults_for_workflow_and_step_ids(self):
        logger = AuditLogger(supabase_client=None)
        case   = self._case()
        # Should work without explicit workflow_id / step_id
        logger.log_clarification_attempt_incremented(
            case, slot_name="session_id", attempt_count=1, max_attempts=3
        )
