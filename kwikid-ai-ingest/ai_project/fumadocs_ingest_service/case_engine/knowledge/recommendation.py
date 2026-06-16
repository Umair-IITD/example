"""
case_engine/knowledge/recommendation.py

Sprint 2.20: Resolution Recommendation Engine.

Per blueprint Section 29, Principle 3: Reasoning before execution.
Per blueprint Section 31 (Knowledge Retrieval Flow):
  Relevant SOP → Recommended Action

ResolutionRecommendationEngine synthesises:
  - InvestigationResult (root cause, recommended action, confidence)
  - SOPMatch (knowledge base evidence, resolution steps)

Into a ResolutionRecommendation that the PROPOSE_ACTION step can consume.

Design:
  - Never raises — always produces a recommendation (may be escalation)
  - Deterministic — no LLM
  - Confidence derivation:
      if SOP match found: max(inv_confidence, sop_match.relevance_score) * 0.9 + 0.1
      if no SOP match:    inv_confidence * 0.7  (reduced confidence without SOP backing)
  - Escalation:
      True if investigation already escalated (root_cause.escalate=True)
      True if confidence < _ESCALATION_THRESHOLD and no SOP match
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.knowledge.models import (
    ResolutionRecommendation,
    SOPMatch,
)

LOGGER = logging.getLogger(__name__)

_ESCALATION_CONFIDENCE_THRESHOLD = 0.35


class ResolutionRecommendationEngine:
    """
    Synthesises a ResolutionRecommendation from investigation + SOP evidence.

    Usage:
        engine = ResolutionRecommendationEngine()
        recommendation = engine.recommend(
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            recommended_action="SESSION_RESET",
            investigation_confidence=0.90,
            investigation_escalate=False,
            sop_match=sop_match,  # or None
        )
    """

    def recommend(
        self,
        topic: str,
        root_cause_category: str,
        recommended_action: str,
        investigation_confidence: float,
        investigation_escalate: bool,
        sop_match: SOPMatch | None,
    ) -> ResolutionRecommendation:
        """
        Produce a ResolutionRecommendation. Never raises.

        Args:
            topic:                   Ticket topic (e.g. "VKYC_Session_Failure")
            root_cause_category:     RootCauseCategory.value string
            recommended_action:      RecommendedAction.value string
            investigation_confidence: 0.0–1.0 confidence from RootCauseAnalysis
            investigation_escalate:  True if RootCauseAnalysis.escalate=True
            sop_match:               Best SOPMatch from knowledge search, or None
        """
        try:
            return self._recommend(
                topic, root_cause_category, recommended_action,
                investigation_confidence, investigation_escalate, sop_match,
            )
        except Exception:
            LOGGER.exception(
                "recommendation_engine.error topic=%s root_cause=%s",
                topic, root_cause_category,
            )
            return self._fallback_recommendation(topic, root_cause_category, recommended_action)

    # ── Private ───────────────────────────────────────────────────────────────

    def _recommend(
        self,
        topic: str,
        root_cause_category: str,
        recommended_action: str,
        investigation_confidence: float,
        investigation_escalate: bool,
        sop_match: SOPMatch | None,
    ) -> ResolutionRecommendation:
        if sop_match is not None:
            # SOP found: blend investigation confidence with SOP relevance
            combined = max(investigation_confidence, sop_match.relevance_score)
            confidence = combined * 0.9 + 0.1  # min 10%, scaled by evidence quality
            confidence = round(min(confidence, 1.0), 4)
            source_ids = (sop_match.entry.entry_id,)
            sop_steps  = sop_match.entry.resolution_steps or _DEFAULT_STEPS.get(
                recommended_action, ()
            )
            explanation = (
                f"Root cause '{root_cause_category}' matched SOP entry "
                f"'{sop_match.entry.title}' (score={sop_match.relevance_score:.2f}). "
                f"Recommended action: {recommended_action}."
            )
        else:
            # No SOP match: rely purely on investigation result
            confidence = round(investigation_confidence * 0.7, 4)
            source_ids = ()
            sop_steps  = _DEFAULT_STEPS.get(recommended_action, ())
            explanation = (
                f"Root cause '{root_cause_category}' identified with confidence "
                f"{investigation_confidence:.2f}. No matching SOP found. "
                f"Recommended action: {recommended_action} (investigation-driven)."
            )

        # Escalation: inherit from investigation OR low confidence without SOP
        escalation_required = investigation_escalate or (
            sop_match is None and confidence < _ESCALATION_CONFIDENCE_THRESHOLD
        )

        LOGGER.info(
            "recommendation_engine.complete topic=%s action=%s confidence=%.2f"
            " escalate=%s sop_match=%s",
            topic, recommended_action, confidence, escalation_required, sop_match is not None,
        )

        return ResolutionRecommendation(
            recommendation_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category=root_cause_category,
            recommended_action=recommended_action,
            confidence=confidence,
            explanation=explanation,
            sop_steps=sop_steps,
            escalation_required=escalation_required,
            source_entry_ids=source_ids,
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )

    def _fallback_recommendation(
        self,
        topic: str,
        root_cause_category: str,
        recommended_action: str,
    ) -> ResolutionRecommendation:
        """Safe fallback when the engine itself errors."""
        return ResolutionRecommendation(
            recommendation_id=str(uuid.uuid4()),
            topic=topic,
            root_cause_category=root_cause_category,
            recommended_action="ESCALATE",
            confidence=0.0,
            explanation="Recommendation engine error — escalating for manual review.",
            sop_steps=(),
            escalation_required=True,
            source_entry_ids=(),
            created_at=datetime.now(tz=timezone.utc).isoformat(),
        )


# ── Default SOP steps by action (fallback when no SOP match) ─────────────────
# These provide basic procedural guidance from the blueprint
# when no knowledge base entry is available.

_DEFAULT_STEPS: dict[str, tuple[str, ...]] = {
    "SESSION_RESET": (
        "1. Navigate to Admin Portal.",
        "2. Search for the session by Session ID.",
        "3. Verify the session status is in a failed or expired state.",
        "4. Click 'Reset Session' and confirm.",
        "5. Notify the user to retry their VKYC session.",
    ),
    "OTP_RESEND": (
        "1. Verify the phone number on record matches the user request.",
        "2. Check SMS provider status for delivery failures.",
        "3. Trigger OTP resend from Admin Portal.",
        "4. Confirm delivery status.",
        "5. Notify user to check their SMS inbox.",
    ),
    "PORTAL_REFRESH": (
        "1. Check Agent Portal status page for known outages.",
        "2. Clear browser cache and cookies.",
        "3. Attempt portal reload.",
        "4. If issue persists, escalate to L2.",
    ),
    "CALLBACK_RETRY": (
        "1. Verify the callback URL configuration.",
        "2. Check API logs for the failed callback event.",
        "3. Trigger manual callback retry from Admin Portal.",
        "4. Monitor for successful acknowledgement.",
    ),
    "RETRY": (
        "1. Review the failure logs.",
        "2. Retry the operation from Admin Portal.",
        "3. Monitor for success.",
    ),
    "AUTO_ADVANCE": (
        "1. Review the stuck application state.",
        "2. Apply auto-advance from Admin Portal.",
        "3. Verify the application progressed to the next step.",
    ),
    "MANUAL_REVIEW": (
        "1. Assign to human support agent.",
        "2. Attach investigation report.",
        "3. Escalate with priority.",
    ),
    "ESCALATE": (
        "1. Compile investigation findings.",
        "2. Create L2 escalation ticket.",
        "3. Attach session ID, logs, and root cause summary.",
    ),
}
