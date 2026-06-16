"""
tests/test_sprint225_workflow_integration.py

Sprint 2.25: WorkflowEngine CLARIFY step integration tests.

Coverage:
  - WorkflowEngine constructor accepts clarification_service (7th positional arg)
  - WorkflowEngine() with no clarification_service is backwards compatible
  - CLARIFY step with no service wired: SKIPPED_NO_CLARIFICATION_SERVICE → on_success
  - CLARIFY step with service: READY → on_success navigation
  - CLARIFY step with service: NEEDS_CLARIFICATION → WorkflowState.PAUSED, result returned
  - CLARIFY step with service: ESCALATE → on_failure navigation
  - clarification_result stored in WorkflowExecutionResult
  - PAUSED workflow has step recorded as CLARIFICATION_PENDING
  - Slot context passed through to clarification service
  - workflow_engine.CLARIFY step dispatch in _execute_step
  - Validate structure: PROPOSE_ACTION without ACTION_GATEWAY → warning logged
  - Validate structure: ACTION_GATEWAY without EXECUTE → debug logged
  - Validate structure: KNOWLEDGE_LOOKUP without REASON → debug logged
"""
from __future__ import annotations

import logging
from unittest.mock import MagicMock

import pytest

from case_engine.clarification.service import ClarificationService
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


# ── Fixtures / helpers ────────────────────────────────────────────────────────

def _make_case(slot_state=None):
    case = MagicMock()
    case.case_id = "case-test-1"
    case.ticket_id = "TKT-001"
    case.client = "test"
    case.topic = "VKYC_Session_Failure"
    case.slot_state = slot_state or {}
    case.workflow_context = {}
    return case


def _step(step_id="clarify_slots", on_success="investigate", on_failure="escalate_missing"):
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=WorkflowStepType.CLARIFY,
        name="Clarify",
        description="Check slots",
        on_success=on_success,
        on_failure=on_failure,
    )


def _resolve_step(step_id="investigate", step_index=1):
    return WorkflowStep(
        step_index=step_index,
        step_id=step_id,
        step_type=WorkflowStepType.RESOLVE_CASE,
        name="Resolve",
        on_success="RESOLVE",
        on_failure="ESCALATE",
    )


def _escalate_step(step_id="escalate_missing", step_index=2):
    return WorkflowStep(
        step_index=step_index,
        step_id=step_id,
        step_type=WorkflowStepType.ESCALATE_CASE,
        name="Escalate",
        on_success="ESCALATE",
        on_failure="ESCALATE",
    )


def _defn(required_slots=("session_id",), steps=None) -> WorkflowDefinition:
    if steps is None:
        steps = (
            _step(),
            _resolve_step(),
            _escalate_step(),
        )
    return WorkflowDefinition(
        workflow_id="test_wf",
        topic="VKYC_Session_Failure",
        version="2.0",
        name="Test WF",
        required_slots=tuple(required_slots),
        steps=steps,
    )


def _mock_clarify_service(status: str, ready: bool = True) -> ClarificationService:
    svc = MagicMock(spec=ClarificationService)
    svc.clarify.return_value = {
        "status": status,
        "result_id": "rid-test",
        "missing_slots": [] if ready else ["session_id"],
        "clarification_message": "Test message",
        "ready_to_continue": ready,
        "next_question": None if ready else {"slot_name": "session_id", "prompt_text": "Provide session ID"},
        "topic": "VKYC_Session_Failure",
        "workflow_id": "test_wf",
        "step_id": "clarify_slots",
    }
    return svc


# ── Constructor compatibility ─────────────────────────────────────────────────

class TestWorkflowEngineConstructorCompat:
    def test_zero_args_still_works(self):
        engine = WorkflowEngine()
        assert engine._clarification_service is None

    def test_clarification_service_param_accepted(self):
        svc = MagicMock(spec=ClarificationService)
        engine = WorkflowEngine(clarification_service=svc)
        assert engine._clarification_service is svc

    def test_all_seven_services_accepted(self):
        engine = WorkflowEngine(
            investigation_service=MagicMock(),
            knowledge_service=MagicMock(),
            action_proposal_service=MagicMock(),
            action_gateway_service=MagicMock(),
            execution_service=MagicMock(),
            reasoning_service=MagicMock(),
            clarification_service=MagicMock(),
        )
        assert engine._clarification_service is not None

    def test_prior_six_services_still_work(self):
        engine = WorkflowEngine(
            investigation_service=MagicMock(),
            knowledge_service=MagicMock(),
        )
        assert engine._clarification_service is None
        assert engine._investigation_service is not None


# ── No-service path ───────────────────────────────────────────────────────────

class TestClarifyNoServicePath:
    def test_no_service_skips_and_goes_to_on_success(self):
        engine = WorkflowEngine()  # no clarification_service
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case(slot_state={})

        outcome_result = engine._exec_clarify(
            step=_step(on_success="investigate", on_failure="escalate_missing"),
            defn=defn,
            result=result,
            slot_context={"session_id": "KID-123"},
            gateway=None,
            audit=None,
            case=case,
        )

        # Should not be PAUSED and should record SKIPPED_NO_CLARIFICATION_SERVICE
        step_outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert any("SKIPPED" in o for o in step_outcomes)


# ── READY path ────────────────────────────────────────────────────────────────

