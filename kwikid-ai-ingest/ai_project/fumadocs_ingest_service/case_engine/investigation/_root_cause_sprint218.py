"""
case_engine/investigation/root_cause.py

Sprint 2.18: RootCauseEngine — deterministic root cause analysis from EvidenceBundle.

Per blueprint Section 13:
  Input:  EvidenceBundle (from EvidenceCollector)
  Output: RootCauseAnalysis (category, confidence, explanation, evidence_ids,
          recommended_action, escalate)

Design invariants:
  - No LLM calls — all logic is rule-based Python
  - Never raises — failures produce UNKNOWN category with escalate=True
  - Fully auditable — every conclusion references the evidence_ids that drove it
  - Per-topic rule chains applied in priority order; first match wins

Public API:
  engine = RootCauseEngine()
  rca    = engine.analyse(bundle)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)

LOGGER = logging.getLogger(__name__)

# ── Confidence constants ───────────────────────────────────────────────────────
_HIGH   = 0.90
_MEDIUM = 0.70
_LOW    = 0.50
_NONE   = 0.30


class RootCauseEngine:
    """
    Applies deterministic per-topic rule chains to an EvidenceBundle and
    produces a RootCauseAnalysis.

    Stateless — safe to share across requests.
    """

    def analyse(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Analyse the bundle and return a RootCauseAnalysis.

        Args:
            bundle: EvidenceBundle produced by EvidenceCollector.

        Returns:
            RootCauseAnalysis — never raises; worst-case returns UNKNOWN.
        """
        try:
            return self._analyse(bundle)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("root_cause_engine.analyse.unexpected_error bundle_id=%s", bundle.bundle_id)
            return self._unknown(bundle, str(exc))

    # ── Main dispatch ──────────────────────────────────────────────────────────

    def _analyse(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        topic = bundle.topic

        if topic == "VKYC_Session_Failure":
            return self._vkyc_session_failure(bundle)
        if topic == "OTP_Delivery_Failure":
            return self._otp_delivery_failure(bundle)
        if topic == "Document_OCR_Failure":
            return self._document_ocr_failure(bundle)
        if topic == "Agent_Portal_Issue":
            return self._agent_portal_issue(bundle)
        if topic == "API_Callback_Failure":
            return self._api_callback_failure(bundle)

        LOGGER.warning("root_cause_engine: unrecognised topic=%s", topic)
        return self._unknown(bundle, f"No rule chain defined for topic '{topic}'")

    # ── Per-topic rule chains ──────────────────────────────────────────────────

    def _vkyc_session_failure(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Rule chain for VKYC_Session_Failure.

        Evidence sources:
          SessionEvidence  — session status, attempt count, failure code, reset eligibility
          UserEvidence     — KYC status, account state, risk tier
        """
        session_ev = bundle.get_by_source(EvidenceSource.GET_SESSION_DETAILS)
        user_ev    = bundle.get_by_source(EvidenceSource.GET_USER_DETAILS)
        evidence_ids: list[str] = [
            e.evidence_id for e in [session_ev, user_ev] if e is not None
        ]

        if session_ev is None:
            return self._build(
                bundle, RootCauseCategory.UNKNOWN, _NONE,
                "Could not retrieve session details — investigation incomplete.",
                evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
            )

        payload = session_ev.payload

        # Rule 1: Network / infrastructure failures from failure_code
        failure_code = str(payload.get("failure_code", "")).upper()
        if any(k in failure_code for k in ("NETWORK", "TIMEOUT", "CONN")):
            return self._build(
                bundle, RootCauseCategory.NETWORK_FAILURE, _HIGH,
                f"Session ended with network-related failure code '{failure_code}'. "
                "Likely transient infrastructure issue.",
                evidence_ids, RecommendedAction.SESSION_RESET, escalate=False,
            )

        # Rule 2: Session expired
        session_status = str(payload.get("session_status", "")).upper()
        if "EXPIRED" in session_status or failure_code == "SESSION_EXPIRED":
            return self._build(
                bundle, RootCauseCategory.EXPIRED_SESSION, _HIGH,
                "Session expired before VKYC completion. "
                "User should initiate a fresh session.",
                evidence_ids, RecommendedAction.SESSION_RESET, escalate=False,
            )

        # Rule 3: Liveness check failure
        if "LIVENESS" in failure_code or "LIVENESS" in session_status:
            return self._build(
                bundle, RootCauseCategory.LIVENESS_FAILURE, _HIGH,
                "Liveness check failed during VKYC session. "
                "Agent should guide user to retry with better lighting and stable position.",
                evidence_ids, RecommendedAction.SESSION_RESET, escalate=False,
            )

        # Rule 4: Document-related failure
        if any(k in failure_code for k in ("DOCUMENT", "DOC", "OCR", "PAN", "AADHAAR")):
            return self._build(
                bundle, RootCauseCategory.DOCUMENT_FAILURE, _HIGH,
                f"Document verification step failed (code: {failure_code}). "
                "Document may be damaged or unreadable.",
                evidence_ids, RecommendedAction.RETRY, escalate=False,
            )

        # Rule 5: KYC rejected (user-level)
        if user_ev is not None:
            kyc_status = str(user_ev.payload.get("kyc_status", "")).upper()
            if kyc_status in ("REJECTED", "BLACKLISTED", "FROZEN"):
                return self._build(
                    bundle, RootCauseCategory.KYC_REJECTED, _HIGH,
                    f"User KYC status is '{kyc_status}'. "
                    "No automated resolution possible — manual review required.",
                    evidence_ids, RecommendedAction.ESCALATE, escalate=True,
                )

        # Rule 6: Repeated failure
        attempt_count = int(payload.get("attempt_count", 0))
        if attempt_count >= 3:
            return self._build(
                bundle, RootCauseCategory.REPEATED_FAILURE, _MEDIUM,
                f"Session has failed {attempt_count} times. "
                "Repeated failures suggest persistent user or system issue.",
                evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
            )

        # Rule 7: Validation failure (catch-all for failed validation codes)
        if "VALID" in failure_code or "VERIFY" in failure_code:
            return self._build(
                bundle, RootCauseCategory.VALIDATION_FAILURE, _MEDIUM,
                f"Validation step failed with code '{failure_code}'.",
                evidence_ids, RecommendedAction.RETRY, escalate=False,
            )

        # Fallback
        return self._build(
            bundle, RootCauseCategory.UNKNOWN, _LOW,
            f"VKYC session failed (status: {session_status}, code: {failure_code}). "
            "Cause indeterminate from available evidence.",
            evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
        )

    def _otp_delivery_failure(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Rule chain for OTP_Delivery_Failure.

        Evidence sources:
          UserEvidence  — phone number, account state
          LogEvidence   — failure category, failure code, transience
        """
        user_ev = bundle.get_by_source(EvidenceSource.GET_USER_DETAILS)
        log_ev  = bundle.get_by_source(EvidenceSource.GET_FAILURE_REASON)
        evidence_ids = [e.evidence_id for e in [user_ev, log_ev] if e is not None]

        if log_ev is None and user_ev is None:
            return self._build(
                bundle, RootCauseCategory.UNKNOWN, _NONE,
                "No evidence available — could not determine OTP failure cause.",
                evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
            )

        # Rule 1: SMS provider failure from log evidence
        if log_ev is not None:
            failure_code = str(log_ev.payload.get("failure_code", "")).upper()
            failure_cat  = str(log_ev.payload.get("failure_category", "")).upper()
            is_transient = bool(log_ev.payload.get("is_transient", False))

            if any(k in failure_cat for k in ("SMS", "DELIVERY", "SEND")):
                action = RecommendedAction.OTP_RESEND if is_transient else RecommendedAction.MANUAL_REVIEW
                return self._build(
                    bundle, RootCauseCategory.SMS_DELIVERY_FAILURE,
                    _HIGH if is_transient else _MEDIUM,
                    f"SMS delivery failed (category: {failure_cat}, code: {failure_code}). "
                    f"Transient: {is_transient}.",
                    evidence_ids, action, escalate=not is_transient,
                )

            if "TIMEOUT" in failure_code or "TIMEOUT" in failure_cat:
                return self._build(
                    bundle, RootCauseCategory.TIMEOUT, _HIGH,
                    f"OTP generation or delivery timed out (code: {failure_code}).",
                    evidence_ids, RecommendedAction.OTP_RESEND, escalate=False,
                )

            if "QUOTA" in failure_code or "RATE" in failure_code:
                return self._build(
                    bundle, RootCauseCategory.QUOTA_EXCEEDED, _HIGH,
                    "OTP delivery blocked due to quota or rate limit exhaustion.",
                    evidence_ids, RecommendedAction.ESCALATE, escalate=True,
                )

        # Rule 2: User account state
        if user_ev is not None:
            account_state = str(user_ev.payload.get("account_state", "")).upper()
            if account_state in ("SUSPENDED", "BLOCKED", "DEACTIVATED"):
                return self._build(
                    bundle, RootCauseCategory.VALIDATION_FAILURE, _HIGH,
                    f"User account is '{account_state}' — OTP delivery rejected at provider level.",
                    evidence_ids, RecommendedAction.ESCALATE, escalate=True,
                )

        return self._build(
            bundle, RootCauseCategory.SMS_DELIVERY_FAILURE, _LOW,
            "OTP delivery failed. Specific cause indeterminate from available evidence.",
            evidence_ids, RecommendedAction.OTP_RESEND, escalate=False,
        )

    def _document_ocr_failure(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Rule chain for Document_OCR_Failure.

        Evidence sources:
          UserEvidence     — user profile
          SummaryEvidence  — onboarding stage, completion percentage, blocking step
        """
        user_ev    = bundle.get_by_source(EvidenceSource.GET_USER_DETAILS)
        onboard_ev = bundle.get_by_source(EvidenceSource.GET_ONBOARDING_STATUS)
        evidence_ids = [e.evidence_id for e in [user_ev, onboard_ev] if e is not None]

        if onboard_ev is None:
            return self._build(
                bundle, RootCauseCategory.UNKNOWN, _NONE,
                "Onboarding status unavailable — cannot determine OCR failure cause.",
                evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
            )

        payload = onboard_ev.payload
        blocking_step = str(payload.get("blocking_step", "")).upper()
        stage         = str(payload.get("stage", "")).upper()

        # Rule 1: Explicitly blocked
        is_blocked = bool(payload.get("is_blocked", False))
        if is_blocked:
            return self._build(
                bundle, RootCauseCategory.ONBOARDING_BLOCKED, _HIGH,
                f"Onboarding is blocked at step '{blocking_step}' (stage: {stage}). "
                "Manual review required to unblock.",
                evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
            )

        # Rule 2: Document step is the blocking step
        if any(k in blocking_step for k in ("OCR", "DOCUMENT", "PAN", "AADHAAR", "DOC")):
            completion = payload.get("completion_percentage", 0)
            return self._build(
                bundle, RootCauseCategory.DOCUMENT_FAILURE, _HIGH,
                f"Onboarding blocked at document step '{blocking_step}' "
                f"({completion}% complete). OCR may have failed to extract valid data.",
                evidence_ids, RecommendedAction.RETRY, escalate=False,
            )

        # Rule 3: Low completion
        completion = int(payload.get("completion_percentage", 0))
        if completion < 30:
            return self._build(
                bundle, RootCauseCategory.DOCUMENT_FAILURE, _MEDIUM,
                f"Onboarding is only {completion}% complete. Early-stage document failure likely.",
                evidence_ids, RecommendedAction.RETRY, escalate=False,
            )

        return self._build(
            bundle, RootCauseCategory.DOCUMENT_FAILURE, _LOW,
            f"Document OCR issue detected (stage: {stage}, completion: {completion}%). "
            "Specific failure step unclear.",
            evidence_ids, RecommendedAction.MANUAL_REVIEW, escalate=True,
        )

    def _agent_portal_issue(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Rule chain for Agent_Portal_Issue.

        Evidence sources:
          UserEvidence — user/account lookup
          LogEvidence  — failure reason, portal error code
        """
        user_ev = bundle.get_by_source(EvidenceSource.GET_USER_DETAILS)
        log_ev  = bundle.get_by_source(EvidenceSource.GET_FAILURE_REASON)
        evidence_ids = [e.evidence_id for e in [user_ev, log_ev] if e is not None]

        if log_ev is not None:
            failure_code = str(log_ev.payload.get("failure_code", "")).upper()
            failure_cat  = str(log_ev.payload.get("failure_category", "")).upper()

            if any(k in failure_code for k in ("UNAVAILABLE", "DOWN", "503", "502")):
                return self._build(
                    bundle, RootCauseCategory.PORTAL_UNAVAILABLE, _HIGH,
                    f"Agent portal returned error code '{failure_code}'. "
                    "Portal is likely unavailable or experiencing an outage.",
                    evidence_ids, RecommendedAction.PORTAL_REFRESH, escalate=True,
                )

            if "TIMEOUT" in failure_code or "TIMEOUT" in failure_cat:
                return self._build(
                    bundle, RootCauseCategory.TIMEOUT, _HIGH,
                    f"Portal request timed out (code: {failure_code}).",
                    evidence_ids, RecommendedAction.PORTAL_REFRESH, escalate=False,
                )

            if "AUTH" in failure_code or "PERMISSION" in failure_code or "FORBIDDEN" in failure_code:
                return self._build(
                    bundle, RootCauseCategory.VALIDATION_FAILURE, _HIGH,
                    f"Portal access denied (code: {failure_code}). "
                    "Agent may lack required permissions or session may have expired.",
                    evidence_ids, RecommendedAction.ESCALATE, escalate=True,
                )

        return self._build(
            bundle, RootCauseCategory.PORTAL_UNAVAILABLE, _LOW,
            "Agent portal issue reported. Specific cause could not be determined from available logs.",
            evidence_ids, RecommendedAction.PORTAL_REFRESH, escalate=True,
        )

    def _api_callback_failure(self, bundle: EvidenceBundle) -> RootCauseAnalysis:
        """
        Rule chain for API_Callback_Failure.

        Evidence sources:
          LogEvidence — callback failure code, HTTP status, transience
        """
        log_ev = bundle.get_by_source(EvidenceSource.GET_FAILURE_REASON)
        evidence_ids = [log_ev.evidence_id] if log_ev else []

        if log_ev is None:
            return self._build(
                bundle, RootCauseCategory.CALLBACK_FAILURE, _LOW,
                "Callback failure reported but no log evidence could be retrieved.",
                evidence_ids, RecommendedAction.CALLBACK_RETRY, escalate=True,
            )

        failure_code = str(log_ev.payload.get("failure_code", "")).upper()
        http_status  = int(log_ev.payload.get("http_status", 0))
        is_transient = bool(log_ev.payload.get("is_transient", False))

        # Rule 1: Network failure
        if any(k in failure_code for k in ("NETWORK", "CONN", "UNREACHABLE")):
            return self._build(
                bundle, RootCauseCategory.NETWORK_FAILURE, _HIGH,
                f"Callback endpoint unreachable (code: {failure_code}). "
                "Network path to receiver is broken.",
                evidence_ids, RecommendedAction.CALLBACK_RETRY, escalate=not is_transient,
            )

        # Rule 2: Timeout
        if "TIMEOUT" in failure_code or http_status == 408:
            return self._build(
                bundle, RootCauseCategory.TIMEOUT, _HIGH,
                "Callback delivery timed out. Receiver did not acknowledge within the allowed window.",
                evidence_ids, RecommendedAction.CALLBACK_RETRY, escalate=False,
            )

        # Rule 3: HTTP 5xx — server error on receiver side
        if 500 <= http_status < 600:
            return self._build(
                bundle, RootCauseCategory.CALLBACK_FAILURE, _HIGH,
                f"Callback receiver returned HTTP {http_status}. Server-side error on receiver.",
                evidence_ids, RecommendedAction.CALLBACK_RETRY, escalate=http_status >= 503,
            )

        # Rule 4: HTTP 4xx — client/configuration error
        if 400 <= http_status < 500:
            return self._build(
                bundle, RootCauseCategory.CALLBACK_FAILURE, _HIGH,
                f"Callback rejected by receiver with HTTP {http_status}. "
                "Likely misconfiguration — endpoint URL, auth header, or payload format.",
                evidence_ids, RecommendedAction.ESCALATE, escalate=True,
            )

        return self._build(
            bundle, RootCauseCategory.CALLBACK_FAILURE, _MEDIUM,
            f"API callback failed (code: {failure_code}). Transient: {is_transient}.",
            evidence_ids, RecommendedAction.CALLBACK_RETRY, escalate=not is_transient,
        )

    # ── Builders ───────────────────────────────────────────────────────────────

    def _build(
        self,
        bundle: EvidenceBundle,
        category: RootCauseCategory,
        confidence: float,
        explanation: str,
        evidence_ids: list[str],
        recommended_action: RecommendedAction,
        escalate: bool,
    ) -> RootCauseAnalysis:
        rca = RootCauseAnalysis(
            analysis_id=str(uuid.uuid4()),
            case_id=bundle.case_id,
            topic=bundle.topic,
            category=category,
            confidence=confidence,
            explanation=explanation,
            evidence_ids=evidence_ids,
            recommended_action=recommended_action,
            escalate=escalate,
            analysed_at=datetime.now(tz=timezone.utc).isoformat(),
        )
        LOGGER.info(
            "root_cause_engine.result topic=%s category=%s confidence=%.2f escalate=%s",
            bundle.topic, category.value, confidence, escalate,
        )
        return rca

    def _unknown(self, bundle: EvidenceBundle, reason: str) -> RootCauseAnalysis:
        return self._build(
            bundle, RootCauseCategory.UNKNOWN, _NONE,
            f"Root cause could not be determined. Reason: {reason}",
            bundle.evidence_ids(),
            RecommendedAction.ESCALATE,
            escalate=True,
        )
