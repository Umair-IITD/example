"""
tests/test_sprint224_proposal_guard.py

Sprint 2.24: BLOCKED_NO_REASONING guardrail tests.

Coverage:
  - ActionProposalService.propose() blocks with reasoning_required=True and reasoning_result=None
  - ActionProposalService.propose() passes with reasoning_result provided
  - ActionProposalService.propose() backwards compat: reasoning_required=False (default)
  - WorkflowEngine PROPOSE_ACTION guard: BLOCKED_NO_REASONING when REASON in workflow
  - WorkflowEngine PROPOSE_ACTION guard: no block when no REASON step in workflow
  - ActionProposalService BLOCKED_NO_REASONING response structure
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.actions.service import ActionProposalService
from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.models import Case
from case_engine.case_state import CaseState


def _make_case() -> Case:
    c = Case()
    c.topic         = "VKYC_Session_Failure"
    c.current_state = CaseState.WORKFLOW_ACTIVE
    return c


def _inv() -> dict:
    return {
        "topic": "VKYC_Session_Failure",
        "root_cause": {
            "category":   "NETWORK_FAILURE",
            "confidence": 0.85,
            "escalate":   False,
        },
    }


def _make_step(step_id: str, step_type: WorkflowStepType, on_success: str = "RESOLVE", on_failure: str = "ESCALATE") -> WorkflowStep:
    return WorkflowStep(
        step_index=0, step_id=step_id, step_type=step_type,
        name=step_id, on_success=on_success, on_failure=on_failure,
    )


def _make_defn(*steps: WorkflowStep) -> WorkflowDefinition:
    return WorkflowDefinition(
        workflow_id="test-wf", topic="VKYC_Session_Failure",
        version="1.0", name="Test", steps=tuple(steps),
    )


# ── ActionProposalService BLOCKED_NO_REASONING ────────────────────────────────

class TestActionProposalServiceReasoningGuard:
    def test_blocked_when_reasoning_required_and_no_result(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result=None,
        )
        assert r["status"] == "BLOCKED"
        assert r["block_reason"] == "NO_REASONING_RESULT"

    def test_not_blocked_when_reasoning_provided(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result={"status": "COMPLETED", "recommended_action": "RESET_SESSION"},
        )
        assert r.get("block_reason") != "NO_REASONING_RESULT"

    def test_backwards_compat_not_blocked_by_default(self):
        svc = ActionProposalService()
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result=None,
        )
        assert r.get("block_reason") != "NO_REASONING_RESULT"

    def test_blocked_response_has_bundle_id(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result=None,
        )
        assert "bundle_id" in r
        assert r["bundle_id"] != ""

    def test_blocked_response_has_blocked_at(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result=None,
        )
        assert "blocked_at" in r

    def test_blocked_response_proposals_empty(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=_inv(),
            reasoning_result=None,
        )
        assert r["proposals"] == []
        assert r["proposal_count"] == 0

    def test_investigation_required_still_takes_precedence(self):
        svc = ActionProposalService(reasoning_required=True)
        r   = svc.propose(
            topic="VKYC_Session_Failure",
            investigation_result=None,
            reasoning_result={"status": "COMPLETED"},
        )
        assert r["status"] == "BLOCKED"
        assert r["block_reason"] == "NO_INVESTIGATION_RESULT"

    def test_reasoning_required_flag_stored(self):
        svc = ActionProposalService(reasoning_required=True)
        assert svc._reasoning_required is True

    def test_reasoning_not_required_flag_default(self):
        svc = ActionProposalService()
        assert svc._reasoning_required is False


# ── WorkflowEngine PROPOSE_ACTION guard ───────────────────────────────────────

class TestWorkflowEngineProposeActionGuard:
    def _make_engine_and_workflow(self):
        reason_step  = _make_step("reason-1",  WorkflowStepType.REASON,        on_success="propose-1", on_failure="ESCALATE")
        propose_step = _make_step("propose-1", WorkflowStepType.PROPOSE_ACTION, on_success="RESOLVE",   on_failure="ESCALATE")
        defn         = _make_defn(reason_step, propose_step)
        engine       = WorkflowEngine()
        return engine, defn

    def test_guard_fires_when_reason_step_in_workflow_but_no_result(self):
        engine, defn = self._make_engine_and_workflow()
        propose_step = defn.step_by_id("propose-1")
        result       = WorkflowExecutionResult(workflow_id="test-wf")
        result.reasoning_result = None

        outcome = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome.step_results]
        assert "BLOCKED_NO_REASONING" in outcomes

    def test_guard_does_not_fire_when_reasoning_result_present(self):
        engine, defn = self._make_engine_and_workflow()
        propose_step = defn.step_by_id("propose-1")
        result       = WorkflowExecutionResult(workflow_id="test-wf")
        result.reasoning_result = {"status": "COMPLETED", "recommended_action": "RESET_SESSION"}

        outcome = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome.step_results]
        assert "BLOCKED_NO_REASONING" not in outcomes

    def test_guard_not_active_without_reason_step_in_workflow(self):
        propose_step = _make_step("propose-1", WorkflowStepType.PROPOSE_ACTION, on_success="RESOLVE", on_failure="ESCALATE")
        defn         = _make_defn(propose_step)
        engine       = WorkflowEngine()
        result       = WorkflowExecutionResult(workflow_id="test-wf")
        result.reasoning_result = None  # no REASON step in workflow → guard inactive

        outcome = engine._exec_propose_action(
            propose_step, defn, result, {}, None, None, _make_case()
        )
        outcomes = [s["outcome"] for s in outcome.step_results]
        assert "BLOCKED_NO_REASONING" not in outcomes
