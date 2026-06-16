"""
tests/test_sprint222_workflow_integration.py

Sprint 2.22: Tests for WorkflowEngine ACTION_GATEWAY step integration

Coverage:
- WorkflowEngine constructor: action_gateway_service param accepted
- WorkflowEngine() with no args: backwards compat
- WorkflowEngine(action_gateway_service=svc): wired correctly
- WorkflowStepType.ACTION_GATEWAY dispatched to _exec_action_gateway
- APPROVED -> on_success navigation
- PENDING_APPROVAL -> WorkflowState.PAUSED
- BLOCKED -> on_failure navigation
- No gateway service wired -> SKIPPED_NO_GATEWAY_SERVICE + on_success
- gateway_result stored in WorkflowExecutionResult
- Exception inside gateway service -> on_failure (never raises)
"""
import pytest
from unittest.mock import MagicMock, patch

from case_engine.workflows.models import (
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine


def _case() -> MagicMock:
    c = MagicMock()
    c.case_id = "case-001"
    c.ticket_id = "ticket-001"
    c.client = "test"
    return c


def _step(
    step_id: str = "gateway-step",
    on_success: str = "RESOLVE",
    on_failure: str = "ESCALATE",
) -> WorkflowStep:
    return WorkflowStep(
        step_index=0,
        step_id=step_id,
        step_type=WorkflowStepType.ACTION_GATEWAY,
        name="Action Gateway",
        on_success=on_success,
        on_failure=on_failure,
    )


def _defn(topic: str = "VKYC_Session_Failure", steps=None) -> MagicMock:
    d = MagicMock()
    d.workflow_id = "wf-001"
    d.topic = topic
    d.steps = steps or []
    d.step_by_id.return_value = None  # terminal navigation
    return d


def _result_with_proposal() -> WorkflowExecutionResult:
    r = WorkflowExecutionResult(workflow_id="wf-001")
    r.action_proposal_result = {
        "status": "COMPLETED",
        "bundle_id": "bundle-001",
        "top_proposal": {
            "action_type": "RESET_SESSION",
            "risk_assessment": {"risk_level": "SAFE"},
        },
    }
    r.investigation_result = {
        "status": "COMPLETED",
        "root_cause": {"category": "TIMEOUT", "confidence": 0.9},
    }
    return r


# ── Constructor backwards compat ──────────────────────────────────────────────

class TestConstructor:
    def test_no_args(self):
        engine = WorkflowEngine()
        assert engine._action_gateway_service is None

    def test_with_all_services(self):
        ags = MagicMock()
        engine = WorkflowEngine(action_gateway_service=ags)
        assert engine._action_gateway_service is ags

    def test_prior_params_still_work(self):
        inv = MagicMock()
        kb = MagicMock()
        aps = MagicMock()
        ags = MagicMock()
        engine = WorkflowEngine(
            investigation_service=inv,
            knowledge_service=kb,
            action_proposal_service=aps,
            action_gateway_service=ags,
        )
        assert engine._investigation_service is inv
        assert engine._knowledge_service is kb
        assert engine._action_proposal_service is aps
        assert engine._action_gateway_service is ags


# ── ACTION_GATEWAY dispatch ───────────────────────────────────────────────────

class TestActionGatewayDispatch:
    def test_action_gateway_step_type_dispatches(self):
        """WorkflowEngine dispatches ACTION_GATEWAY steps to _exec_action_gateway."""
        ags = MagicMock()
        ags.process.return_value = {
            "status": "APPROVED",
            "can_execute": True,
            "risk_level": "SAFE",
            "block_reason": None,
            "result_id": "r1",
            "gateway_decision": {},
            "approval_decision": {},
            "created_at": "2026-06-12T00:00:00+00:00",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step()
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        ags.process.assert_called_once()

    def test_approved_stores_gateway_result(self):
        ags = MagicMock()
        ags.process.return_value = {
            "status": "APPROVED", "can_execute": True, "risk_level": "SAFE",
            "block_reason": None, "result_id": "r1",
            "gateway_decision": {}, "approval_decision": {}, "created_at": "t",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step()
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.gateway_result is not None
        assert result.gateway_result["status"] == "APPROVED"

    def test_approved_navigates_on_success(self):
        ags = MagicMock()
        ags.process.return_value = {
            "status": "APPROVED", "can_execute": True, "risk_level": "SAFE",
            "block_reason": None, "result_id": "r1",
            "gateway_decision": {}, "approval_decision": {}, "created_at": "t",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_success="RESOLVE", on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        # RESOLVE terminal: result.workflow_state should be COMPLETED
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_pending_approval_pauses_workflow(self):
        ags = MagicMock()
        ags.process.return_value = {
            "status": "PENDING_APPROVAL", "can_execute": False, "risk_level": "REVERSIBLE",
            "block_reason": None, "result_id": "r1",
            "gateway_decision": {}, "approval_decision": {}, "created_at": "t",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step()
        result = _result_with_proposal()
        defn = _defn()

        out = engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert out.workflow_state == WorkflowState.PAUSED

    def test_blocked_navigates_on_failure(self):
        ags = MagicMock()
        ags.process.return_value = {
            "status": "BLOCKED", "can_execute": False, "risk_level": "HIGH_RISK",
            "block_reason": "no_investigation", "result_id": "r1",
            "gateway_decision": {}, "approval_decision": None, "created_at": "t",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_success="RESOLVE", on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_error_status_navigates_on_failure(self):
        ags = MagicMock()
        ags.process.return_value = {
            "status": "ERROR", "can_execute": False, "risk_level": "HIGH_RISK",
            "block_reason": "internal_error", "result_id": "r1",
            "gateway_decision": {}, "approval_decision": None, "created_at": "t",
        }
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_success="RESOLVE", on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.workflow_state == WorkflowState.ESCALATED


# ── No gateway service wired ──────────────────────────────────────────────────

class TestNoGatewayServiceWired:
    def test_skipped_outcome_recorded(self):
        engine = WorkflowEngine()  # no action_gateway_service
        step = _step(on_success="RESOLVE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        outcomes = [s["outcome"] for s in result.step_results]
        assert any("SKIPPED" in o for o in outcomes)

    def test_navigates_on_success_when_no_service(self):
        engine = WorkflowEngine()
        step = _step(on_success="RESOLVE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_gateway_result_not_set_when_no_service(self):
        engine = WorkflowEngine()
        step = _step(on_success="RESOLVE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.gateway_result is None


# ── Exception isolation ───────────────────────────────────────────────────────

class TestExceptionIsolation:
    def test_service_exception_does_not_raise(self):
        ags = MagicMock()
        ags.process.side_effect = RuntimeError("gateway exploded")
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        try:
            engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        except Exception as e:
            pytest.fail(f"_exec_action_gateway raised: {e}")

    def test_service_exception_navigates_on_failure(self):
        ags = MagicMock()
        ags.process.side_effect = RuntimeError("boom")
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_service_exception_records_step_result(self):
        ags = MagicMock()
        ags.process.side_effect = RuntimeError("boom")
        engine = WorkflowEngine(action_gateway_service=ags)
        step = _step(on_failure="ESCALATE")
        result = _result_with_proposal()
        defn = _defn()

        engine._exec_action_gateway(step, defn, result, {}, None, None, _case())
        outcomes = [s["outcome"] for s in result.step_results]
        assert any("EXCEPTION" in o for o in outcomes)
