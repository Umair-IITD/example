"""
case_engine/reasoning/engine.py

Sprint 2.24: InvestigationReasoningEngine — deterministic investigation reasoning.

This engine sits between the Investigation Layer (ROOTCAUSE) and the Action
Proposal Layer (ACTIONPROPOSAL), implementing the flow_diagram.mermaid path:

    ROOTCAUSE --> HYBRIDRAG --> REASONING --> GUARDRAILS --> ACTIONPROPOSAL

Responsibilities:
  - Accept investigation_result dict (contains RootCauseAnalysis)
  - Accept knowledge_result dict (contains SOP matches from HYBRIDRAG)
  - Apply explicit rule chains to produce a ReasoningResult
  - Generate a full ReasoningTrace for audit/compliance

Design constraints:
  - NO LLM. NO prompts. NO inference. NO AI generation.
  - Rule-based only. Every rule is explicit and traceable.
  - Never raises — all exceptions produce a safe UNCERTAIN result.
  - Deterministic: same input → same output every time.
  - Fail-closed: unknown/uncertain situations → ESCALATE.

Rule priority:
  1. Force-escalate if root_cause.escalate=True
  2. Force-escalate if confidence < MIN_CONFIDENCE_THRESHOLD (0.4)
  3. Apply SOP override if SOP match is found and sop_override_action known
  4. Apply root_cause_category rule table
  5. Default → ESCALATE (fail-closed)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.reasoning.models import (
    ReasoningBundle,
    ReasoningOutcome,
    ReasoningRecommendation,
    ReasoningResult,
    ReasoningTrace,
)

LOGGER = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

MIN_CONFIDENCE_THRESHOLD: float = 0.4

# Root cause category → (action_type, base_confidence, rationale)
_CATEGORY_RULES: dict[str, tuple[str, float, str]] = {
    "NETWORK_FAILURE":      ("RESET_SESSION",          0.90, "Network failures resolve via session reset per SOP"),
    "TIMEOUT":              ("RESET_SESSION",          0.85, "Timeout errors require session reset with retry"),
    "EXPIRED_SESSION":      ("RESET_SESSION",          0.92, "Expired sessions must be reset before retrying"),
    "REPEATED_FAILURE":     ("RESET_SESSION",          0.80, "Repeated failures indicate session state corruption"),
    "QUOTA_EXCEEDED":       ("WAIT_AND_RETRY",         0.88, "Quota limits require waiting before next attempt"),
    "LIVENESS_FAILURE":     ("RETRY_DOCUMENT_CAPTURE", 0.85, "Liveness check failures need recapture attempt"),
    "DOCUMENT_FAILURE":     ("RETRY_DOCUMENT_CAPTURE", 0.85, "Document processing errors require recapture"),
    "VALIDATION_FAILURE":   ("RETRY_DOCUMENT_CAPTURE", 0.80, "Validation failures may resolve on fresh capture"),
    "KYC_REJECTED":         ("ESCALATE_L2",            0.95, "KYC rejection requires manual review — escalating"),
    "SMS_DELIVERY_FAILURE": ("RESEND_OTP",             0.92, "SMS delivery failures require OTP resend"),
    "CALLBACK_FAILURE":     ("RETRY_CALLBACK",         0.88, "Callback failures need scheduled retry per SOP"),
    "ONBOARDING_BLOCKED":   ("MANUAL_REVIEW",          0.90, "Onboarding blockage requires manual intervention"),
    "PORTAL_UNAVAILABLE":   ("CHECK_SERVER_STATUS",    0.85, "Portal unavailability requires server health check"),
    "UNKNOWN":              ("ESCALATE_L2",            0.70, "Unknown root cause requires human investigation"),
}

# SOP tag/keyword → action override (only applied when SOP match found)
_SOP_KEYWORD_OVERRIDES: dict[str, str] = {
    "retry":               "RESET_SESSION",
    "resend otp":          "RESEND_OTP",
    "otp resend":          "RESEND_OTP",
    "session reset":       "RESET_SESSION",
    "manual review":       "MANUAL_REVIEW",
    "escalate":            "ESCALATE_L2",
    "wait and retry":      "WAIT_AND_RETRY",
    "check server":        "CHECK_SERVER_STATUS",
    "retry callback":      "RETRY_CALLBACK",
    "retry ocr":           "RETRY_DOCUMENT_CAPTURE",
    "document recapture":  "RETRY_DOCUMENT_CAPTURE",
}

# Categories that always escalate regardless of SOP or confidence
_ALWAYS_ESCALATE: frozenset[str] = frozenset({"KYC_REJECTED"})

# Categories where SOP override is explicitly honored
_SOP_OVERRIDE_ELIGIBLE: frozenset[str] = frozenset({
    "NETWORK_FAILURE", "TIMEOUT", "EXPIRED_SESSION",
    "SMS_DELIVERY_FAILURE", "CALLBACK_FAILURE",
    "PORTAL_UNAVAILABLE", "LIVENESS_FAILURE", "DOCUMENT_FAILURE",
})


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class InvestigationReasoningEngine:
    """
    Deterministic rule engine for Investigation Reasoning.

    Maps (investigation_result, knowledge_result) → ReasoningResult.

    Stateless — create once, use across many cases.
    Never raises — all failures produce a safe UNCERTAIN/ESCALATE result.
    """

    def reason(
        self,
        investigation_result: dict[str, Any],
        knowledge_result:     dict[str, Any] | None = None,
    ) -> ReasoningResult:
        """
        Apply reasoning rules and return an immutable ReasoningResult.

        Never raises. On exception → UNCERTAIN outcome, escalate=True.
        """
        try:
            return self._reason(investigation_result, knowledge_result or {})
        except Exception as exc:
            LOGGER.exception(
                "investigation_reasoning_engine.reason failed: %s — returning UNCERTAIN", exc
            )
            return self._uncertain_result(
                topic="UNKNOWN",
                root_cause_category="UNKNOWN",
                root_cause_confidence=0.0,
                exception=str(exc),
            )

    # ── Private reasoning pipeline ────────────────────────────────────────────

    def _reason(
        self,
        investigation_result: dict[str, Any],
        knowledge_result:     dict[str, Any],
    ) -> ReasoningResult:
        decision_path: list[str] = []

        # Extract investigation context
        root_cause = investigation_result.get("root_cause") or {}
        category   = root_cause.get("category", "UNKNOWN")
        confidence = float(root_cause.get("confidence", 0.0))
        escalate   = bool(root_cause.get("escalate", False))
        evidence_ids = tuple(investigation_result.get("evidence_ids", []) or [])
        topic = investigation_result.get("topic", "")

        decision_path.append(f"root_cause.category={category}")
        decision_path.append(f"root_cause.confidence={confidence:.2f}")
        decision_path.append(f"root_cause.escalate={escalate}")

        # Extract knowledge context
        sop_match_found = bool(knowledge_result.get("sop_match_found", False))
        sop_match       = knowledge_result.get("sop_match") or {}
        sop_entry       = sop_match.get("entry") or {} if sop_match else {}
        sop_id          = sop_entry.get("entry_id", "") if sop_entry else ""
        sop_title       = (sop_entry.get("title", "") or "").lower() if sop_entry else ""
        knowledge_ids   = self._extract_knowledge_ids(knowledge_result)
        sop_ids         = tuple([sop_id]) if sop_id else ()

        decision_path.append(f"sop_match_found={sop_match_found}")
        if sop_id:
            decision_path.append(f"sop_id={sop_id}")

        # ── Rule 1: Force-escalate if investigation engine says to ────────────
        if escalate:
            decision_path.append("rule1:root_cause.escalate=True → ESCALATE")
            return self._build_result(
                topic=topic,
                category=category,
                confidence=confidence,
                outcome=ReasoningOutcome.ESCALATE,
                action_type="ESCALATE_L2",
                rec_confidence=0.95,
                rationale="Investigation determined escalation is required",
                rule_applied="rule1_investigation_escalate",
                should_escalate=True,
                escalate_reason="root_cause.escalate=True from investigation",
                evidence_ids=evidence_ids,
                sop_ids=sop_ids,
                knowledge_ids=knowledge_ids,
                decision_path=tuple(decision_path),
            )

        # ── Rule 2: Force-escalate if confidence is too low ──────────────────
        if confidence < MIN_CONFIDENCE_THRESHOLD:
            decision_path.append(
                f"rule2:confidence={confidence:.2f}<{MIN_CONFIDENCE_THRESHOLD} → UNCERTAIN"
            )
            return self._build_result(
                topic=topic,
                category=category,
                confidence=confidence,
                outcome=ReasoningOutcome.UNCERTAIN,
                action_type="ESCALATE_L2",
                rec_confidence=0.60,
                rationale=f"Root cause confidence {confidence:.2f} below threshold {MIN_CONFIDENCE_THRESHOLD}",
                rule_applied="rule2_low_confidence",
                should_escalate=True,
                escalate_reason=f"confidence={confidence:.2f} below threshold {MIN_CONFIDENCE_THRESHOLD}",
                evidence_ids=evidence_ids,
                sop_ids=sop_ids,
                knowledge_ids=knowledge_ids,
                decision_path=tuple(decision_path),
            )

        # ── Rule 3: Always-escalate categories ───────────────────────────────
        if category in _ALWAYS_ESCALATE:
            _, base_conf, rationale = _CATEGORY_RULES.get(
                category, ("ESCALATE_L2", 0.95, "Category requires escalation")
            )
            decision_path.append(f"rule3:category={category} in ALWAYS_ESCALATE → ESCALATE")
            return self._build_result(
                topic=topic,
                category=category,
                confidence=confidence,
                outcome=ReasoningOutcome.ESCALATE,
                action_type="ESCALATE_L2",
                rec_confidence=base_conf,
                rationale=rationale,
                rule_applied="rule3_always_escalate_category",
                should_escalate=True,
                escalate_reason=f"Category {category} always requires human review",
                evidence_ids=evidence_ids,
                sop_ids=sop_ids,
                knowledge_ids=knowledge_ids,
                decision_path=tuple(decision_path),
            )

        # ── Rule 4: SOP override (when SOP match found for eligible category) ─
        if sop_match_found and category in _SOP_OVERRIDE_ELIGIBLE and sop_title:
            override_action = self._find_sop_override(sop_title)
            if override_action:
                decision_path.append(
                    f"rule4:sop_override sop_title='{sop_title}' → {override_action}"
                )
                base_conf = _CATEGORY_RULES.get(category, ("", 0.85, ""))[1]
                return self._build_result(
                    topic=topic,
                    category=category,
                    confidence=confidence,
                    outcome=ReasoningOutcome.RECOMMEND_ACTION,
                    action_type=override_action,
                    rec_confidence=min(1.0, base_conf + 0.05),
                    rationale=f"SOP match '{sop_title}' overrides to {override_action}",
                    rule_applied="rule4_sop_override",
                    should_escalate=False,
                    escalate_reason=None,
                    evidence_ids=evidence_ids,
                    sop_ids=sop_ids,
                    knowledge_ids=knowledge_ids,
                    decision_path=tuple(decision_path),
                )

        # ── Rule 5: Category rule table ───────────────────────────────────────
        if category in _CATEGORY_RULES:
            action_type, base_conf, rationale = _CATEGORY_RULES[category]
            decision_path.append(f"rule5:category_rule category={category} → {action_type}")
            is_escalate = action_type in ("ESCALATE_L2", "MANUAL_REVIEW")
            return self._build_result(
                topic=topic,
                category=category,
                confidence=confidence,
                outcome=ReasoningOutcome.RECOMMEND_ACTION,
                action_type=action_type,
                rec_confidence=base_conf,
                rationale=rationale,
                rule_applied="rule5_category_rule_table",
                should_escalate=is_escalate,
                escalate_reason=f"Category {category} maps to {action_type}" if is_escalate else None,
                evidence_ids=evidence_ids,
                sop_ids=sop_ids,
                knowledge_ids=knowledge_ids,
                decision_path=tuple(decision_path),
            )

        # ── Rule 6: Default — fail-closed → ESCALATE ─────────────────────────
        decision_path.append(f"rule6:default_escalate category={category}")
        return self._build_result(
            topic=topic,
            category=category,
            confidence=confidence,
            outcome=ReasoningOutcome.ESCALATE,
            action_type="ESCALATE_L2",
            rec_confidence=0.70,
            rationale=f"No rule matched for category '{category}' — fail-closed escalation",
            rule_applied="rule6_default_escalate",
            should_escalate=True,
            escalate_reason=f"No reasoning rule for category: {category}",
            evidence_ids=evidence_ids,
            sop_ids=sop_ids,
            knowledge_ids=knowledge_ids,
            decision_path=tuple(decision_path),
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _find_sop_override(self, sop_title: str) -> str | None:
        """Return override action if SOP title contains a known keyword, else None."""
        lower = sop_title.lower()
        for keyword, action in _SOP_KEYWORD_OVERRIDES.items():
            if keyword in lower:
                return action
        return None

    def _extract_knowledge_ids(self, knowledge_result: dict[str, Any]) -> tuple[str, ...]:
        """Extract knowledge article IDs from knowledge_result dict."""
        try:
            ids: list[str] = []
            recs = knowledge_result.get("recommendations") or []
            if isinstance(recs, list):
                for r in recs:
                    if isinstance(r, dict):
                        entry_id = (r.get("entry") or {}).get("entry_id", "")
                        if entry_id:
                            ids.append(entry_id)
            sop = knowledge_result.get("sop_match") or {}
            if isinstance(sop, dict):
                entry_id = (sop.get("entry") or {}).get("entry_id", "")
                if entry_id and entry_id not in ids:
                    ids.append(entry_id)
            return tuple(ids)
        except Exception:
            return ()

    def _build_result(
        self,
        topic:           str,
        category:        str,
        confidence:      float,
        outcome:         ReasoningOutcome,
        action_type:     str,
        rec_confidence:  float,
        rationale:       str,
        rule_applied:    str,
        should_escalate: bool,
        escalate_reason: str | None,
        evidence_ids:    tuple[str, ...],
        sop_ids:         tuple[str, ...],
        knowledge_ids:   tuple[str, ...],
        decision_path:   tuple[str, ...],
    ) -> ReasoningResult:
        now = _now_iso()
        recommendation = ReasoningRecommendation(
            action_type=action_type,
            confidence=rec_confidence,
            rationale=rationale,
            sop_ids_used=sop_ids,
            knowledge_ids_used=knowledge_ids,
        )
        trace = ReasoningTrace(
            rule_applied=rule_applied,
            evidence_ids_used=evidence_ids,
            sop_ids_used=sop_ids,
            knowledge_ids_used=knowledge_ids,
            decision_path=decision_path,
            final_recommendation=action_type,
            created_at=now,
        )
        bundle = ReasoningBundle(
            bundle_id=_new_id(),
            topic=topic,
            root_cause_category=category,
            root_cause_confidence=confidence,
            outcome=outcome,
            recommendation=recommendation,
            trace=trace,
            should_escalate=should_escalate,
            escalate_reason=escalate_reason,
            created_at=now,
        )
        return ReasoningResult(
            result_id=_new_id(),
            bundle=bundle,
            created_at=now,
        )

    def _uncertain_result(
        self,
        topic:                 str,
        root_cause_category:   str,
        root_cause_confidence: float,
        exception:             str = "",
    ) -> ReasoningResult:
        now = _now_iso()
        rationale = f"Reasoning engine exception — fail-closed escalation. Error: {exception[:100]}"
        recommendation = ReasoningRecommendation(
            action_type="ESCALATE_L2",
            confidence=0.0,
            rationale=rationale,
            sop_ids_used=(),
            knowledge_ids_used=(),
        )
        trace = ReasoningTrace(
            rule_applied="exception_handler",
            evidence_ids_used=(),
            sop_ids_used=(),
            knowledge_ids_used=(),
            decision_path=("exception_during_reasoning",),
            final_recommendation="ESCALATE_L2",
            created_at=now,
        )
        bundle = ReasoningBundle(
            bundle_id=_new_id(),
            topic=topic,
            root_cause_category=root_cause_category,
            root_cause_confidence=root_cause_confidence,
            outcome=ReasoningOutcome.UNCERTAIN,
            recommendation=recommendation,
            trace=trace,
            should_escalate=True,
            escalate_reason=f"Reasoning exception: {exception[:200]}",
            created_at=now,
        )
        return ReasoningResult(result_id=_new_id(), bundle=bundle, created_at=now)


def build_reasoning_engine() -> InvestigationReasoningEngine:
    """Factory: return a default InvestigationReasoningEngine instance."""
    return InvestigationReasoningEngine()
