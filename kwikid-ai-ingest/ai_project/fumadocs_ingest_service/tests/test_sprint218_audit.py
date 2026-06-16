"""
tests/test_sprint218_audit.py

Sprint 2.18: Audit layer tests for investigation events (Part H — audit coverage).

Coverage:
  - AuditEventType has INVESTIGATION_STARTED and INVESTIGATION_COMPLETED
  - AuditLogger.log_investigation_started writes correct entry
  - AuditLogger.log_investigation_completed writes correct entry
  - Both methods are no-ops when supabase_client is None (log-only mode)
  - Neither method raises on failure
  - Entries have correct action_type values
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEventType, Case, CaseState


# ── Fixtures ──────────────────────────────────────────────────────────────────

def make_case() -> Case:
    return Case(
        case_id="case-audit-001",
        ticket_id="ticket-audit-001",
        client="test_client",
        current_state=CaseState.WORKFLOW_ACTIVE,
    )


def make_audit_logger(with_sb: bool = True) -> tuple[AuditLogger, MagicMock | None]:
    if with_sb:
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = None
        return AuditLogger(sb), sb
    return AuditLogger(None), None


# ── AuditEventType presence tests ─────────────────────────────────────────────

class TestAuditEventTypes:
    def test_investigation_started_event_type_exists(self):
        assert hasattr(AuditEventType, "INVESTIGATION_STARTED")

    def test_investigation_completed_event_type_exists(self):
        assert hasattr(AuditEventType, "INVESTIGATION_COMPLETED")

    def test_investigation_started_value(self):
        assert AuditEventType.INVESTIGATION_STARTED.value == "INVESTIGATION_STARTED"

    def test_investigation_completed_value(self):
        assert AuditEventType.INVESTIGATION_COMPLETED.value == "INVESTIGATION_COMPLETED"


# ── log_investigation_started tests ──────────────────────────────────────────

class TestLogInvestigationStarted:
    def test_writes_to_supabase(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_started(case, topic="VKYC_Session_Failure", plan_id="p-001", step_count=2)
        sb.table.assert_called_with("case_audit_log")
        sb.table.return_value.insert.assert_called_once()

    def test_entry_has_correct_action_type(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_started(case, topic="VKYC_Session_Failure", plan_id="p-001", step_count=2)
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_type"] == "INVESTIGATION_STARTED"

    def test_entry_includes_topic_and_plan_id(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_started(case, topic="OTP_Delivery_Failure", plan_id="plan-xyz", step_count=3)
        row = sb.table.return_value.insert.call_args[0][0]
        detail = row["action_detail"]
        assert detail["topic"] == "OTP_Delivery_Failure"
        assert detail["plan_id"] == "plan-xyz"
        assert detail["step_count"] == 3

    def test_outcome_is_started(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_started(case, topic="VKYC_Session_Failure", plan_id="p", step_count=1)
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "STARTED"

    def test_noop_when_no_supabase_client(self):
        audit, _ = make_audit_logger(with_sb=False)
        case = make_case()
        # Must not raise
        audit.log_investigation_started(case, topic="VKYC_Session_Failure", plan_id="p", step_count=2)

    def test_never_raises_on_supabase_error(self):
        audit, sb = make_audit_logger(with_sb=True)
        sb.table.side_effect = RuntimeError("DB is down")
        case = make_case()
        audit.log_investigation_started(case, topic="VKYC_Session_Failure", plan_id="p", step_count=1)


# ── log_investigation_completed tests ────────────────────────────────────────

class TestLogInvestigationCompleted:
    def test_writes_to_supabase(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p-001", bundle_id="b-001",
            analysis_id="a-001", category="EXPIRED_SESSION", escalate=False,
        )
        sb.table.assert_called_with("case_audit_log")
        sb.table.return_value.insert.assert_called_once()

    def test_entry_has_correct_action_type(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p", bundle_id="b", analysis_id="a",
            category="TIMEOUT", escalate=True,
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["action_type"] == "INVESTIGATION_COMPLETED"

    def test_outcome_escalated_when_escalate_true(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p", bundle_id="b", analysis_id="a",
            category="UNKNOWN", escalate=True,
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "ESCALATED"

    def test_outcome_completed_when_escalate_false(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p", bundle_id="b", analysis_id="a",
            category="EXPIRED_SESSION", escalate=False,
        )
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["outcome"] == "COMPLETED"

    def test_detail_includes_all_fields(self):
        audit, sb = make_audit_logger(with_sb=True)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="PLAN-X", bundle_id="BUNDLE-X",
            analysis_id="ANALYSIS-X", category="TIMEOUT", escalate=False,
        )
        row    = sb.table.return_value.insert.call_args[0][0]
        detail = row["action_detail"]
        assert detail["plan_id"]     == "PLAN-X"
        assert detail["bundle_id"]   == "BUNDLE-X"
        assert detail["analysis_id"] == "ANALYSIS-X"
        assert detail["category"]    == "TIMEOUT"
        assert detail["escalate"]    is False

    def test_noop_when_no_supabase_client(self):
        audit, _ = make_audit_logger(with_sb=False)
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p", bundle_id="b", analysis_id="a",
            category="TIMEOUT", escalate=True,
        )

    def test_never_raises_on_supabase_error(self):
        audit, sb = make_audit_logger(with_sb=True)
        sb.table.side_effect = Exception("Network error")
        case = make_case()
        audit.log_investigation_completed(
            case, plan_id="p", bundle_id="b", analysis_id="a",
            category="UNKNOWN", escalate=True,
        )
