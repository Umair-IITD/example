"""
tests/test_sprint226_attempt_tracking.py

Sprint 2.26 Part C: CaseService.resume_clarification_workflow() attempt tracking tests.

Coverage:
  - No registry → returns FAILED result
  - PENDING slots get attempt_count incremented
  - FILLED slots do not get incremented
  - Max attempts exceeded → ESCALATED
  - Max attempts NOT exceeded → calls resume_after_clarification
  - Audit events emitted on increment
  - Slot_state persisted on each cycle
  - Never raises
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch, call

from case_engine.case_state import CaseState
from case_engine.slot_filling.models import SlotStatus, SlotValue
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
from case_engine.service import CaseService, WorkflowStartResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(
    case_id: str = "case-track-1",
    current_state: CaseState = CaseState.AWAITING_INPUT,
    workflow_context: dict | None = None,
    slot_state: dict | None = None,
    workflow_state: str = WorkflowState.PAUSED.value,
    workflow_id: str = "wf-vkyc",
):
    case = MagicMock()
    case.case_id          = case_id
    case.ticket_id        = "ticket-1"
    case.client           = "unity_bank"
    case.topic            = "VKYC_Session_Failure"
    case.current_state    = current_state
    case.workflow_context = workflow_context or {"workflow_id": workflow_id, "current_step_id": "clarify_slots"}
    case.slot_state       = slot_state or {}
    case.workflow_state   = workflow_state
    case.workflow_id      = workflow_id
    case.escalation_reason = None
    return case


def _make_service(
    workflow_engine_result: WorkflowExecutionResult | None = None,
) -> tuple[CaseService, MagicMock]:
    from case_engine.workflows.playbook_registry import PlaybookRegistry

    repo          = MagicMock()
    audit_logger  = MagicMock()
    registry      = PlaybookRegistry.build()
    wf_engine     = MagicMock()
    sm            = MagicMock()
    sm.safe_transition = MagicMock()

    if workflow_engine_result is None:
        workflow_engine_result = WorkflowExecutionResult(
            workflow_state=WorkflowState.RUNNING,
            step_results=[],
        )
    wf_engine.resume_after_clarification.return_value = workflow_engine_result

    svc = CaseService(
        repository=repo,
        audit_logger=audit_logger,
        workflow_engine=wf_engine,
        playbook_registry=registry,
    )
    svc._sm = sm
    return svc, audit_logger


def _pending_slot(slot_name: str, attempt: int = 0) -> SlotValue:
    return SlotValue(slot_name=slot_name, status=SlotStatus.PENDING, value=None, attempt_count=attempt)


def _filled_slot(slot_name: str) -> SlotValue:
    return SlotValue(slot_name=slot_name, status=SlotStatus.FILLED, value="value-x", attempt_count=0)


# ── No-registry path ──────────────────────────────────────────────────────────

class TestAttemptTrackingNoRegistry:
    def test_no_registry_returns_failed(self):
        repo   = MagicMock()
        audit  = MagicMock()
        svc    = CaseService(repository=repo, audit_logger=audit)  # no registry
        case   = _make_case()
        result = svc.resume_clarification_workflow(case, {})
        assert result.workflow_state == WorkflowState.FAILED.value
        assert result.escalation_reason == "no_registry"


# ── Attempt count increment ───────────────────────────────────────────────────

class TestAttemptCountIncrement:
    def test_pending_slot_incremented(self):
        svc, audit = _make_service()
        slot = _pending_slot("session_id", attempt=0)
        updated_slots = {"session_id": slot}
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        # After the call, attempt_count should be 1 for the PENDING slot
        assert updated_slots["session_id"].attempt_count == 1

    def test_filled_slot_not_incremented(self):
        svc, audit = _make_service()
        updated_slots = {
            "session_id":   _pending_slot("session_id", attempt=0),
            "phone_number": _filled_slot("phone_number"),
        }
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        # FILLED slot should NOT be incremented
        assert updated_slots["phone_number"].attempt_count == 0

    def test_multiple_pending_all_incremented(self):
        svc, audit = _make_service()
        updated_slots = {
            "session_id":   _pending_slot("session_id",   attempt=0),
            "phone_number": _pending_slot("phone_number", attempt=1),
        }
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        assert updated_slots["session_id"].attempt_count   == 1
        assert updated_slots["phone_number"].attempt_count == 2

    def test_audit_event_emitted_for_increment(self):
        svc, audit = _make_service()
        updated_slots = {"session_id": _pending_slot("session_id", attempt=0)}
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        audit.log_clarification_attempt_incremented.assert_called()


# ── Max attempts exceeded → ESCALATED ────────────────────────────────────────

class TestMaxAttemptsExceeded:
    def test_escalates_when_max_reached(self):
        svc, audit = _make_service()
        # attempt_count=1 + increment = 2 >= max_attempts=2 → ESCALATED
        updated_slots = {"session_id": _pending_slot("session_id", attempt=1)}
        case = _make_case()
        result = svc.resume_clarification_workflow(case, updated_slots, max_attempts=2)
        assert result.escalated is True
        assert result.workflow_state == WorkflowState.ESCALATED.value

    def test_not_escalated_when_below_max(self):
        svc, audit = _make_service()
        updated_slots = {"session_id": _pending_slot("session_id", attempt=0)}
        case = _make_case()
        result = svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        # 0 + 1 = 1 < 5, so should NOT be escalated at this point
        assert result.escalated is False

    def test_escalation_reason_mentions_slots(self):
        svc, audit = _make_service()
        updated_slots = {"session_id": _pending_slot("session_id", attempt=1)}
        case = _make_case()
        result = svc.resume_clarification_workflow(case, updated_slots, max_attempts=2)
        assert result.escalation_reason is not None
        assert "session_id" in result.escalation_reason or "max_attempts" in result.escalation_reason

    def test_wf_engine_not_called_when_escalated(self):
        repo  = MagicMock()
        audit = MagicMock()
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry  = PlaybookRegistry.build()
        wf_engine = MagicMock()
        sm        = MagicMock()
        sm.safe_transition = MagicMock()
        svc = CaseService(
            repository=repo,
            audit_logger=audit,
            workflow_engine=wf_engine,
            playbook_registry=registry,
        )
        svc._sm = sm
        updated_slots = {"session_id": _pending_slot("session_id", attempt=5)}
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=2)
        wf_engine.resume_after_clarification.assert_not_called()


# ── Engine call path ──────────────────────────────────────────────────────────

class TestResumeCallsEngine:
    def test_engine_called_when_below_max(self):
        repo  = MagicMock()
        audit = MagicMock()
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry  = PlaybookRegistry.build()
        wf_engine = MagicMock()
        sm        = MagicMock()
        sm.safe_transition = MagicMock()
        wf_engine.resume_after_clarification.return_value = WorkflowExecutionResult(
            workflow_state=WorkflowState.PAUSED
        )
        svc = CaseService(
            repository=repo,
            audit_logger=audit,
            workflow_engine=wf_engine,
            playbook_registry=registry,
        )
        svc._sm = sm
        updated_slots = {"session_id": _pending_slot("session_id", attempt=0)}
        case = _make_case()
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        wf_engine.resume_after_clarification.assert_called_once()

    def test_result_workflow_id_from_engine(self):
        svc, _ = _make_service(
            WorkflowExecutionResult(
                workflow_id="wf-test",
                workflow_state=WorkflowState.PAUSED,
            )
        )
        updated_slots = {"session_id": _pending_slot("session_id", attempt=0)}
        case   = _make_case()
        result = svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        assert result.workflow_id == "wf-test"

    def test_paused_result_transitions_to_awaiting_input(self):
        sm = MagicMock()
        sm.safe_transition = MagicMock()
        repo  = MagicMock()
        audit = MagicMock()
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry  = PlaybookRegistry.build()
        wf_engine = MagicMock()
        wf_engine.resume_after_clarification.return_value = WorkflowExecutionResult(
            workflow_state=WorkflowState.PAUSED,
            step_results=[],
        )
        svc     = CaseService(
            repository=repo,
            audit_logger=audit,
            workflow_engine=wf_engine,
            playbook_registry=registry,
        )
        svc._sm = sm
        updated_slots = {"session_id": _pending_slot("session_id", attempt=0)}
        case = _make_case(current_state=CaseState.WORKFLOW_ACTIVE)
        svc.resume_clarification_workflow(case, updated_slots, max_attempts=5)
        sm.safe_transition.assert_called()
        call_args = [str(c) for c in sm.safe_transition.call_args_list]
        assert any("awaiting_clarification_response" in c or "AWAITING_INPUT" in c for c in call_args)


# ── Never raises ──────────────────────────────────────────────────────────────

class TestResumeNeverRaises:
    def test_returns_failed_on_engine_exception(self):
        repo  = MagicMock()
        audit = MagicMock()
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry  = PlaybookRegistry.build()
        wf_engine = MagicMock()
        wf_engine.resume_after_clarification.side_effect = RuntimeError("boom")
        sm        = MagicMock()
        sm.safe_transition = MagicMock()
        svc = CaseService(
            repository=repo,
            audit_logger=audit,
            workflow_engine=wf_engine,
            playbook_registry=registry,
        )
        svc._sm = sm
        case   = _make_case()
        result = svc.resume_clarification_workflow(case, {}, max_attempts=5)
        assert result.workflow_state == WorkflowState.FAILED.value

    def test_returns_result_on_audit_exception(self):
        repo  = MagicMock()
        audit = MagicMock()
        audit.log_clarification_attempt_incremented.side_effect = RuntimeError("audit bomb")
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        registry  = PlaybookRegistry.build()
        wf_engine = MagicMock()
        wf_engine.resume_after_clarification.return_value = WorkflowExecutionResult(
            workflow_state=WorkflowState.PAUSED
        )
        sm = MagicMock()
        sm.safe_transition = MagicMock()
        svc = CaseService(
            repository=repo,
            audit_logger=audit,
            workflow_engine=wf_engine,
            playbook_registry=registry,
        )
        svc._sm = sm
        case   = _make_case()
        result = svc.resume_clarification_workflow(case, {"session_id": _pending_slot("session_id")}, max_attempts=5)
        # Should still return a result (audit failure is non-fatal)
        assert result is not None
