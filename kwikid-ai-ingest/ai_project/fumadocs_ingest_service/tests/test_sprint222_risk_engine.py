"""
tests/test_sprint222_risk_engine.py

Sprint 2.22: Tests for case_engine/action_gateway/risk_engine.py

Coverage:
- GatewayRiskEngine.map_from_proposal: known values, unknown defaults HIGH_RISK
- GatewayRiskEngine.route: SAFE->EXECUTE, REVERSIBLE/HIGH_RISK->APPROVAL
- GatewayRiskEngine.requires_approval: table values
- GatewayRiskEngine.can_auto_approve: only SAFE
- GatewayRiskEngine.is_high_risk: only HIGH_RISK
- GatewayRiskEngine.highest: multiple levels, empty list
- GatewayRiskEngine.assess_bundle: happy path, missing fields, error recovery
"""
import pytest

from case_engine.action_gateway.models import GatewayRiskLevel
from case_engine.action_gateway.risk_engine import (
    ROUTE_APPROVAL,
    ROUTE_EXECUTE,
    GatewayRiskEngine,
)


@pytest.fixture
def engine() -> GatewayRiskEngine:
    return GatewayRiskEngine()


# ── map_from_proposal ────────────────────────────────────────────────────────

class TestMapFromProposal:
    def test_safe(self, engine):
        assert engine.map_from_proposal("SAFE") == GatewayRiskLevel.SAFE

    def test_reversible(self, engine):
        assert engine.map_from_proposal("REVERSIBLE") == GatewayRiskLevel.REVERSIBLE

    def test_high_risk(self, engine):
        assert engine.map_from_proposal("HIGH_RISK") == GatewayRiskLevel.HIGH_RISK

    def test_unknown_defaults_high_risk(self, engine):
        assert engine.map_from_proposal("UNKNOWN_LEVEL") == GatewayRiskLevel.HIGH_RISK

    def test_empty_defaults_high_risk(self, engine):
        assert engine.map_from_proposal("") == GatewayRiskLevel.HIGH_RISK

    def test_case_insensitive_safe(self, engine):
        assert engine.map_from_proposal("safe") == GatewayRiskLevel.SAFE

    def test_case_insensitive_reversible(self, engine):
        assert engine.map_from_proposal("reversible") == GatewayRiskLevel.REVERSIBLE

    def test_case_insensitive_high_risk(self, engine):
        assert engine.map_from_proposal("high_risk") == GatewayRiskLevel.HIGH_RISK

    def test_none_defaults_high_risk(self, engine):
        # None should not raise — fail-closed
        result = engine.map_from_proposal(None)  # type: ignore
        assert result == GatewayRiskLevel.HIGH_RISK


# ── route ────────────────────────────────────────────────────────────────────

class TestRoute:
    def test_safe_executes(self, engine):
        assert engine.route(GatewayRiskLevel.SAFE) == ROUTE_EXECUTE

    def test_reversible_requires_approval(self, engine):
        assert engine.route(GatewayRiskLevel.REVERSIBLE) == ROUTE_APPROVAL

    def test_high_risk_requires_approval(self, engine):
        assert engine.route(GatewayRiskLevel.HIGH_RISK) == ROUTE_APPROVAL

    def test_route_constants(self):
        assert ROUTE_EXECUTE == "EXECUTE"
        assert ROUTE_APPROVAL == "APPROVAL"


# ── requires_approval ────────────────────────────────────────────────────────

class TestRequiresApproval:
    def test_safe_no_approval(self, engine):
        assert engine.requires_approval(GatewayRiskLevel.SAFE) is False

    def test_reversible_needs_approval(self, engine):
        assert engine.requires_approval(GatewayRiskLevel.REVERSIBLE) is True

    def test_high_risk_needs_approval(self, engine):
        assert engine.requires_approval(GatewayRiskLevel.HIGH_RISK) is True


# ── can_auto_approve ─────────────────────────────────────────────────────────

class TestCanAutoApprove:
    def test_safe_can_auto_approve(self, engine):
        assert engine.can_auto_approve(GatewayRiskLevel.SAFE) is True

    def test_reversible_cannot_auto_approve(self, engine):
        assert engine.can_auto_approve(GatewayRiskLevel.REVERSIBLE) is False

    def test_high_risk_cannot_auto_approve(self, engine):
        assert engine.can_auto_approve(GatewayRiskLevel.HIGH_RISK) is False


