"""
tests/test_sprint223_verification.py

Sprint 2.23: VerificationEngine tests.

Coverage:
  - VERIFIED_SUCCESS for known successful actions
  - VERIFIED_FAILURE for failed executions
  - UNCERTAIN for unknown action types
  - UNCERTAIN as fail-closed default
  - additional_evidence incorporated
  - Exception isolation (never raises)
  - is_success / requires_recovery integration
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from case_engine.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    VerificationStatus,
)
from case_engine.execution.verification import VerificationEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_result(
    action_type: str = "resend_otp",
    status: ExecutionStatus = ExecutionStatus.SUCCESS,
    success: bool = True,
    error_code: str | None = None,
    error_message: str | None = None,
) -> ExecutionResult:
    return ExecutionResult(
        result_id="r-001",
        adapter_name="mock",
        action_type=action_type,
        action_params={},
        status=status,
        success=success,
        response_data={"mock": True} if success else {},
        error_code=error_code,
        error_message=error_message,
        executed_at="2026-06-12T10:00:00+00:00",
        duration_ms=10,
    )


_VERIFIABLE_ACTIONS = [
    "resend_otp",
    "retry_ocr",
    "refresh_session",
    "acknowledge_session",
    "ping_callback",
    "reset_session",
    "unlock_account",
    "force_logout",
    "clear_cache",
]


# ── VERIFIED_SUCCESS ──────────────────────────────────────────────────────────

class TestVerificationSuccess:
    def _verify(self, action_type: str) -> object:
        engine = VerificationEngine()
        result = _make_result(action_type=action_type)
        return engine.verify(result)

    def test_resend_otp_verified_success(self):
        v = self._verify("resend_otp")
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_retry_ocr_verified_success(self):
        v = self._verify("retry_ocr")
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_refresh_session_verified_success(self):
        v = self._verify("refresh_session")
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_reset_session_verified_success(self):
        v = self._verify("reset_session")
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_force_logout_verified_success(self):
        v = self._verify("force_logout")
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_success_confirmed_true(self):
        v = self._verify("resend_otp")
        assert v.confirmed is True

    def test_success_is_success_true(self):
        v = self._verify("resend_otp")
        assert v.is_success() is True

    def test_success_requires_recovery_false(self):
        v = self._verify("resend_otp")
        assert v.requires_recovery() is False

    def test_success_failure_reason_none(self):
        v = self._verify("resend_otp")
        assert v.failure_reason is None

    def test_success_evidence_has_execution_status(self):
        v = self._verify("resend_otp")
        assert "execution_status" in v.evidence

    def test_success_additional_evidence_incorporated(self):
        engine = VerificationEngine()
        result = _make_result()
        v = engine.verify(result, additional_evidence={"callback_ack": True})
        assert "additional" in v.evidence

    def test_all_verifiable_actions_succeed(self):
        engine = VerificationEngine()
        for action in _VERIFIABLE_ACTIONS:
            v = engine.verify(_make_result(action_type=action))
            assert v.status == VerificationStatus.VERIFIED_SUCCESS, f"Failed for {action}"


# ── VERIFIED_FAILURE ──────────────────────────────────────────────────────────

class TestVerificationFailure:
    def _verify_failed(self, action_type: str = "resend_otp") -> object:
        engine = VerificationEngine()
        result = _make_result(
            action_type=action_type,
            status=ExecutionStatus.FAILED,
            success=False,
            error_code="MOCK_FORCED_FAILURE",
            error_message="execution failed",
        )
        return engine.verify(result)

    def test_execution_failure_gives_verified_failure(self):
        v = self._verify_failed()
        assert v.status == VerificationStatus.VERIFIED_FAILURE

    def test_failure_confirmed_true(self):
        v = self._verify_failed()
        assert v.confirmed is True

    def test_failure_is_success_false(self):
        v = self._verify_failed()
        assert v.is_success() is False

    def test_failure_requires_recovery_true(self):
        v = self._verify_failed()
        assert v.requires_recovery() is True

    def test_failure_reason_set(self):
        v = self._verify_failed()
        assert v.failure_reason is not None

    def test_failure_evidence_has_error_code(self):
        v = self._verify_failed()
        assert "error_code" in v.evidence


# ── UNCERTAIN ─────────────────────────────────────────────────────────────────

class TestVerificationUncertain:
    def test_unknown_action_type_uncertain(self):
        engine = VerificationEngine()
        result = _make_result(action_type="custom_portal_action")
        v = engine.verify(result)
        assert v.status == VerificationStatus.UNCERTAIN

    def test_uncertain_confirmed_false(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result(action_type="unknown_xyz"))
        assert v.confirmed is False

    def test_uncertain_is_success_false(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result(action_type="unknown_xyz"))
        assert v.is_success() is False

    def test_uncertain_requires_recovery_true(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result(action_type="unknown_xyz"))
        assert v.requires_recovery() is True

    def test_uncertain_failure_reason_set(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result(action_type="unknown_xyz"))
        assert v.failure_reason is not None


# ── Exception Isolation ───────────────────────────────────────────────────────

class TestVerificationExceptionIsolation:
    def test_bad_result_object_does_not_raise(self):
        engine = VerificationEngine()
        bad_result = MagicMock()
        bad_result.action_type = "test"
        bad_result.status = MagicMock(side_effect=RuntimeError("boom"))
        v = engine.verify(bad_result)
        assert v is not None
        assert v.status == VerificationStatus.UNCERTAIN

    def test_none_additional_evidence_safe(self):
        engine = VerificationEngine()
        result = _make_result()
        v = engine.verify(result, additional_evidence=None)
        assert v.status == VerificationStatus.VERIFIED_SUCCESS

    def test_verification_id_always_set(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result())
        assert v.verification_id != ""

    def test_verified_at_always_set(self):
        engine = VerificationEngine()
        v = engine.verify(_make_result())
        assert v.verified_at != ""
