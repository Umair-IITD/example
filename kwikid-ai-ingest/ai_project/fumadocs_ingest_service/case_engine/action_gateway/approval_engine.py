"""
case_engine/action_gateway/approval_engine.py

Sprint 2.22: APPROVAL node — deterministic approval simulation.

Per flow_diagram.mermaid:
    RISKCHECK -->|REVERSIBLE / HIGH| APPROVAL
    APPROVAL --> HUMANAPPROVER
    HUMANAPPROVER --> APPROVED{Approved?}
    APPROVED -->|No| ESCALATE
    APPROVED -->|Yes| EXECUTE

Per blueprint Section 18:
    Human-in-the-loop protection.
    All approval decisions are audited.

Current version: deterministic simulation (no external integrations).

Architecture is designed for future integration with:
    - Freshdesk Approvals API
    - Slack Approval Workflow
    - Human Approver UI

Guardrail (blueprint Section 26 + Principle 7):
    HIGH_RISK CANNOT reach executor without explicit human approval.
    This is mechanically enforced: HIGH_RISK always returns PENDING.
    SAFE is auto-approved (BYPASSED path).
    REVERSIBLE returns PENDING (requires human) in current simulation.

Never raises.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.action_gateway.models import (
    GatewayApprovalDecision,
    GatewayApprovalStatus,
    GatewayRiskLevel,
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ApprovalEngine:
    """
    APPROVAL node — routes to human approval or auto-approves based on risk level.

    Architecture extensibility:
        - Override `_request_freshdesk_approval()` to integrate Freshdesk
        - Override `_request_slack_approval()` to integrate Slack
        - Override `_request_ui_approval()` to integrate custom UI

    Current implementation: deterministic simulation.
    SAFE → auto-approved immediately (no human required).
    REVERSIBLE → PENDING (architecture requires human; simulation returns PENDING).
    HIGH_RISK → PENDING (GUARDRAIL: never auto-executed; always requires human).
    """

    def decide(
        self,
        risk_level:  GatewayRiskLevel,
        bundle_id:   str = "",
        case_id:     str = "",
        topic:       str = "",
        action_type: str = "",
    ) -> GatewayApprovalDecision:
        """
        Make approval routing decision for the given risk level.

        SAFE     → APPROVED (auto — no human required, bypass path)
        REVERSIBLE → PENDING (requires human; simulation records as PENDING)
        HIGH_RISK  → PENDING (GUARDRAIL — mechanically enforced, never auto-executed)

        Never raises.
        """
        try:
            return self._decide(
                risk_level=risk_level,
                bundle_id=bundle_id,
                case_id=case_id,
                topic=topic,
                action_type=action_type,
            )
        except Exception:
            LOGGER.exception(
                "approval_engine.decide failed risk_level=%s — returning PENDING (fail-safe)",
                risk_level,
            )
            return GatewayApprovalDecision(
                approval_id=str(uuid.uuid4()),
                status=GatewayApprovalStatus.PENDING,
                approver=None,
                reason="internal_error_defaulting_to_pending",
                notes=None,
                requires_human=True,
                decided_at=_now_iso(),
            )

    def is_auto_approvable(self, risk_level: GatewayRiskLevel) -> bool:
        """Return True only for SAFE — all other risk levels require human."""
        return risk_level == GatewayRiskLevel.SAFE

    def is_high_risk_guardrail(self, risk_level: GatewayRiskLevel) -> bool:
        """
        Mechanical HIGH_RISK guardrail check.

        Returns True if risk_level is HIGH_RISK (must not proceed without human approval).
        This is the implementation of blueprint Section 26:
        'The system must never perform irreversible actions without appropriate safeguards.'
        """
        return risk_level == GatewayRiskLevel.HIGH_RISK

    # ── Private ───────────────────────────────────────────────────────────────

    def _decide(
        self,
        risk_level:  GatewayRiskLevel,
        bundle_id:   str,
        case_id:     str,
        topic:       str,
        action_type: str,
    ) -> GatewayApprovalDecision:
        approval_id = str(uuid.uuid4())
        now = _now_iso()

        if risk_level == GatewayRiskLevel.SAFE:
            return GatewayApprovalDecision(
                approval_id=approval_id,
                status=GatewayApprovalStatus.APPROVED,
                approver="auto_approval",
                reason="SAFE_action_auto_approved",
                notes=None,
                requires_human=False,
                decided_at=now,
            )

        if risk_level == GatewayRiskLevel.HIGH_RISK:
            # GUARDRAIL: HIGH_RISK is NEVER auto-approved — always PENDING
            LOGGER.warning(
                "approval_engine: HIGH_RISK action requires explicit human approval "
                "bundle_id=%s topic=%s action=%s — returning PENDING",
                bundle_id, topic, action_type,
            )
            return GatewayApprovalDecision(
                approval_id=approval_id,
                status=GatewayApprovalStatus.PENDING,
                approver=None,
                reason="HIGH_RISK_requires_explicit_human_approval",
                notes=f"Action type '{action_type}' classified as HIGH_RISK for topic '{topic}'. "
                      f"Human approval is mandatory before execution. Bundle: {bundle_id}",
                requires_human=True,
                decided_at=now,
            )

        # REVERSIBLE
        return GatewayApprovalDecision(
            approval_id=approval_id,
            status=GatewayApprovalStatus.PENDING,
            approver=None,
            reason="REVERSIBLE_action_requires_human_approval",
            notes=f"Action type '{action_type}' classified as REVERSIBLE for topic '{topic}'. "
                  f"Pending human approval. Bundle: {bundle_id}",
            requires_human=True,
            decided_at=now,
        )

    # ── Future integration hooks ───────────────────────────────────────────────
    # These methods are stubs for future integrations.
    # Override in subclasses to connect to external approval systems.

    def _request_freshdesk_approval(
        self, bundle_id: str, case_id: str, topic: str, action_type: str, risk_level: GatewayRiskLevel,
    ) -> None:
        """Future: POST approval request to Freshdesk ticket."""
        pass

    def _request_slack_approval(
        self, bundle_id: str, case_id: str, topic: str, action_type: str, risk_level: GatewayRiskLevel,
    ) -> None:
        """Future: Send Slack approval message to approver channel."""
        pass

    def _request_ui_approval(
        self, bundle_id: str, case_id: str, topic: str, action_type: str, risk_level: GatewayRiskLevel,
    ) -> None:
        """Future: Create approval record in human approver UI."""
        pass
