"""
tests/test_sprint221_e2e.py

Sprint 2.21: End-to-end pipeline tests.

Coverage:
  - Full pipeline: build_action_proposal_service → propose → JSONB round-trip
  - EXPIRED_SESSION produces RESET_SESSION as top proposal
  - KYC_REJECTED produces ESCALATE_L2 as top proposal
  - SOP correlation in full pipeline changes primary action
  - investigation_escalate=True always forces ESCALATE_L2
  - BLOCKED when investigation_result is None — no crash
  - proposal_count matches len(proposals) throughout round-trip
  - All action types in proposals have risk levels
  - requires_approval True when any REVERSIBLE proposal present
  - Full JSONB round-trip preserves all fields
"""
from __future__ import annotations

import json

import pytest

from case_engine.actions import build_action_proposal_service
from case_engine.actions.models import ProposedActionType, ProposalRiskLevel
from case_engine.workflows.models import WorkflowExecutionResult


# ── Helpers ───────────────────────────────────────────────────────────────────

def _svc():
    return build_action_proposal_service()


def _inv(
    category: str = "EXPIRED_SESSION",
    confidence: float = 0.85,
    escalate: bool = False,
) -> dict:
    return {
        "result_id": "r-001",
        "root_cause": {
            "category":   category,
            "confidence": confidence,
            "escalate":   escalate,
        },
    }


def _kr(recommended_action: str = "SESSION_RESET") -> dict:
    return {
        "sop_match_found": True,
        "recommendation": {"recommended_action": recommended_action},
        "sop_match": {"entry": {"entry_id": "sop-e-001"}},
    }


# ── Core topic scenarios ──────────────────────────────────────────────────────

class TestCoreScenarios:
    def test_expired_session_top_action_reset_session(self):
        r = _svc().propose("VKYC_Session_Failure", _inv("EXPIRED_SESSION"))
        assert r["top_proposal"]["action_type"] == "RESET_SESSION"

    def test_kyc_rejected_top_action_escalate_l2(self):
        r = _svc().propose("VKYC_KYC_Rejection", _inv("KYC_REJECTED"))
        assert r["top_proposal"]["action_type"] == "ESCALATE_L2"

    def test_sms_delivery_failure_top_action_resend_otp(self):
        r = _svc().propose("OTP_Delivery", _inv("SMS_DELIVERY_FAILURE"))
        assert r["top_proposal"]["action_type"] == "RESEND_OTP"

    def test_callback_failure_top_action_retry_callback(self):
        r = _svc().propose("Callback_Failure", _inv("CALLBACK_FAILURE"))
        assert r["top_proposal"]["action_type"] == "RETRY_CALLBACK"

    def test_portal_unavailable_top_action_check_server(self):
        r = _svc().propose("Portal_Issue", _inv("PORTAL_UNAVAILABLE"))
        assert r["top_proposal"]["action_type"] == "CHECK_SERVER_STATUS"


# ── SOP correlation e2e ───────────────────────────────────────────────────────

class TestSOPCorrelationE2E:
    def test_sop_session_reset_overrides_liveness_primary(self):
        r = _svc().propose(
            "VKYC_Liveness",
            _inv("LIVENESS_FAILURE"),
            knowledge_result=_kr("SESSION_RESET"),
        )
        assert r["top_proposal"]["action_type"] == "RESET_SESSION"

    def test_sop_otp_resend_overrides_primary(self):
        r = _svc().propose(
            "OTP_Delivery",
            _inv("REPEATED_FAILURE"),
            knowledge_result=_kr("OTP_RESEND"),
        )
        assert r["top_proposal"]["action_type"] == "RESEND_OTP"

    def test_sop_backed_flag_set_on_top_proposal(self):
        r = _svc().propose(
            "VKYC_Liveness",
            _inv("LIVENESS_FAILURE"),
            knowledge_result=_kr("SESSION_RESET"),
        )
        assert r["top_proposal"]["sop_backed"] is True

    def test_reasoning_source_sop_backed(self):
        r = _svc().propose(
            "VKYC_Liveness",
            _inv("LIVENESS_FAILURE"),
            knowledge_result=_kr("SESSION_RESET"),
        )
        assert r["reasoning"]["recommendation_source"] == "sop_backed"


# ── Escalation e2e ────────────────────────────────────────────────────────────

