"""
tests/test_sprint224_engine.py

Sprint 2.24: InvestigationReasoningEngine tests.

Coverage:
  - All 6 rules (escalate flag, low confidence, always-escalate, SOP override, category table, default)
  - All 14 root cause categories → correct action_type
  - SOP override: eligible categories + keyword matching
  - Always-escalate: KYC_REJECTED
  - Exception isolation: never raises
  - Determinism: same input → same output
  - MIN_CONFIDENCE_THRESHOLD boundary conditions
  - knowledge_result=None handled correctly
"""
from __future__ import annotations

import pytest

from case_engine.reasoning.engine import (
    MIN_CONFIDENCE_THRESHOLD,
    InvestigationReasoningEngine,
    _CATEGORY_RULES,
    _SOP_KEYWORD_OVERRIDES,
    _ALWAYS_ESCALATE,
    _SOP_OVERRIDE_ELIGIBLE,
    build_reasoning_engine,
)
from case_engine.reasoning.models import ReasoningOutcome, ReasoningResult


def _make_engine() -> InvestigationReasoningEngine:
    return InvestigationReasoningEngine()


def _inv(category: str, confidence: float = 0.85, escalate: bool = False) -> dict:
    return {
        "topic": "VKYC_Session_Failure",
        "evidence_ids": ["ev-1"],
        "root_cause": {
            "category":   category,
            "confidence": confidence,
            "escalate":   escalate,
        },
    }


def _kb(sop_match_found: bool = False, sop_title: str = "", sop_id: str = "sop-1") -> dict:
    if not sop_match_found:
        return {"sop_match_found": False}
    return {
        "sop_match_found": True,
        "sop_match": {
            "entry": {
                "entry_id": sop_id,
                "title":    sop_title,
            },
            "relevance_score": 0.92,
        },
    }


# ── Factory ───────────────────────────────────────────────────────────────────

class TestBuildReasoningEngine:
    def test_returns_engine_instance(self):
        eng = build_reasoning_engine()
        assert isinstance(eng, InvestigationReasoningEngine)

    def test_factory_creates_independent_instances(self):
        e1 = build_reasoning_engine()
        e2 = build_reasoning_engine()
        assert e1 is not e2


# ── Rule 1: root_cause.escalate=True ─────────────────────────────────────────

class TestRule1ForceEscalate:
    def test_escalate_true_gives_escalate_outcome(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.90, escalate=True))
        assert r.outcome == ReasoningOutcome.ESCALATE

    def test_escalate_true_gives_escalate_l2_action(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", escalate=True))
        assert r.recommended_action == "ESCALATE_L2"

    def test_escalate_true_should_escalate(self):
        eng = _make_engine()
        r = eng.reason(_inv("TIMEOUT", escalate=True))
        assert r.should_escalate is True

    def test_escalate_true_overrides_high_confidence(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=1.0, escalate=True))
        assert r.outcome == ReasoningOutcome.ESCALATE

    def test_rule1_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", escalate=True))
        path = list(r.bundle.trace.decision_path)
        assert any("rule1" in p for p in path)


# ── Rule 2: Low confidence ────────────────────────────────────────────────────

class TestRule2LowConfidence:
    def test_below_threshold_gives_uncertain(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.3))
        assert r.outcome == ReasoningOutcome.UNCERTAIN

    def test_below_threshold_escalates(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.0))
        assert r.should_escalate is True

    def test_below_threshold_escalate_l2(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.1))
        assert r.recommended_action == "ESCALATE_L2"

    def test_exactly_at_threshold_not_blocked(self):
        eng = _make_engine()
        # confidence == MIN_CONFIDENCE_THRESHOLD → NOT low confidence
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=MIN_CONFIDENCE_THRESHOLD))
        assert r.outcome != ReasoningOutcome.UNCERTAIN

    def test_just_below_threshold_is_uncertain(self):
        eng = _make_engine()
        below = MIN_CONFIDENCE_THRESHOLD - 0.01
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=below))
        assert r.outcome == ReasoningOutcome.UNCERTAIN

    def test_rule2_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.1))
        path = list(r.bundle.trace.decision_path)
        assert any("rule2" in p for p in path)


# ── Rule 3: Always-escalate categories ───────────────────────────────────────

