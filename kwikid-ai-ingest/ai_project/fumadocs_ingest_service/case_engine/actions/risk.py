"""
case_engine/actions/risk.py

Sprint 2.21: Risk Assessment Engine — RISKCHECK node.

Per flow_diagram.mermaid:
    ACTIONGW → RISKCHECK{Risk Level}
    RISKCHECK -->|SAFE| EXECUTE
    RISKCHECK -->|REVERSIBLE / HIGH| APPROVAL

Per blueprint Section 17 (Risk Model):
    SAFE        — Auto execution allowed (OTP resend, OCR retry)
    REVERSIBLE  — Approval may be required (session reset)
    HIGH_RISK   — Approval required (financial, destructive operations)

This module implements the deterministic risk classification lookup.
No LLM. No guessing. Table-driven rules only.
Never raises.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.actions.models import (
    ActionProposalItem,
    ActionRiskAssessment,
    ProposedActionType,
    ProposalRiskLevel,
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Risk classification table ─────────────────────────────────────────────────
# Per blueprint Section 17. Deterministic — not configurable at runtime.

_RISK_TABLE: dict[ProposedActionType, tuple[ProposalRiskLevel, bool, str]] = {
    #  action_type                  risk_level               requires_approval  reason
    ProposedActionType.ASK_USER_RETRY:         (ProposalRiskLevel.SAFE,       False, "Informational request — no system state change"),
    ProposedActionType.WAIT_AND_RETRY:         (ProposalRiskLevel.SAFE,       False, "Passive wait — no system mutation"),
    ProposedActionType.RESEND_OTP:             (ProposalRiskLevel.SAFE,       False, "OTP resend is idempotent and user-initiated"),
    ProposedActionType.RETRY_DOCUMENT_CAPTURE: (ProposalRiskLevel.SAFE,       False, "OCR retry is non-destructive and user-recoverable"),
    ProposedActionType.CHECK_SERVER_STATUS:    (ProposalRiskLevel.SAFE,       False, "Read-only diagnostic — no state change"),
    ProposedActionType.REFRESH_PORTAL:         (ProposalRiskLevel.SAFE,       False, "Portal cache refresh is non-destructive"),
    ProposedActionType.RESET_SESSION:          (ProposalRiskLevel.REVERSIBLE, True,  "Session reset terminates active session; can be restarted"),
    ProposedActionType.RETRY_CALLBACK:         (ProposalRiskLevel.REVERSIBLE, True,  "Callback retry touches external API; may have side effects"),
    ProposedActionType.MANUAL_REVIEW:          (ProposalRiskLevel.REVERSIBLE, True,  "Manual review requires human assignment and attention"),
    ProposedActionType.ESCALATE_L2:            (ProposalRiskLevel.REVERSIBLE, True,  "L2 escalation triggers engineering engagement"),
    ProposedActionType.CREATE_ASANA_TICKET:    (ProposalRiskLevel.REVERSIBLE, True,  "Creates external artefact in Asana; requires human context"),
    ProposedActionType.UNKNOWN_ACTION:         (ProposalRiskLevel.HIGH_RISK,  True,  "Unknown actions are treated as high-risk by default"),
}

# Default for any action type not in the table
_DEFAULT_RISK = (ProposalRiskLevel.HIGH_RISK, True, "Unclassified action defaults to HIGH_RISK")


class RiskAssessmentEngine:
    """
    Deterministic risk classification for proposed actions.

    Table-driven: each ProposedActionType maps to a fixed ProposalRiskLevel.
    No LLM. No external I/O. Never raises.

    Per blueprint Principle 3 (Reasoning before execution): risk is assessed
    at proposal time, before anything is passed to the Action Gateway.
    """

    def assess(self, action_type: ProposedActionType) -> ActionRiskAssessment:
        """
        Return an ActionRiskAssessment for the given action type.

        Never raises — unknown action types produce HIGH_RISK assessment.
        """
        try:
            risk_level, requires_approval, reason = _RISK_TABLE.get(action_type, _DEFAULT_RISK)
            return ActionRiskAssessment(
                assessment_id=str(uuid.uuid4()),
                action_type=action_type,
                risk_level=risk_level,
                requires_approval=requires_approval,
                risk_reason=reason,
                assessed_at=_now_iso(),
            )
        except Exception:
            LOGGER.exception(
                "risk_assessment_engine.assess failed action_type=%s — defaulting to HIGH_RISK",
                action_type,
            )
            return ActionRiskAssessment(
                assessment_id=str(uuid.uuid4()),
                action_type=action_type if isinstance(action_type, ProposedActionType)
                             else ProposedActionType.UNKNOWN_ACTION,
                risk_level=ProposalRiskLevel.HIGH_RISK,
                requires_approval=True,
                risk_reason="Internal error during risk assessment — defaulting to HIGH_RISK",
                assessed_at=_now_iso(),
            )

    def assess_bundle(self, action_types: list[ProposedActionType]) -> list[ActionRiskAssessment]:
        """Assess a list of action types in order. Never raises."""
        return [self.assess(at) for at in action_types]

    def highest_risk(self, assessments: list[ActionRiskAssessment]) -> ProposalRiskLevel:
        """Return the highest risk level across a list of assessments."""
        if not assessments:
            return ProposalRiskLevel.SAFE
        order = {
            ProposalRiskLevel.SAFE:       0,
            ProposalRiskLevel.REVERSIBLE: 1,
            ProposalRiskLevel.HIGH_RISK:  2,
        }
        return max(assessments, key=lambda a: order.get(a.risk_level, 0)).risk_level

    def any_requires_approval(self, assessments: list[ActionRiskAssessment]) -> bool:
        """Return True if any assessment requires approval."""
        return any(a.requires_approval for a in assessments)
