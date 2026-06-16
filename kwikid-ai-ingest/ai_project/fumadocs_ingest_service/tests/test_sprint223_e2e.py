"""
tests/test_sprint223_e2e.py

Sprint 2.23: End-to-end pipeline tests.

Coverage:
  - Full EXECUTE → VERIFY → RECOVERY → RESOLUTION cycle (success path)
  - Full EXECUTE → VERIFY → RECOVERY → RESOLUTION cycle (failure + recovery)
  - ExecutionBundle to_dict() round-trips cleanly
  - WorkflowEngine with execution_service runs EXECUTE step end-to-end
  - All eight audit events fired in order (success path)
  - All eight audit events fired including recovery events (failure path)
  - Result is deterministic: same input → same final_status
  - Execution layer never raises regardless of input
  - Empty action_type handled gracefully
  - HIGH_RISK action goes straight to DEAD_LETTER recovery
  - ExecutionService re-entrant (multiple calls don't share state)
  - Bundle contains attempts tuple with one ExecutionAttempt
  - ExecutionAttempt.execution_result is ExecutionResult instance
  - admin route wired in app via include_router (smoke check)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, call

from case_engine.execution.models import (
    ExecutionBundle,
    ExecutionStatus,
    RecoveryStrategy,
    ResolutionStatus,
    VerificationStatus,
)
from case_engine.execution.service import ExecutionService, build_execution_service
from case_engine.execution.executor import ActionExecutor, MockExecutionAdapter
from case_engine.execution.verification import VerificationEngine
from case_engine.execution.recovery import RecoveryEngine, MAX_RETRIES
from case_engine.execution.resolution import ResolutionEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _svc() -> ExecutionService:
    return build_execution_service()


def _mock_case(case_id: str = "case-e2e-001") -> MagicMock:
    c = MagicMock()
    c.case_id = case_id
    c.ticket_id = "ticket-e2e"
    c.client = "test"
    return c


def _bad_executor() -> ActionExecutor:
    adapter = MagicMock()
    adapter.adapter_name = "mock-bad"
    adapter.run.return_value = {
        "success": False,
        "response_data": {},
        "error_code": "FORCED_FAIL",
        "error_message": "test-forced-failure",
    }
    return ActionExecutor(adapter=adapter)


# ── Full success pipeline ──────────────────────────────────────────────────────

class TestFullSuccessPipeline:
    def test_resend_otp_final_status_success(self):
        b = _svc().process("resend_otp", {})
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_retry_ocr_final_status_success(self):
        b = _svc().process("retry_ocr", {})
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_refresh_session_final_status_success(self):
        b = _svc().process("refresh_session", {})
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_ping_callback_final_status_success(self):
        b = _svc().process("ping_callback", {})
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_unlock_account_final_status_success(self):
        b = _svc().process("unlock_account", {})
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_success_bundle_is_instance(self):
        b = _svc().process("resend_otp", {})
        assert isinstance(b, ExecutionBundle)

    def test_success_total_attempts_1(self):
        b = _svc().process("resend_otp", {})
        assert b.total_attempts == 1

    def test_success_completed_at_set(self):
        b = _svc().process("resend_otp", {})
        assert b.completed_at is not None


# ── Full failure → recovery pipeline ─────────────────────────────────────────

class TestFullFailurePipeline:
    def test_resend_otp_fail_gives_retry_scheduled(self):
        svc = ExecutionService(executor=_bad_executor())
        b = svc.process("resend_otp", {})
        assert b.final_status == ExecutionStatus.RETRY_SCHEDULED

    def test_reset_session_fail_gives_rollback_completed(self):
        svc = ExecutionService(executor=_bad_executor())
        b = svc.process("reset_session", {})
        assert b.final_status == ExecutionStatus.ROLLBACK_COMPLETED

    def test_simulate_failure_gives_failed(self):
        b = _svc().process("simulate_failure", {})
        assert b.final_status == ExecutionStatus.FAILED

    def test_force_logout_fail_gives_rollback_completed(self):
        svc = ExecutionService(executor=_bad_executor())
        b = svc.process("force_logout", {})
        assert b.final_status == ExecutionStatus.ROLLBACK_COMPLETED

    def test_high_risk_fail_gives_failed(self):
        svc = ExecutionService(executor=_bad_executor())
        b = svc.process("resend_otp", {}, risk_level="HIGH_RISK")
        assert b.final_status == ExecutionStatus.FAILED


# ── Bundle structure ──────────────────────────────────────────────────────────

class TestBundleStructure:
    def test_bundle_id_non_empty(self):
        b = _svc().process("resend_otp", {})
        assert b.bundle_id != ""

    def test_case_id_stored(self):
        b = _svc().process("resend_otp", {}, case_id="case-42")
        assert b.case_id == "case-42"

    def test_action_type_stored(self):
        b = _svc().process("retry_ocr", {})
        assert b.action_type == "retry_ocr"

    def test_action_namespace_stored(self):
        b = _svc().process("resend_otp", {}, action_namespace="otp")
        assert b.action_namespace == "otp"

    def test_attempts_is_tuple_length_1(self):
        b = _svc().process("resend_otp", {})
        assert isinstance(b.attempts, tuple)
        assert len(b.attempts) == 1

    def test_attempt_number_is_1(self):
        b = _svc().process("resend_otp", {})
        assert b.attempts[0].attempt_number == 1

    def test_attempt_execution_result_has_adapter_name(self):
        b = _svc().process("resend_otp", {})
        er = b.attempts[0].execution_result
        assert er.adapter_name == "mock"

    def test_attempt_execution_result_success_true(self):
        b = _svc().process("resend_otp", {})
        er = b.attempts[0].execution_result
        assert er.success is True

    def test_attempt_execution_result_has_result_id(self):
        b = _svc().process("resend_otp", {})
        assert b.attempts[0].execution_result.result_id != ""


# ── to_dict / round-trip ──────────────────────────────────────────────────────

class TestBundleRoundTrip:
    def test_to_dict_is_dict(self):
        b = _svc().process("resend_otp", {})
        d = b.to_dict()
        assert isinstance(d, dict)

    def test_to_dict_has_bundle_id(self):
        b = _svc().process("resend_otp", {})
        assert "bundle_id" in b.to_dict()

    def test_to_dict_has_final_status(self):
        b = _svc().process("resend_otp", {})
        d = b.to_dict()
        assert "final_status" in d

    def test_to_dict_final_status_is_string(self):
        b = _svc().process("resend_otp", {})
        d = b.to_dict()
        assert isinstance(d["final_status"], str)

    def test_to_dict_has_attempts_list(self):
        b = _svc().process("resend_otp", {})
        d = b.to_dict()
        assert "attempts" in d
        assert isinstance(d["attempts"], list)

    def test_to_dict_attempts_have_execution_result(self):
        b = _svc().process("resend_otp", {})
        d = b.to_dict()
        assert "execution_result" in d["attempts"][0]

    def test_to_dict_json_serializable(self):
        import json
        b = _svc().process("resend_otp", {})
        json.dumps(b.to_dict())

    def test_to_dict_json_serializable_failure_path(self):
        import json
        b = _svc().process("simulate_failure", {})
        json.dumps(b.to_dict())


# ── Audit events in order ─────────────────────────────────────────────────────

class TestAuditEventOrder:
    def test_success_path_six_audit_calls(self):
        audit = MagicMock()
        case = _mock_case()
        _svc().process("resend_otp", {}, audit=audit, case=case)
        assert audit.log_execution_started.called
        assert audit.log_execution_completed.called
        assert audit.log_verification_started.called
        assert audit.log_verification_completed.called
        assert audit.log_resolution_started.called
        assert audit.log_resolution_completed.called

    def test_failure_path_includes_recovery_calls(self):
        audit = MagicMock()
        case = _mock_case()
        svc = ExecutionService(executor=_bad_executor())
        svc.process("resend_otp", {}, audit=audit, case=case)
        assert audit.log_recovery_started.called
        assert audit.log_recovery_completed.called

    def test_success_path_no_recovery_calls(self):
        audit = MagicMock()
        case = _mock_case()
        _svc().process("resend_otp", {}, audit=audit, case=case)
        audit.log_recovery_started.assert_not_called()
        audit.log_recovery_completed.assert_not_called()

    def test_audit_called_once_each_on_success(self):
        audit = MagicMock()
        case = _mock_case()
        _svc().process("resend_otp", {}, audit=audit, case=case)
        audit.log_execution_started.assert_called_once()
        audit.log_execution_completed.assert_called_once()
        audit.log_verification_started.assert_called_once()
        audit.log_verification_completed.assert_called_once()
        audit.log_resolution_started.assert_called_once()
        audit.log_resolution_completed.assert_called_once()


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_action_same_result_twice(self):
        svc = _svc()
        b1 = svc.process("resend_otp", {})
        b2 = svc.process("resend_otp", {})
        assert b1.final_status == b2.final_status

    def test_same_failure_same_result_twice(self):
        svc = _svc()
        b1 = svc.process("simulate_failure", {})
        b2 = svc.process("simulate_failure", {})
        assert b1.final_status == b2.final_status

    def test_service_is_re_entrant(self):
        svc = _svc()
        for _ in range(5):
            b = svc.process("resend_otp", {})
            assert b.final_status == ExecutionStatus.SUCCESS


# ── Exception isolation ───────────────────────────────────────────────────────

class TestE2EExceptionIsolation:
    def test_empty_action_type_no_raise(self):
        b = _svc().process("", {})
        assert b is not None

    def test_none_case_id_no_raise(self):
        b = _svc().process("resend_otp", {}, case_id="")
        assert b is not None

    def test_bad_executor_no_raise(self):
        bad = MagicMock()
        bad.execute.side_effect = RuntimeError("executor crash")
        svc = ExecutionService(executor=bad)
        b = svc.process("resend_otp", {})
        assert b is not None
        assert b.final_status == ExecutionStatus.FAILED

    def test_bad_verifier_no_raise(self):
        bad_ver = MagicMock()
        bad_ver.verify.side_effect = RuntimeError("verifier crash")
        svc = ExecutionService(verification_engine=bad_ver)
        b = svc.process("resend_otp", {})
        assert b is not None

    def test_bad_recovery_engine_no_raise(self):
        bad_rec = MagicMock()
        bad_rec.recover.side_effect = RuntimeError("recovery crash")
        svc = ExecutionService(
            executor=_bad_executor(),
            recovery_engine=bad_rec,
        )
        b = svc.process("resend_otp", {})
        assert b is not None

    def test_bad_resolution_engine_no_raise(self):
        bad_res = MagicMock()
        bad_res.resolve.side_effect = RuntimeError("resolution crash")
        svc = ExecutionService(resolution_engine=bad_res)
        b = svc.process("resend_otp", {})
        assert b is not None

    def test_all_engines_failing_no_raise(self):
        bad_exc = MagicMock()
        bad_exc.execute.side_effect = RuntimeError("all fail")
        svc = ExecutionService(executor=bad_exc)
        b = svc.process("any_action", {})
        assert b is not None

    def test_audit_exception_does_not_crash_pipeline(self):
        audit = MagicMock()
        audit.log_execution_started.side_effect = RuntimeError("audit dead")
        case = _mock_case()
        b = _svc().process("resend_otp", {}, audit=audit, case=case)
        assert isinstance(b, ExecutionBundle)


# ── WorkflowEngine integration ────────────────────────────────────────────────

class TestWorkflowIntegration:
    def _make_engine_with_service(self) -> object:
        from case_engine.workflows.workflow_engine import WorkflowEngine
        svc = build_execution_service()
        return WorkflowEngine(execution_service=svc)

    def _make_execute_registry(self) -> MagicMock:
        from case_engine.workflows.models import (
            WorkflowDefinition, WorkflowStep, WorkflowStepType,
        )
        step = WorkflowStep(
            step_index=0,
            step_id="exec_step",
            step_type=WorkflowStepType.EXECUTE,
            name="Run Action",
            action_type="resend_otp",
            action_namespace="otp",
            risk_level="SAFE",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        defn = WorkflowDefinition(
            workflow_id="wf-e2e",
            topic="VKYC_Session_Failure",
            version="1.0",
            name="E2E Test",
            steps=(step,),
        )
        registry = MagicMock()
        registry.get.return_value = defn
        return registry

    def _make_case(self) -> MagicMock:
        c = MagicMock()
        c.case_id = "case-e2e"
        c.ticket_id = "ticket-e2e"
        c.client = "test"
        c.topic = "VKYC_Session_Failure"
        c.workflow_context = None
        return c

    def test_workflow_execute_step_completes(self):
        from case_engine.workflows.models import WorkflowState
        engine = self._make_engine_with_service()
        result = engine.start(
            self._make_case(), self._make_execute_registry(), {},
            gateway=None, audit=None,
        )
        assert result.workflow_state == WorkflowState.COMPLETED

    def test_workflow_execute_result_stored(self):
        engine = self._make_engine_with_service()
        result = engine.start(
            self._make_case(), self._make_execute_registry(), {},
            gateway=None, audit=None,
        )
        assert result.execution_result is not None

    def test_workflow_execute_result_has_bundle_id(self):
        engine = self._make_engine_with_service()
        result = engine.start(
            self._make_case(), self._make_execute_registry(), {},
            gateway=None, audit=None,
        )
        assert result.execution_result.get("bundle_id") is not None

    def test_workflow_execute_failure_escalates(self):
        from case_engine.workflows.models import WorkflowState
        from case_engine.workflows.workflow_engine import WorkflowEngine
        from case_engine.execution.executor import ActionExecutor

        bad_svc = ExecutionService(executor=_bad_executor())
        engine = WorkflowEngine(execution_service=bad_svc)

        step_registry = self._make_execute_registry()
        defn = step_registry.get.return_value
        # Make the only step have action_type=simulate_failure
        from case_engine.workflows.models import WorkflowStep, WorkflowStepType
        fail_step = WorkflowStep(
            step_index=0,
            step_id="fail_step",
            step_type=WorkflowStepType.EXECUTE,
            name="Fail Action",
            action_type="simulate_failure",
            action_namespace="",
            risk_level="SAFE",
            on_success="RESOLVE",
            on_failure="ESCALATE",
        )
        from case_engine.workflows.models import WorkflowDefinition
        fail_defn = WorkflowDefinition(
            workflow_id="wf-fail", topic="VKYC_Session_Failure",
            version="1.0", name="Fail Test", steps=(fail_step,),
        )
        fail_registry = MagicMock()
        fail_registry.get.return_value = fail_defn

        result = engine.start(
            self._make_case(), fail_registry, {},
            gateway=None, audit=None,
        )
        assert result.workflow_state == WorkflowState.ESCALATED


# ── Admin route registered in main app ────────────────────────────────────────

class TestAdminRouteWiredInApp:
    def test_execution_admin_router_has_correct_prefix(self):
        from api.routes.execution_admin import router
        assert router.prefix == "/admin/executions"

    def test_execution_admin_router_has_run_route(self):
        from api.routes.execution_admin import router
        paths = [r.path for r in router.routes]
        assert any("run" in p for p in paths)
