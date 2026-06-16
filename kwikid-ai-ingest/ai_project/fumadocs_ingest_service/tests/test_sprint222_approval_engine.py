"""
tests/test_sprint222_approval_engine.py

Sprint 2.22: Tests for case_engine/action_gateway/approval_engine.py

Coverage:
- ApprovalEngine.decide: SAFE -> APPROVED, REVERSIBLE -> PENDING, HIGH_RISK -> PENDING
- HIGH_RISK guardrail: always PENDING, never auto-executed
- ApprovalEngine.is_auto_approvable: only SAFE
- ApprovalEngine.is_high_risk_guardrail: only HIGH_RISK
- Returned GatewayApprovalDecision fields
- Exception isolation (decide never raises)
"""
import pytest

from case_engine.action_gateway.approval_engine import ApprovalEngine
from case_engine.action_gateway.models import (
    GatewayApprovalStatus,
    GatewayRiskLevel,
)


@pytest.fixture
def engine() -> ApprovalEngine:
    return ApprovalEngine()


# ── SAFE path ────────────────────────────────────────────────────────────────

class TestSafeApproval:
    def test_safe_status_approved(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.status == GatewayApprovalStatus.APPROVED

    def test_safe_auto_approver(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.approver == "auto_approval"

    def test_safe_no_human_required(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.requires_human is False

    def test_safe_can_execute(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.can_execute() is True

    def test_safe_not_pending(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.is_pending() is False

    def test_safe_reason_contains_safe(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert "SAFE" in decision.reason

    def test_safe_approval_id_is_uuid(self, engine):
        import uuid
        decision = engine.decide(GatewayRiskLevel.SAFE)
        uuid.UUID(decision.approval_id)  # should not raise

    def test_safe_decided_at_set(self, engine):
        decision = engine.decide(GatewayRiskLevel.SAFE)
        assert decision.decided_at is not None
        assert "T" in decision.decided_at  # ISO format

    def test_safe_with_context(self, engine):
        decision = engine.decide(
            GatewayRiskLevel.SAFE,
            bundle_id="b1", case_id="c1", topic="VKYC", action_type="CHECK_SERVER_STATUS"
        )
        assert decision.status == GatewayApprovalStatus.APPROVED


# ── REVERSIBLE path ──────────────────────────────────────────────────────────

class TestReversibleApproval:
    def test_reversible_status_pending(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert decision.status == GatewayApprovalStatus.PENDING

    def test_reversible_no_approver(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert decision.approver is None

    def test_reversible_requires_human(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert decision.requires_human is True

    def test_reversible_cannot_execute(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert decision.can_execute() is False

    def test_reversible_is_pending(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert decision.is_pending() is True

    def test_reversible_reason_contains_reversible(self, engine):
        decision = engine.decide(GatewayRiskLevel.REVERSIBLE)
        assert "REVERSIBLE" in decision.reason

    def test_reversible_notes_contain_action_type(self, engine):
        decision = engine.decide(
            GatewayRiskLevel.REVERSIBLE,
            bundle_id="b1", topic="VKYC", action_type="RESET_SESSION"
        )
        assert "RESET_SESSION" in (decision.notes or "")


# ── HIGH_RISK guardrail ───────────────────────────────────────────────────────

class TestHighRiskGuardrail:
    def test_high_risk_status_pending(self, engine):
        """GUARDRAIL: HIGH_RISK MUST return PENDING — never auto-executed."""
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert decision.status == GatewayApprovalStatus.PENDING

    def test_high_risk_no_approver(self, engine):
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert decision.approver is None

    def test_high_risk_requires_human(self, engine):
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert decision.requires_human is True

    def test_high_risk_cannot_execute(self, engine):
        """GUARDRAIL: HIGH_RISK can_execute MUST be False."""
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert decision.can_execute() is False

    def test_high_risk_is_pending(self, engine):
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert decision.is_pending() is True

    def test_high_risk_reason_contains_high_risk(self, engine):
        decision = engine.decide(GatewayRiskLevel.HIGH_RISK)
        assert "HIGH_RISK" in decision.reason

    def test_high_risk_notes_contain_context(self, engine):
        decision = engine.decide(
            GatewayRiskLevel.HIGH_RISK,
            bundle_id="b-danger", topic="VKYC", action_type="UNKNOWN_ACTION"
        )
        assert "UNKNOWN_ACTION" in (decision.notes or "")
        assert "b-danger" in (decision.notes or "")

    def test_high_risk_never_approved_regardless_of_input(self, engine):
        """Mechanical guarantee: no combination of inputs auto-approves HIGH_RISK."""
        for bundle_id in ["", "safe_bundle", "auto_approved_bundle"]:
            decision = engine.decide(
                GatewayRiskLevel.HIGH_RISK,
                bundle_id=bundle_id, case_id="c1",
                topic="VKYC", action_type="IRREVERSIBLE_OP"
            )
            assert decision.status != GatewayApprovalStatus.APPROVED, \
                f"HIGH_RISK was auto-approved for bundle_id={bundle_id!r}"


# ── is_auto_approvable ────────────────────────────────────────────────────────

class TestIsAutoApprovable:
    def test_safe_auto_approvable(self, engine):
        assert engine.is_auto_approvable(GatewayRiskLevel.SAFE) is True

    def test_reversible_not_auto_approvable(self, engine):
        assert engine.is_auto_approvable(GatewayRiskLevel.REVERSIBLE) is False

    def test_high_risk_not_auto_approvable(self, engine):
        assert engine.is_auto_approvable(GatewayRiskLevel.HIGH_RISK) is False


# ── is_high_risk_guardrail ────────────────────────────────────────────────────

class TestIsHighRiskGuardrail:
    def test_high_risk_true(self, engine):
        assert engine.is_high_risk_guardrail(GatewayRiskLevel.HIGH_RISK) is True

    def test_safe_false(self, engine):
        assert engine.is_high_risk_guardrail(GatewayRiskLevel.SAFE) is False

    def test_reversible_false(self, engine):
        assert engine.is_high_risk_guardrail(GatewayRiskLevel.REVERSIBLE) is False


# ── Exception isolation ───────────────────────────────────────────────────────

class TestExceptionIsolation:
    def test_decide_never_raises_on_bad_risk_level(self, engine):
        """decide() must never raise — returns PENDING on internal error."""
        # Pass an invalid risk level to trigger exception path
        class BadRiskLevel:
            pass

        decision = engine.decide(BadRiskLevel())  # type: ignore
        # Should return PENDING as fail-safe
        assert decision.status == GatewayApprovalStatus.PENDING
        assert decision.requires_human is True

    def test_decide_returns_valid_decision_on_exception(self, engine):
        decision = engine.decide(BadRiskLevel())  # type: ignore
        assert decision.approval_id is not None
        assert decision.decided_at is not None


class BadRiskLevel:
    pass
