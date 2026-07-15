"""
tests/test_sprint218_root_cause.py

Sprint 2.18: RootCauseEngine tests (Part D).

Coverage:
  - All 5 topic rule chains (VKYC, OTP, OCR, Portal, Callback)
  - Each rule within each chain
  - Unknown topic → UNKNOWN category, escalate=True
  - No evidence available → UNKNOWN with escalate=True
  - RootCauseAnalysis fields (confidence bounds, evidence_ids, recommended_action)
  - Unexpected exception → UNKNOWN (never raises)
"""
from __future__ import annotations

import pytest

from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    LogEvidence,
    RecommendedAction,
    RootCauseCategory,
    SessionEvidence,
    SummaryEvidence,
    UserEvidence,
)
from case_engine.investigation._root_cause_sprint218 import RootCauseEngine


# ── Factories ──────────────────────────────────────────────────────────────────

def make_bundle(topic: str, items=None) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="b-001",
        case_id="c-001",
        topic=topic,
        plan_id="p-001",
        items=items or [],
        collected_at="2026-06-10T00:00:00+00:00",
    )


def make_session_ev(payload: dict, success: bool = True) -> SessionEvidence:
    return SessionEvidence(
        evidence_id="ev-s",
        evidence_type=EvidenceType.SESSION,
        source=EvidenceSource.GET_SESSION_DETAILS,
        tool_name="GetSessionDetailsTool",
        payload=payload,
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-001",
        success=success,
    )


def make_user_ev(payload: dict, success: bool = True) -> UserEvidence:
    return UserEvidence(
        evidence_id="ev-u",
        evidence_type=EvidenceType.USER,
        source=EvidenceSource.GET_USER_DETAILS,
        tool_name="GetUserDetailsTool",
        payload=payload,
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-002",
        success=success,
    )


def make_log_ev(payload: dict, success: bool = True) -> LogEvidence:
    return LogEvidence(
        evidence_id="ev-l",
        evidence_type=EvidenceType.LOG,
        source=EvidenceSource.GET_FAILURE_REASON,
        tool_name="GetFailureReasonTool",
        payload=payload,
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-003",
        success=success,
    )


def make_onboard_ev(payload: dict, success: bool = True) -> SummaryEvidence:
    return SummaryEvidence(
        evidence_id="ev-ob",
        evidence_type=EvidenceType.SUMMARY,
        source=EvidenceSource.GET_ONBOARDING_STATUS,
        tool_name="GetOnboardingStatusTool",
        payload=payload,
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-004",
        success=success,
    )


RCA = RootCauseEngine()


# ── VKYC Session Failure ───────────────────────────────────────────────────────

class TestVKYCSessionFailure:
    def test_network_failure_code(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"failure_code": "NETWORK_TIMEOUT", "session_status": "FAILED"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.NETWORK_FAILURE
        assert rca.recommended_action == RecommendedAction.SESSION_RESET
        assert rca.escalate is False
        assert rca.confidence >= 0.8

    def test_expired_session(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "EXPIRED", "failure_code": ""}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.EXPIRED_SESSION
        assert rca.recommended_action == RecommendedAction.SESSION_RESET

    def test_liveness_failure(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "FAILED", "failure_code": "LIVENESS_CHECK_FAILED"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.LIVENESS_FAILURE

    def test_document_failure(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "FAILED", "failure_code": "PAN_DOCUMENT_CORRUPT"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.DOCUMENT_FAILURE

    def test_kyc_rejected_user(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "FAILED", "failure_code": "", "attempt_count": 1}),
            make_user_ev({"kyc_status": "REJECTED"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.KYC_REJECTED
        assert rca.escalate is True

    def test_repeated_failure(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "FAILED", "failure_code": "", "attempt_count": 5}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.REPEATED_FAILURE
        assert rca.escalate is True

    def test_no_session_evidence_escalates(self):
        bundle = make_bundle("VKYC_Session_Failure", [])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.UNKNOWN
        assert rca.escalate is True

    def test_analysis_id_is_non_empty(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "EXPIRED", "failure_code": ""}),
        ])
        rca = RCA.analyse(bundle)
        assert len(rca.analysis_id) > 0

    def test_analysed_at_is_iso_timestamp(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "EXPIRED", "failure_code": ""}),
        ])
        rca = RCA.analyse(bundle)
        assert "T" in rca.analysed_at

    def test_validation_failure_fallback(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "FAILED", "failure_code": "VERIFY_FAILED", "attempt_count": 1}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.VALIDATION_FAILURE


