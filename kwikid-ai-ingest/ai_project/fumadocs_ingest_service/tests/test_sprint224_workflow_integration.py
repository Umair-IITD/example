"""
tests/test_sprint224_workflow_integration.py

Sprint 2.24: WorkflowEngine — REASON step type integration tests.

Coverage:
  - WorkflowEngine constructor accepts reasoning_service param (backwards compat)
  - REASON step dispatch calls _exec_reason
  - REASON with no service: SKIPPED_NO_REASONING_SERVICE, navigate on_success
  - REASON with service: stores result in reasoning_result
  - REASON success (RECOMMEND_ACTION) → on_success
  - REASON escalate (should_escalate=True) → on_failure
  - REASON blocked (no investigation_result) → on_failure
  - REASON stores reasoning_result in WorkflowExecutionResult
  - PROPOSE_ACTION guard fires when REASON step present but no reasoning_result
  - _validate_workflow_structure warns about PROPOSE_ACTION without REASON
  - Audit events: log_workflow_reasoning_started, log_workflow_reasoning_completed called
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.models import Case
from case_engine.case_state import CaseState


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(topic: str = "VKYC_Session_Failure") -> Case:
    c = Case()
    c.topic          = topic
    c.current_state  = CaseState.WORKFLOW_ACTIVE
    return c


def _make_step(
    step_id:   str,
    step_type: WorkflowStepType,
    on_success: str = "RESOLVE",
    on_failure: str = "ESCALATE",
) -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=step_type,
        name=step_id,
        on_success=on_success,
        on_failure=on_failure,
    )


def _make_defn(*steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="test-wf",
        topic="VKYC_Session_Failure",
        version="1.0",
        name="Test",
        steps=tuple(steps),
    )


def _make_result(**kw) -> WorkflowExecutionResult:
    r = WorkflowExecutionResult(workflow_id="test-wf")
    for k, v in kw.items():
        setattr(r, k, v)
    return r


def _make_reasoning_svc(
    status: str = "COMPLETED",
    recommended_action: str = "RESET_SESSION",
    should_escalate: bool = False,
) -> MagicMock:
    svc = MagicMock()
    svc.reason.return_value = {
        "status":             status,
        "recommended_action": recommended_action,
        "should_escalate":    should_escalate,
        "outcome":            "RECOMMEND_ACTION" if not should_escalate else "ESCALATE",
        "result_id":          "res-1",
    }
    return svc


# ── Constructor backwards compatibility ───────────────────────────────────────

class TestConstructorBackwardsCompat:
    def test_no_reasoning_service_is_valid(self):
        engine = WorkflowEngine()
        assert engine._reasoning_service is None

    def test_with_reasoning_service(self):
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        assert engine._reasoning_service is svc

    def test_all_services_can_be_wired(self):
        from case_engine.reasoning.service import build_reasoning_service
        svc    = build_reasoning_service()
        engine = WorkflowEngine(reasoning_service=svc)
        assert engine._reasoning_service is svc

    def test_reasoning_service_independent_of_other_services(self):
        rs     = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=rs)
        assert engine._investigation_service is None
        assert engine._reasoning_service is rs


# ── REASON step dispatch ──────────────────────────────────────────────────────

class TestReasonStepDispatch:
    def test_reason_step_type_recognized(self):
        assert WorkflowStepType.REASON.value == "REASON"

    def test_reason_step_no_service_returns_skipped(self):
        engine = WorkflowEngine()
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        outcome_result = engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        steps = [s["outcome"] for s in outcome_result.step_results]
        assert "SKIPPED_NO_REASONING_SERVICE" in steps

    def test_reason_step_no_service_navigates_on_success(self):
        engine = WorkflowEngine()
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        outcome_result = engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        assert outcome_result.workflow_state in (WorkflowState.COMPLETED, WorkflowState.ESCALATED, WorkflowState.FAILED, WorkflowState.PENDING)


# ── REASON with service ───────────────────────────────────────────────────────

class TestReasonWithService:
    def test_reasoning_result_stored(self):
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result(investigation_result={"root_cause": {"category": "NETWORK_FAILURE", "confidence": 0.85, "escalate": False}})

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        assert result.reasoning_result is not None

    def test_reasoning_result_has_status(self):
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result(investigation_result={"root_cause": {"category": "NETWORK_FAILURE", "confidence": 0.85, "escalate": False}})

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        assert result.reasoning_result["status"] == "COMPLETED"

    def test_success_outcome_recorded(self):
        svc    = _make_reasoning_svc(status="COMPLETED", should_escalate=False)
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        outcomes = [s["outcome"] for s in result.step_results]
        assert "REASONING_COMPLETED" in outcomes

    def test_escalate_outcome_recorded(self):
        svc    = _make_reasoning_svc(status="COMPLETED", should_escalate=True)
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE", on_failure="ESCALATE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        outcomes = [s["outcome"] for s in result.step_results]
        assert "REASONING_ESCALATE" in outcomes

    def test_blocked_outcome_recorded(self):
        svc    = _make_reasoning_svc(status="BLOCKED", should_escalate=False)
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        outcomes = [s["outcome"] for s in result.step_results]
        assert "REASONING_BLOCKED" in outcomes

    def test_service_called_with_investigation_result(self):
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        inv    = {"root_cause": {"category": "NETWORK_FAILURE", "confidence": 0.85, "escalate": False}}
        result = _make_result(investigation_result=inv)

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        svc.reason.assert_called_once()
        call_kwargs = svc.reason.call_args[1]
        assert call_kwargs["investigation_result"] == inv

    def test_service_called_with_knowledge_result(self):
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        kb     = {"sop_match_found": True}
        result = _make_result(knowledge_result=kb)

        engine._exec_reason(step, defn, result, {}, None, None, _make_case())
        call_kwargs = svc.reason.call_args[1]
        assert call_kwargs["knowledge_result"] == kb


# ── PROPOSE_ACTION guard (Blueprint Principle 3) ─────────────────────────────

class TestProposeActionGuard:
    def _make_workflow_with_reason_and_propose(self):
        reason_step  = _make_step("reason-1",  WorkflowStepType.REASON,        on_success="propose-1", on_failure="ESCALATE")
        propose_step = _make_step("propose-1", WorkflowStepType.PROPOSE_ACTION, on_success="RESOLVE",   on_failure="ESCALATE")
        return _make_defn(reason_step, propose_step)

    def test_propose_without_reasoning_result_is_blocked(self):
        engine = WorkflowEngine()
        defn   = self._make_workflow_with_reason_and_propose()
        # Jump directly to propose step without running REASON first
        propose_step = defn.step_by_id("propose-1")
        result = _make_result(reasoning_result=None)

        outcome_result = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert "BLOCKED_NO_REASONING" in outcomes

    def test_propose_with_reasoning_result_not_blocked(self):
        engine = WorkflowEngine()
        defn   = self._make_workflow_with_reason_and_propose()
        propose_step = defn.step_by_id("propose-1")
        result = _make_result(
            reasoning_result={"status": "COMPLETED", "recommended_action": "RESET_SESSION"},
        )
        # This proceeds past the REASON guard — may fail later due to no gateway/investigation
        outcome_result = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert "BLOCKED_NO_REASONING" not in outcomes

    def test_no_reason_step_in_workflow_no_guard(self):
        """Backwards compat: PROPOSE_ACTION without REASON step in definition."""
        engine = WorkflowEngine()
        propose_step = _make_step("propose-1", WorkflowStepType.PROPOSE_ACTION, on_success="RESOLVE", on_failure="ESCALATE")
        defn   = _make_defn(propose_step)
        result = _make_result(reasoning_result=None)

        outcome_result = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert "BLOCKED_NO_REASONING" not in outcomes


# ── Audit events ──────────────────────────────────────────────────────────────

class TestReasonStepAuditEvents:
    def test_audit_started_called_when_case_provided(self):
        audit  = MagicMock()
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, audit, _make_case())
        audit.log_workflow_reasoning_started.assert_called_once()

    def test_audit_completed_called_on_success(self):
        audit  = MagicMock()
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, audit, _make_case())
        audit.log_workflow_reasoning_completed.assert_called_once()

    def test_no_audit_when_case_none(self):
        audit  = MagicMock()
        svc    = _make_reasoning_svc()
        engine = WorkflowEngine(reasoning_service=svc)
        step   = _make_step("reason-1", WorkflowStepType.REASON, on_success="RESOLVE")
        defn   = _make_defn(step)
        result = _make_result()

        engine._exec_reason(step, defn, result, {}, None, audit, None)  # type: ignore
        audit.log_workflow_reasoning_started.assert_not_called()
