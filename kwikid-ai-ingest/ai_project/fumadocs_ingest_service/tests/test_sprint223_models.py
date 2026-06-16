"""
tests/test_sprint223_models.py

Sprint 2.23: Execution Layer domain model tests.

Coverage:
  - ExecutionStatus (7 values)
  - VerificationStatus (3 values)
  - RecoveryStrategy (4 values)
  - RecoveryStatus (4 values)
  - ResolutionStatus (4 values)
  - ExecutionResult (frozen, to_dict, from_dict)
  - ExecutionAttempt (frozen, to_dict, from_dict)
  - ExecutionBundle (frozen, to_dict, from_dict)
  - VerificationResult (frozen, is_success, requires_recovery, to_dict, from_dict)
  - RecoveryResult (frozen, to_dict, from_dict)
  - ResolutionResult (frozen, to_dict, from_dict)
  - WorkflowStepType.EXECUTE exists (10th step type)
  - WorkflowExecutionResult.execution_result field
  - AuditEventType Sprint 2.23 values
"""
from __future__ import annotations

import json
import pytest
from dataclasses import FrozenInstanceError

from case_engine.execution.models import (
    ExecutionAttempt,
    ExecutionBundle,
    ExecutionResult,
    ExecutionStatus,
    RecoveryResult,
    RecoveryStatus,
    RecoveryStrategy,
    ResolutionResult,
    ResolutionStatus,
    VerificationResult,
    VerificationStatus,
)
from case_engine.workflows.models import WorkflowExecutionResult, WorkflowStepType
from case_engine.models import AuditEventType


# ── Helpers ───────────────────────────────────────────────────────────────────

def _exec_result(
    action_type: str = "resend_otp",
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    success: bool = True,
) -> ExecutionResult:
    return ExecutionResult(
        result_id="r-001",
        adapter_name="mock",
        action_type=action_type,
        action_params={"session_id": "s-001"},
        status=status,
        success=success,
        response_data={"mock": True},
        error_code=None,
        error_message=None,
        executed_at="2026-06-12T10:00:00+00:00",
        duration_ms=42,
    )


def _attempt(n: int = 1) -> ExecutionAttempt:
    return ExecutionAttempt(
        attempt_id="a-001",
        attempt_number=n,
        execution_result=_exec_result(),
        recovery_strategy=None,
        attempted_at="2026-06-12T10:00:00+00:00",
    )


def _bundle() -> ExecutionBundle:
    return ExecutionBundle(
        bundle_id="b-001",
        case_id="case-001",
        action_type="resend_otp",
        action_namespace="otp",
        attempts=(_attempt(),),
        final_status=ExecutionStatus.SUCCESS,
        total_attempts=1,
        created_at="2026-06-12T10:00:00+00:00",
        completed_at="2026-06-12T10:00:01+00:00",
    )


def _verification(status: VerificationStatus = VerificationStatus.VERIFIED_SUCCESS) -> VerificationResult:
    return VerificationResult(
        verification_id="v-001",
        action_type="resend_otp",
        status=status,
        confirmed=status == VerificationStatus.VERIFIED_SUCCESS,
        evidence={"execution_status": "SUCCESS"},
        failure_reason=None,
        verified_at="2026-06-12T10:00:01+00:00",
    )


def _recovery(strategy: RecoveryStrategy = RecoveryStrategy.RETRY) -> RecoveryResult:
    return RecoveryResult(
        recovery_id="rec-001",
        strategy_applied=strategy,
        status=RecoveryStatus.RECOVERY_PENDING,
        max_retries=3,
        attempts_used=1,
        can_retry=True,
        notes="scheduled for retry",
        recovered_at="2026-06-12T10:00:02+00:00",
    )


def _resolution(status: ResolutionStatus = ResolutionStatus.RESOLVED) -> ResolutionResult:
    return ResolutionResult(
        resolution_id="res-001",
        action_type="resend_otp",
        status=status,
        resolved=status == ResolutionStatus.RESOLVED,
        resolution_note="Action verified successful.",
        evidence_keys=("execution_status", "adapter"),
        resolved_at="2026-06-12T10:00:03+00:00",
    )


# ── ExecutionStatus ───────────────────────────────────────────────────────────