# ── is_high_risk ─────────────────────────────────────────────────────────────

class TestIsHighRisk:
    def test_high_risk_true(self, engine):
        assert engine.is_high_risk(GatewayRiskLevel.HIGH_RISK) is True

    def test_safe_not_high_risk(self, engine):
        assert engine.is_high_risk(GatewayRiskLevel.SAFE) is False

    def test_reversible_not_high_risk(self, engine):
        assert engine.is_high_risk(GatewayRiskLevel.REVERSIBLE) is False


# ── highest ──────────────────────────────────────────────────────────────────

class TestHighest:
    def test_empty_returns_safe(self, engine):
        assert engine.highest([]) == GatewayRiskLevel.SAFE

    def test_single_safe(self, engine):
        assert engine.highest([GatewayRiskLevel.SAFE]) == GatewayRiskLevel.SAFE

    def test_single_reversible(self, engine):
        assert engine.highest([GatewayRiskLevel.REVERSIBLE]) == GatewayRiskLevel.REVERSIBLE

    def test_single_high_risk(self, engine):
        assert engine.highest([GatewayRiskLevel.HIGH_RISK]) == GatewayRiskLevel.HIGH_RISK

    def test_safe_and_reversible(self, engine):
        result = engine.highest([GatewayRiskLevel.SAFE, GatewayRiskLevel.REVERSIBLE])
        assert result == GatewayRiskLevel.REVERSIBLE

    def test_all_three(self, engine):
        result = engine.highest([GatewayRiskLevel.SAFE, GatewayRiskLevel.HIGH_RISK, GatewayRiskLevel.REVERSIBLE])
        assert result == GatewayRiskLevel.HIGH_RISK

    def test_multiple_safe(self, engine):
        result = engine.highest([GatewayRiskLevel.SAFE, GatewayRiskLevel.SAFE])
        assert result == GatewayRiskLevel.SAFE


# ── assess_bundle ────────────────────────────────────────────────────────────

def _make_bundle(risk_level: str = "SAFE") -> dict:
    return {
        "top_proposal": {
            "risk_assessment": {"risk_level": risk_level, "requires_approval": False}
        }
    }


class TestAssessBundle:
    def test_safe_bundle(self, engine):
        level, approval, route = engine.assess_bundle(_make_bundle("SAFE"))
        assert level == GatewayRiskLevel.SAFE
        assert approval is False
        assert route == ROUTE_EXECUTE

    def test_reversible_bundle(self, engine):
        level, approval, route = engine.assess_bundle(_make_bundle("REVERSIBLE"))
        assert level == GatewayRiskLevel.REVERSIBLE
        assert approval is True
        assert route == ROUTE_APPROVAL

    def test_high_risk_bundle(self, engine):
        level, approval, route = engine.assess_bundle(_make_bundle("HIGH_RISK"))
        assert level == GatewayRiskLevel.HIGH_RISK
        assert approval is True
        assert route == ROUTE_APPROVAL

    def test_missing_top_proposal_defaults_high_risk(self, engine):
        level, approval, route = engine.assess_bundle({})
        assert level == GatewayRiskLevel.HIGH_RISK
        assert approval is True

    def test_missing_risk_assessment_defaults_high_risk(self, engine):
        level, approval, route = engine.assess_bundle({"top_proposal": {}})
        assert level == GatewayRiskLevel.HIGH_RISK

    def test_unknown_risk_level_defaults_high_risk(self, engine):
        level, _, _ = engine.assess_bundle(_make_bundle("UNKNOWN"))
        assert level == GatewayRiskLevel.HIGH_RISK

    def test_none_bundle_defaults_high_risk(self, engine):
        level, approval, route = engine.assess_bundle(None)  # type: ignore
        assert level == GatewayRiskLevel.HIGH_RISK
        assert approval is True
        assert route == ROUTE_APPROVAL

    def test_exception_in_bundle_defaults_high_risk(self, engine):
        # Passing a non-dict should not raise
        level, approval, route = engine.assess_bundle("invalid")  # type: ignore
        assert level == GatewayRiskLevel.HIGH_RISK
