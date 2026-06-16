"""
tests/test_sprint222_gateway.py

Sprint 2.22: Tests for case_engine/action_gateway/gateway.py

Coverage:
- ProposalGateway.validate: all validation checks
- VALID path: all checks pass
- BLOCKED paths: proposal missing, proposal not COMPLETED, investigation missing,
  top_proposal missing, root_cause missing, confidence below threshold
- SOP advisory check: never blocks
- Risk level extracted from proposal
- Unknown risk level defaults to HIGH_RISK
- Exception isolation: never raises
"""
import pytest

from case_engine.action_gateway.gateway import CONFIDENCE_THRESHOLD, ProposalGateway
from case_engine.action_gateway.models import (
    GatewayRiskLevel,
    GatewayValidationStatus,
)


@pytest.fixture
def gw() -> ProposalGateway:
    return ProposalGateway()


def _investigation(confidence: float = 0.85, category: str = "TIMEOUT") -> dict:
    return {
        "status": "COMPLETED",
        "root_cause": {
            "category": category,
            "confidence": confidence,
            "escalate": False,
        },
    }


def _proposal(risk_level: str = "SAFE", action_type: str = "RESET_SESSION", status: str = "COMPLETED") -> dict:
    return {
        "status": status,
        "bundle_id": "bundle-test",
        "top_proposal": {
            "action_type": action_type,
            "risk_assessment": {"risk_level": risk_level, "requires_approval": False},
        },
    }


def _knowledge(sop_found: bool = True) -> dict:
    return {"sop_match_found": sop_found, "result_id": "k-001"}


# ── VALID path ────────────────────────────────────────────────────────────────

