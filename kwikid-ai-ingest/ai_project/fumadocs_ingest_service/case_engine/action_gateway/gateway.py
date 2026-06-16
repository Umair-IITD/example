"""
case_engine/action_gateway/gateway.py

Sprint 2.22: ACTIONGW node — ProposalGateway validation layer.

Per flow_diagram.mermaid:
    ACTIONPROPOSAL → ACTIONGW → RISKCHECK

Validates the ActionProposalBundle before risk routing.

Checks (in order):
1. Proposal result is present and status == "COMPLETED"
2. investigation_result is present (Principle 1: Investigation Before Action)
3. Top proposal is present
4. Root cause category is present in investigation
5. Confidence check (investigation confidence >= threshold)
6. SOP correlation (advisory — never blocks)

Returns ActionGatewayDecision: VALID or BLOCKED.

Fail-closed: any structural gap → BLOCKED (never proceeds without evidence).
Never raises.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.action_gateway.models import (
    ActionGatewayDecision,
    GatewayRiskLevel,
    GatewayValidationStatus,
)

LOGGER = logging.getLogger(__name__)

# Minimum investigation confidence to proceed to gateway
# Below this threshold the investigation is considered inconclusive.
CONFIDENCE_THRESHOLD = 0.4


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ProposalGateway:
    """
    ACTIONGW node — validates the ActionProposalBundle before risk routing.

    Stateless. All decisions are deterministic.
    Fail-closed: any structural gap → BLOCKED.
    Never raises.
    """

    def validate(
        self,
        topic: str,
        action_proposal_result: dict[str, Any] | None,
        investigation_result: dict[str, Any] | None,
        knowledge_result: dict[str, Any] | None,
    ) -> ActionGatewayDecision:
        """
        Validate an ActionProposalBundle for gateway entry.

        Returns ActionGatewayDecision with:
          - VALID   → all checks passed, proceed to RISKCHECK
          - BLOCKED → one or more checks failed, do not proceed

        Never raises.
        """
        try:
            return self._validate(
                topic=topic,
                action_proposal_result=action_proposal_result or {},
                investigation_result=investigation_result or {},
                knowledge_result=knowledge_result or {},
            )
        except Exception:
            LOGGER.exception(
                "proposal_gateway.validate failed topic=%s — returning BLOCKED (fail-safe)", topic
            )
            return ActionGatewayDecision(
                decision_id=_new_id(),
                validation_status=GatewayValidationStatus.BLOCKED,
                risk_level=GatewayRiskLevel.HIGH_RISK,
                requires_approval=True,
                validation_failures=("internal_error_in_gateway_validation",),
                bundle_id=None,
                top_action_type=None,
                confidence_check_passed=False,
                investigation_check_passed=False,
                sop_check_passed=False,
                decided_at=_now_iso(),
            )

    # ── Private ───────────────────────────────────────────────────────────────

    def _validate(
        self,
        topic: str,
        action_proposal_result: dict[str, Any],
        investigation_result: dict[str, Any],
        knowledge_result: dict[str, Any],
    ) -> ActionGatewayDecision:
        failures: list[str] = []

        # ── Check 1: Proposal result present and status=COMPLETED ─────────────
        proposal_status = action_proposal_result.get("status", "")
        if not action_proposal_result or proposal_status != "COMPLETED":
            reason = (
                "proposal_result_missing"
                if not action_proposal_result
                else f"proposal_status_not_completed:{proposal_status}"
            )
            failures.append(reason)

        bundle_id: str | None = action_proposal_result.get("bundle_id")

        # ── Check 2: investigation_result present ─────────────────────────────
        investigation_check_passed = bool(
            investigation_result and investigation_result.get("status") == "COMPLETED"
        )
        if not investigation_check_passed:
            failures.append("investigation_result_missing_or_incomplete")

        # ── Check 3: top_proposal present ─────────────────────────────────────
        top_proposal: dict[str, Any] = action_proposal_result.get("top_proposal") or {}
        top_action_type: str | None = top_proposal.get("action_type")
        if not top_proposal or not top_action_type:
            failures.append("top_proposal_missing")

        # ── Check 4: root_cause present ───────────────────────────────────────
        root_cause: dict[str, Any] = (investigation_result.get("root_cause") or {})
        root_cause_category: str | None = root_cause.get("category")
        if investigation_check_passed and not root_cause_category:
            failures.append("root_cause_category_missing")

        # ── Check 5: confidence check ─────────────────────────────────────────
        investigation_confidence: float = 0.0
        try:
            investigation_confidence = float(root_cause.get("confidence", 0.0))
        except (TypeError, ValueError):
            investigation_confidence = 0.0

        confidence_check_passed = investigation_confidence >= CONFIDENCE_THRESHOLD
        if investigation_check_passed and not confidence_check_passed:
            failures.append(
                f"investigation_confidence_below_threshold:"
                f"{investigation_confidence:.2f}<{CONFIDENCE_THRESHOLD}"
            )

        # ── Check 6: SOP correlation (advisory — never blocks) ────────────────
        sop_check_passed = bool(
            knowledge_result and knowledge_result.get("sop_match_found")
        )
        # SOP not found is not a blocker — it means proceed without SOP guidance

        # ── Determine risk level from proposal ────────────────────────────────
        risk_assessment: dict[str, Any] = top_proposal.get("risk_assessment") or {}
        proposal_risk_str = risk_assessment.get("risk_level", "HIGH_RISK")
        try:
            risk_level = GatewayRiskLevel(str(proposal_risk_str).upper())
        except ValueError:
            risk_level = GatewayRiskLevel.HIGH_RISK

        # ── Final decision ────────────────────────────────────────────────────
        if failures:
            LOGGER.warning(
                "proposal_gateway.validate: BLOCKED topic=%s bundle_id=%s failures=%s",
                topic, bundle_id, failures,
            )
            return ActionGatewayDecision(
                decision_id=_new_id(),
                validation_status=GatewayValidationStatus.BLOCKED,
                risk_level=GatewayRiskLevel.HIGH_RISK,
                requires_approval=True,
                validation_failures=tuple(failures),
                bundle_id=bundle_id,
                top_action_type=top_action_type,
                confidence_check_passed=confidence_check_passed,
                investigation_check_passed=investigation_check_passed,
                sop_check_passed=sop_check_passed,
                decided_at=_now_iso(),
            )

        LOGGER.info(
            "proposal_gateway.validate: VALID topic=%s bundle_id=%s risk=%s sop=%s",
            topic, bundle_id, risk_level.value, sop_check_passed,
        )
        return ActionGatewayDecision(
            decision_id=_new_id(),
            validation_status=GatewayValidationStatus.VALID,
            risk_level=risk_level,
            requires_approval=(risk_level != GatewayRiskLevel.SAFE),
            validation_failures=(),
            bundle_id=bundle_id,
            top_action_type=top_action_type,
            confidence_check_passed=confidence_check_passed,
            investigation_check_passed=investigation_check_passed,
            sop_check_passed=sop_check_passed,
            decided_at=_now_iso(),
        )
