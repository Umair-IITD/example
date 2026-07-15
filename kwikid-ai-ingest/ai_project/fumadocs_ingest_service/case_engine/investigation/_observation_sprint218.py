"""
case_engine/investigation/_observation_sprint218.py

Sprint 2.18: ObservationGenerator — produces L1-style Freshdesk internal notes.

Relocated from observation.py to avoid shadowing by the Sprint 2.44
case_engine.investigation.observation package. Import from this module
for Sprint 2.18 compatibility; import from
case_engine.investigation.observation for the Sprint 2.44 generator.

Per blueprint Section 14, the generated note format is:
  Issue Summary
  Observed Evidence
  Root Cause
  Recommended Action
  Escalation Required

Design invariants:
  - No LLM calls — all content is derived from typed evidence and RootCauseAnalysis
  - Never raises — failures produce a safe fallback note
  - Output is plain-text structured for Freshdesk internal notes
  - Deterministic — same inputs produce the same observation text

Public API:
  generator   = ObservationGenerator()
  observation = generator.generate(bundle, root_cause)
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)

LOGGER = logging.getLogger(__name__)

# ── Human-readable labels ──────────────────────────────────────────────────────

_CATEGORY_LABELS: dict[RootCauseCategory, str] = {
    RootCauseCategory.NETWORK_FAILURE:      "Network / Infrastructure Failure",
    RootCauseCategory.TIMEOUT:              "Request Timeout",
    RootCauseCategory.QUOTA_EXCEEDED:       "Rate Limit / Quota Exceeded",
    RootCauseCategory.EXPIRED_SESSION:      "Expired Session",
    RootCauseCategory.REPEATED_FAILURE:     "Repeated Failure Pattern",
    RootCauseCategory.LIVENESS_FAILURE:     "Liveness Check Failure",
    RootCauseCategory.DOCUMENT_FAILURE:     "Document / OCR Failure",
    RootCauseCategory.VALIDATION_FAILURE:   "Validation Error",
    RootCauseCategory.KYC_REJECTED:         "KYC Rejected",
    RootCauseCategory.SMS_DELIVERY_FAILURE: "SMS Delivery Failure",
    RootCauseCategory.CALLBACK_FAILURE:     "API Callback Failure",
    RootCauseCategory.ONBOARDING_BLOCKED:   "Onboarding Blocked",
    RootCauseCategory.PORTAL_UNAVAILABLE:   "Agent Portal Unavailable",
    RootCauseCategory.UNKNOWN:              "Unknown / Indeterminate",
}

_ACTION_LABELS: dict[RecommendedAction, str] = {
    RecommendedAction.SESSION_RESET:  "Reset the VKYC session and request user to retry",
    RecommendedAction.OTP_RESEND:     "Resend the OTP to the user's registered number",
    RecommendedAction.PORTAL_REFRESH: "Refresh agent portal and attempt login again",
    RecommendedAction.CALLBACK_RETRY: "Retry the API callback delivery",
    RecommendedAction.AUTO_ADVANCE:   "Auto-advance onboarding to the next stage",
    RecommendedAction.MANUAL_REVIEW:  "Refer to L2 for manual review",
    RecommendedAction.ESCALATE:       "Escalate to L2 / Engineering immediately",
    RecommendedAction.RETRY:          "Retry the failed operation",
}

_TOPIC_SUMMARIES: dict[str, str] = {
    "VKYC_Session_Failure":  "User reported a Video KYC session failure.",
    "OTP_Delivery_Failure":  "User did not receive the One-Time Password (OTP).",
    "Document_OCR_Failure":  "Document OCR processing failed during onboarding.",
    "Agent_Portal_Issue":    "Agent reported an issue with the support portal.",
    "API_Callback_Failure":  "API callback to the partner system failed to deliver.",
}


class ObservationGenerator:
    """
    Generates a structured Freshdesk internal note from investigation results.

    Stateless — safe to share across requests.
    """

    def generate(
        self,
        bundle: EvidenceBundle,
        root_cause: RootCauseAnalysis,
    ) -> str:
        """
        Build and return the L1 observation note text.

        Args:
            bundle:     EvidenceBundle produced by EvidenceCollector.
            root_cause: RootCauseAnalysis produced by RootCauseEngine.

        Returns:
            Plain-text observation suitable for a Freshdesk internal note.
            Never raises.
        """
        try:
            return self._generate(bundle, root_cause)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception(
                "observation_generator.error bundle_id=%s", bundle.bundle_id
            )
            return (
                f"[OBSERVATION ERROR — MANUAL REVIEW REQUIRED]\n\n"
                f"Case ID:  {bundle.case_id}\n"
                f"Topic:    {bundle.topic}\n"
                f"Error:    {exc}\n\n"
                "Automated observation could not be generated. "
                "Please investigate manually."
            )

    # ── Private ────────────────────────────────────────────────────────────────

    def _generate(
        self,
        bundle: EvidenceBundle,
        root_cause: RootCauseAnalysis,
    ) -> str:
        sections: list[str] = []

        sections.append(self._issue_summary(bundle))
        sections.append(self._observed_evidence(bundle))
        sections.append(self._root_cause_section(root_cause))
        sections.append(self._recommended_action_section(root_cause))
        sections.append(self._escalation_section(root_cause))
        sections.append(self._metadata_footer(bundle, root_cause))

        observation = "\n\n".join(s for s in sections if s)
        LOGGER.info(
            "observation_generator.generated case_id=%s topic=%s chars=%d",
            bundle.case_id, bundle.topic, len(observation),
        )
        return observation

    def _issue_summary(self, bundle: EvidenceBundle) -> str:
        summary = _TOPIC_SUMMARIES.get(bundle.topic, f"Support ticket regarding: {bundle.topic}.")
        return (
            "=== ISSUE SUMMARY ===\n"
            f"{summary}\n"
            f"Case ID: {bundle.case_id}  |  Topic: {bundle.topic}"
        )

    def _observed_evidence(self, bundle: EvidenceBundle) -> str:
        lines: list[str] = ["=== OBSERVED EVIDENCE ==="]

        if not bundle.items:
            lines.append("No evidence was collected.")
            return "\n".join(lines)

        for ev in bundle.items:
            status = "OK" if ev.success else "FAILED"
            lines.append(f"\n[{status}] {ev.tool_name}")

            if ev.success and ev.payload:
                for key, val in _relevant_fields(ev.source, ev.payload).items():
                    lines.append(f"  {key}: {val}")
            elif not ev.success:
                code = ev.error_code or "UNKNOWN_ERROR"
                msg  = ev.error_message or "No details available."
                lines.append(f"  Error: {code} — {msg}")

        successful = len(bundle.successful_items)
        total      = len(bundle.items)
        lines.append(f"\nEvidence collected: {successful}/{total} tools succeeded.")
        return "\n".join(lines)

    def _root_cause_section(self, root_cause: RootCauseAnalysis) -> str:
        category_label = _CATEGORY_LABELS.get(root_cause.category, root_cause.category.value)
        confidence_pct = f"{root_cause.confidence * 100:.0f}%"
        return (
            "=== ROOT CAUSE ===\n"
            f"Category:   {category_label}\n"
            f"Confidence: {confidence_pct}\n"
            f"Finding:    {root_cause.explanation}"
        )

    def _recommended_action_section(self, root_cause: RootCauseAnalysis) -> str:
        action_label = _ACTION_LABELS.get(
            root_cause.recommended_action,
            root_cause.recommended_action.value,
        )
        return (
            "=== RECOMMENDED ACTION ===\n"
            f"{action_label}"
        )

    def _escalation_section(self, root_cause: RootCauseAnalysis) -> str:
        if root_cause.escalate:
            return (
                "=== ESCALATION REQUIRED ===\n"
                "YES — This case requires L2 / Engineering review.\n"
                f"Reason: {root_cause.explanation}"
            )
        return (
            "=== ESCALATION REQUIRED ===\n"
            "NO — Automated resolution may proceed."
        )

    def _metadata_footer(
        self,
        bundle: EvidenceBundle,
        root_cause: RootCauseAnalysis,
    ) -> str:
        return (
            "---\n"
            f"Investigation Bundle: {bundle.bundle_id}\n"
            f"Root Cause Analysis:  {root_cause.analysis_id}\n"
            f"Collected At:         {bundle.collected_at}\n"
            f"Analysed At:          {root_cause.analysed_at}\n"
            "[Generated by KwikID AI Support Agent — L1 Investigation Layer]"
        )


# ── Evidence field extraction ──────────────────────────────────────────────────

def _relevant_fields(source: EvidenceSource, payload: dict[str, Any]) -> dict[str, Any]:
    """
    Return a curated subset of payload fields for a given evidence source.

    Shows the fields that are most meaningful for an L1 observation note.
    Falls back to the full payload (capped at 10 entries) for unknown sources.
    """
    if source == EvidenceSource.GET_USER_DETAILS:
        keys = ["phone_number", "kyc_status", "account_state", "risk_tier", "name"]
    elif source == EvidenceSource.GET_SESSION_DETAILS:
        keys = ["session_id", "session_status", "failure_code", "attempt_count", "reset_eligible"]
    elif source == EvidenceSource.GET_FAILURE_REASON:
        keys = ["failure_category", "failure_code", "is_transient", "recommended_action", "http_status"]
    elif source == EvidenceSource.GET_CASE_HISTORY:
        keys = ["case_count", "repeat_pattern", "escalation_rate", "last_resolution"]
    elif source == EvidenceSource.GET_ONBOARDING_STATUS:
        keys = ["stage", "completion_percentage", "blocking_step", "is_blocked"]
    else:
        # Unknown source — show up to 10 fields
        items = list(payload.items())[:10]
        return {k: v for k, v in items}

    return {k: payload[k] for k in keys if k in payload}
