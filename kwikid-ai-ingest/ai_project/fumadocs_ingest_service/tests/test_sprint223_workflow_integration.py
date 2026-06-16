"""
tests/test_sprint223_workflow_integration.py

Sprint 2.23: WorkflowEngine + ExecutionService integration tests.

Coverage:
  - WorkflowEngine accepts execution_service constructor param
  - WorkflowEngine(execution_service=None) -- backwards compatible
  - All prior constructor params still accepted
  - EXECUTE step dispatch: success path --> COMPLETED
  - EXECUTE step dispatch: failure path --> ESCALATED
  - EXECUTE step with no service: SKIPPED_NO_EXECUTION_SERVICE --> on_success
  - EXECUTE step with gateway_result.can_execute=False --> BLOCKED_GATEWAY_NOT_APPROVED
  - WorkflowExecutionResult.execution_result stored after EXECUTE step
  - Exception isolation: execution_service raises --> on_failure
  - Architecture validation: EXECUTE without ACTION_GATEWAY logs warning
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from case_engine.workflows.models import (
    WorkflowDefinition,
    WorkflowExecutionResult,
    WorkflowState,
    WorkflowStep,
    WorkflowStepType,
)
from case_engine.workflows.workflow_engine import WorkflowEngine
from case_engine.execution.models import ExecutionBundle, ExecutionStatus


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_case(topic: str = "VKYC_Session_Failure") -> MagicMock:
    c = MagicMock()
    c.case_id = "case-001"
    c.ticket_id = "ticket-001"
    c.client = "test"
    c.topic = topic
    c.workflow_context = None
    return c


def _make_execute_playbook(topic: str = "VKYC_Session_Failure") -> MagicMock:
    step = WorkflowStep(
        step_index=0,
        step_id="execute_step",
        step_type=WorkflowStepType.EXECUTE,
        name="Execute Action",
        action_type="resend_otp",
        action_namespace="otp",
        risk_level="SAFE",
        on_success="RESOLVE",
        on_failure="ESCALATE",
    )
    defn = WorkflowDefinition(
        workflow_id="wf-execute-test",
        topic=topic,
        version="1.0",
        name="Execute Test Playbook",
        steps=(step,),
    )
    registry = MagicMock()
    registry.get.return_value = defn
    return registry


def _make_success_bundle() -> ExecutionBundle:
    from case_engine.execution.models import ExecutionAttempt, ExecutionResult
    result = ExecutionResult(
        result_id="r-001", adapter_name="mock",
        action_type="resend_otp", action_params={},
        status=ExecutionStatus.SUCCESS, success=True,
        response_data={}, error_code=None, error_message=None,
        executed_at="2026-06-12T10:00:00+00:00", duration_ms=10,
    )
    attempt = ExecutionAttempt(
        attempt_id="a-001", attempt_number=1,
        execution_result=result, recovery_strategy=None,
        attempted_at="2026-06-12T10:00:00+00:00",
    )
    return ExecutionBundle(
        bundle_id="b-001", case_id="case-001", action_type="resend_otp",
        action_namespace="otp", attempts=(attempt,),
        final_status=ExecutionStatus.SUCCESS, total_attempts=1,
        created_at="2026-06-12T10:00:00+00:00",
        completed_at="2026-06-12T10:00:01+00:00",
    )


def _make_failed_bundle() -> ExecutionBundle:
    from case_engine.execution.models import ExecutionAttempt, ExecutionResult
    result = ExecutionResult(
        result_id="r-002", adapter_name="mock",
        action_type="resend_otp", action_params={},
        status=ExecutionStatus.FAILED, success=False,
        response_data={}, error_code="FAIL", error_message="failed",
        executed_at="2026-06-12T10:00:00+00:00", duration_ms=10,
    )
    attempt = ExecutionAttempt(
        attempt_id="a-002", attempt_number=1,
        execution_result=result, recovery_strategy=None,
        attempted_at="2026-06-12T10:00:00+00:00",
    )
    return ExecutionBundle(
        bundle_id="b-002", case_id="case-001", action_type="resend_otp",
        action_namespace="otp", attempts=(attempt,),
        final_status=ExecutionStatus.FAILED, total_attempts=1,
        created_at="2026-06-12T10:00:00+00:00",
        completed_at="2026-06-12T10:00:01+00:00",
    )


# ── Constructor ────────────────────────────────────────────────────────────────

class TestWorkflowEngineConstructor:
    def test_execution_service_param_accepted(self):
        svc = MagicMock()
        engine = WorkflowEngine(execution_service=svc)
        assert engine._execution_service is svc

    def test_none_execution_service_accepted(self):
        engine = WorkflowEngine(execution_service=None)
        assert engine._execution_service is None

    def test_all_services_accepted(self):
        inv = MagicMock()
        kb = MagicMock()
        aps = MagicMock()
        ags = MagicMock()
        exs = MagicMock()
        engine = WorkflowEngine(
            investigation_service=inv,
            knowledge_service=kb,
            action_proposal_service=aps,
            action_gateway_service=ags,
            execution_service=exs,
        )
        assert engine._execution_service is exs
        assert engine._action_gateway_service is ags


# ── EXECUTE dispatch ──────────────────────────────────────────────────────────

class TestExecuteDispatch:
    def test_execute_success_state_completed(self):
        svc = MagicMock()
        svc.process.return_value = _make_success_bundle()
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_execute_success_result_stored(self):
        svc = MagicMock()
        svc.process.return_value = _make_success_bundle()
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.execution_result is not None
        assert result.execution_result.get("bundle_id") == "b-001"

    def test_execute_failure_state_escalated(self):
        svc = MagicMock()
        svc.process.return_value = _make_failed_bundle()
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.workflow_state == WorkflowState.ESCALATED

    def test_execute_service_called_with_action_type(self):
        svc = MagicMock()
        svc.process.return_value = _make_success_bundle()
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        engine.start(case, registry, {}, gateway=None, audit=None)
        call_kwargs = svc.process.call_args
        assert call_kwargs.kwargs.get("action_type") == "resend_otp" or \
               call_kwargs[1].get("action_type") == "resend_otp" or \
               "resend_otp" in str(call_kwargs)

    def test_execute_rollback_completed_navigates_on_success(self):
        from case_engine.execution.models import ExecutionAttempt, ExecutionResult
        rb_result = ExecutionResult(
            result_id="r-x", adapter_name="mock", action_type="reset_session",
            action_params={}, status=ExecutionStatus.ROLLBACK_COMPLETED, success=False,
            response_data={}, error_code=None, error_message=None,
            executed_at="2026-06-12T10:00:00+00:00", duration_ms=10,
        )
        rb_attempt = ExecutionAttempt(
            attempt_id="a-x", attempt_number=1,
            execution_result=rb_result, recovery_strategy=None,
            attempted_at="2026-06-12T10:00:00+00:00",
        )
        rb_bundle = ExecutionBundle(
            bundle_id="b-rb", case_id="c", action_type="reset_session",
            action_namespace="", attempts=(rb_attempt,),
            final_status=ExecutionStatus.ROLLBACK_COMPLETED, total_attempts=1,
            created_at="2026-06-12T10:00:00+00:00",
            completed_at="2026-06-12T10:00:01+00:00",
        )
        svc = MagicMock()
        svc.process.return_value = rb_bundle
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.workflow_state == WorkflowState.COMPLETED


# ── No service wired ──────────────────────────────────────────────────────────

class TestNoExecutionService:
    def test_no_service_skipped_navigates_on_success(self):
        engine = WorkflowEngine(execution_service=None)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result is not None
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_no_service_execution_result_none(self):
        engine = WorkflowEngine(execution_service=None)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.execution_result is None


# ── Gateway guard ─────────────────────────────────────────────────────────────

class TestGatewayGuard:
    def test_gateway_not_approved_escalates(self):
        svc = MagicMock()
        svc.process.return_value = _make_success_bundle()
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()

        # Pre-set gateway_result with can_execute=False in workflow context
        # We need to inject gateway_result into the result before EXECUTE runs
        # This is done by patching _exec_execute to check result.gateway_result
        # The simplest test: override via monkey-patching the result
        original_start = engine._execute_step

        def mock_execute_step(step, defn, result, slot_context, gateway, audit, case):
            if step.step_type == WorkflowStepType.EXECUTE:
                result.gateway_result = {"can_execute": False, "status": "BLOCKED"}
            return original_start(step, defn, result, slot_context, gateway, audit, case)

        engine._execute_step = mock_execute_step
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result.workflow_state == WorkflowState.ESCALATED
        svc.process.assert_not_called()


# ── Exception isolation ───────────────────────────────────────────────────────

class TestExecutionExceptionIsolation:
    def test_execution_service_raises_does_not_propagate(self):
        svc = MagicMock()
        svc.process.side_effect = RuntimeError("service boom")
        engine = WorkflowEngine(execution_service=svc)
        case = _make_case()
        registry = _make_execute_playbook()
        result = engine.start(case, registry, {}, gateway=None, audit=None)
        assert result is not None
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Architecture validation ───────────────────────────────────────────────────

class TestArchitectureValidation:
    def test_execute_without_action_gateway_logs_warning(self, caplog):
        import logging
        engine = WorkflowEngine()
        case = _make_case()
        registry = _make_execute_playbook()
        with caplog.at_level(logging.WARNING, logger="case_engine.workflows.workflow_engine"):
            engine.start(case, registry, {}, gateway=None, audit=None)
        # Should have logged a warning about missing ACTION_GATEWAY
        assert any("EXECUTE" in msg or "ACTION_GATEWAY" in msg or "Action Gateway" in msg
                   for msg in caplog.messages)
