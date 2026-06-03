"""
case_engine/escalation.py

Deterministic escalation engine.

Rules:
- No LLM decisions. All logic is pure Python deterministic rules.
- Evaluates case state + RAG result → EscalationDecision.
- Ten trigger types from 07_GOVERNANCE_POLICY_AND_HANDOFF.md + extras from prompt.
- Priority assignment is static (defined in TRIGGER_PRIORITY map).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from case_engine.models import Case


class EscalationTrigger(str, Enum):
    """Exhaustive list of escalation triggers (Level 1 + Level 2)."""
    # Classifier gates
    UNKNOWN_TOPIC          = "unknown_topic"
    BELOW_THRESHOLD        = "below_threshold"

    # Retrieval gates
    NO_MATCH               = "no_match"
    WEAK_MATCH             = "weak_match"

    # Generation gates
    LOW_GENERATION_CONF    = "low_generation_confidence"
    BRANCH_INCOMPLETE      = "branch_completeness_failure"

    # Workflow gates
    CONSECUTIVE_FAILURES   = "consecutive_workflow_failures"
    SLOT_FILL_TIMEOUT      = "slot_fill_timeout"
    WORKFLOW_FAILURE       = "workflow_failure"

    # Customer intent
    CUSTOMER_REQUESTED     = "customer_requested_escalation"

    # Security / compliance
    SECURITY_SIGNAL        = "fraud_or_security_signal"
    SECURITY_FREEZE_ACTIVE = "security_freeze_active"
    COMPLIANCE_FLAG        = "compliance_flag"

    # Action gateway
    IRREVERSIBLE_ACTION    = "irreversible_action_proposed"

    # PII / session
    PII_DETECTED           = "pii_detected_in_output"
    SESSION_MISMATCH       = "session_ownership_mismatch"

    # Manual
    MANUAL_REVIEW_REQUIRED = "manual_review_required"


class EscalationPriority(str, Enum):
    LOW    = "low"
    MEDIUM = "medium"
    HIGH   = "high"
    URGENT = "urgent"


# Static priority per trigger (compile-time, not LLM-determined)
TRIGGER_PRIORITY: dict[EscalationTrigger, EscalationPriority] = {
    EscalationTrigger.UNKNOWN_TOPIC:          EscalationPriority.MEDIUM,
    EscalationTrigger.BELOW_THRESHOLD:        EscalationPriority.MEDIUM,
    EscalationTrigger.NO_MATCH:               EscalationPriority.MEDIUM,
    EscalationTrigger.WEAK_MATCH:             EscalationPriority.LOW,
    EscalationTrigger.LOW_GENERATION_CONF:    EscalationPriority.MEDIUM,
    EscalationTrigger.BRANCH_INCOMPLETE:      EscalationPriority.MEDIUM,
    EscalationTrigger.CONSECUTIVE_FAILURES:   EscalationPriority.HIGH,
    EscalationTrigger.SLOT_FILL_TIMEOUT:      EscalationPriority.MEDIUM,
    EscalationTrigger.WORKFLOW_FAILURE:       EscalationPriority.HIGH,
    EscalationTrigger.CUSTOMER_REQUESTED:     EscalationPriority.HIGH,
    EscalationTrigger.SECURITY_SIGNAL:        EscalationPriority.URGENT,
    EscalationTrigger.SECURITY_FREEZE_ACTIVE: EscalationPriority.URGENT,
    EscalationTrigger.COMPLIANCE_FLAG:        EscalationPriority.URGENT,
    EscalationTrigger.IRREVERSIBLE_ACTION:    EscalationPriority.HIGH,
    EscalationTrigger.PII_DETECTED:           EscalationPriority.URGENT,
    EscalationTrigger.SESSION_MISMATCH:       EscalationPriority.URGENT,
    EscalationTrigger.MANUAL_REVIEW_REQUIRED: EscalationPriority.MEDIUM,
}

# Customer text patterns that signal manual escalation intent
_CUSTOMER_ESCALATION_PATTERNS = re.compile(
    r"\b(speak to an agent|talk to.+human|escalate|complaint|rbi|consumer forum|banking ombudsman|lodge.+complaint|raise.+grievance)\b",
    re.IGNORECASE,
)

# Security flag values from VKYC service
_SECURITY_FLAGS = frozenset({"DEEPFAKE_DETECTED", "SPOOFING_ATTEMPT", "FOREIGN_IP_CONNECTION", "MULTI_DEVICE_SAME_SESSION"})


@dataclass
class EscalationDecision:
    should_escalate:  bool
    trigger:          EscalationTrigger | None = None
    priority:         EscalationPriority = EscalationPriority.MEDIUM
    reason:           str = ""
    transfer_context: dict[str, Any] | None = None


class EscalationEngine:
    """
    Evaluates whether a case should be escalated to a human.

    All decisions are deterministic. No LLM calls.
    Evaluation order matches the priority defined in governance docs.
    """

    def evaluate(
        self,
        case: Case,
        *,
        # Classification inputs
        classification_confidence: float | None = None,
        topic_known: bool = True,
        # Retrieval inputs
        match_type: str | None = None,       # "exact_match" | "related_match" | "weak_match" | "no_match"
        generation_confidence: str | None = None,  # "high" | "medium" | "low"
        requires_human: bool = False,
        # Ticket text (for customer intent check)
        ticket_text: str = "",
        # Security / compliance inputs
        security_freeze_active: bool = False,
        security_flags: list[str] | None = None,
        hard_lock_active: bool = False,
        # Workflow inputs
        consecutive_failures: int = 0,
        slot_fill_timed_out: bool = False,
        workflow_error: bool = False,
        # Action gateway inputs
        irreversible_action_proposed: bool = False,
        session_ownership_mismatch: bool = False,
        # PII
        pii_detected_in_output: bool = False,
    ) -> EscalationDecision:
        """
        Evaluate all escalation triggers in priority order.

        Returns the first matching trigger (highest priority first).
        """
        # ── URGENT: Security and compliance (check first, no exceptions) ─────
        if security_flags:
            active = [f for f in security_flags if f in _SECURITY_FLAGS]
            if active:
                return self._decide(
                    EscalationTrigger.SECURITY_SIGNAL,
                    reason=f"Security flag(s) detected: {', '.join(active)}",
                    transfer_ctx={"security_flags": active, "case_id": case.case_id},
                )

        if security_freeze_active:
            return self._decide(
                EscalationTrigger.SECURITY_FREEZE_ACTIVE,
                reason="Account has a Security Freeze active. No automated resolution permitted.",
            )

        if hard_lock_active:
            return self._decide(
                EscalationTrigger.COMPLIANCE_FLAG,
                reason="Account is in Hard Lock state. Resolution requires branch visit per RBI guidelines.",
            )

        if pii_detected_in_output:
            return self._decide(
                EscalationTrigger.PII_DETECTED,
                reason="Unmasked PII detected in generated output. Response suppressed.",
            )

        if session_ownership_mismatch:
            return self._decide(
                EscalationTrigger.SESSION_MISMATCH,
                reason="Session ID in action proposal does not match case session.",
            )

        if irreversible_action_proposed:
            return self._decide(
                EscalationTrigger.IRREVERSIBLE_ACTION,
                reason="Workflow proposed an IRREVERSIBLE action. Human sign-off required.",
            )

        # ── HIGH: Classifier and retrieval gates ───────────────────────────────
        if not topic_known:
            return self._decide(
                EscalationTrigger.UNKNOWN_TOPIC,
                reason="Ticket topic is not in the known classification registry.",
            )

        if classification_confidence is not None and classification_confidence < 0.85:
            return self._decide(
                EscalationTrigger.BELOW_THRESHOLD,
                reason=f"Classifier confidence {classification_confidence:.3f} is below 0.85 threshold.",
            )

        if match_type == "no_match":
            return self._decide(
                EscalationTrigger.NO_MATCH,
                reason="Phase 1 RAG returned no_match: no relevant SOP content found.",
            )

        if requires_human:
            return self._decide(
                EscalationTrigger.LOW_GENERATION_CONF,
                reason="RAG governance engine flagged requires_human=True.",
            )

        # ── HIGH: Workflow execution failures ──────────────────────────────────
        if consecutive_failures >= 3:
            return self._decide(
                EscalationTrigger.CONSECUTIVE_FAILURES,
                reason=f"Three consecutive workflow step failures (count={consecutive_failures}).",
            )

        if slot_fill_timed_out:
            return self._decide(
                EscalationTrigger.SLOT_FILL_TIMEOUT,
                reason="Slot fill TTL expired (30 minutes). Customer did not respond.",
            )

        if workflow_error:
            return self._decide(
                EscalationTrigger.WORKFLOW_FAILURE,
                reason="Unhandled workflow execution error.",
            )

        # ── MEDIUM: Customer intent ────────────────────────────────────────────
        if ticket_text and _CUSTOMER_ESCALATION_PATTERNS.search(ticket_text):
            return self._decide(
                EscalationTrigger.CUSTOMER_REQUESTED,
                reason="Customer text contains explicit escalation intent.",
            )

        # ── LOW: Weak match (not escalation, but flag for monitoring) ──────────
        # weak_match alone does not escalate — it triggers BranchCompletenessChecker
        # in full Level 2. At Level 1 we let it through to the human note.

        return EscalationDecision(should_escalate=False)

    def _decide(
        self,
        trigger: EscalationTrigger,
        *,
        reason: str,
        transfer_ctx: dict[str, Any] | None = None,
    ) -> EscalationDecision:
        return EscalationDecision(
            should_escalate=True,
            trigger=trigger,
            priority=TRIGGER_PRIORITY[trigger],
            reason=reason,
            transfer_context=transfer_ctx,
        )
