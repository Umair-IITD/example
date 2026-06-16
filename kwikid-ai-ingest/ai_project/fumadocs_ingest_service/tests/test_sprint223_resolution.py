"""
tests/test_sprint223_resolution.py

Sprint 2.23: ResolutionEngine tests.

Coverage:
  - RESOLVED: verified success, no recovery
  - PARTIALLY_RESOLVED: verified success after recovery
  - PARTIALLY_RESOLVED: rollback applied
  - UNRESOLVED: retry scheduled
  - ESCALATED: escalate strategy
  - ESCALATED: dead_letter strategy
  - UNRESOLVED: failure, no recovery
  - UNRESOLVED: uncertain, no recovery
  - action_type override
  - Exception isolation (never raises)
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock

from case_engine.execution.models import (
    RecoveryResult,
    RecoveryStatus,
    RecoveryStrategy,
    ResolutionResult,
    ResolutionStatus,
    VerificationResult,
    VerificationStatus,
)
from case_engine.execution.resolution import ResolutionEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _vresult(status: VerificationStatus = VerificationStatus.VERIFIED_SUCCESS) -> VerificationResult:
    return VerificationResult(
        verification_id="v-001",
        action_type="resend_otp",
        status=status,
        confirmed=status == VerificationStatus.VERIFIED_SUCCESS,
        evidence={"execution_status": "SUCCESS"},
        failure_reason=None if status == VerificationStatus.VERIFIED_SUCCESS else "failed",
        verified_at="2026-06-12T10:00:00+00:00",
    )


def _rresult(strategy: RecoveryStrategy) -> RecoveryResult:
    can_retry = strategy == RecoveryStrategy.RETRY
    return RecoveryResult(
        recovery_id="rec-001",
        strategy_applied=strategy,
        status=RecoveryStatus.RECOVERY_PENDING if can_retry else RecoveryStatus.ESCALATED,
        max_retries=3,
        attempts_used=1,
        can_retry=can_retry,
        notes="test recovery",
        recovered_at="2026-06-12T10:00:01+00:00",
    )


def _engine() -> ResolutionEngine:
    return ResolutionEngine()


# ── RESOLVED ──────────────────────────────────────────────────────────────────

class TestResolvedPath:
    def test_verified_success_no_recovery_resolved(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert r.status == ResolutionStatus.RESOLVED

    def test_resolved_resolved_is_true(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert r.resolved is True

    def test_resolved_note_mentions_verified(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert "verified" in r.resolution_note.lower() or "successful" in r.resolution_note.lower()

    def test_resolved_resolution_id_set(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert r.resolution_id != ""

    def test_resolved_action_type_stored(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert r.action_type == "resend_otp"

    def test_resolved_evidence_keys_list(self):
        r = _engine().resolve(_vresult(VerificationStatus.VERIFIED_SUCCESS))
        assert isinstance(r.evidence_keys, tuple)


# ── PARTIALLY_RESOLVED after recovery ────────────────────────────────────────

class TestPartiallyResolvedAfterRecovery:
    def test_verified_success_with_rollback_recovery_partial(self):
        vr = _vresult(VerificationStatus.VERIFIED_SUCCESS)
        rr = _rresult(RecoveryStrategy.ROLLBACK)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.PARTIALLY_RESOLVED

    def test_verified_success_with_retry_recovery_partial(self):
        vr = _vresult(VerificationStatus.VERIFIED_SUCCESS)
        rr = _rresult(RecoveryStrategy.RETRY)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.PARTIALLY_RESOLVED

    def test_partially_resolved_is_false(self):
        vr = _vresult(VerificationStatus.VERIFIED_SUCCESS)
        rr = _rresult(RecoveryStrategy.ROLLBACK)
        r = _engine().resolve(vr, rr)
        assert r.resolved is False


# ── PARTIALLY_RESOLVED: rollback applied ──────────────────────────────────────

class TestPartiallyResolvedRollback:
    def test_rollback_applied_partial(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ROLLBACK)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.PARTIALLY_RESOLVED

    def test_rollback_applied_resolved_false(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ROLLBACK)
        r = _engine().resolve(vr, rr)
        assert r.resolved is False

    def test_rollback_note_mentions_rollback(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ROLLBACK)
        r = _engine().resolve(vr, rr)
        assert "roll" in r.resolution_note.lower()


# ── UNRESOLVED: retry scheduled ───────────────────────────────────────────────

class TestUnresolvedRetry:
    def test_retry_scheduled_unresolved(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.RETRY)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.UNRESOLVED

    def test_retry_unresolved_resolved_false(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.RETRY)
        r = _engine().resolve(vr, rr)
        assert r.resolved is False

    def test_retry_note_mentions_retry(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.RETRY)
        r = _engine().resolve(vr, rr)
        assert "retry" in r.resolution_note.lower() or "pending" in r.resolution_note.lower()


# ── ESCALATED: escalate / dead_letter ────────────────────────────────────────

class TestEscalated:
    def test_escalate_strategy_escalated(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ESCALATE)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.ESCALATED

    def test_dead_letter_strategy_escalated(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.DEAD_LETTER)
        r = _engine().resolve(vr, rr)
        assert r.status == ResolutionStatus.ESCALATED

    def test_escalated_resolved_false(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ESCALATE)
        r = _engine().resolve(vr, rr)
        assert r.resolved is False

    def test_escalated_note_mentions_escalated(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        rr = _rresult(RecoveryStrategy.ESCALATE)
        r = _engine().resolve(vr, rr)
        assert "escalat" in r.resolution_note.lower()


# ── UNRESOLVED: failure, no recovery ─────────────────────────────────────────

class TestUnresolvedNoRecovery:
    def test_failure_no_recovery_unresolved(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        r = _engine().resolve(vr)
        assert r.status == ResolutionStatus.UNRESOLVED

    def test_uncertain_no_recovery_unresolved(self):
        vr = _vresult(VerificationStatus.UNCERTAIN)
        r = _engine().resolve(vr)
        assert r.status == ResolutionStatus.UNRESOLVED

    def test_unresolved_resolved_false(self):
        vr = _vresult(VerificationStatus.VERIFIED_FAILURE)
        r = _engine().resolve(vr)
        assert r.resolved is False


# ── action_type override ──────────────────────────────────────────────────────

class TestActionTypeOverride:
    def test_action_type_override_used(self):
        vr = _vresult(VerificationStatus.VERIFIED_SUCCESS)
        r = _engine().resolve(vr, action_type="reset_session")
        assert r.action_type == "reset_session"

    def test_action_type_from_verification_if_not_overridden(self):
        vr = _vresult(VerificationStatus.VERIFIED_SUCCESS)
        r = _engine().resolve(vr)
        assert r.action_type == "resend_otp"


# ── Exception isolation ───────────────────────────────────────────────────────

class TestResolutionExceptionIsolation:
    def test_bad_verification_does_not_raise(self):
        bad_vr = MagicMock()
        bad_vr.action_type = "t"
        bad_vr.is_success = MagicMock(side_effect=RuntimeError("boom"))
        r = _engine().resolve(bad_vr)
        assert r is not None
        assert r.status == ResolutionStatus.UNRESOLVED

    def test_resolution_id_always_set(self):
        r = _engine().resolve(_vresult())
        assert r.resolution_id != ""

    def test_resolved_at_always_set(self):
        r = _engine().resolve(_vresult())
        assert r.resolved_at != ""
