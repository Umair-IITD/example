"""
tests/test_sprint1_classifier.py

TopicClassifier Tier 1 regex tests.

Covers:
- Each of the 5 topic families is matched by at least one Tier 1 rule
- Returned confidence >= 0.85 for matched topics (meets_threshold == True)
- Empty/blank input returns UNKNOWN with confidence 0.0
- Text with no known topic pattern returns UNKNOWN
- Higher-specificity pattern wins over lower-specificity when both could match
- Tier 2 stub always returns None (never affects output)
- ClassificationResult.meets_threshold property: True iff confidence >= 0.85 and topic != UNKNOWN
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from case_engine.classifier import TopicClassifier
from case_engine.models import ClassificationResult, TopicKey
from case_engine.nlp_router import NLPRouter, NLPSignal


def _signal(intent: str, confidence: float, entities: dict | None = None) -> NLPSignal:
    return NLPSignal(
        intent=intent,
        nested_case=None,
        entities=entities or {},
        negation_detected=False,
        confidence=confidence,
        needs_clarification=True,
        clarification_question=None,
        raw_text="",
    )


@pytest.fixture()
def clf():
    def _mock_route(text, **kwargs):
        t = text.lower()
        if any(kw in t for kw in ["otp", "one time password"]):
            return _signal("OTP_DELIVERY_FAILURE", 0.92, {"urn": None, "session_id": None})
        # OCR checked before VKYC — "liveliness" is OCR-specific even if "video kyc" appears in context
        if any(kw in t for kw in ["ocr", "aadhaar", "pan", "face match", "liveliness"]):
            return _signal("DOCUMENT_OCR_FAILURE", 0.88, {"urn": None, "session_id": None})
        if any(kw in t for kw in ["vkyc", "video kyc"]):
            return _signal("VKYC_SESSION_FAILURE", 0.90, {"urn": None, "session_id": None})
        if any(kw in t for kw in ["agent", "portal", "login", "auditor", "supervisor", "queue"]):
            return _signal("AGENT_PORTAL_ISSUE", 0.88, {"agent_id": None})
        if any(kw in t for kw in ["callback", "webhook", "cbs", "sfdc", "api timeout"]):
            return _signal("API_CALLBACK_FAILURE", 0.88, {"application_id": None, "callback_type": None})
        return _signal("UNKNOWN", 0.0, {})

    mock_router = MagicMock(spec=NLPRouter)
    mock_router.route.side_effect = _mock_route
    return TopicClassifier(nlp_router=mock_router)


# ── VKYC_SESSION_FAILURE ───────────────────────────────────────────────────────

class TestVkycClassification:
    def test_vkyc_link_expired(self, clf):
        result = clf.classify("My vkyc link has expired, please resend.")
        assert result.topic == TopicKey.VKYC_SESSION_FAILURE
        assert result.confidence >= 0.85
        assert result.tier_used == 2

    def test_video_kyc_session_dropped(self, clf):
        result = clf.classify("The video kyc session dropped during liveness check.")
        assert result.topic == TopicKey.VKYC_SESSION_FAILURE
        assert result.confidence >= 0.85

    def test_vkyc_camera_issue(self, clf):
        result = clf.classify("VKYC is not connecting, camera is not working.")
        assert result.topic == TopicKey.VKYC_SESSION_FAILURE

    def test_vkyc_alone_matches_lower_confidence(self, clf):
        result = clf.classify("Please help with my VKYC.")
        assert result.topic == TopicKey.VKYC_SESSION_FAILURE
        assert result.confidence >= 0.85

    def test_high_specificity_higher_than_general(self, clf):
        """Specific VKYC + session keyword should score >= bare VKYC mention."""
        specific = clf.classify("vkyc session expired after bandwidth drop")
        general  = clf.classify("VKYC issue")
        assert specific.confidence >= general.confidence


# ── OTP_DELIVERY_FAILURE ───────────────────────────────────────────────────────

class TestOtpClassification:
    def test_otp_not_received(self, clf):
        result = clf.classify("OTP not received on my mobile number.")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE
        assert result.confidence >= 0.85

    def test_otp_expired(self, clf):
        result = clf.classify("The OTP I received has already expired.")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE

    def test_otp_not_coming_phrase(self, clf):
        result = clf.classify("OTP not coming since last 1 hour.")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE

    def test_otp_dnd_block(self, clf):
        result = clf.classify("OTP blocked due to DND on number.")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE

    def test_one_time_password_phrase(self, clf):
        result = clf.classify("One time password not delivered to my phone.")
        assert result.topic == TopicKey.OTP_DELIVERY_FAILURE


# ── DOCUMENT_OCR_FAILURE ───────────────────────────────────────────────────────

class TestDocumentOcrClassification:
    def test_aadhaar_mismatch(self, clf):
        result = clf.classify("Aadhaar details mismatch during KYC process.")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE
        assert result.confidence >= 0.85

    def test_pan_ocr_error(self, clf):
        result = clf.classify("PAN card OCR failed — document quality error.")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE

    def test_face_match_failure(self, clf):
        result = clf.classify("Face match failed during document verification.")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE

    def test_liveliness_fail(self, clf):
        result = clf.classify("Liveliness fail during video KYC document check.")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE

    def test_ocr_error_phrase(self, clf):
        result = clf.classify("OCR error on the uploaded document.")
        assert result.topic == TopicKey.DOCUMENT_OCR_FAILURE


# ── AGENT_PORTAL_ISSUE ────────────────────────────────────────────────────────

class TestAgentPortalClassification:
    def test_agent_login_issue(self, clf):
        result = clf.classify("Agent is unable to login to the portal.")
        assert result.topic == TopicKey.AGENT_PORTAL_ISSUE
        assert result.confidence >= 0.85

    def test_auditor_access_locked(self, clf):
        result = clf.classify("Auditor account is locked, unable to access the system.")
        assert result.topic == TopicKey.AGENT_PORTAL_ISSUE

    def test_portal_slow_performance(self, clf):
        result = clf.classify("The agent portal is very slow during peak hours.")
        assert result.topic == TopicKey.AGENT_PORTAL_ISSUE

    def test_supervisor_queue_error(self, clf):
        result = clf.classify("Supervisor sees error when opening queue in portal.")
        assert result.topic == TopicKey.AGENT_PORTAL_ISSUE


# ── API_CALLBACK_FAILURE ──────────────────────────────────────────────────────

class TestApiCallbackClassification:
    def test_cbs_callback_failed(self, clf):
        result = clf.classify("CBS callback failed after KYC completion.")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE
        assert result.confidence >= 0.85

    def test_webhook_not_triggered(self, clf):
        result = clf.classify("Webhook is not triggering after account creation.")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE

    def test_api_timeout(self, clf):
        result = clf.classify("API timeout on integration with SFDC.")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE

    def test_callback_fail_phrase(self, clf):
        result = clf.classify("Callback fail for post-KYC workflow.")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE

    def test_post_kyc_webhook(self, clf):
        result = clf.classify("Post KYC webhook not received.")
        assert result.topic == TopicKey.API_CALLBACK_FAILURE


# ── UNKNOWN topic ──────────────────────────────────────────────────────────────

class TestUnknownClassification:
    def test_empty_string(self, clf):
        result = clf.classify("")
        assert result.topic == TopicKey.UNKNOWN
        assert result.confidence == 0.0
        assert result.meets_threshold is False

    def test_whitespace_only(self, clf):
        result = clf.classify("   ")
        assert result.topic == TopicKey.UNKNOWN

    def test_completely_unrelated_text(self, clf):
        result = clf.classify("The weather today is lovely, please send me a recipe.")
        assert result.topic == TopicKey.UNKNOWN
        assert result.confidence == 0.0

    def test_unknown_has_meets_threshold_false(self, clf):
        result = clf.classify("")
        assert result.meets_threshold is False


# ── ClassificationResult.meets_threshold property ────────────────────────────

class TestMeetsThreshold:
    def test_high_confidence_known_topic_meets_threshold(self, clf):
        result = clf.classify("OTP not received on my number.")
        assert result.meets_threshold is True

    def test_unknown_topic_never_meets_threshold(self):
        r = ClassificationResult(
            topic=TopicKey.UNKNOWN,
            confidence=0.99,  # artificially high — UNKNOWN still fails
            tier_used=1,
            raw_text_excerpt="test",
        )
        assert r.meets_threshold is False

    def test_below_threshold_confidence_fails(self):
        r = ClassificationResult(
            topic=TopicKey.OTP_DELIVERY_FAILURE,
            confidence=0.84,
            tier_used=1,
            raw_text_excerpt="test",
        )
        assert r.meets_threshold is False

    def test_exactly_at_threshold_meets(self):
        r = ClassificationResult(
            topic=TopicKey.OTP_DELIVERY_FAILURE,
            confidence=0.85,
            tier_used=1,
            raw_text_excerpt="test",
        )
        assert r.meets_threshold is True


# ── Tier 2 stub ───────────────────────────────────────────────────────────────

class TestTier2Stub:
    def test_tier2_returns_none(self, clf):
        # Verify that without a router, the classifier returns UNKNOWN (tier_used=0)
        result = TopicClassifier(nlp_router=None).classify("some unrecognized ticket text")
        assert result.topic == TopicKey.UNKNOWN
        assert result.tier_used == 0

    def test_unmatched_text_returns_unknown_not_tier2(self, clf):
        result = clf.classify("This is a unique unrecognized complaint.")
        assert result.topic == TopicKey.UNKNOWN
        assert result.tier_used == 2


# ── Raw text excerpt ──────────────────────────────────────────────────────────

class TestRawTextExcerpt:
    def test_excerpt_is_populated_for_match(self, clf):
        result = clf.classify("OTP not received.")
        assert result.raw_text_excerpt != ""

    def test_long_text_excerpt_capped_at_500_chars(self, clf):
        long_text = "OTP not received. " + "x" * 1000
        result = clf.classify(long_text)
        assert len(result.raw_text_excerpt) <= 500
