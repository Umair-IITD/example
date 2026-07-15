"""
case_engine/investigation/root_cause/rules.py

Sprint 2.43: Concrete rule classes for the Root Cause Engine.

11 rules covering every RootCauseCategory.  Evaluation order is set by
the `priority` attribute (ascending = first evaluated).

Design constraints:
  - Rules are stateless — no instance state mutated between calls.
  - evaluate() NEVER raises — all exceptions are caught and return
    RuleResult.unknown().
  - All payload access uses .get() with safe defaults.
  - No LLM, no network, no database, no providers.

Dependency direction:
  rules.py → root_cause/contracts.py (RuleEvaluationContext, RuleResult)
  rules.py → root_cause/models.py (RuleMatchStatus, ConfidenceAdjustment)
  rules.py → case_engine.investigation.models (RootCauseCategory,
             RecommendedAction, EvidenceSource, EvidenceType)
  rules.py → stdlib (logging, typing)
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.investigation.models import (
    EvidenceSource,
    EvidenceType,
    RecommendedAction,
    RootCauseCategory,
)
from case_engine.investigation.root_cause.contracts import (
    RuleEvaluationContext,
    RuleResult,
)
from case_engine.investigation.root_cause.models import (
    ConfidenceAdjustment,
    RuleMatchStatus,
)

LOGGER = logging.getLogger(__name__)

# ── Payload key constants ──────────────────────────────────────────────────────

_FAILURE_CATEGORY  = "failure_category"
_FAILURE_CODE      = "failure_code"
_IS_TRANSIENT      = "is_transient"
_SESSION_STATUS    = "session_status"
_ATTEMPT_COUNT     = "attempt_count"
_KYC_STATUS        = "kyc_status"
_ACCOUNT_STATE     = "account_state"
_ONBOARDING_STATUS = "onboarding_status"
_REPEAT_PATTERN    = "repeat_pattern"
_FAILURE_RATE      = "failure_rate"
_RESET_ELIGIBLE    = "reset_eligible"

# ── Private helpers ────────────────────────────────────────────────────────────

def _successful(bundle: Any) -> list[Any]:
    """Return only successful evidence items from a bundle."""
    return [e for e in getattr(bundle, "items", []) if getattr(e, "success", False)]


def _by_type(items: list[Any], evidence_type: EvidenceType) -> list[Any]:
    return [e for e in items if getattr(e, "evidence_type", None) == evidence_type]


def _by_source(items: list[Any], source: EvidenceSource) -> list[Any]:
    return [e for e in items if getattr(e, "source", None) == source]


def _payload(evidence: Any) -> dict[str, Any]:
    return getattr(evidence, "payload", {}) or {}


def _evidence_ids(items: list[Any]) -> tuple[str, ...]:
    return tuple(getattr(e, "evidence_id", "") for e in items)


def _category_match(log_items: list[Any], *categories: str) -> list[Any]:
    """Return log items whose failure_category is one of the given values."""
    cats = {c.upper() for c in categories}
    return [
        e for e in log_items
        if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in cats
    ]


# ── Rule 1: Network Failure ────────────────────────────────────────────────────

class NetworkFailureRule:
    """Detect network / connectivity failures from log evidence."""
    rule_id     = "network_failure"
    name        = "NetworkFailureRule"
    priority    = 1
    description = "Matches NETWORK_FAILURE or TIMEOUT from log evidence."

    _NETWORK_CATEGORIES = {"NETWORK", "NETWORK_FAILURE", "TIMEOUT", "CONNECTION_REFUSED", "DNS_FAILURE"}
    _NETWORK_CODES      = {"ERR_NETWORK", "ETIMEDOUT", "ECONNRESET", "ECONNREFUSED", "ENETUNREACH"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items     = _successful(bundle)
            log_items = _by_type(items, EvidenceType.LOG)
            if not log_items:
                return RuleResult.no_match(self.rule_id, "No log evidence available.")

            category_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._NETWORK_CATEGORIES
            ]
            code_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CODE, "")).upper() in self._NETWORK_CODES
            ]
            hits = list({id(e): e for e in category_hits + code_hits}.values())

            if hits:
                transient = any(_payload(e).get(_IS_TRANSIENT, False) for e in hits)
                adj: tuple[ConfidenceAdjustment, ...] = ()
                if transient:
                    adj = (ConfidenceAdjustment(
                        reason="Failure flagged as transient — network blip likely",
                        delta=+0.05,
                        component="network_failure_rule",
                    ),)
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.MATCH,
                    confidence=0.85,
                    evidence_ids_used=_evidence_ids(hits),
                    explanation=(
                        f"Network failure detected. "
                        f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}. "
                        f"Transient: {transient}."
                    ),
                    category_hint=RootCauseCategory.NETWORK_FAILURE,
                    confidence_adjustments=adj,
                )

            # Partial: any transient log failure might be network related
            transient_items = [e for e in log_items if _payload(e).get(_IS_TRANSIENT, False)]
            if transient_items:
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.PARTIAL_MATCH,
                    confidence=0.40,
                    evidence_ids_used=_evidence_ids(transient_items),
                    explanation="Transient failure detected — may indicate intermittent network issue.",
                    category_hint=RootCauseCategory.NETWORK_FAILURE,
                )

            return RuleResult.no_match(self.rule_id, "No network-related failure indicators found.")
        except Exception as exc:
            LOGGER.warning("NetworkFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 2: Session Expired ────────────────────────────────────────────────────

class SessionExpiredRule:
    """Detect expired or invalidated VKYC sessions from session evidence."""
    rule_id     = "session_expired"
    name        = "SessionExpiredRule"
    priority    = 2
    description = "Matches EXPIRED_SESSION from session or log evidence."

    _EXPIRED_STATUSES   = {"EXPIRED", "INVALIDATED", "TIMED_OUT"}
    _EXPIRED_CATEGORIES = {"SESSION_EXPIRED", "EXPIRED_SESSION", "SESSION_TIMEOUT"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items          = _successful(bundle)
            session_items  = _by_type(items, EvidenceType.SESSION)
            log_items      = _by_type(items, EvidenceType.LOG)

            session_hits = [
                e for e in session_items
                if str(_payload(e).get(_SESSION_STATUS, "")).upper() in self._EXPIRED_STATUSES
            ]
            log_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._EXPIRED_CATEGORIES
            ]

            if session_hits:
                all_hits = session_hits + log_hits
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.MATCH,
                    confidence=0.90,
                    evidence_ids_used=_evidence_ids(all_hits),
                    explanation=(
                        f"Session expired. "
                        f"session_status={_payload(session_hits[0]).get(_SESSION_STATUS, 'N/A')!r}."
                    ),
                    category_hint=RootCauseCategory.EXPIRED_SESSION,
                )

            if log_hits:
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.PARTIAL_MATCH,
                    confidence=0.65,
                    evidence_ids_used=_evidence_ids(log_hits),
                    explanation="Log evidence indicates session expiry; session evidence unavailable for confirmation.",
                    category_hint=RootCauseCategory.EXPIRED_SESSION,
                )

            return RuleResult.no_match(self.rule_id, "No session expiry indicators found.")
        except Exception as exc:
            LOGGER.warning("SessionExpiredRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 3: Liveness Failure ───────────────────────────────────────────────────

class LivenessFailureRule:
    """Detect liveness check failures from log or video evidence."""
    rule_id     = "liveness_failure"
    name        = "LivenessFailureRule"
    priority    = 3
    description = "Matches LIVENESS_FAILURE from log or video evidence."

    _LIVENESS_CATEGORIES = {"LIVENESS", "LIVENESS_FAILURE", "LIVENESS_CHECK_FAILED", "ANTI_SPOOF_FAILED"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items      = _successful(bundle)
            log_items  = _by_type(items, EvidenceType.LOG)
            vid_items  = _by_type(items, EvidenceType.VIDEO)

            log_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._LIVENESS_CATEGORIES
            ]
            # Video evidence with liveness_passed = False is also a signal
            vid_hits = [
                e for e in vid_items
                if _payload(e).get("liveness_passed") is False
                or str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._LIVENESS_CATEGORIES
            ]

            hits = log_hits + vid_hits
            if not hits:
                return RuleResult.no_match(self.rule_id, "No liveness failure indicators found.")

            confidence = 0.90 if (log_hits and vid_hits) else 0.80
            status     = RuleMatchStatus.MATCH if confidence >= 0.80 else RuleMatchStatus.PARTIAL_MATCH

            adj: tuple[ConfidenceAdjustment, ...] = ()
            if log_hits and vid_hits:
                adj = (ConfidenceAdjustment(
                    reason="Liveness failure corroborated by both log and video evidence",
                    delta=+0.05,
                    component="liveness_failure_rule",
                ),)

            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                confidence=confidence,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"Liveness check failed. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}. "
                    f"Corroborated by video: {bool(vid_hits)}."
                ),
                category_hint=RootCauseCategory.LIVENESS_FAILURE,
                confidence_adjustments=adj,
            )
        except Exception as exc:
            LOGGER.warning("LivenessFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 4: Document Failure ───────────────────────────────────────────────────

class DocumentFailureRule:
    """Detect document scan / OCR failures from log evidence."""
    rule_id     = "document_failure"
    name        = "DocumentFailureRule"
    priority    = 4
    description = "Matches DOCUMENT_FAILURE from log evidence."

    _DOC_CATEGORIES = {
        "DOCUMENT", "DOCUMENT_FAILURE", "DOC_SCAN_FAILED", "OCR_FAILED",
        "DOCUMENT_EXPIRED", "DOCUMENT_UNREADABLE", "DOCUMENT_MISMATCH",
    }

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items     = _successful(bundle)
            log_items = _by_type(items, EvidenceType.LOG)

            hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._DOC_CATEGORIES
            ]
            if not hits:
                return RuleResult.no_match(self.rule_id, "No document failure indicators found.")

            # Distinguish expired doc (partial) from unreadable/mismatch (full match)
            full_match_subs = {"OCR_FAILED", "DOCUMENT_UNREADABLE", "DOCUMENT_MISMATCH", "DOC_SCAN_FAILED"}
            strong = [
                e for e in hits
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in full_match_subs
            ]
            status     = RuleMatchStatus.MATCH if strong else RuleMatchStatus.PARTIAL_MATCH
            confidence = 0.85 if strong else 0.60

            return RuleResult(
                rule_id=self.rule_id,
                status=status,
                confidence=confidence,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"Document failure detected. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}."
                ),
                category_hint=RootCauseCategory.DOCUMENT_FAILURE,
            )
        except Exception as exc:
            LOGGER.warning("DocumentFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 5: KYC Rejected ──────────────────────────────────────────────────────

class KycRejectedRule:
    """Detect KYC rejection from user evidence."""
    rule_id     = "kyc_rejected"
    name        = "KycRejectedRule"
    priority    = 5
    description = "Matches KYC_REJECTED from user evidence kyc_status field."

    _REJECTED_STATUSES = {"REJECTED", "FAILED", "DENIED", "KYC_REJECTED"}
    _BLOCKED_STATES    = {"BLOCKED", "SUSPENDED", "FROZEN"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items      = _successful(bundle)
            user_items = _by_type(items, EvidenceType.USER)

            if not user_items:
                return RuleResult.no_match(self.rule_id, "No user evidence available.")

            kyc_hits = [
                e for e in user_items
                if str(_payload(e).get(_KYC_STATUS, "")).upper() in self._REJECTED_STATUSES
            ]
            account_hits = [
                e for e in user_items
                if str(_payload(e).get(_ACCOUNT_STATE, "")).upper() in self._BLOCKED_STATES
            ]

            if kyc_hits:
                all_hits = kyc_hits + account_hits
                adj: tuple[ConfidenceAdjustment, ...] = ()
                if account_hits:
                    adj = (ConfidenceAdjustment(
                        reason="KYC rejection accompanied by blocked account state",
                        delta=+0.05,
                        component="kyc_rejected_rule",
                    ),)
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.MATCH,
                    confidence=0.92,
                    evidence_ids_used=_evidence_ids(all_hits),
                    explanation=(
                        f"KYC rejected. "
                        f"kyc_status={_payload(kyc_hits[0]).get(_KYC_STATUS, 'N/A')!r}."
                    ),
                    category_hint=RootCauseCategory.KYC_REJECTED,
                    confidence_adjustments=adj,
                )

            if account_hits:
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.PARTIAL_MATCH,
                    confidence=0.55,
                    evidence_ids_used=_evidence_ids(account_hits),
                    explanation=(
                        f"Account blocked/suspended — possible KYC rejection. "
                        f"account_state={_payload(account_hits[0]).get(_ACCOUNT_STATE, 'N/A')!r}."
                    ),
                    category_hint=RootCauseCategory.KYC_REJECTED,
                )

            return RuleResult.no_match(self.rule_id, "No KYC rejection indicators found.")
        except Exception as exc:
            LOGGER.warning("KycRejectedRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 6: SMS Delivery Failure ───────────────────────────────────────────────

class SmsDeliveryFailureRule:
    """Detect OTP / SMS delivery failures from log evidence."""
    rule_id     = "sms_delivery_failure"
    name        = "SmsDeliveryFailureRule"
    priority    = 6
    description = "Matches SMS_DELIVERY_FAILURE from log evidence."

    _SMS_CATEGORIES = {
        "SMS_DELIVERY", "SMS_DELIVERY_FAILURE", "OTP_DELIVERY_FAILED",
        "SMS_NOT_DELIVERED", "OTP_FAILED",
    }

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items     = _successful(bundle)
            log_items = _by_type(items, EvidenceType.LOG)

            hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._SMS_CATEGORIES
            ]
            if not hits:
                return RuleResult.no_match(self.rule_id, "No SMS delivery failure indicators found.")

            transient = any(_payload(e).get(_IS_TRANSIENT, False) for e in hits)
            adj: tuple[ConfidenceAdjustment, ...] = ()
            if transient:
                adj = (ConfidenceAdjustment(
                    reason="SMS failure flagged transient — retry likely to succeed",
                    delta=+0.05,
                    component="sms_delivery_rule",
                ),)

            return RuleResult(
                rule_id=self.rule_id,
                status=RuleMatchStatus.MATCH,
                confidence=0.82,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"SMS/OTP delivery failure detected. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}. "
                    f"Transient: {transient}."
                ),
                category_hint=RootCauseCategory.SMS_DELIVERY_FAILURE,
                confidence_adjustments=adj,
            )
        except Exception as exc:
            LOGGER.warning("SmsDeliveryFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 7: Callback Failure ───────────────────────────────────────────────────

class CallbackFailureRule:
    """Detect webhook / callback failures from log evidence."""
    rule_id     = "callback_failure"
    name        = "CallbackFailureRule"
    priority    = 7
    description = "Matches CALLBACK_FAILURE from log evidence."

    _CALLBACK_CATEGORIES = {
        "CALLBACK", "CALLBACK_FAILURE", "WEBHOOK_FAILED", "WEBHOOK_TIMEOUT",
        "CALLBACK_NOT_RECEIVED", "CALLBACK_ERROR",
    }

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items     = _successful(bundle)
            log_items = _by_type(items, EvidenceType.LOG)

            hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._CALLBACK_CATEGORIES
            ]
            if not hits:
                return RuleResult.no_match(self.rule_id, "No callback failure indicators found.")

            return RuleResult(
                rule_id=self.rule_id,
                status=RuleMatchStatus.MATCH,
                confidence=0.80,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"Callback/webhook failure detected. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}."
                ),
                category_hint=RootCauseCategory.CALLBACK_FAILURE,
            )
        except Exception as exc:
            LOGGER.warning("CallbackFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 8: Quota Exceeded ────────────────────────────────────────────────────

class QuotaExceededRule:
    """Detect API quota / rate limit exhaustion from log evidence."""
    rule_id     = "quota_exceeded"
    name        = "QuotaExceededRule"
    priority    = 8
    description = "Matches QUOTA_EXCEEDED or RATE_LIMIT from log evidence."

    _QUOTA_CATEGORIES = {
        "QUOTA", "QUOTA_EXCEEDED", "RATE_LIMIT", "RATE_LIMIT_EXCEEDED",
        "TOO_MANY_REQUESTS", "THROTTLED",
    }
    _QUOTA_CODES = {"429", "ERR_QUOTA", "ERR_RATE_LIMIT", "QUOTA_EXCEEDED"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items     = _successful(bundle)
            log_items = _by_type(items, EvidenceType.LOG)

            category_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._QUOTA_CATEGORIES
            ]
            code_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CODE, "")).upper() in self._QUOTA_CODES
            ]
            hits = list({id(e): e for e in category_hits + code_hits}.values())

            if not hits:
                return RuleResult.no_match(self.rule_id, "No quota/rate-limit indicators found.")

            return RuleResult(
                rule_id=self.rule_id,
                status=RuleMatchStatus.MATCH,
                confidence=0.88,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"API quota/rate limit exceeded. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}."
                ),
                category_hint=RootCauseCategory.QUOTA_EXCEEDED,
            )
        except Exception as exc:
            LOGGER.warning("QuotaExceededRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 9: Portal Unavailable ─────────────────────────────────────────────────

class PortalUnavailableRule:
    """Detect portal / provider system unavailability."""
    rule_id     = "portal_unavailable"
    name        = "PortalUnavailableRule"
    priority    = 9
    description = "Matches PORTAL_UNAVAILABLE from session or log evidence."

    _PORTAL_CATEGORIES = {
        "PORTAL", "PORTAL_UNAVAILABLE", "PORTAL_DOWN", "SERVICE_UNAVAILABLE",
        "PROVIDER_DOWN", "SYSTEM_UNAVAILABLE",
    }
    _PORTAL_SESSION_STATUSES = {"PORTAL_DOWN", "SERVICE_UNAVAILABLE"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items         = _successful(bundle)
            log_items     = _by_type(items, EvidenceType.LOG)
            session_items = _by_type(items, EvidenceType.SESSION)

            log_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._PORTAL_CATEGORIES
            ]
            session_hits = [
                e for e in session_items
                if str(_payload(e).get(_SESSION_STATUS, "")).upper() in self._PORTAL_SESSION_STATUSES
            ]

            hits = log_hits + session_hits
            if not hits:
                return RuleResult.no_match(self.rule_id, "No portal unavailability indicators found.")

            confidence = 0.87 if (log_hits and session_hits) else 0.75
            adj: tuple[ConfidenceAdjustment, ...] = ()
            if log_hits and session_hits:
                adj = (ConfidenceAdjustment(
                    reason="Portal unavailability corroborated by both log and session evidence",
                    delta=+0.05,
                    component="portal_unavailable_rule",
                ),)

            return RuleResult(
                rule_id=self.rule_id,
                status=RuleMatchStatus.MATCH,
                confidence=confidence,
                evidence_ids_used=_evidence_ids(hits),
                explanation=(
                    f"Portal/provider system unavailable. "
                    f"failure_category={_payload(hits[0]).get(_FAILURE_CATEGORY, 'N/A')!r}."
                ),
                category_hint=RootCauseCategory.PORTAL_UNAVAILABLE,
                confidence_adjustments=adj,
            )
        except Exception as exc:
            LOGGER.warning("PortalUnavailableRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 10: Onboarding Blocked ────────────────────────────────────────────────

class OnboardingBlockedRule:
    """Detect user onboarding blocked from user evidence."""
    rule_id     = "onboarding_blocked"
    name        = "OnboardingBlockedRule"
    priority    = 10
    description = "Matches ONBOARDING_BLOCKED from user evidence onboarding_status field."

    _BLOCKED_ONBOARDING = {"BLOCKED", "FROZEN", "SUSPENDED", "PENDING_REVIEW", "ONBOARDING_BLOCKED"}
    _BLOCKED_LOG        = {"ONBOARDING_BLOCKED", "ONBOARDING_FAILED", "REGISTRATION_BLOCKED"}

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items      = _successful(bundle)
            user_items = _by_type(items, EvidenceType.USER)
            log_items  = _by_type(items, EvidenceType.LOG)

            user_hits = [
                e for e in user_items
                if str(_payload(e).get(_ONBOARDING_STATUS, "")).upper() in self._BLOCKED_ONBOARDING
            ]
            log_hits = [
                e for e in log_items
                if str(_payload(e).get(_FAILURE_CATEGORY, "")).upper() in self._BLOCKED_LOG
            ]

            if user_hits:
                all_hits = user_hits + log_hits
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.MATCH,
                    confidence=0.88,
                    evidence_ids_used=_evidence_ids(all_hits),
                    explanation=(
                        f"Onboarding blocked. "
                        f"onboarding_status={_payload(user_hits[0]).get(_ONBOARDING_STATUS, 'N/A')!r}."
                    ),
                    category_hint=RootCauseCategory.ONBOARDING_BLOCKED,
                )

            if log_hits:
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.PARTIAL_MATCH,
                    confidence=0.60,
                    evidence_ids_used=_evidence_ids(log_hits),
                    explanation="Log evidence suggests onboarding block; user evidence unavailable for confirmation.",
                    category_hint=RootCauseCategory.ONBOARDING_BLOCKED,
                )

            return RuleResult.no_match(self.rule_id, "No onboarding block indicators found.")
        except Exception as exc:
            LOGGER.warning("OnboardingBlockedRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")


# ── Rule 11: Repeated Failure ──────────────────────────────────────────────────

class RepeatedFailureRule:
    """Detect chronic repeated failures from case history evidence."""
    rule_id     = "repeated_failure"
    name        = "RepeatedFailureRule"
    priority    = 11
    description = "Matches REPEATED_FAILURE from case history (high attempt_count or repeat_pattern)."

    _HIGH_ATTEMPT_THRESHOLD  = 3
    _VERY_HIGH_ATTEMPT_THRESHOLD = 5
    _HIGH_FAILURE_RATE       = 0.7   # 70%

    def evaluate(self, bundle: Any, context: RuleEvaluationContext) -> RuleResult:
        try:
            items         = _successful(bundle)
            summary_items = _by_type(items, EvidenceType.SUMMARY)
            session_items = _by_type(items, EvidenceType.SESSION)

            # Prefer summary evidence; fall back to session attempt_count
            attempt_count  = 0
            failure_rate   = 0.0
            repeat_pattern = False
            evidence_hits: list[Any] = []

            for e in summary_items:
                p = _payload(e)
                cnt = p.get(_ATTEMPT_COUNT, 0)
                if isinstance(cnt, (int, float)) and cnt >= self._HIGH_ATTEMPT_THRESHOLD:
                    attempt_count  = max(attempt_count, int(cnt))
                    failure_rate   = float(p.get(_FAILURE_RATE, 0.0))
                    repeat_pattern = bool(p.get(_REPEAT_PATTERN, False))
                    evidence_hits.append(e)

            if not evidence_hits:
                for e in session_items:
                    p = _payload(e)
                    cnt = p.get(_ATTEMPT_COUNT, 0)
                    if isinstance(cnt, (int, float)) and cnt >= self._HIGH_ATTEMPT_THRESHOLD:
                        attempt_count = max(attempt_count, int(cnt))
                        evidence_hits.append(e)

            if not evidence_hits:
                return RuleResult.no_match(self.rule_id, "Attempt count below threshold or no history evidence.")

            # Strong match: very high attempts OR explicit repeat_pattern
            if attempt_count >= self._VERY_HIGH_ATTEMPT_THRESHOLD or repeat_pattern:
                adj: tuple[ConfidenceAdjustment, ...] = ()
                if failure_rate >= self._HIGH_FAILURE_RATE:
                    adj = (ConfidenceAdjustment(
                        reason=f"High historical failure rate ({failure_rate:.0%})",
                        delta=+0.08,
                        component="repeated_failure_rule",
                    ),)
                return RuleResult(
                    rule_id=self.rule_id,
                    status=RuleMatchStatus.MATCH,
                    confidence=0.82,
                    evidence_ids_used=_evidence_ids(evidence_hits),
                    explanation=(
                        f"Repeated failure pattern detected. "
                        f"attempt_count={attempt_count}, "
                        f"repeat_pattern={repeat_pattern}, "
                        f"failure_rate={failure_rate:.0%}."
                    ),
                    category_hint=RootCauseCategory.REPEATED_FAILURE,
                    confidence_adjustments=adj,
                )

            # Partial match: attempt_count >= threshold but not yet extreme
            return RuleResult(
                rule_id=self.rule_id,
                status=RuleMatchStatus.PARTIAL_MATCH,
                confidence=0.55,
                evidence_ids_used=_evidence_ids(evidence_hits),
                explanation=(
                    f"Elevated attempt count ({attempt_count}) suggests developing repeated failure. "
                    f"Manual review recommended."
                ),
                category_hint=RootCauseCategory.REPEATED_FAILURE,
            )
        except Exception as exc:
            LOGGER.warning("RepeatedFailureRule.evaluate() raised: %s", exc)
            return RuleResult.unknown(self.rule_id, f"Rule evaluation error: {exc}")
