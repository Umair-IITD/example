"""
tests/test_sprint223_recovery.py

Sprint 2.23: RecoveryEngine tests.

Coverage:
  - RETRY strategy for retryable actions within limit
  - ROLLBACK strategy for reversible actions
  - DEAD_LETTER for high-risk or dead-letter actions
  - ESCALATE when retry limit exceeded
  - ESCALATE as default (fail-closed)
  - attempt_count boundary (0, 1, 2, 3)
  - risk_level HIGH_RISK always dead-letters
  - Exception isolation (never raises)
  - MAX_RETRIES constant
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from case_engine.execution.models import (
    RecoveryResult,
    RecoveryStatus,
    RecoveryStrategy,
    VerificationResult,
    VerificationStatus,
)
from case_engine.execution.recovery import RecoveryEngine, MAX_RETRIES


# ── Helpers ───────────────────────────────────────────────────────────────────

def _vresult(action_type: str = "resend_otp") -> VerificationResult:
    return VerificationResult(
        verification_id="v-001",
        action_type=action_type,
        status=VerificationStatus.VERIFIED_FAILURE,
        confirmed=True,
        evidence={},
        failure_reason="execution_failed",
        verified_at="2026-06-12T10:00:00+00:00",
    )


def _engine() -> RecoveryEngine:
    return RecoveryEngine()


# ── MAX_RETRIES constant ──────────────────────────────────────────────────────

class TestMaxRetries:
    def test_max_retries_is_3(self):
        assert MAX_RETRIES == 3

    def test_max_retries_imported_from_package(self):
        from case_engine.execution import MAX_RETRIES as pkg_max
        assert pkg_max == 3


# ── RETRY strategy ────────────────────────────────────────────────────────────

class TestRetryStrategy:
    def test_resend_otp_retry_at_attempt_0(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=0)
        assert r.strategy_applied == RecoveryStrategy.RETRY

    def test_retry_ocr_retry_at_attempt_1(self):
        r = _engine().recover(_vresult("retry_ocr"), attempt_count=1)
        assert r.strategy_applied == RecoveryStrategy.RETRY

    def test_ping_callback_retry_at_attempt_2(self):
        r = _engine().recover(_vresult("ping_callback"), attempt_count=2)
        assert r.strategy_applied == RecoveryStrategy.RETRY

    def test_retry_can_retry_true(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=0)
        assert r.can_retry is True

    def test_retry_status_recovery_pending(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=0)
        assert r.status == RecoveryStatus.RECOVERY_PENDING

    def test_retry_max_retries_is_3(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=0)
        assert r.max_retries == MAX_RETRIES

    def test_retry_notes_mention_attempt(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=1)
        assert "retry" in r.notes.lower() or "attempt" in r.notes.lower()

    def test_retry_at_exactly_max_minus_1_allowed(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=MAX_RETRIES - 1)
        assert r.strategy_applied == RecoveryStrategy.RETRY


# ── Retry limit exceeded → ESCALATE ──────────────────────────────────────────

class TestRetryLimitExceeded:
    def test_resend_otp_at_max_retries_escalates(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=MAX_RETRIES)
        assert r.strategy_applied == RecoveryStrategy.ESCALATE

    def test_retry_limit_can_retry_false(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=MAX_RETRIES)
        assert r.can_retry is False

    def test_retry_limit_status_escalated(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=MAX_RETRIES)
        assert r.status == RecoveryStatus.ESCALATED

    def test_retry_limit_notes_mention_max(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=MAX_RETRIES)
        assert "max" in r.notes.lower() or "exceeded" in r.notes.lower() or "escalat" in r.notes.lower()


# ── ROLLBACK strategy ─────────────────────────────────────────────────────────

class TestRollbackStrategy:
    def test_reset_session_rollback(self):
        r = _engine().recover(_vresult("reset_session"))
        assert r.strategy_applied == RecoveryStrategy.ROLLBACK

    def test_force_logout_rollback(self):
        r = _engine().recover(_vresult("force_logout"))
        assert r.strategy_applied == RecoveryStrategy.ROLLBACK

    def test_clear_cache_rollback(self):
        r = _engine().recover(_vresult("clear_cache"))
        assert r.strategy_applied == RecoveryStrategy.ROLLBACK

    def test_rollback_can_retry_false(self):
        r = _engine().recover(_vresult("reset_session"))
        assert r.can_retry is False

    def test_rollback_status_completed(self):
        r = _engine().recover(_vresult("reset_session"))
        assert r.status == RecoveryStatus.RECOVERY_COMPLETED

    def test_rollback_notes_mention_rollback(self):
        r = _engine().recover(_vresult("reset_session"))
        assert "roll" in r.notes.lower() or "reversib" in r.notes.lower()


# ── DEAD_LETTER strategy ──────────────────────────────────────────────────────

class TestDeadLetterStrategy:
    def test_financial_operation_dead_letter(self):
        r = _engine().recover(_vresult("financial_operation"))
        assert r.strategy_applied == RecoveryStrategy.DEAD_LETTER

    def test_account_delete_dead_letter(self):
        r = _engine().recover(_vresult("account_delete"))
        assert r.strategy_applied == RecoveryStrategy.DEAD_LETTER

    def test_high_risk_action_dead_letter(self):
        r = _engine().recover(_vresult("any_action"), risk_level="HIGH_RISK")
        assert r.strategy_applied == RecoveryStrategy.DEAD_LETTER

    def test_dead_letter_can_retry_false(self):
        r = _engine().recover(_vresult("financial_operation"))
        assert r.can_retry is False

    def test_dead_letter_status_escalated(self):
        r = _engine().recover(_vresult("financial_operation"))
        assert r.status == RecoveryStatus.ESCALATED

    def test_high_risk_regardless_of_attempt_count(self):
        for attempts in range(4):
            r = _engine().recover(_vresult("resend_otp"), attempt_count=attempts, risk_level="HIGH_RISK")
            assert r.strategy_applied == RecoveryStrategy.DEAD_LETTER

    def test_high_risk_case_insensitive(self):
        r = _engine().recover(_vresult("resend_otp"), risk_level="high_risk")
        assert r.strategy_applied == RecoveryStrategy.DEAD_LETTER


# ── Default ESCALATE (fail-closed) ────────────────────────────────────────────

class TestDefaultEscalate:
    def test_unknown_action_escalates(self):
        r = _engine().recover(_vresult("custom_portal_operation"))
        assert r.strategy_applied == RecoveryStrategy.ESCALATE

    def test_unknown_action_can_retry_false(self):
        r = _engine().recover(_vresult("custom_portal_operation"))
        assert r.can_retry is False

    def test_unknown_action_status_escalated(self):
        r = _engine().recover(_vresult("custom_portal_operation"))
        assert r.status == RecoveryStatus.ESCALATED


# ── Exception Isolation ───────────────────────────────────────────────────────

class TestRecoveryExceptionIsolation:
    def test_bad_verification_result_does_not_raise(self):
        bad_vr = MagicMock()
        bad_vr.action_type = MagicMock(side_effect=RuntimeError("boom"))
        engine = RecoveryEngine()
        result = engine.recover(bad_vr)
        assert result is not None
        assert result.strategy_applied == RecoveryStrategy.ESCALATE

    def test_recovery_id_always_set(self):
        r = _engine().recover(_vresult("resend_otp"))
        assert r.recovery_id != ""

    def test_recovered_at_always_set(self):
        r = _engine().recover(_vresult("resend_otp"))
        assert r.recovered_at != ""

    def test_attempts_used_stored(self):
        r = _engine().recover(_vresult("resend_otp"), attempt_count=2)
        assert r.attempts_used == 2
