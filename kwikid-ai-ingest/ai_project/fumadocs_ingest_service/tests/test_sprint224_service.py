"""
tests/test_sprint224_service.py

Sprint 2.24: ReasoningService tests.

Coverage:
  - No investigation_result → BLOCKED result
  - Success path: COMPLETED status, recommended_action, should_escalate
  - Failure path: engine returns ESCALATE outcome → should_escalate=True
  - Audit events emitted on success
  - Audit events emitted on BLOCKED
  - No audit when audit_logger=None
  - No audit when case=None
  - Never raises (exception isolation)
  - build_reasoning_service factory
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.reasoning.service import ReasoningService, build_reasoning_service
from case_engine.reasoning.engine import build_reasoning_engine
from case_engine.reasoning.models import ReasoningOutcome


def _inv(category: str = "NETWORK_FAILURE", confidence: float = 0.85) -> dict:
    return {
        "topic": "VKYC_Session_Failure",
        "evidence_ids": ["ev-1"],
        "root_cause": {
            "category":   category,
            "confidence": confidence,
            "escalate":   False,
        },
    }


def _make_case() -> MagicMock:
    case = MagicMock()
    case.case_id   = "case-abc"
    case.ticket_id = "ticket-xyz"
    case.client    = "test-client"
    return case


# ── Factory ───────────────────────────────────────────────────────────────────

class TestBuildReasoningService:
    def test_returns_service_instance(self):
        svc = build_reasoning_service()
        assert isinstance(svc, ReasoningService)

    def test_with_audit_logger(self):
        audit = MagicMock()
        svc   = build_reasoning_service(audit_logger=audit)
        assert svc._audit is audit

    def test_without_audit_logger(self):
        svc = build_reasoning_service()
        assert svc._audit is None


# ── BLOCKED path (no investigation_result) ────────────────────────────────────

class TestServiceBlockedPath:
    def test_none_investigation_returns_blocked(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=None)
        assert r["status"] == "BLOCKED"

    def test_blocked_has_block_reason(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=None)
        assert r["block_reason"] == "NO_INVESTIGATION_RESULT"

    def test_blocked_has_bundle_id(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=None)
        assert r["bundle_id"] != ""

    def test_blocked_has_blocked_at(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=None)
        assert "blocked_at" in r

    def test_blocked_emits_audit_when_case_provided(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=None, case=_make_case())
        audit.log_reasoning_started.assert_called_once()

    def test_blocked_no_audit_when_no_case(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=None, case=None)
        audit.log_reasoning_started.assert_not_called()


# ── Success path ──────────────────────────────────────────────────────────────

class TestServiceSuccessPath:
    def test_completed_status_returned(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv())
        assert r["status"] == "COMPLETED"

    def test_recommended_action_in_result(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        assert r["recommended_action"] == "RESET_SESSION"

    def test_should_escalate_false_for_actionable(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        assert r["should_escalate"] is False

    def test_should_escalate_true_for_kyc_rejected(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv("KYC_REJECTED", confidence=0.95))
        assert r["should_escalate"] is True

    def test_result_id_present(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv())
        assert "result_id" in r

    def test_outcome_present(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv("NETWORK_FAILURE"))
        assert "outcome" in r

    def test_bundle_nested_in_result(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv())
        assert "bundle" in r

    def test_knowledge_result_none_accepted(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv(), knowledge_result=None)
        assert r["status"] == "COMPLETED"

    def test_knowledge_result_empty_accepted(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv(), knowledge_result={})
        assert r["status"] == "COMPLETED"


# ── Audit events ──────────────────────────────────────────────────────────────

class TestServiceAuditEvents:
    def test_reasoning_started_emitted(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=_inv(), case=_make_case())
        audit.log_reasoning_started.assert_called_once()

    def test_reasoning_completed_emitted(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=_inv(), case=_make_case())
        audit.log_reasoning_completed.assert_called_once()

    def test_no_audit_when_case_none(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=_inv(), case=None)
        audit.log_reasoning_started.assert_not_called()
        audit.log_reasoning_completed.assert_not_called()

    def test_no_audit_when_audit_none(self):
        svc = ReasoningService(audit_logger=None)
        # Should not raise
        svc.reason(investigation_result=_inv(), case=_make_case())

    def test_audit_started_receives_topic(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        inv   = _inv("NETWORK_FAILURE")
        inv["topic"] = "VKYC_Session_Failure"
        svc.reason(investigation_result=inv, case=_make_case())
        call_kwargs = audit.log_reasoning_started.call_args[1]
        assert call_kwargs.get("topic") == "VKYC_Session_Failure"

    def test_audit_completed_receives_outcome(self):
        audit = MagicMock()
        svc   = ReasoningService(audit_logger=audit)
        svc.reason(investigation_result=_inv("NETWORK_FAILURE"), case=_make_case())
        call_kwargs = audit.log_reasoning_completed.call_args[1]
        assert "outcome" in call_kwargs

    def test_audit_exception_does_not_crash(self):
        audit = MagicMock()
        audit.log_reasoning_started.side_effect = RuntimeError("audit crash")
        svc = ReasoningService(audit_logger=audit)
        r   = svc.reason(investigation_result=_inv(), case=_make_case())
        assert isinstance(r, dict)


# ── Exception isolation ───────────────────────────────────────────────────────

class TestServiceExceptionIsolation:
    def test_engine_exception_does_not_propagate(self):
        bad_engine = MagicMock()
        bad_engine.reason.side_effect = RuntimeError("engine crash")
        svc = ReasoningService(engine=bad_engine)
        r   = svc.reason(investigation_result=_inv())
        assert isinstance(r, dict)

    def test_engine_exception_returns_error_result(self):
        bad_engine = MagicMock()
        bad_engine.reason.side_effect = RuntimeError("engine crash")
        svc = ReasoningService(engine=bad_engine)
        r   = svc.reason(investigation_result=_inv())
        assert r.get("status") in ("ERROR", "BLOCKED")

    def test_completely_corrupt_input_does_not_raise(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result={"root_cause": "bad"})
        assert isinstance(r, dict)

    def test_never_raises_on_none_input(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=None)
        assert isinstance(r, dict)

    def test_workflow_id_and_step_id_optional(self):
        svc = ReasoningService()
        r   = svc.reason(investigation_result=_inv(), workflow_id="wf-1", step_id="s-1")
        assert r["status"] == "COMPLETED"