# ── OTP Delivery Failure ───────────────────────────────────────────────────────

class TestOTPDeliveryFailure:
    def test_sms_delivery_failure_transient(self):
        bundle = make_bundle("OTP_Delivery_Failure", [
            make_log_ev({"failure_category": "SMS_DELIVERY", "failure_code": "SEND_FAILED", "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.SMS_DELIVERY_FAILURE
        assert rca.recommended_action == RecommendedAction.OTP_RESEND
        assert rca.escalate is False

    def test_sms_delivery_failure_permanent(self):
        bundle = make_bundle("OTP_Delivery_Failure", [
            make_log_ev({"failure_category": "SMS_DELIVERY", "failure_code": "SEND_FAILED", "is_transient": False}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.SMS_DELIVERY_FAILURE
        assert rca.recommended_action == RecommendedAction.MANUAL_REVIEW
        assert rca.escalate is True

    def test_timeout(self):
        bundle = make_bundle("OTP_Delivery_Failure", [
            make_log_ev({"failure_category": "TIMEOUT", "failure_code": "TIMEOUT_5000", "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.TIMEOUT
        assert rca.recommended_action == RecommendedAction.OTP_RESEND

    def test_quota_exceeded(self):
        bundle = make_bundle("OTP_Delivery_Failure", [
            make_log_ev({"failure_category": "QUOTA", "failure_code": "QUOTA_EXHAUSTED", "is_transient": False}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.QUOTA_EXCEEDED
        assert rca.escalate is True

    def test_suspended_account(self):
        bundle = make_bundle("OTP_Delivery_Failure", [
            make_user_ev({"account_state": "SUSPENDED"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.escalate is True

    def test_no_evidence_fallback(self):
        bundle = make_bundle("OTP_Delivery_Failure", [])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.UNKNOWN
        assert rca.escalate is True


# ── Document OCR Failure ───────────────────────────────────────────────────────

class TestDocumentOCRFailure:
    def test_blocked_onboarding(self):
        bundle = make_bundle("Document_OCR_Failure", [
            make_onboard_ev({"is_blocked": True, "blocking_step": "OCR_VERIFICATION", "stage": "KYC"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.ONBOARDING_BLOCKED
        assert rca.escalate is True

    def test_document_step_blocking(self):
        bundle = make_bundle("Document_OCR_Failure", [
            make_onboard_ev({"is_blocked": False, "blocking_step": "PAN_DOCUMENT_CHECK",
                             "stage": "KYC", "completion_percentage": 60}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.DOCUMENT_FAILURE
        assert rca.recommended_action == RecommendedAction.RETRY

    def test_low_completion_percentage(self):
        bundle = make_bundle("Document_OCR_Failure", [
            make_onboard_ev({"is_blocked": False, "blocking_step": "OTHER", "stage": "KYC",
                             "completion_percentage": 20}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.DOCUMENT_FAILURE

    def test_no_onboarding_evidence_escalates(self):
        bundle = make_bundle("Document_OCR_Failure", [])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.UNKNOWN
        assert rca.escalate is True


# ── Agent Portal Issue ─────────────────────────────────────────────────────────

class TestAgentPortalIssue:
    def test_portal_unavailable_503(self):
        bundle = make_bundle("Agent_Portal_Issue", [
            make_log_ev({"failure_code": "503_SERVICE_UNAVAILABLE", "failure_category": "INFRA"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.PORTAL_UNAVAILABLE
        assert rca.recommended_action == RecommendedAction.PORTAL_REFRESH

    def test_timeout_portal(self):
        bundle = make_bundle("Agent_Portal_Issue", [
            make_log_ev({"failure_code": "TIMEOUT_30000", "failure_category": "TIMEOUT"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.TIMEOUT

    def test_auth_failure(self):
        bundle = make_bundle("Agent_Portal_Issue", [
            make_log_ev({"failure_code": "AUTH_DENIED", "failure_category": "AUTH"}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.VALIDATION_FAILURE
        assert rca.escalate is True

    def test_no_log_evidence_fallback(self):
        bundle = make_bundle("Agent_Portal_Issue", [])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.PORTAL_UNAVAILABLE
        assert rca.escalate is True


# ── API Callback Failure ───────────────────────────────────────────────────────

class TestAPICallbackFailure:
    def test_network_failure(self):
        bundle = make_bundle("API_Callback_Failure", [
            make_log_ev({"failure_code": "NETWORK_UNREACHABLE", "http_status": 0, "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.NETWORK_FAILURE
        assert rca.recommended_action == RecommendedAction.CALLBACK_RETRY

    def test_timeout_408(self):
        bundle = make_bundle("API_Callback_Failure", [
            make_log_ev({"failure_code": "TIMEOUT", "http_status": 408, "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.TIMEOUT

    def test_server_error_500(self):
        bundle = make_bundle("API_Callback_Failure", [
            make_log_ev({"failure_code": "INTERNAL", "http_status": 500, "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.CALLBACK_FAILURE
        assert rca.recommended_action == RecommendedAction.CALLBACK_RETRY

    def test_server_error_503_escalates(self):
        bundle = make_bundle("API_Callback_Failure", [
            make_log_ev({"failure_code": "INTERNAL", "http_status": 503, "is_transient": True}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.escalate is True

    def test_client_error_400(self):
        bundle = make_bundle("API_Callback_Failure", [
            make_log_ev({"failure_code": "BAD_REQUEST", "http_status": 400, "is_transient": False}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.recommended_action == RecommendedAction.ESCALATE
        assert rca.escalate is True

    def test_no_log_evidence_fallback(self):
        bundle = make_bundle("API_Callback_Failure", [])
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.CALLBACK_FAILURE
        assert rca.escalate is True


# ── Unknown topic / error safety ──────────────────────────────────────────────

class TestRCAUnknownAndSafety:
    def test_unknown_topic_returns_unknown_category(self):
        bundle = make_bundle("TOTALLY_UNKNOWN_TOPIC_XYZ")
        rca = RCA.analyse(bundle)
        assert rca.category == RootCauseCategory.UNKNOWN
        assert rca.escalate is True

    def test_rca_confidence_in_range(self):
        for topic in ["VKYC_Session_Failure", "OTP_Delivery_Failure",
                      "Document_OCR_Failure", "Agent_Portal_Issue", "API_Callback_Failure"]:
            bundle = make_bundle(topic)
            rca = RCA.analyse(bundle)
            assert 0.0 <= rca.confidence <= 1.0

    def test_never_raises_on_malformed_payload(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"unexpected_key": None, "another": [1, 2, 3]}),
        ])
        rca = RCA.analyse(bundle)
        assert rca is not None

    def test_rca_case_id_matches_bundle(self):
        bundle = make_bundle("VKYC_Session_Failure", [
            make_session_ev({"session_status": "EXPIRED", "failure_code": ""}),
        ])
        rca = RCA.analyse(bundle)
        assert rca.case_id == "c-001"

    def test_rca_topic_matches_bundle(self):
        bundle = make_bundle("OTP_Delivery_Failure", [])
        rca = RCA.analyse(bundle)
        assert rca.topic == "OTP_Delivery_Failure"