class TestExecutionStatus:
    def test_pending(self):
        assert ExecutionStatus.PENDING.value == "PENDING"

    def test_executing(self):
        assert ExecutionStatus.EXECUTING.value == "EXECUTING"

    def test_success(self):
        assert ExecutionStatus.SUCCESS.value == "SUCCESS"

    def test_failed(self):
        assert ExecutionStatus.FAILED.value == "FAILED"

    def test_partial_success(self):
        assert ExecutionStatus.PARTIAL_SUCCESS.value == "PARTIAL_SUCCESS"

    def test_rollback_completed(self):
        assert ExecutionStatus.ROLLBACK_COMPLETED.value == "ROLLBACK_COMPLETED"

    def test_retry_scheduled(self):
        assert ExecutionStatus.RETRY_SCHEDULED.value == "RETRY_SCHEDULED"

    def test_seven_values(self):
        assert len(ExecutionStatus) == 7

    def test_is_str_subclass(self):
        assert isinstance(ExecutionStatus.SUCCESS, str)


# ── VerificationStatus ────────────────────────────────────────────────────────

class TestVerificationStatus:
    def test_verified_success(self):
        assert VerificationStatus.VERIFIED_SUCCESS.value == "VERIFIED_SUCCESS"

    def test_verified_failure(self):
        assert VerificationStatus.VERIFIED_FAILURE.value == "VERIFIED_FAILURE"

    def test_uncertain(self):
        assert VerificationStatus.UNCERTAIN.value == "UNCERTAIN"

    def test_three_values(self):
        assert len(VerificationStatus) == 3


# ── RecoveryStrategy ──────────────────────────────────────────────────────────

class TestRecoveryStrategy:
    def test_retry(self):
        assert RecoveryStrategy.RETRY.value == "RETRY"

    def test_rollback(self):
        assert RecoveryStrategy.ROLLBACK.value == "ROLLBACK"

    def test_escalate(self):
        assert RecoveryStrategy.ESCALATE.value == "ESCALATE"

    def test_dead_letter(self):
        assert RecoveryStrategy.DEAD_LETTER.value == "DEAD_LETTER"

    def test_four_values(self):
        assert len(RecoveryStrategy) == 4


# ── RecoveryStatus ────────────────────────────────────────────────────────────

class TestRecoveryStatus:
    def test_recovery_pending(self):
        assert RecoveryStatus.RECOVERY_PENDING.value == "RECOVERY_PENDING"

    def test_recovery_completed(self):
        assert RecoveryStatus.RECOVERY_COMPLETED.value == "RECOVERY_COMPLETED"

    def test_recovery_failed(self):
        assert RecoveryStatus.RECOVERY_FAILED.value == "RECOVERY_FAILED"

    def test_escalated(self):
        assert RecoveryStatus.ESCALATED.value == "ESCALATED"

    def test_four_values(self):
        assert len(RecoveryStatus) == 4


# ── ResolutionStatus ──────────────────────────────────────────────────────────

class TestResolutionStatus:
    def test_resolved(self):
        assert ResolutionStatus.RESOLVED.value == "RESOLVED"

    def test_partially_resolved(self):
        assert ResolutionStatus.PARTIALLY_RESOLVED.value == "PARTIALLY_RESOLVED"

    def test_unresolved(self):
        assert ResolutionStatus.UNRESOLVED.value == "UNRESOLVED"

    def test_escalated(self):
        assert ResolutionStatus.ESCALATED.value == "ESCALATED"

    def test_four_values(self):
        assert len(ResolutionStatus) == 4


# ── ExecutionResult ───────────────────────────────────────────────────────────