class TestEscalationE2E:
    def test_escalate_true_always_escalate_l2_first(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION", escalate=True))
        assert r["top_proposal"]["action_type"] == "ESCALATE_L2"

    def test_escalate_with_sop_still_escalate_l2_first(self):
        r = _svc().propose(
            "T",
            _inv("EXPIRED_SESSION", escalate=True),
            knowledge_result=_kr("SESSION_RESET"),
        )
        assert r["top_proposal"]["action_type"] == "ESCALATE_L2"

    def test_reasoning_source_investigation_escalate(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION", escalate=True))
        assert r["reasoning"]["recommendation_source"] == "investigation_escalate"


# ── BLOCKED guard e2e ─────────────────────────────────────────────────────────

class TestBlockedE2E:
    def test_no_investigation_result_returns_blocked(self):
        r = _svc().propose("T", investigation_result=None)
        assert r["status"] == "BLOCKED"

    def test_blocked_proposals_empty(self):
        r = _svc().propose("T", investigation_result=None)
        assert r["proposals"] == []

    def test_blocked_never_raises(self):
        r = _svc().propose("T", investigation_result=None)
        assert isinstance(r, dict)


# ── Bundle completeness ───────────────────────────────────────────────────────

class TestBundleCompleteness:
    def test_all_proposals_have_risk_assessment(self):
        r = _svc().propose("T", _inv("LIVENESS_FAILURE"))
        for p in r["proposals"]:
            assert "risk_assessment" in p
            assert "risk_level" in p["risk_assessment"]

    def test_proposal_count_matches_proposals_len(self):
        r = _svc().propose("T", _inv("LIVENESS_FAILURE"))
        assert r["proposal_count"] == len(r["proposals"])

    def test_requires_approval_true_for_reversible(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        assert r["requires_approval"] is True

    def test_requires_approval_false_for_all_safe(self):
        r = _svc().propose("T", _inv("SMS_DELIVERY_FAILURE"))
        assert r["requires_approval"] is False

    def test_all_safe_true_for_safe_only_proposals(self):
        r = _svc().propose("T", _inv("SMS_DELIVERY_FAILURE"))
        assert r["all_safe"] is True

    def test_all_safe_false_when_reset_session_present(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        assert r["all_safe"] is False

    def test_reasoning_confidence_stored(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION", confidence=0.72))
        assert abs(r["reasoning"]["investigation_confidence"] - 0.72) < 0.001


# ── JSONB round-trip ──────────────────────────────────────────────────────────

class TestJSONBRoundTrip:
    def test_full_round_trip_preserves_status(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        json_str = json.dumps(r)
        restored = json.loads(json_str)
        assert restored["status"] == "COMPLETED"

    def test_full_round_trip_preserves_bundle_id(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        restored = json.loads(json.dumps(r))
        assert restored["bundle_id"] == r["bundle_id"]

    def test_full_round_trip_preserves_proposal_count(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        restored = json.loads(json.dumps(r))
        assert restored["proposal_count"] == r["proposal_count"]

    def test_full_round_trip_preserves_top_proposal_action(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        restored = json.loads(json.dumps(r))
        assert restored["top_proposal"]["action_type"] == r["top_proposal"]["action_type"]

    def test_workflow_execution_result_stores_proposal_result(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        wer = WorkflowExecutionResult()
        wer.action_proposal_result = r
        d = wer.to_dict()
        assert d["action_proposal_result"]["status"] == "COMPLETED"

    def test_workflow_execution_result_round_trip_via_from_dict(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"))
        wer = WorkflowExecutionResult()
        wer.action_proposal_result = r
        d = wer.to_dict()
        json_str = json.dumps(d)
        restored_d = json.loads(json_str)
        wer2 = WorkflowExecutionResult.from_dict(restored_d)
        assert wer2.action_proposal_result["bundle_id"] == r["bundle_id"]


# ── Error resilience e2e ──────────────────────────────────────────────────────

class TestErrorResilienceE2E:
    def test_empty_topic_does_not_raise(self):
        r = _svc().propose("", _inv("EXPIRED_SESSION"))
        assert isinstance(r, dict)

    def test_unknown_root_cause_produces_proposals(self):
        r = _svc().propose("T", _inv("COMPLETELY_UNKNOWN_ROOT_CAUSE_XYZ"))
        assert r["proposal_count"] > 0

    def test_zero_confidence_produces_proposals(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION", confidence=0.0))
        assert r["proposal_count"] > 0

    def test_malformed_knowledge_result_does_not_crash(self):
        r = _svc().propose("T", _inv("EXPIRED_SESSION"), knowledge_result={"bad": "data"})
        assert isinstance(r, dict)
