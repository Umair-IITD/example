"""
case_engine/action_gateway/risk_engine.py

Sprint 2.22: RISKCHECK node — Gateway Risk Engine.

Per flow_diagram.mermaid:
    ACTIONGW → RISKCHECK{Risk Level}
    RISKCHECK -->|SAFE| EXECUTE
    RISKCHECK -->|REVERSIBLE / HIGH| APPROVAL

This module implements the deterministic risk routing at the gateway level.
It maps ProposalRiskLevel (from Sprint 2.21) → GatewayRiskLevel → routing decision.

Design:
- All rules are centralized here (blueprint Section 17: "Rules must be centralized").
- No hardcoded logic scattered elsewhere.
- Never raises.
- Deterministic — same input always produces same output.
"""
from __future__ import annotations

import logging

from case_engine.action_gateway.models import GatewayRiskLevel

LOGGER = logging.getLogger(__name__)

# Routing destinations
ROUTE_EXECUTE  = "EXECUTE"
ROUTE_APPROVAL = "APPROVAL"

# Approval requirement table (centralized per blueprint Section 17)
_APPROVAL_REQUIRED: dict[GatewayRiskLevel, bool] = {
    GatewayRiskLevel.SAFE:       False,
    GatewayRiskLevel.REVERSIBLE: True,
    GatewayRiskLevel.HIGH_RISK:  True,
}

# Routing table (RISKCHECK node)
_ROUTING: dict[GatewayRiskLevel, str] = {
    GatewayRiskLevel.SAFE:       ROUTE_EXECUTE,
    GatewayRiskLevel.REVERSIBLE: ROUTE_APPROVAL,
    GatewayRiskLevel.HIGH_RISK:  ROUTE_APPROVAL,
}

# Risk level ordering for comparison
_RISK_ORDER: dict[GatewayRiskLevel, int] = {
    GatewayRiskLevel.SAFE:       0,
    GatewayRiskLevel.REVERSIBLE: 1,
    GatewayRiskLevel.HIGH_RISK:  2,
}

# ProposalRiskLevel string → GatewayRiskLevel mapping
_PROPOSAL_TO_GATEWAY: dict[str, GatewayRiskLevel] = {
    "SAFE":       GatewayRiskLevel.SAFE,
    "REVERSIBLE": GatewayRiskLevel.REVERSIBLE,
    "HIGH_RISK":  GatewayRiskLevel.HIGH_RISK,
}


class GatewayRiskEngine:
    """
    RISKCHECK node implementation.

    Converts the risk level from an ActionProposalBundle into a routing decision.
    Centralizes all risk classification logic per blueprint Section 17.

    Never raises.
    """

    def map_from_proposal(self, proposal_risk_level: str) -> GatewayRiskLevel:
        """
        Map a ProposalRiskLevel string (from Sprint 2.21) to GatewayRiskLevel.

        Unknown values default to HIGH_RISK (fail-closed).
        """
        try:
            return _PROPOSAL_TO_GATEWAY.get(
                str(proposal_risk_level).upper(),
                GatewayRiskLevel.HIGH_RISK,
            )
        except Exception:
            LOGGER.exception(
                "gateway_risk_engine.map_from_proposal failed risk=%s — defaulting to HIGH_RISK",
                proposal_risk_level,
            )
            return GatewayRiskLevel.HIGH_RISK

    def route(self, risk_level: GatewayRiskLevel) -> str:
        """
        Determine routing destination for a given risk level.

        Returns ROUTE_EXECUTE or ROUTE_APPROVAL.
        """
        return _ROUTING.get(risk_level, ROUTE_APPROVAL)

    def requires_approval(self, risk_level: GatewayRiskLevel) -> bool:
        """Return True if this risk level requires human approval."""
        return _APPROVAL_REQUIRED.get(risk_level, True)

    def can_auto_approve(self, risk_level: GatewayRiskLevel) -> bool:
        """Return True if this risk level may be auto-approved without human."""
        return risk_level == GatewayRiskLevel.SAFE

    def is_high_risk(self, risk_level: GatewayRiskLevel) -> bool:
        """Return True if this is HIGH_RISK (requires explicit approval, cannot auto-execute)."""
        return risk_level == GatewayRiskLevel.HIGH_RISK

    def highest(self, levels: list[GatewayRiskLevel]) -> GatewayRiskLevel:
        """Return the highest risk level from a list. Empty list returns SAFE."""
        if not levels:
            return GatewayRiskLevel.SAFE
        return max(levels, key=lambda l: _RISK_ORDER.get(l, 0))

    def assess_bundle(self, action_proposal_result: dict) -> tuple[GatewayRiskLevel, bool, str]:
        """
        Extract and assess risk from an ActionProposalBundle dict.

        Returns (risk_level, requires_approval, routing_destination).
        Falls back to HIGH_RISK on any error (fail-closed).
        """
        try:
            top = action_proposal_result.get("top_proposal") or {}
            risk_assessment = top.get("risk_assessment") or {}
            proposal_risk = risk_assessment.get("risk_level", "HIGH_RISK")
            risk_level = self.map_from_proposal(proposal_risk)
            return (
                risk_level,
                self.requires_approval(risk_level),
                self.route(risk_level),
            )
        except Exception:
            LOGGER.exception(
                "gateway_risk_engine.assess_bundle failed — defaulting to HIGH_RISK/APPROVAL"
            )
            return (GatewayRiskLevel.HIGH_RISK, True, ROUTE_APPROVAL)
