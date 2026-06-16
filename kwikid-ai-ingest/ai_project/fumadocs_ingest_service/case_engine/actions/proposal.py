"""
case_engine/actions/proposal.py

Sprint 2.21: Action Proposal Engine — ACTIONPROPOSAL node.

Per flow_diagram.mermaid:
    ROOTCAUSE → HYBRIDRAG → REASONING → GUARDRAILS → ACTIONPROPOSAL
    ACTIONPROPOSAL → ACTIONGW

Input:
  - root_cause_category (str from investigation_result)
  - investigation_confidence (float)
  - investigation_escalate (bool)
  - knowledge_result (dict — from KNOWLEDGE_LOOKUP step, may be None)
  - topic (str)

Output:
  - ActionProposalBundle — ordered proposals, reasoning, risk assessments

Design:
  - Deterministic rule table: root_cause_category → [ProposedActionType, ...]
  - SOP correlation: when knowledge_result has sop_match_found=True, the SOP
    recommendation is used to boost or select the primary action.
  - Risk assessed at proposal time via RiskAssessmentEngine.
  - Never raises.
  - No LLM.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.actions.models import (
    ActionProposalBundle,
    ActionProposalItem,
    ActionReasoning,
    ProposedActionType,
    ProposalRiskLevel,
)
from case_engine.actions.risk import RiskAssessmentEngine

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Deterministic proposal rules ──────────────────────────────────────────────
# Per blueprint Section 15 (Action System) and root cause taxonomy in
# case_engine/investigation/models.py::RootCauseCategory.
# Order matters: higher priority actions appear first.

_PROPOSAL_RULES: dict[str, list[ProposedActionType]] = {
    # Network / infrastructure
    "NETWORK_FAILURE":      [ProposedActionType.RESET_SESSION,
                             ProposedActionType.ASK_USER_RETRY],
    "TIMEOUT":              [ProposedActionType.RESET_SESSION,
                             ProposedActionType.WAIT_AND_RETRY,
                             ProposedActionType.ASK_USER_RETRY],
    "QUOTA_EXCEEDED":       [ProposedActionType.WAIT_AND_RETRY,
                             ProposedActionType.ESCALATE_L2],
    # Session / VKYC
    "EXPIRED_SESSION":      [ProposedActionType.RESET_SESSION,
                             ProposedActionType.ASK_USER_RETRY],
    "REPEATED_FAILURE":     [ProposedActionType.RESET_SESSION,
                             ProposedActionType.MANUAL_REVIEW],
    "LIVENESS_FAILURE":     [ProposedActionType.RETRY_DOCUMENT_CAPTURE,
                             ProposedActionType.ASK_USER_RETRY,
                             ProposedActionType.ESCALATE_L2],
    "DOCUMENT_FAILURE":     [ProposedActionType.RETRY_DOCUMENT_CAPTURE,
                             ProposedActionType.ASK_USER_RETRY],
    "VALIDATION_FAILURE":   [ProposedActionType.RETRY_DOCUMENT_CAPTURE,
                             ProposedActionType.MANUAL_REVIEW],
    "KYC_REJECTED":         [ProposedActionType.ESCALATE_L2,
                             ProposedActionType.MANUAL_REVIEW],
    # Delivery / callback
    "SMS_DELIVERY_FAILURE": [ProposedActionType.RESEND_OTP,
                             ProposedActionType.ASK_USER_RETRY],
    "CALLBACK_FAILURE":     [ProposedActionType.RETRY_CALLBACK,
                             ProposedActionType.ASK_USER_RETRY],
    # Onboarding
    "ONBOARDING_BLOCKED":   [ProposedActionType.MANUAL_REVIEW,
                             ProposedActionType.ESCALATE_L2],
    # Portal
    "PORTAL_UNAVAILABLE":   [ProposedActionType.CHECK_SERVER_STATUS,
                             ProposedActionType.REFRESH_PORTAL,
                             ProposedActionType.WAIT_AND_RETRY],
    # Fallback
    "UNKNOWN":              [ProposedActionType.MANUAL_REVIEW,
                             ProposedActionType.ESCALATE_L2],
}

# Rationale templates for each action type
_RATIONALE: dict[ProposedActionType, str] = {
    ProposedActionType.RESET_SESSION:          "Root cause indicates session state corruption or expiry; reset clears the session to allow fresh start",
    ProposedActionType.ASK_USER_RETRY:         "Root cause may be transient; user retry at a later time may resolve the issue without system intervention",
    ProposedActionType.RESEND_OTP:             "OTP delivery failure detected; resending via primary channel should resolve the issue",
    ProposedActionType.RETRY_DOCUMENT_CAPTURE: "Document/OCR failure detected; requesting re-upload typically resolves image quality or processing issues",
    ProposedActionType.RETRY_CALLBACK:         "API callback failure detected; retrying the callback to the registered endpoint should resolve the issue",
    ProposedActionType.CHECK_SERVER_STATUS:    "Portal/server unavailability detected; verify infrastructure status before further action",
    ProposedActionType.REFRESH_PORTAL:         "Portal appears stale or cached; refresh resolves most temporary availability issues",
    ProposedActionType.MANUAL_REVIEW:          "Root cause requires human assessment; escalating for manual agent review",
    ProposedActionType.ESCALATE_L2:            "Issue requires engineering or L2 support team intervention",
    ProposedActionType.CREATE_ASANA_TICKET:    "Engineering intervention required; creating Asana ticket for tracking",
    ProposedActionType.WAIT_AND_RETRY:         "Transient infrastructure issue; waiting before retry is the safest approach",
    ProposedActionType.UNKNOWN_ACTION:         "Root cause is unclear; defaulting to manual review path",
}

# SOP recommended_action → ProposedActionType mapping (SOP correlation)
_SOP_ACTION_MAP: dict[str, ProposedActionType] = {
    "SESSION_RESET":    ProposedActionType.RESET_SESSION,
    "OTP_RESEND":       ProposedActionType.RESEND_OTP,
    "PORTAL_REFRESH":   ProposedActionType.REFRESH_PORTAL,
    "CALLBACK_RETRY":   ProposedActionType.RETRY_CALLBACK,
    "AUTO_ADVANCE":     ProposedActionType.ASK_USER_RETRY,
    "MANUAL_REVIEW":    ProposedActionType.MANUAL_REVIEW,
    "ESCALATE":         ProposedActionType.ESCALATE_L2,
    "RETRY":            ProposedActionType.RETRY_DOCUMENT_CAPTURE,
}


class ActionProposalEngine:
    """
    Deterministic action proposal engine.

    Converts root cause analysis + knowledge results into an ordered
    ActionProposalBundle. Uses rule table + SOP correlation.

    No LLM. No external I/O. Never raises.
    """

    def __init__(self, risk_engine: RiskAssessmentEngine | None = None) -> None:
        self._risk = risk_engine or RiskAssessmentEngine()

    def propose(
        self,
        topic:                    str,
        root_cause_category:      str,
        investigation_confidence: float,
        investigation_escalate:   bool,
        knowledge_result:         dict[str, Any] | None = None,
    ) -> ActionProposalBundle:
        """
        Generate an ActionProposalBundle.

        Never raises — returns a fallback bundle with ESCALATE_L2 + MANUAL_REVIEW
        on any internal error.
        """
        try:
            return self._propose(
                topic=topic,
                root_cause_category=root_cause_category,
                investigation_confidence=investigation_confidence,
                investigation_escalate=investigation_escalate,
                knowledge_result=knowledge_result,
            )
        except Exception:
            LOGGER.exception(
                "action_proposal_engine.propose failed topic=%s root_cause=%s",
                topic, root_cause_category,
            )
            return self._fallback_bundle(topic, root_cause_category)

    # ── Private ───────────────────────────────────────────────────────────────

    def _propose(
        self,
        topic:                    str,
        root_cause_category:      str,
        investigation_confidence: float,
        investigation_escalate:   bool,
        knowledge_result:         dict[str, Any] | None,
    ) -> ActionProposalBundle:
        bundle_id = str(uuid.uuid4())
        created_at = _now_iso()

        # 1. Resolve base action list from rule table
        base_actions = list(_PROPOSAL_RULES.get(
            root_cause_category.upper(),
            _PROPOSAL_RULES["UNKNOWN"],
        ))

        # 2. SOP correlation — insert SOP-recommended action at priority 1
        sop_found = False
        sop_entry_id = None
        sop_action: ProposedActionType | None = None
        recommendation_source = "rule_based"

        if knowledge_result:
            sop_found = bool(knowledge_result.get("sop_match_found", False))
            if sop_found:
                rec = knowledge_result.get("recommendation") or {}
                sop_recommended_action = rec.get("recommended_action", "")
                sop_action = _SOP_ACTION_MAP.get(sop_recommended_action)
                sop_match = knowledge_result.get("sop_match") or {}
                sop_entry = sop_match.get("entry") or {}
                sop_entry_id = sop_entry.get("entry_id")
                recommendation_source = "sop_backed"
                if sop_action and sop_action not in base_actions:
                    base_actions.insert(0, sop_action)
                elif sop_action and sop_action in base_actions:
                    # Move SOP action to front
                    base_actions.remove(sop_action)
                    base_actions.insert(0, sop_action)

        # 3. If investigation says escalate, force ESCALATE_L2 as primary
        if investigation_escalate:
            if ProposedActionType.ESCALATE_L2 not in base_actions:
                base_actions.insert(0, ProposedActionType.ESCALATE_L2)
            else:
                base_actions.remove(ProposedActionType.ESCALATE_L2)
                base_actions.insert(0, ProposedActionType.ESCALATE_L2)
            recommendation_source = "investigation_escalate"

        # 4. Build reasoning
        reasoning = ActionReasoning(
            reasoning_id=str(uuid.uuid4()),
            root_cause_category=root_cause_category,
            investigation_confidence=investigation_confidence,
            sop_match_found=sop_found,
            sop_entry_id=sop_entry_id,
            recommendation_source=recommendation_source,
            explanation=self._build_explanation(
                root_cause_category, investigation_confidence,
                sop_found, investigation_escalate,
            ),
            created_at=created_at,
        )

        # 5. Build proposal items with risk assessments
        items: list[ActionProposalItem] = []
        for priority, action_type in enumerate(base_actions, start=1):
            risk = self._risk.assess(action_type)
            item = ActionProposalItem(
                item_id=str(uuid.uuid4()),
                action_type=action_type,
                priority=priority,
                rationale=_RATIONALE.get(action_type, f"Proposed based on root cause: {root_cause_category}"),
                risk_assessment=risk,
                sop_backed=(action_type == sop_action and sop_found),
                created_at=created_at,
            )
            items.append(item)

        # 6. Bundle metadata
        all_assessments = [it.risk_assessment for it in items]
        all_safe = all(a.risk_level == ProposalRiskLevel.SAFE for a in all_assessments)
        requires_approval = self._risk.any_requires_approval(all_assessments)

        proposals_tuple = tuple(items)
        top_proposal = items[0] if items else None

        return ActionProposalBundle(
            bundle_id=bundle_id,
            topic=topic,
            root_cause_category=root_cause_category,
            proposals=proposals_tuple,
            reasoning=reasoning,
            top_proposal=top_proposal,
            all_safe=all_safe,
            requires_approval=requires_approval,
            proposal_count=len(items),
            created_at=created_at,
        )

    def _build_explanation(
        self,
        root_cause_category:      str,
        investigation_confidence: float,
        sop_found:                bool,
        investigation_escalate:   bool,
    ) -> str:
        parts = [
            f"Root cause: {root_cause_category}",
            f"Investigation confidence: {investigation_confidence:.0%}",
        ]
        if sop_found:
            parts.append("SOP match found — proposal incorporates SOP recommendation")
        if investigation_escalate:
            parts.append("Investigation flagged for escalation — ESCALATE_L2 promoted to primary")
        if investigation_confidence < 0.35 and not sop_found:
            parts.append("Low confidence and no SOP — defaulting to manual review path")
        return "; ".join(parts)

    def _fallback_bundle(self, topic: str, root_cause_category: str) -> ActionProposalBundle:
        """Emergency fallback — always ESCALATE_L2."""
        created_at = _now_iso()
        risk = self._risk.assess(ProposedActionType.ESCALATE_L2)
        item = ActionProposalItem(
            item_id=str(uuid.uuid4()),
            action_type=ProposedActionType.ESCALATE_L2,
            priority=1,
            rationale="Fallback: internal error in proposal engine — escalating for safety",
            risk_assessment=risk,
            sop_backed=False,
            created_at=created_at,
        )
        reasoning = ActionReasoning(
            reasoning_id=str(uuid.uuid4()),
            root_cause_category=root_cause_category or "UNKNOWN",
            investigation_confidence=0.0,
            sop_match_found=False,
            sop_entry_id=None,
            recommendation_source="fallback",
            explanation="Internal error during proposal generation; escalating for safety",
            created_at=created_at,
        )
        return ActionProposalBundle(
            bundle_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category=root_cause_category or "UNKNOWN",
            proposals=(item,),
            reasoning=reasoning,
            top_proposal=item,
            all_safe=False,
            requires_approval=True,
            proposal_count=1,
            created_at=created_at,
        )
