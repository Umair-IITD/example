"""
tests/test_sprint219_audit.py

Sprint 2.19 Part 7: Workflow investigation audit events.

Coverage:
  - AuditEventType has WORKFLOW_INVESTIGATION_STARTED
  - AuditEventType has WORKFLOW_INVESTIGATION_COMPLETED
  - log_workflow_investigation_started() writes to case_audit_log
  - log_workflow_investigation_started() entry has correct action_type
  - log_workflow_investigation_started() entry includes workflow_id, step_id, topic
  - log_workflow_investigation_completed() writes to case_audit_log
  - log_workflow_investigation_completed() entry has correct action_type
  - log_workflow_investigation_completed() outcome is ESCALATED when escalate=True
  - log_workflow_investigation_completed() outcome is COMPLETED when escalate=False
  - Both methods are no-ops when supabase_client is None
  - Both methods never raise on DB error
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case, CaseState


def _make_case() -> Case:
    return Case(
        case_id="case-219-audit",
        ticket_id="ticket-219-audit",
        client="test_client",
        current_state=CaseState.WORKFLOW_ACTIVE,
    )


def _make_logger(with_sb: bool = True) -> tuple[AuditLogger, MagicMock | None]:
    if with_sb:
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = None
        return AuditLogger(sb), sb
    return AuditLogger(None), None


class TestWorkflowInvestigationAuditEventTypes:
    def test_workflow_investigation_started_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_INVESTIGATION_STARTED")

    def test_workflow_investigation_completed_exists(self):
        assert hasattr(AuditEventType, "WORKFLOW_INVESTIGATION_COMPLETED")

    def test_workflow_investigation_started_value(self):
        assert AuditEventType.WORKFLOW_INVESTIGATION_STARTED.value == "WORKFLOW_INVESTIGATION_STARTED"

    def test_workflow_investigation_completed_value(self):
        assert AuditEventType.WORKFLOW_INVESTIGATION_COMPLETED.value == "WORKFLOW_INVESTIGATION_COMPLETED"


class TestLogWorkflowInvestigationStarted:
    def test_writes_to_supabase(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="wf-001", step_id="step-inv", topic="VKYC_Session_Failure"
        )
        sb.table.assert_called_with("case_audit_log")
        sb.table.return_value.insert.assert_called_once()

    def test_action_type_correct(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="wf-001", step_id="step-inv", topic="OTP_Delivery_Failure"
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_type"] == "WORKFLOW_INVESTIGATION_STARTED"

    def test_detail_includes_workflow_id(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="WF-XYZ", step_id="s1", topic="T"
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_detail"]["workflow_id"] == "WF-XYZ"

    def test_detail_includes_step_id(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="w", step_id="STEP-ABC", topic="T"
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_detail"]["step_id"] == "STEP-ABC"

    def test_detail_includes_topic(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="w", step_id="s", topic="Document_OCR_Failure"
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_detail"]["topic"] == "Document_OCR_Failure"

    def test_outcome_is_started(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="w", step_id="s", topic="T"
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "STARTED"

    def test_noop_when_no_supabase(self):
        logger, _ = _make_logger(with_sb=False)
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="w", step_id="s", topic="T"
        )

    def test_never_raises_on_db_error(self):
        logger, sb = _make_logger()
        sb.table.side_effect = RuntimeError("DB down")
        logger.log_workflow_investigation_started(
            _make_case(), workflow_id="w", step_id="s", topic="T"
        )


class TestLogWorkflowInvestigationCompleted:
    def test_writes_to_supabase(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="wf-001", step_id="s1",
            category="EXPIRED_SESSION", confidence=0.9, escalate=False, result_id="r-001",
        )
        sb.table.assert_called_with("case_audit_log")
        sb.table.return_value.insert.assert_called_once()

    def test_action_type_correct(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="w", step_id="s",
            category="TIMEOUT", confidence=0.7, escalate=True, result_id="r",
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_type"] == "WORKFLOW_INVESTIGATION_COMPLETED"

    def test_outcome_escalated_when_escalate_true(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="w", step_id="s",
            category="UNKNOWN", confidence=0.0, escalate=True, result_id="r",
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "ESCALATED"

    def test_outcome_completed_when_escalate_false(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="w", step_id="s",
            category="EXPIRED_SESSION", confidence=0.9, escalate=False, result_id="r",
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "COMPLETED"

    def test_detail_has_all_fields(self):
        logger, sb = _make_logger()
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="WF-A", step_id="S-B",
            category="LIVENESS_FAILURE", confidence=0.75, escalate=False, result_id="R-C",
        )
        detail = sb.table.return_value.insert.call_args[0][0]["action_detail"]
        assert detail["workflow_id"] == "WF-A"
        assert detail["step_id"] == "S-B"
        assert detail["category"] == "LIVENESS_FAILURE"
        assert detail["confidence"] == 0.75
        assert detail["escalate"] is False
        assert detail["result_id"] == "R-C"

    def test_noop_when_no_supabase(self):
        logger, _ = _make_logger(with_sb=False)
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="w", step_id="s",
            category="UNKNOWN", confidence=0.0, escalate=True, result_id="r",
        )

    def test_never_raises_on_db_error(self):
        logger, sb = _make_logger()
        sb.table.side_effect = Exception("Network error")
        logger.log_workflow_investigation_completed(
            _make_case(), workflow_id="w", step_id="s",
            category="UNKNOWN", confidence=0.0, escalate=True, result_id="r",
        )
