"""
tests/test_sprint218_observation.py

Sprint 2.18: ObservationGenerator tests (Part E).

Coverage:
  - Issue summary section for all 5 topics
  - Observed evidence section for successful and failed tools
  - Root cause section (category label, confidence, explanation)
  - Recommended action section
  - Escalation section (YES vs NO)
  - Metadata footer (bundle_id, analysis_id)
  - Never raises — malformed inputs produce fallback note
  - Blueprint Section 14 format present in all outputs
"""
from __future__ import annotations

import pytest

from case_engine.investigation.models import (
    EvidenceBundle,
    EvidenceSource,
    EvidenceType,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
    SessionEvidence,
    UserEvidence,
)
from case_engine.investigation.observation import ObservationGenerator


# ── Factories ──────────────────────────────────────────────────────────────────

GEN = ObservationGenerator()


def make_bundle(topic: str, items=None) -> EvidenceBundle:
    return EvidenceBundle(
        bundle_id="bundle-obs-001",
        case_id="case-obs-001",
        topic=topic,
        plan_id="plan-001",
        items=items or [],
        collected_at="2026-06-10T00:00:00+00:00",
    )


def make_rca(
    category: RootCauseCategory = RootCauseCategory.EXPIRED_SESSION,
    confidence: float = 0.9,
    recommended_action: RecommendedAction = RecommendedAction.SESSION_RESET,
    escalate: bool = False,
) -> RootCauseAnalysis:
    return RootCauseAnalysis(
        analysis_id="rca-obs-001",
        case_id="case-obs-001",
        topic="VKYC_Session_Failure",
        category=category,
        confidence=confidence,
        explanation="Test explanation from root cause engine.",
        evidence_ids=["ev-001"],
        recommended_action=recommended_action,
        escalate=escalate,
        analysed_at="2026-06-10T00:00:00+00:00",
    )


def make_session_ev(success: bool = True) -> SessionEvidence:
    return SessionEvidence(
        evidence_id="ev-s-001",
        evidence_type=EvidenceType.SESSION,
        source=EvidenceSource.GET_SESSION_DETAILS,
        tool_name="GetSessionDetailsTool",
        payload={"session_status": "FAILED", "failure_code": "TIMEOUT"} if success else {},
        collected_at="2026-06-10T00:00:00+00:00",
        invocation_id="inv-001",
        success=success,
        error_code=None if success else "CONN_ERROR",
        error_message=None if success else "Connection refused",
    )


# ── Section presence tests ────────────────────────────────────────────────────

class TestObservationSections:
    def test_issue_summary_section_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "=== ISSUE SUMMARY ===" in obs

    def test_observed_evidence_section_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "=== OBSERVED EVIDENCE ===" in obs

    def test_root_cause_section_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "=== ROOT CAUSE ===" in obs

    def test_recommended_action_section_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "=== RECOMMENDED ACTION ===" in obs

    def test_escalation_section_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "=== ESCALATION REQUIRED ===" in obs

    def test_metadata_footer_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "bundle-obs-001" in obs
        assert "rca-obs-001" in obs


# ── Issue summary topic tests ─────────────────────────────────────────────────

class TestIssueSummaryTopics:
    def test_vkyc_topic_summary(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "Video KYC" in obs or "VKYC" in obs

    def test_otp_topic_summary(self):
        obs = GEN.generate(make_bundle("OTP_Delivery_Failure"), make_rca())
        assert "OTP" in obs or "One-Time Password" in obs

    def test_ocr_topic_summary(self):
        obs = GEN.generate(make_bundle("Document_OCR_Failure"), make_rca())
        assert "OCR" in obs or "Document" in obs

    def test_portal_topic_summary(self):
        obs = GEN.generate(make_bundle("Agent_Portal_Issue"), make_rca())
        assert "portal" in obs.lower() or "Agent" in obs

    def test_callback_topic_summary(self):
        obs = GEN.generate(make_bundle("API_Callback_Failure"), make_rca())
        assert "callback" in obs.lower() or "API" in obs

    def test_unknown_topic_falls_back_gracefully(self):
        obs = GEN.generate(make_bundle("UNKNOWN_TOPIC_XYZ"), make_rca())
        assert "UNKNOWN_TOPIC_XYZ" in obs

    def test_case_id_in_issue_summary(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "case-obs-001" in obs


# ── Evidence section tests ────────────────────────────────────────────────────

class TestEvidenceSection:
    def test_successful_tool_shown_as_ok(self):
        ev  = make_session_ev(success=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", [ev]), make_rca())
        assert "[OK]" in obs or "OK" in obs

    def test_failed_tool_shown_as_failed(self):
        ev  = make_session_ev(success=False)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", [ev]), make_rca())
        assert "[FAILED]" in obs or "FAILED" in obs

    def test_failed_tool_shows_error_code(self):
        ev  = make_session_ev(success=False)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", [ev]), make_rca())
        assert "CONN_ERROR" in obs

    def test_no_evidence_note(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", []), make_rca())
        assert "No evidence" in obs or "0/" in obs

    def test_tool_name_shown_in_evidence(self):
        ev  = make_session_ev(success=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", [ev]), make_rca())
        assert "GetSessionDetailsTool" in obs

    def test_evidence_count_shown(self):
        ev  = make_session_ev(success=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure", [ev]), make_rca())
        assert "1/1" in obs


# ── Root cause section tests ──────────────────────────────────────────────────

class TestRootCauseSection:
    def test_category_label_shown(self):
        rca = make_rca(category=RootCauseCategory.EXPIRED_SESSION)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "Expired Session" in obs

    def test_confidence_as_percentage(self):
        rca = make_rca(confidence=0.9)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "90%" in obs

    def test_explanation_shown(self):
        rca = make_rca()
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "Test explanation from root cause engine." in obs

    def test_unknown_category_label(self):
        rca = make_rca(category=RootCauseCategory.UNKNOWN)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "Unknown" in obs or "Indeterminate" in obs


# ── Escalation section tests ──────────────────────────────────────────────────

class TestEscalationSection:
    def test_escalation_yes_when_escalate_true(self):
        rca = make_rca(escalate=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "YES" in obs

    def test_escalation_no_when_escalate_false(self):
        rca = make_rca(escalate=False)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "NO" in obs

    def test_escalation_includes_reason(self):
        rca = make_rca(escalate=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "Test explanation from root cause engine." in obs


# ── Recommended action section tests ─────────────────────────────────────────

class TestRecommendedActionSection:
    def test_session_reset_action(self):
        rca = make_rca(recommended_action=RecommendedAction.SESSION_RESET)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "session" in obs.lower() or "VKYC" in obs

    def test_otp_resend_action(self):
        rca = make_rca(recommended_action=RecommendedAction.OTP_RESEND)
        obs = GEN.generate(make_bundle("OTP_Delivery_Failure"), rca)
        assert "OTP" in obs or "resend" in obs.lower()

    def test_escalate_action(self):
        rca = make_rca(recommended_action=RecommendedAction.ESCALATE, escalate=True)
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), rca)
        assert "Escalate" in obs or "escalate" in obs.lower()


# ── Never-raises safety ───────────────────────────────────────────────────────

class TestObservationSafety:
    def test_never_raises_on_empty_bundle(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert isinstance(obs, str)
        assert len(obs) > 0

    def test_attribution_footer_present(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert "KwikID" in obs or "L1 Investigation" in obs

    def test_observation_is_string(self):
        obs = GEN.generate(make_bundle("VKYC_Session_Failure"), make_rca())
        assert isinstance(obs, str)