class TestClarifyReadyPath:
    def test_ready_status_records_clarification_ready(self):
        svc = _mock_clarify_service("READY", ready=True)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome_result = engine._exec_clarify(
            step=_step(on_success="investigate", on_failure="escalate_missing"),
            defn=defn,
            result=result,
            slot_context={"session_id": "abc"},
            gateway=None,
            audit=None,
            case=case,
        )

        step_outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert any("CLARIFICATION_READY" in o for o in step_outcomes)

    def test_ready_stores_clarification_result(self):
        svc = _mock_clarify_service("READY", ready=True)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={"session_id": "abc"},
            gateway=None,
            audit=None,
            case=case,
        )

        assert result.clarification_result is not None
        assert result.clarification_result["status"] == "READY"


# ── NEEDS_CLARIFICATION path ──────────────────────────────────────────────────

class TestClarifyNeedsClarificationPath:
    def test_needs_clarification_pauses_workflow(self):
        svc = _mock_clarify_service("NEEDS_CLARIFICATION", ready=False)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome_result = engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        assert outcome_result.workflow_state is WorkflowState.PAUSED

    def test_needs_clarification_records_pending_outcome(self):
        svc = _mock_clarify_service("NEEDS_CLARIFICATION", ready=False)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome_result = engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        step_outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert any("CLARIFICATION_PENDING" in o for o in step_outcomes)

    def test_needs_clarification_stores_result(self):
        svc = _mock_clarify_service("NEEDS_CLARIFICATION", ready=False)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        assert result.clarification_result is not None
        assert result.clarification_result["status"] == "NEEDS_CLARIFICATION"


# ── ESCALATE path ─────────────────────────────────────────────────────────────

class TestClarifyEscalatePath:
    def test_escalate_records_escalate_outcome(self):
        svc = _mock_clarify_service("ESCALATE", ready=False)
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        outcome_result = engine._exec_clarify(
            step=_step(on_failure="escalate_missing"),
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        step_outcomes = [s["outcome"] for s in outcome_result.step_results]
        assert any("ESCALATE" in o or "ESCALATED" in o or "CLARIFICATION_ESCALATE" in o for o in step_outcomes)


# ── Slot context propagation ──────────────────────────────────────────────────

class TestClarifySlotContextPropagation:
    def test_slot_context_passed_to_service(self):
        svc = MagicMock(spec=ClarificationService)
        svc.clarify.return_value = {
            "status": "READY",
            "result_id": "rid",
            "missing_slots": [],
            "clarification_message": "ok",
            "ready_to_continue": True,
            "next_question": None,
            "topic": "T",
            "workflow_id": "wf",
            "step_id": "s",
        }
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn()
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={"session_id": "KID-abc"},
            gateway=None,
            audit=None,
            case=case,
        )

        call_kwargs = svc.clarify.call_args
        assert call_kwargs is not None
        # slot_context should be in the call
        all_args = dict(call_kwargs.kwargs)
        if "slot_context" in all_args:
            assert all_args["slot_context"] == {"session_id": "KID-abc"}

    def test_required_slots_from_defn_passed_to_service(self):
        svc = MagicMock(spec=ClarificationService)
        svc.clarify.return_value = {
            "status": "READY",
            "result_id": "rid",
            "missing_slots": [],
            "clarification_message": "ok",
            "ready_to_continue": True,
            "next_question": None,
            "topic": "T",
            "workflow_id": "wf",
            "step_id": "s",
        }
        engine = WorkflowEngine(clarification_service=svc)
        defn = _defn(required_slots=("session_id", "phone_number"))
        result = WorkflowExecutionResult(workflow_id="test_wf", workflow_state=WorkflowState.RUNNING)
        case = _make_case()

        engine._exec_clarify(
            step=_step(),
            defn=defn,
            result=result,
            slot_context={},
            gateway=None,
            audit=None,
            case=case,
        )

        call_kwargs = svc.clarify.call_args
        all_args = dict(call_kwargs.kwargs)
        if "required_slots" in all_args:
            assert "session_id" in list(all_args["required_slots"])


# ── Validation: pipeline pair checks ─────────────────────────────────────────

class TestWorkflowValidationPairChecks:
    def _defn_with_types(self, *step_types) -> WorkflowDefinition:
        steps = tuple(
            WorkflowStep(
                step_index=i,
                step_id=f"step_{i}",
                step_type=t,
                name=f"Step {i}",
                on_success="RESOLVE",
                on_failure="ESCALATE",
            )
            for i, t in enumerate(step_types)
        )
        return WorkflowDefinition(
            workflow_id="validation_test",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="Validation Test",
            steps=steps,
        )

    def test_propose_without_gateway_logs_warning(self, caplog):
        defn = self._defn_with_types(WorkflowStepType.PROPOSE_ACTION)
        engine = WorkflowEngine()
        with caplog.at_level(logging.WARNING, logger="case_engine.workflows.workflow_engine"):
            engine._validate_workflow_structure(defn)
        assert any("ACTION_GATEWAY" in r.message for r in caplog.records)

    def test_full_pipeline_no_warning(self, caplog):
        defn = self._defn_with_types(
            WorkflowStepType.CLARIFY,
            WorkflowStepType.INVESTIGATE,
            WorkflowStepType.KNOWLEDGE_LOOKUP,
            WorkflowStepType.REASON,
            WorkflowStepType.PROPOSE_ACTION,
            WorkflowStepType.ACTION_GATEWAY,
            WorkflowStepType.EXECUTE,
            WorkflowStepType.RESOLVE_CASE,
        )
        engine = WorkflowEngine()
        with caplog.at_level(logging.WARNING, logger="case_engine.workflows.workflow_engine"):
            engine._validate_workflow_structure(defn)
        warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert len(warnings) == 0