class TestExecutionResult:
    def test_construction(self):
        r = _exec_result()
        assert r.action_type == "resend_otp"
        assert r.success is True

    def test_frozen(self):
        r = _exec_result()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            r.action_type = "changed"  # type: ignore[misc]

    def test_to_dict_keys(self):
        d = _exec_result().to_dict()
        for key in ("result_id", "adapter_name", "action_type", "action_params",
                    "status", "success", "response_data", "error_code",
                    "error_message", "executed_at", "duration_ms"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_status_is_string(self):
        assert _exec_result().to_dict()["status"] == "SUCCESS"

    def test_to_dict_json_serializable(self):
        assert json.dumps(_exec_result().to_dict())

    def test_from_dict_roundtrip(self):
        r = _exec_result()
        r2 = ExecutionResult.from_dict(r.to_dict())
        assert r2.result_id == r.result_id
        assert r2.status == r.status
        assert r2.success == r.success

    def test_from_dict_failed_status(self):
        d = _exec_result(status=ExecutionStatus.FAILED, success=False).to_dict()
        r = ExecutionResult.from_dict(d)
        assert r.status == ExecutionStatus.FAILED
        assert r.success is False

    def test_from_dict_defaults(self):
        r = ExecutionResult.from_dict({})
        assert r.status == ExecutionStatus.FAILED
        assert r.success is False


# ── ExecutionAttempt ──────────────────────────────────────────────────────────

class TestExecutionAttempt:
    def test_construction(self):
        a = _attempt()
        assert a.attempt_number == 1
        assert a.recovery_strategy is None

    def test_frozen(self):
        a = _attempt()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            a.attempt_number = 2  # type: ignore[misc]

    def test_to_dict_keys(self):
        d = _attempt().to_dict()
        for key in ("attempt_id", "attempt_number", "execution_result",
                    "recovery_strategy", "attempted_at"):
            assert key in d

    def test_to_dict_nested_result_is_dict(self):
        assert isinstance(_attempt().to_dict()["execution_result"], dict)

    def test_to_dict_recovery_strategy_none(self):
        assert _attempt().to_dict()["recovery_strategy"] is None

    def test_to_dict_recovery_strategy_set(self):
        a = ExecutionAttempt(
            attempt_id="a-x",
            attempt_number=2,
            execution_result=_exec_result(),
            recovery_strategy=RecoveryStrategy.RETRY,
            attempted_at="2026-06-12T10:00:00+00:00",
        )
        assert a.to_dict()["recovery_strategy"] == "RETRY"

    def test_from_dict_roundtrip(self):
        a = _attempt()
        a2 = ExecutionAttempt.from_dict(a.to_dict())
        assert a2.attempt_id == a.attempt_id
        assert a2.attempt_number == a.attempt_number

    def test_json_serializable(self):
        assert json.dumps(_attempt().to_dict())


# ── ExecutionBundle ───────────────────────────────────────────────────────────

class TestExecutionBundle:
    def test_construction(self):
        b = _bundle()
        assert b.total_attempts == 1
        assert b.final_status == ExecutionStatus.SUCCESS

    def test_frozen(self):
        b = _bundle()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            b.bundle_id = "changed"  # type: ignore[misc]

    def test_to_dict_keys(self):
        d = _bundle().to_dict()
        for key in ("bundle_id", "case_id", "action_type", "action_namespace",
                    "attempts", "final_status", "total_attempts",
                    "created_at", "completed_at"):
            assert key in d

    def test_to_dict_attempts_is_list(self):
        assert isinstance(_bundle().to_dict()["attempts"], list)

    def test_to_dict_final_status_is_string(self):
        assert _bundle().to_dict()["final_status"] == "SUCCESS"

    def test_from_dict_roundtrip(self):
        b = _bundle()
        b2 = ExecutionBundle.from_dict(b.to_dict())
        assert b2.bundle_id == b.bundle_id
        assert b2.final_status == b.final_status
        assert b2.total_attempts == b.total_attempts

    def test_from_dict_attempts_restored(self):
        b = _bundle()
        b2 = ExecutionBundle.from_dict(b.to_dict())
        assert len(b2.attempts) == 1

    def test_json_serializable(self):
        assert json.dumps(_bundle().to_dict())

    def test_completed_at_none(self):
        b = ExecutionBundle(
            bundle_id="b-x", case_id="", action_type="t", action_namespace="",
            attempts=(), final_status=ExecutionStatus.PENDING, total_attempts=0,
            created_at="2026-06-12T10:00:00+00:00", completed_at=None,
        )
        assert b.to_dict()["completed_at"] is None


# ── VerificationResult ────────────────────────────────────────────────────────

class TestVerificationResult:
    def test_construction(self):
        v = _verification()
        assert v.confirmed is True
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_frozen(self):
        v = _verification()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            v.confirmed = False  # type: ignore[misc]

    def test_is_success_true(self):
        assert _verification(VerificationStatus.VERIFIED_SUCCESS).is_success() is True

    def test_is_success_false_on_failure(self):
        assert _verification(VerificationStatus.VERIFIED_FAILURE).is_success() is False

    def test_is_success_false_on_uncertain(self):
        assert _verification(VerificationStatus.UNCERTAIN).is_success() is False

    def test_requires_recovery_failure(self):
        assert _verification(VerificationStatus.VERIFIED_FAILURE).requires_recovery() is True

    def test_requires_recovery_uncertain(self):
        assert _verification(VerificationStatus.UNCERTAIN).requires_recovery() is True

    def test_requires_recovery_success_false(self):
        assert _verification(VerificationStatus.VERIFIED_SUCCESS).requires_recovery() is False

    def test_to_dict_keys(self):
        d = _verification().to_dict()
        for key in ("verification_id", "action_type", "status", "confirmed",
                    "evidence", "failure_reason", "verified_at"):
            assert key in d

    def test_to_dict_status_is_string(self):
        assert _verification().to_dict()["status"] == "VERIFIED_SUCCESS"

    def test_from_dict_roundtrip(self):
        v = _verification(VerificationStatus.VERIFIED_FAILURE)
        v2 = VerificationResult.from_dict(v.to_dict())
        assert v2.verification_id == v.verification_id
        assert v2.status == VerificationStatus.VERIFIED_FAILURE

    def test_from_dict_defaults(self):
        v = VerificationResult.from_dict({})
        assert v.status == VerificationStatus.UNCERTAIN


# ── RecoveryResult ────────────────────────────────────────────────────────────

class TestRecoveryResult:
    def test_construction(self):
        r = _recovery()
        assert r.can_retry is True
        assert r.strategy_applied == RecoveryStrategy.RETRY

    def test_frozen(self):
        r = _recovery()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            r.can_retry = False  # type: ignore[misc]

    def test_to_dict_keys(self):
        d = _recovery().to_dict()
        for key in ("recovery_id", "strategy_applied", "status", "max_retries",
                    "attempts_used", "can_retry", "notes", "recovered_at"):
            assert key in d

    def test_to_dict_strategy_is_string(self):
        assert _recovery().to_dict()["strategy_applied"] == "RETRY"

    def test_from_dict_roundtrip(self):
        r = _recovery(RecoveryStrategy.ROLLBACK)
        r2 = RecoveryResult.from_dict(r.to_dict())
        assert r2.strategy_applied == RecoveryStrategy.ROLLBACK
        assert r2.can_retry == r.can_retry

    def test_from_dict_defaults(self):
        r = RecoveryResult.from_dict({})
        assert r.strategy_applied == RecoveryStrategy.ESCALATE

    def test_all_strategies_round_trip(self):
        for strategy in RecoveryStrategy:
            r = RecoveryResult(
                recovery_id="x", strategy_applied=strategy,
                status=RecoveryStatus.ESCALATED, max_retries=3,
                attempts_used=0, can_retry=False, notes="",
                recovered_at="2026-06-12T10:00:00+00:00",
            )
            r2 = RecoveryResult.from_dict(r.to_dict())
            assert r2.strategy_applied == strategy


# ── ResolutionResult ──────────────────────────────────────────────────────────

class TestResolutionResult:
    def test_construction(self):
        r = _resolution()
        assert r.resolved is True
        assert r.status == ResolutionStatus.RESOLVED

    def test_frozen(self):
        r = _resolution()
        with pytest.raises((FrozenInstanceError, AttributeError, TypeError)):
            r.resolved = False  # type: ignore[misc]

    def test_to_dict_keys(self):
        d = _resolution().to_dict()
        for key in ("resolution_id", "action_type", "status", "resolved",
                    "resolution_note", "evidence_keys", "resolved_at"):
            assert key in d

    def test_to_dict_evidence_keys_is_list(self):
        assert isinstance(_resolution().to_dict()["evidence_keys"], list)

    def test_from_dict_roundtrip(self):
        r = _resolution(ResolutionStatus.ESCALATED)
        r2 = ResolutionResult.from_dict(r.to_dict())
        assert r2.status == ResolutionStatus.ESCALATED
        assert r2.resolved is False

    def test_from_dict_defaults(self):
        r = ResolutionResult.from_dict({})
        assert r.status == ResolutionStatus.UNRESOLVED
        assert r.resolved is False

    def test_all_statuses_round_trip(self):
        for status in ResolutionStatus:
            r = ResolutionResult(
                resolution_id="x", action_type="t", status=status,
                resolved=status == ResolutionStatus.RESOLVED,
                resolution_note="", evidence_keys=(),
                resolved_at="2026-06-12T10:00:00+00:00",
            )
            r2 = ResolutionResult.from_dict(r.to_dict())
            assert r2.status == status

    def test_json_serializable(self):
        assert json.dumps(_resolution().to_dict())


# ── WorkflowStepType.EXECUTE ──────────────────────────────────────────────────

class TestWorkflowStepTypeExecute:
    def test_execute_exists(self):
        assert hasattr(WorkflowStepType, "EXECUTE")

    def test_execute_value(self):
        assert WorkflowStepType.EXECUTE.value == "EXECUTE"

    def test_ten_step_types(self):
        # Sprint 2.25 added CLARIFY — now 12 total
        assert len(WorkflowStepType) == 12

    def test_all_prior_step_types_unchanged(self):
        assert WorkflowStepType.COLLECT_INFORMATION.value == "COLLECT_INFORMATION"
        assert WorkflowStepType.CHECK_CONDITION.value == "CHECK_CONDITION"
        assert WorkflowStepType.PROPOSE_ACTION.value == "PROPOSE_ACTION"
        assert WorkflowStepType.REQUEST_APPROVAL.value == "REQUEST_APPROVAL"
        assert WorkflowStepType.RESOLVE_CASE.value == "RESOLVE_CASE"
        assert WorkflowStepType.ESCALATE_CASE.value == "ESCALATE_CASE"
        assert WorkflowStepType.INVESTIGATE.value == "INVESTIGATE"
        assert WorkflowStepType.KNOWLEDGE_LOOKUP.value == "KNOWLEDGE_LOOKUP"
        assert WorkflowStepType.ACTION_GATEWAY.value == "ACTION_GATEWAY"


# ── WorkflowExecutionResult.execution_result ──────────────────────────────────

class TestWorkflowExecutionResultExecutionField:
    def test_default_is_none(self):
        r = WorkflowExecutionResult()
        assert r.execution_result is None

    def test_can_set(self):
        r = WorkflowExecutionResult()
        r.execution_result = {"bundle_id": "b-001", "final_status": "SUCCESS"}
        assert r.execution_result["bundle_id"] == "b-001"

    def test_to_dict_includes_key(self):
        r = WorkflowExecutionResult()
        assert "execution_result" in r.to_dict()

    def test_to_dict_none_by_default(self):
        r = WorkflowExecutionResult()
        assert r.to_dict()["execution_result"] is None

    def test_to_dict_with_data(self):
        r = WorkflowExecutionResult()
        r.execution_result = _bundle().to_dict()
        assert r.to_dict()["execution_result"]["bundle_id"] == "b-001"

    def test_from_dict_restores_field(self):
        r = WorkflowExecutionResult()
        r.execution_result = {"bundle_id": "b-002"}
        r2 = WorkflowExecutionResult.from_dict(r.to_dict())
        assert r2.execution_result is not None
        assert r2.execution_result["bundle_id"] == "b-002"

    def test_from_dict_none_when_missing(self):
        r = WorkflowExecutionResult.from_dict({"workflow_id": "wf-x"})
        assert r.execution_result is None

    def test_json_roundtrip(self):
        r = WorkflowExecutionResult()
        r.execution_result = _bundle().to_dict()
        encoded = json.dumps(r.to_dict())
        decoded = WorkflowExecutionResult.from_dict(json.loads(encoded))
        assert decoded.execution_result["final_status"] == "SUCCESS"


# ── AuditEventType Sprint 2.23 values ────────────────────────────────────────

class TestAuditEventTypeSprint223:
    def test_execution_started(self):
        assert AuditEventType.EXECUTION_STARTED.value == "EXECUTION_STARTED"

    def test_execution_completed(self):
        assert AuditEventType.EXECUTION_COMPLETED.value == "EXECUTION_COMPLETED"

    def test_verification_started(self):
        assert AuditEventType.VERIFICATION_STARTED.value == "VERIFICATION_STARTED"

    def test_verification_completed(self):
        assert AuditEventType.VERIFICATION_COMPLETED.value == "VERIFICATION_COMPLETED"

    def test_recovery_started(self):
        assert AuditEventType.RECOVERY_STARTED.value == "RECOVERY_STARTED"

    def test_recovery_completed(self):
        assert AuditEventType.RECOVERY_COMPLETED.value == "RECOVERY_COMPLETED"

    def test_resolution_started(self):
        assert AuditEventType.RESOLUTION_STARTED.value == "RESOLUTION_STARTED"

    def test_resolution_completed(self):
        assert AuditEventType.RESOLUTION_COMPLETED.value == "RESOLUTION_COMPLETED"