class TestValidPath:
    def test_all_checks_pass_returns_valid(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.VALID

    def test_all_checks_pass_is_valid(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert decision.is_valid() is True

    def test_no_failures(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert len(decision.validation_failures) == 0

    def test_risk_level_extracted(self, gw):
        decision = gw.validate("VKYC", _proposal("SAFE"), _investigation(), _knowledge())
        assert decision.risk_level == GatewayRiskLevel.SAFE

    def test_reversible_risk_extracted(self, gw):
        decision = gw.validate("VKYC", _proposal("REVERSIBLE"), _investigation(), _knowledge())
        assert decision.risk_level == GatewayRiskLevel.REVERSIBLE

    def test_high_risk_level_extracted(self, gw):
        decision = gw.validate("VKYC", _proposal("HIGH_RISK"), _investigation(), _knowledge())
        assert decision.risk_level == GatewayRiskLevel.HIGH_RISK

    def test_safe_requires_approval_false(self, gw):
        decision = gw.validate("VKYC", _proposal("SAFE"), _investigation(), _knowledge())
        assert decision.requires_approval is False

    def test_reversible_requires_approval_true(self, gw):
        decision = gw.validate("VKYC", _proposal("REVERSIBLE"), _investigation(), _knowledge())
        assert decision.requires_approval is True

    def test_investigation_check_passed(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert decision.investigation_check_passed is True

    def test_confidence_check_passed(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(0.85), _knowledge())
        assert decision.confidence_check_passed is True

    def test_sop_check_passed(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge(True))
        assert decision.sop_check_passed is True

    def test_bundle_id_captured(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert decision.bundle_id == "bundle-test"

    def test_top_action_type_captured(self, gw):
        decision = gw.validate("VKYC", _proposal(action_type="CHECK_SERVER_STATUS"), _investigation(), _knowledge())
        assert decision.top_action_type == "CHECK_SERVER_STATUS"

    def test_decision_id_set(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert decision.decision_id is not None

    def test_decided_at_set(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge())
        assert "T" in decision.decided_at


# ── BLOCKED: proposal checks ──────────────────────────────────────────────────

class TestBlockedProposalChecks:
    def test_no_proposal_result_blocked(self, gw):
        decision = gw.validate("VKYC", None, _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_empty_proposal_blocked(self, gw):
        decision = gw.validate("VKYC", {}, _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_proposal_not_completed_blocked(self, gw):
        p = _proposal(status="BLOCKED")
        decision = gw.validate("VKYC", p, _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED
        assert any("proposal_status_not_completed" in f for f in decision.validation_failures)

    def test_proposal_in_progress_blocked(self, gw):
        p = _proposal(status="IN_PROGRESS")
        decision = gw.validate("VKYC", p, _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_blocked_defaults_high_risk(self, gw):
        decision = gw.validate("VKYC", None, _investigation(), _knowledge())
        assert decision.risk_level == GatewayRiskLevel.HIGH_RISK

    def test_blocked_requires_approval(self, gw):
        decision = gw.validate("VKYC", None, _investigation(), _knowledge())
        assert decision.requires_approval is True

    def test_no_top_proposal_blocked(self, gw):
        p = {"status": "COMPLETED", "bundle_id": "b1", "top_proposal": None}
        decision = gw.validate("VKYC", p, _investigation(), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED
        assert any("top_proposal" in f for f in decision.validation_failures)


# ── BLOCKED: investigation checks ────────────────────────────────────────────

class TestBlockedInvestigationChecks:
    def test_no_investigation_blocked(self, gw):
        decision = gw.validate("VKYC", _proposal(), None, _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED
        assert any("investigation_result_missing" in f for f in decision.validation_failures)

    def test_empty_investigation_blocked(self, gw):
        decision = gw.validate("VKYC", _proposal(), {}, _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_investigation_not_completed_blocked(self, gw):
        inv = {"status": "FAILED", "root_cause": {"category": "TIMEOUT", "confidence": 0.9}}
        decision = gw.validate("VKYC", _proposal(), inv, _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_investigation_check_failed_on_missing(self, gw):
        decision = gw.validate("VKYC", _proposal(), None, _knowledge())
        assert decision.investigation_check_passed is False

    def test_low_confidence_blocked(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(confidence=0.1), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED
        assert decision.confidence_check_passed is False

    def test_zero_confidence_blocked(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(confidence=0.0), _knowledge())
        assert decision.validation_status == GatewayValidationStatus.BLOCKED

    def test_exactly_at_threshold_passes(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(confidence=CONFIDENCE_THRESHOLD), _knowledge())
        assert decision.confidence_check_passed is True


# ── SOP advisory check ────────────────────────────────────────────────────────

class TestSopAdvisoryCheck:
    def test_sop_not_found_does_not_block(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge(False))
        assert decision.validation_status == GatewayValidationStatus.VALID

    def test_sop_check_false_when_not_found(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), _knowledge(False))
        assert decision.sop_check_passed is False

    def test_no_knowledge_result_does_not_block(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), None)
        assert decision.validation_status == GatewayValidationStatus.VALID

    def test_sop_check_false_on_missing_knowledge(self, gw):
        decision = gw.validate("VKYC", _proposal(), _investigation(), None)
        assert decision.sop_check_passed is False


# ── Unknown risk level ────────────────────────────────────────────────────────

class TestUnknownRiskLevel:
    def test_unknown_risk_defaults_high_risk_valid_otherwise(self, gw):
        p = {
            "status": "COMPLETED",
            "bundle_id": "b1",
            "top_proposal": {
                "action_type": "SOME_ACTION",
                "risk_assessment": {"risk_level": "UNKNOWN_LEVEL"},
            },
        }
        decision = gw.validate("VKYC", p, _investigation(), _knowledge())
        assert decision.risk_level == GatewayRiskLevel.HIGH_RISK
        assert decision.validation_status == GatewayValidationStatus.VALID


# ── Exception isolation ───────────────────────────────────────────────────────

class TestExceptionIsolation:
    def test_validate_never_raises(self, gw):
        # Even with completely broken input, should not raise
        try:
            decision = gw.validate("topic", "not-a-dict", "also-not-dict", 12345)  # type: ignore
            assert decision.validation_status == GatewayValidationStatus.BLOCKED
        except Exception as e:
            pytest.fail(f"ProposalGateway.validate raised: {e}")

    def test_validate_never_raises_on_none_topic(self, gw):
        try:
            decision = gw.validate(None, _proposal(), _investigation(), _knowledge())  # type: ignore
            assert decision is not None
        except Exception as e:
            pytest.fail(f"ProposalGateway.validate raised on None topic: {e}")
