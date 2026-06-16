"""
tests/test_sprint222_audit.py

Sprint 2.22: Tests for case_engine/audit.py Sprint 2.22 methods

Coverage:
- log_action_gateway_started: no-supabase, insert called, outcome, event_type, isolation
- log_action_gateway_completed: outcome == status string, event_type
- log_approval_requested: outcome == PENDING
- log_approval_granted: outcome == APPROVED
- log_approval_rejected: outcome == REJECTED
- All 5 methods: exception isolation (audit never raises)
- All 5 methods: no-supabase mode (log-only, no insert)
"""
import pytest
from unittest.mock import MagicMock, patch

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case


def _case(case_id: str = "case-001") -> Case:
    return Case(case_id=case_id, ticket_id="ticket-001", client="test_client")


def _mock_supabase() -> MagicMock:
    sb = MagicMock()
    sb.table.return_value.insert.return_value.execute.return_value = None
    return sb


# ── log_action_gateway_started ────────────────────────────────────────────────

class TestLogActionGatewayStarted:
    def test_no_supabase_no_error(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_action_gateway_started(_case(), "VKYC", "bundle-001")  # should not raise

    def test_insert_called(self):
        sb = _mock_supabase()
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_started(_case(), "VKYC", "bundle-001",
                                          workflow_id="wf-1", step_id="step-gw")
        sb.table.assert_called_with("case_audit_log")
        sb.table.return_value.insert.assert_called_once()

    def test_event_type(self):
        inserted_row = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            inserted_row.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_started(_case(), "VKYC", "b1")
        assert inserted_row.get("action_type") == AuditEventType.ACTION_GATEWAY_STARTED.value

    def test_outcome_started(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_started(_case(), "VKYC", "b1")
        assert captured.get("outcome") == "STARTED"

    def test_exception_isolation(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db exploded")
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_started(_case(), "VKYC", "b1")  # must not raise


# ── log_action_gateway_completed ──────────────────────────────────────────────

class TestLogActionGatewayCompleted:
    def test_no_supabase_no_error(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_action_gateway_completed(_case(), "VKYC", "res-001", "APPROVED", "SAFE", True)

    def test_event_type(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_completed(_case(), "VKYC", "r1", "APPROVED", "SAFE", True)
        assert captured["action_type"] == AuditEventType.ACTION_GATEWAY_COMPLETED.value

    def test_outcome_is_status(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_completed(_case(), "VKYC", "r1", "PENDING_APPROVAL", "REVERSIBLE", False)
        assert captured["outcome"] == "PENDING_APPROVAL"

    def test_exception_isolation(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db exploded")
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_completed(_case(), "VKYC", "r1", "APPROVED", "SAFE", True)


# ── log_approval_requested ────────────────────────────────────────────────────

class TestLogApprovalRequested:
    def test_no_supabase_no_error(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_approval_requested(_case(), "VKYC", "b1", "REVERSIBLE")

    def test_event_type(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_requested(_case(), "VKYC", "b1", "HIGH_RISK")
        assert captured["action_type"] == AuditEventType.APPROVAL_REQUESTED.value

    def test_outcome_pending(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_requested(_case(), "VKYC", "b1", "REVERSIBLE")
        assert captured["outcome"] == "PENDING"

    def test_risk_level_in_detail(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_requested(_case(), "VKYC", "b1", "HIGH_RISK")
        assert captured["action_detail"]["risk_level"] == "HIGH_RISK"

    def test_exception_isolation(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db exploded")
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_requested(_case(), "VKYC", "b1", "REVERSIBLE")


# ── log_approval_granted ──────────────────────────────────────────────────────

class TestLogApprovalGranted:
    def test_no_supabase_no_error(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_approval_granted(_case(), "VKYC", "b1", "auto_approval")

    def test_event_type(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_granted(_case(), "VKYC", "b1", "auto_approval")
        assert captured["action_type"] == AuditEventType.APPROVAL_GRANTED.value

    def test_outcome_approved(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_granted(_case(), "VKYC", "b1", "john.doe")
        assert captured["outcome"] == "APPROVED"

    def test_approver_in_detail(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_granted(_case(), "VKYC", "b1", "jane.smith")
        assert captured["action_detail"]["approver"] == "jane.smith"

    def test_exception_isolation(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db exploded")
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_granted(_case(), "VKYC", "b1", "auto_approval")


# ── log_approval_rejected ─────────────────────────────────────────────────────

class TestLogApprovalRejected:
    def test_no_supabase_no_error(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_approval_rejected(_case(), "VKYC", "b1", "reviewer")

    def test_event_type(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_rejected(_case(), "VKYC", "b1", "reviewer")
        assert captured["action_type"] == AuditEventType.APPROVAL_REJECTED.value

    def test_outcome_rejected(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_rejected(_case(), "VKYC", "b1", "boss")
        assert captured["outcome"] == "REJECTED"

    def test_approver_in_detail(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_rejected(_case(), "VKYC", "b1", "head_of_ops")
        assert captured["action_detail"]["approver"] == "head_of_ops"

    def test_exception_isolation(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db exploded")
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_rejected(_case(), "VKYC", "b1", "reviewer")


# ── Workflow/step context ─────────────────────────────────────────────────────

class TestAuditWorkflowContext:
    def test_workflow_id_and_step_id_in_detail(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_action_gateway_started(
            _case(), "VKYC", "b1", workflow_id="wf-001", step_id="step-gw"
        )
        assert captured["action_detail"]["workflow_id"] == "wf-001"
        assert captured["action_detail"]["step_id"] == "step-gw"

    def test_case_id_in_row(self):
        captured = {}
        sb = _mock_supabase()
        sb.table.return_value.insert.side_effect = lambda row: (
            captured.update(row) or sb.table.return_value.insert.return_value
        )
        logger = AuditLogger(supabase_client=sb)
        logger.log_approval_granted(_case("my-case-id"), "VKYC", "b1", "approver")
        assert captured["case_id"] == "my-case-id"
