"""
tests/test_sprint225_service.py

Sprint 2.25: ClarificationService tests.

Coverage:
  - READY path: returns dict with status=READY, ready_to_continue=True
  - NEEDS_CLARIFICATION path: returns dict with missing_slots populated
  - ESCALATE path: returns dict with status=ESCALATE
  - ERROR path: engine raises → service returns ERROR dict
  - Audit emission: CLARIFICATION_STARTED and CLARIFICATION_COMPLETED called when case+audit present
  - Audit skipped when case is None
  - Audit skipped when audit_logger is None
  - Result dict has all required keys
  - workflow_id and step_id echoed in result
  - topic echoed in result
  - build_clarification_service factory returns ClarificationService
  - Service never raises under any input
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.clarification.service import ClarificationService, build_clarification_service
from case_engine.clarification.models import ClarificationStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(case_id="case-1", slot_state=None):
    case = MagicMock()
    case.case_id = case_id
    case.ticket_id = "TKT-001"
    case.client = "test_client"
    case.slot_state = slot_state or {}
    return case


def _service(audit=None) -> ClarificationService:
    return ClarificationService(audit_logger=audit)


def _clarify(
    required_slots,
    slot_context=None,
    slot_state=None,
    case=None,
    audit=None,
    topic="VKYC_Session_Failure",
    workflow_id="wf-1",
    step_id="clarify_slots",
) -> dict:
    svc = _service(audit=audit)
    return svc.clarify(
        topic=topic,
        slot_context=slot_context or {},
        required_slots=required_slots,
        slot_state=slot_state,
        case=case,
        workflow_id=workflow_id,
        step_id=step_id,
    )


# ── READY path ────────────────────────────────────────────────────────────────

class TestClarificationServiceReady:
    def test_all_slots_present_returns_ready(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={"session_id": "KID-12345678"},
        )
        assert result["status"] == "READY"
        assert result["ready_to_continue"] is True

    def test_no_required_slots_returns_ready(self):
        result = _clarify(required_slots=[])
        assert result["status"] == "READY"
        assert result["ready_to_continue"] is True

    def test_ready_has_empty_missing_slots(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={"session_id": "abc"},
        )
        assert result["missing_slots"] == []

    def test_result_dict_required_keys(self):
        result = _clarify(required_slots=[])
        for key in [
            "status", "result_id", "missing_slots",
            "clarification_message", "ready_to_continue",
            "next_question", "topic", "workflow_id", "step_id",
        ]:
            assert key in result, f"Missing key: {key}"

    def test_topic_echoed_in_result(self):
        result = _clarify(required_slots=[], topic="OTP_Delivery_Failure")
        assert result["topic"] == "OTP_Delivery_Failure"

    def test_workflow_id_echoed(self):
        result = _clarify(required_slots=[], workflow_id="wf-xyz")
        assert result["workflow_id"] == "wf-xyz"

    def test_step_id_echoed(self):
        result = _clarify(required_slots=[], step_id="clarify_step_1")
        assert result["step_id"] == "clarify_step_1"


# ── NEEDS_CLARIFICATION path ──────────────────────────────────────────────────

class TestClarificationServiceNeedsClarification:
    def test_missing_slot_returns_needs_clarification(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert result["status"] == "NEEDS_CLARIFICATION"
        assert result["ready_to_continue"] is False

    def test_missing_slots_list_populated(self):
        result = _clarify(
            required_slots=["session_id", "phone_number"],
            slot_context={},
        )
        assert "session_id" in result["missing_slots"]

    def test_next_question_dict_present(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert result["next_question"] is not None
        assert "slot_name" in result["next_question"]

    def test_clarification_message_nonempty(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
        )
        assert len(result["clarification_message"]) > 0


# ── ESCALATE path ─────────────────────────────────────────────────────────────

class TestClarificationServiceEscalate:
    def test_max_attempts_exceeded_returns_escalate(self):
        result = _clarify(
            required_slots=["session_id"],
            slot_context={},
            slot_state={"session_id": {"attempt_count": 2, "max_attempts": 2}},
        )
        assert result["status"] == "ESCALATE"
        assert result["ready_to_continue"] is False


# ── ERROR path ────────────────────────────────────────────────────────────────

class TestClarificationServiceErrorPath:
    def test_exception_in_clarify_returns_error_dict(self):
        engine = MagicMock()
        engine.clarify.side_effect = RuntimeError("boom")
        svc = ClarificationService(engine=engine)
        result = svc.clarify(
            topic="TEST",
            slot_context={},
            required_slots=["session_id"],
            case=None,
            workflow_id="wf-1",
            step_id="step-1",
        )
        assert result["status"] == "ERROR"
        assert "error_at" in result
        assert result["ready_to_continue"] is False

    def test_error_result_has_workflow_id(self):
        engine = MagicMock()
        engine.clarify.side_effect = RuntimeError("boom")
        svc = ClarificationService(engine=engine)
        result = svc.clarify(
            topic="TEST",
            slot_context={},
            required_slots=[],
            workflow_id="wf-error-test",
            step_id="s-1",
        )
        assert result["workflow_id"] == "wf-error-test"


# ── Audit emission ─────────────────────────────────────────────────────────────

class TestClarificationServiceAuditEmission:
    def test_audit_started_called_when_case_and_audit_present(self):
        audit = MagicMock()
        case = _make_case()
        _clarify(required_slots=["session_id"], slot_context={}, case=case, audit=audit)
        audit.log_clarification_started.assert_called_once()

    def test_audit_completed_called_when_case_and_audit_present(self):
        audit = MagicMock()
        case = _make_case()
        _clarify(required_slots=["session_id"], slot_context={}, case=case, audit=audit)
        audit.log_clarification_completed.assert_called_once()

    def test_audit_not_called_when_case_is_none(self):
        audit = MagicMock()
        _clarify(required_slots=["session_id"], slot_context={}, case=None, audit=audit)
        audit.log_clarification_started.assert_not_called()
        audit.log_clarification_completed.assert_not_called()

    def test_audit_not_called_when_audit_is_none(self):
        case = _make_case()
        # Should not raise even with no audit logger
        result = _clarify(required_slots=["session_id"], slot_context={}, case=case, audit=None)
        assert "status" in result

    def test_audit_started_called_with_correct_topic(self):
        audit = MagicMock()
        case = _make_case()
        _clarify(
            required_slots=["session_id"],
            slot_context={},
            case=case,
            audit=audit,
            topic="VKYC_Session_Failure",
        )
        call_kwargs = audit.log_clarification_started.call_args
        assert "topic" in call_kwargs.kwargs or "topic" in str(call_kwargs)

    def test_audit_exception_does_not_propagate(self):
        audit = MagicMock()
        audit.log_clarification_started.side_effect = RuntimeError("audit fail")
        case = _make_case()
        # Should not raise
        result = _clarify(required_slots=[], slot_context={}, case=case, audit=audit)
        assert "status" in result


# ── Service never raises ──────────────────────────────────────────────────────

class TestClarificationServiceNeverRaises:
    def test_none_slot_context_does_not_raise(self):
        svc = _service()
        result = svc.clarify(
            topic="ANY",
            slot_context=None,  # type: ignore[arg-type]
            required_slots=[],
        )
        assert result is not None

    def test_none_required_slots_does_not_raise(self):
        svc = _service()
        result = svc.clarify(
            topic="ANY",
            slot_context={},
            required_slots=None,  # type: ignore[arg-type]
        )
        # May be READY or ERROR — should not raise
        assert "status" in result


# ── Factory ───────────────────────────────────────────────────────────────────

class TestBuildClarificationService:
    def test_factory_returns_service(self):
        svc = build_clarification_service()
        assert isinstance(svc, ClarificationService)

    def test_factory_with_audit_logger(self):
        audit = MagicMock()
        svc = build_clarification_service(audit_logger=audit)
        assert isinstance(svc, ClarificationService)

    def test_factory_service_is_functional(self):
        svc = build_clarification_service()
        result = svc.clarify(
            topic="TEST",
            slot_context={"session_id": "abc"},
            required_slots=["session_id"],
        )
        assert result["status"] == "READY"