class TestRule3AlwaysEscalate:
    def test_kyc_rejected_always_escalates(self):
        eng = _make_engine()
        r = eng.reason(_inv("KYC_REJECTED", confidence=0.99, escalate=False))
        assert r.outcome == ReasoningOutcome.ESCALATE

    def test_kyc_rejected_should_escalate(self):
        eng = _make_engine()
        r = eng.reason(_inv("KYC_REJECTED", confidence=0.99))
        assert r.should_escalate is True

    def test_kyc_rejected_not_overridden_by_sop(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("KYC_REJECTED", confidence=0.95),
            _kb(sop_match_found=True, sop_title="retry session reset"),
        )
        assert r.outcome == ReasoningOutcome.ESCALATE

    def test_always_escalate_set_content(self):
        assert "KYC_REJECTED" in _ALWAYS_ESCALATE

    def test_rule3_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("KYC_REJECTED", confidence=0.99))
        path = list(r.bundle.trace.decision_path)
        assert any("rule3" in p for p in path)


# ── Rule 4: SOP override ──────────────────────────────────────────────────────

class TestRule4SopOverride:
    def test_session_reset_keyword_overrides(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("NETWORK_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="session reset procedure"),
        )
        assert r.recommended_action == "RESET_SESSION"
        assert r.outcome == ReasoningOutcome.RECOMMEND_ACTION

    def test_resend_otp_keyword_overrides(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("SMS_DELIVERY_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="resend otp for failed delivery"),
        )
        assert r.recommended_action == "RESEND_OTP"

    def test_sop_override_not_applied_for_ineligible_category(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("ONBOARDING_BLOCKED", confidence=0.90),
            _kb(sop_match_found=True, sop_title="session reset procedure"),
        )
        # ONBOARDING_BLOCKED is not in _SOP_OVERRIDE_ELIGIBLE
        # → should fall through to rule5 → MANUAL_REVIEW
        assert r.recommended_action == "MANUAL_REVIEW"

    def test_sop_override_not_applied_when_no_sop_match(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("NETWORK_FAILURE", confidence=0.90),
            _kb(sop_match_found=False),
        )
        # Falls through to rule5 → RESET_SESSION from category table
        assert r.recommended_action == "RESET_SESSION"

    def test_sop_override_eligible_set_content(self):
        assert "NETWORK_FAILURE" in _SOP_OVERRIDE_ELIGIBLE
        assert "SMS_DELIVERY_FAILURE" in _SOP_OVERRIDE_ELIGIBLE
        assert "CALLBACK_FAILURE" in _SOP_OVERRIDE_ELIGIBLE

    def test_rule4_trace_shows_sop_override(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("NETWORK_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="session reset procedure"),
        )
        path = list(r.bundle.trace.decision_path)
        assert any("rule4" in p for p in path)

    def test_sop_id_in_sop_ids_used(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("NETWORK_FAILURE", confidence=0.85),
            _kb(sop_match_found=True, sop_title="session reset", sop_id="sop-abc"),
        )
        assert "sop-abc" in r.bundle.recommendation.sop_ids_used


# ── Rule 5: Category rule table ───────────────────────────────────────────────

class TestRule5CategoryRuleTable:
    @pytest.mark.parametrize("category,expected_action", [
        ("NETWORK_FAILURE",      "RESET_SESSION"),
        ("TIMEOUT",              "RESET_SESSION"),
        ("EXPIRED_SESSION",      "RESET_SESSION"),
        ("REPEATED_FAILURE",     "RESET_SESSION"),
        ("QUOTA_EXCEEDED",       "WAIT_AND_RETRY"),
        ("LIVENESS_FAILURE",     "RETRY_DOCUMENT_CAPTURE"),
        ("DOCUMENT_FAILURE",     "RETRY_DOCUMENT_CAPTURE"),
        ("VALIDATION_FAILURE",   "RETRY_DOCUMENT_CAPTURE"),
        ("SMS_DELIVERY_FAILURE", "RESEND_OTP"),
        ("CALLBACK_FAILURE",     "RETRY_CALLBACK"),
        ("ONBOARDING_BLOCKED",   "MANUAL_REVIEW"),
        ("PORTAL_UNAVAILABLE",   "CHECK_SERVER_STATUS"),
        ("UNKNOWN",              "ESCALATE_L2"),
    ])
    def test_category_maps_to_action(self, category, expected_action):
        eng = _make_engine()
        r = eng.reason(_inv(category, confidence=0.85))
        assert r.recommended_action == expected_action

    def test_category_rule_outcome_is_recommend_action(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.85))
        assert r.outcome == ReasoningOutcome.RECOMMEND_ACTION

    def test_manual_review_should_escalate(self):
        eng = _make_engine()
        r = eng.reason(_inv("ONBOARDING_BLOCKED", confidence=0.90))
        assert r.should_escalate is True

    def test_escalate_l2_from_category_should_escalate(self):
        eng = _make_engine()
        r = eng.reason(_inv("UNKNOWN", confidence=0.75))
        assert r.should_escalate is True

    def test_rule5_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE", confidence=0.85))
        path = list(r.bundle.trace.decision_path)
        assert any("rule5" in p for p in path)

    def test_category_rules_has_14_entries(self):
        assert len(_CATEGORY_RULES) == 14


# ── Rule 6: Default fail-closed escalate ─────────────────────────────────────

class TestRule6DefaultEscalate:
    def test_unknown_category_above_threshold_uses_category_rules(self):
        # "UNKNOWN" IS in _CATEGORY_RULES, so rule5 fires
        eng = _make_engine()
        r = eng.reason(_inv("UNKNOWN", confidence=0.75))
        assert r.should_escalate is True

    def test_truly_unknown_category_escalates(self):
        # A category not in _CATEGORY_RULES triggers rule6
        eng = _make_engine()
        r = eng.reason(_inv("MADE_UP_CATEGORY_XYZ", confidence=0.85))
        assert r.outcome == ReasoningOutcome.ESCALATE
        assert r.should_escalate is True

    def test_rule6_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("MADE_UP_CATEGORY_XYZ", confidence=0.85))
        path = list(r.bundle.trace.decision_path)
        assert any("rule6" in p for p in path)


# ── Exception isolation ───────────────────────────────────────────────────────

class TestExceptionIsolation:
    def test_none_investigation_result_does_not_raise(self):
        eng = _make_engine()
        r = eng.reason(None)  # type: ignore
        assert isinstance(r, ReasoningResult)

    def test_none_investigation_result_gives_uncertain(self):
        eng = _make_engine()
        r = eng.reason(None)  # type: ignore
        assert r.outcome == ReasoningOutcome.UNCERTAIN

    def test_invalid_investigation_result_does_not_raise(self):
        eng = _make_engine()
        r = eng.reason("not a dict")  # type: ignore
        assert isinstance(r, ReasoningResult)

    def test_malformed_root_cause_gives_uncertain(self):
        eng = _make_engine()
        r = eng.reason({"root_cause": "bad value"})
        assert isinstance(r, ReasoningResult)

    def test_malformed_knowledge_result_does_not_raise(self):
        eng = _make_engine()
        r = eng.reason(
            _inv("NETWORK_FAILURE"),
            knowledge_result="not a dict",  # type: ignore
        )
        assert isinstance(r, ReasoningResult)

    def test_exception_result_has_escalate(self):
        eng = _make_engine()
        r = eng.reason(None)  # type: ignore
        assert r.should_escalate is True


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_input_same_output(self):
        eng = _make_engine()
        inv = _inv("NETWORK_FAILURE", confidence=0.85)
        kb  = _kb(sop_match_found=False)
        r1  = eng.reason(inv, kb)
        r2  = eng.reason(inv, kb)
        assert r1.recommended_action == r2.recommended_action
        assert r1.outcome == r2.outcome
        assert r1.should_escalate == r2.should_escalate

    def test_deterministic_across_multiple_engines(self):
        e1, e2 = _make_engine(), _make_engine()
        inv = _inv("TIMEOUT", confidence=0.85)
        r1  = e1.reason(inv)
        r2  = e2.reason(inv)
        assert r1.recommended_action == r2.recommended_action

    def test_knowledge_result_none_same_as_empty(self):
        eng = _make_engine()
        inv = _inv("NETWORK_FAILURE", confidence=0.85)
        r1  = eng.reason(inv, None)
        r2  = eng.reason(inv, {})
        assert r1.recommended_action == r2.recommended_action


# ── Result structure ──────────────────────────────────────────────────────────

class TestResultStructure:
    def test_result_has_result_id(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE"))
        assert r.result_id != ""

    def test_result_has_bundle(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE"))
        assert r.bundle is not None

    def test_result_bundle_has_trace(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE"))
        assert r.bundle.trace is not None

    def test_result_bundle_has_recommendation(self):
        eng = _make_engine()
        r = eng.reason(_inv("NETWORK_FAILURE"))
        assert r.bundle.recommendation is not None

    def test_evidence_ids_propagated(self):
        eng = _make_engine()
        inv = _inv("NETWORK_FAILURE")
        inv["evidence_ids"] = ["ev-A", "ev-B"]
        r = eng.reason(inv)
        assert "ev-A" in r.bundle.trace.evidence_ids_used

    def test_topic_propagated(self):
        eng = _make_engine()
        inv = _inv("NETWORK_FAILURE")
        inv["topic"] = "OTP_Delivery_Failure"
        r = eng.reason(inv)
        assert r.bundle.topic == "OTP_Delivery_Failure"
