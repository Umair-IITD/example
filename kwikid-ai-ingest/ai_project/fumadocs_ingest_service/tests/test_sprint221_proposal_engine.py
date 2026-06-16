"""
tests/test_sprint221_proposal_engine.py

Sprint 2.21: ActionProposalEngine tests.

Coverage:
  - All 14 root cause categories produce correct primary action
  - SOP correlation promotes SOP action to priority 1
  - investigation_escalate forces ESCALATE_L2 to priority 1
  - Unknown root cause falls back to MANUAL_REVIEW / ESCALATE_L2
  - All proposals have risk assessments attached
  - Bundle metadata fields (all_safe, requires_approval, proposal_count)
  - recommendation_source is set correctly
  - Engine never raises on bad input
  - Fallback bundle returned on internal error
"""
from __future__ import annotations

import pytest

from case_engine.actions.models import (
    ActionProposalBundle,
    ProposedActionType,
    ProposalRiskLevel,
)
from case_engine.actions.proposal import ActionProposalEngine
from case_engine.actions.risk import RiskAssessmentEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine() -> ActionProposalEngine:
    return ActionProposalEngine(risk_engine=RiskAssessmentEngine())


def _propose(
    root_cause: str,
    confidence: float = 0.8,
    escalate: bool = False,
    knowledge_result: dict | None = None,
    topic: str = "VKYC_Session_Failure",
) -> ActionProposalBundle:
    return _engine().propose(
        topic=topic,
        root_cause_category=root_cause,
        investigation_confidence=confidence,
        investigation_escalate=escalate,
        knowledge_result=knowledge_result,
    )


# ── Root cause → primary action mapping ──────────────────────────────────────

class TestProposalRules:
    def test_network_failure_primary_reset_session(self):
        b = _propose("NETWORK_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_timeout_primary_reset_session(self):
        b = _propose("TIMEOUT")
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_timeout_has_wait_and_retry(self):
        b = _propose("TIMEOUT")
        types = [p.action_type for p in b.proposals]
        assert ProposedActionType.WAIT_AND_RETRY in types

    def test_quota_exceeded_primary_wait_and_retry(self):
        b = _propose("QUOTA_EXCEEDED")
        assert b.top_proposal.action_type == ProposedActionType.WAIT_AND_RETRY

    def test_quota_exceeded_has_escalate_l2(self):
        b = _propose("QUOTA_EXCEEDED")
        types = [p.action_type for p in b.proposals]
        assert ProposedActionType.ESCALATE_L2 in types

    def test_expired_session_primary_reset_session(self):
        b = _propose("EXPIRED_SESSION")
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_repeated_failure_primary_reset_session(self):
        b = _propose("REPEATED_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_liveness_failure_primary_retry_document_capture(self):
        b = _propose("LIVENESS_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RETRY_DOCUMENT_CAPTURE

    def test_liveness_failure_has_escalate_l2(self):
        b = _propose("LIVENESS_FAILURE")
        types = [p.action_type for p in b.proposals]
        assert ProposedActionType.ESCALATE_L2 in types

    def test_document_failure_primary_retry_document_capture(self):
        b = _propose("DOCUMENT_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RETRY_DOCUMENT_CAPTURE

    def test_validation_failure_primary_retry_document_capture(self):
        b = _propose("VALIDATION_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RETRY_DOCUMENT_CAPTURE

    def test_kyc_rejected_primary_escalate_l2(self):
        b = _propose("KYC_REJECTED")
        assert b.top_proposal.action_type == ProposedActionType.ESCALATE_L2

    def test_sms_delivery_failure_primary_resend_otp(self):
        b = _propose("SMS_DELIVERY_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RESEND_OTP

    def test_callback_failure_primary_retry_callback(self):
        b = _propose("CALLBACK_FAILURE")
        assert b.top_proposal.action_type == ProposedActionType.RETRY_CALLBACK

    def test_onboarding_blocked_primary_manual_review(self):
        b = _propose("ONBOARDING_BLOCKED")
        assert b.top_proposal.action_type == ProposedActionType.MANUAL_REVIEW

    def test_portal_unavailable_primary_check_server_status(self):
        b = _propose("PORTAL_UNAVAILABLE")
        assert b.top_proposal.action_type == ProposedActionType.CHECK_SERVER_STATUS

    def test_unknown_root_cause_fallback_to_unknown_rule(self):
        b = _propose("UNKNOWN")
        assert b.top_proposal.action_type == ProposedActionType.MANUAL_REVIEW

    def test_completely_unknown_category_uses_fallback(self):
        b = _propose("FOOBAR_XYZ_NONEXISTENT")
        assert b.top_proposal is not None
        assert b.proposal_count > 0


# ── Priority ordering ─────────────────────────────────────────────────────────

class TestProposalPriority:
    def test_proposals_numbered_starting_at_1(self):
        b = _propose("EXPIRED_SESSION")
        for i, item in enumerate(b.proposals, start=1):
            assert item.priority == i

    def test_top_proposal_matches_priority_1(self):
        b = _propose("EXPIRED_SESSION")
        p1 = next(p for p in b.proposals if p.priority == 1)
        assert b.top_proposal.action_type == p1.action_type


# ── SOP correlation ───────────────────────────────────────────────────────────

class TestSOPCorrelation:
    def _kr(self, recommended_action: str, sop_match_found: bool = True) -> dict:
        return {
            "sop_match_found": sop_match_found,
            "recommendation": {"recommended_action": recommended_action},
            "sop_match": {
                "entry": {"entry_id": "sop-e-001"},
            },
        }

    def test_sop_session_reset_promotes_to_priority_1(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_sop_otp_resend_promotes_resend_otp(self):
        b = _propose("SMS_DELIVERY_FAILURE", knowledge_result=self._kr("OTP_RESEND"))
        assert b.top_proposal.action_type == ProposedActionType.RESEND_OTP

    def test_sop_portal_refresh_promotes_refresh_portal(self):
        b = _propose("PORTAL_UNAVAILABLE", knowledge_result=self._kr("PORTAL_REFRESH"))
        assert b.top_proposal.action_type == ProposedActionType.REFRESH_PORTAL

    def test_sop_callback_retry_promotes_retry_callback(self):
        b = _propose("CALLBACK_FAILURE", knowledge_result=self._kr("CALLBACK_RETRY"))
        assert b.top_proposal.action_type == ProposedActionType.RETRY_CALLBACK

    def test_sop_manual_review_promotes_manual_review(self):
        b = _propose("UNKNOWN", knowledge_result=self._kr("MANUAL_REVIEW"))
        assert b.top_proposal.action_type == ProposedActionType.MANUAL_REVIEW

    def test_sop_escalate_promotes_escalate_l2(self):
        b = _propose("KYC_REJECTED", knowledge_result=self._kr("ESCALATE"))
        assert b.top_proposal.action_type == ProposedActionType.ESCALATE_L2

    def test_sop_not_found_does_not_change_primary(self):
        b_no_sop = _propose("EXPIRED_SESSION")
        b_with_sop = _propose("EXPIRED_SESSION", knowledge_result={"sop_match_found": False})
        assert b_no_sop.top_proposal.action_type == b_with_sop.top_proposal.action_type

    def test_sop_backed_sets_reasoning_source(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        assert b.reasoning.recommendation_source == "sop_backed"

    def test_sop_backed_sets_sop_match_found(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        assert b.reasoning.sop_match_found is True

    def test_sop_backed_stores_entry_id(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        assert b.reasoning.sop_entry_id == "sop-e-001"

    def test_sop_backed_item_flag_set(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        top = b.top_proposal
        assert top.sop_backed is True

    def test_non_sop_items_not_flagged(self):
        b = _propose("LIVENESS_FAILURE", knowledge_result=self._kr("SESSION_RESET"))
        non_sop = [p for p in b.proposals if p.action_type != ProposedActionType.RESET_SESSION]
        for item in non_sop:
            assert item.sop_backed is False


# ── Investigation escalate override ──────────────────────────────────────────

class TestEscalateOverride:
    def test_escalate_true_forces_escalate_l2_primary(self):
        b = _propose("EXPIRED_SESSION", escalate=True)
        assert b.top_proposal.action_type == ProposedActionType.ESCALATE_L2

    def test_escalate_overrides_sop_recommendation(self):
        kr = {"sop_match_found": True, "recommendation": {"recommended_action": "SESSION_RESET"}, "sop_match": {"entry": {"entry_id": "e1"}}}
        b = _propose("EXPIRED_SESSION", escalate=True, knowledge_result=kr)
        assert b.top_proposal.action_type == ProposedActionType.ESCALATE_L2

    def test_escalate_sets_recommendation_source(self):
        b = _propose("EXPIRED_SESSION", escalate=True)
        assert b.reasoning.recommendation_source == "investigation_escalate"


# ── Bundle metadata ───────────────────────────────────────────────────────────

class TestBundleMetadata:
    def test_bundle_id_is_uuid_string(self):
        import re
        b = _propose("EXPIRED_SESSION")
        assert re.match(r"^[0-9a-f-]{36}$", b.bundle_id)

    def test_proposal_count_matches_proposals_length(self):
        b = _propose("EXPIRED_SESSION")
        assert b.proposal_count == len(b.proposals)

    def test_all_safe_false_when_session_reset_present(self):
        b = _propose("EXPIRED_SESSION")
        assert b.all_safe is False

    def test_all_safe_true_when_only_safe_actions(self):
        b = _propose("SMS_DELIVERY_FAILURE")
        # RESEND_OTP and ASK_USER_RETRY are both SAFE
        assert b.all_safe is True

    def test_requires_approval_true_when_reversible_action(self):
        b = _propose("EXPIRED_SESSION")
        assert b.requires_approval is True

    def test_requires_approval_false_for_all_safe(self):
        b = _propose("SMS_DELIVERY_FAILURE")
        assert b.requires_approval is False

    def test_reasoning_confidence_stored(self):
        b = _propose("EXPIRED_SESSION", confidence=0.75)
        assert b.reasoning.investigation_confidence == pytest.approx(0.75)

    def test_reasoning_root_cause_stored(self):
        b = _propose("EXPIRED_SESSION")
        assert b.reasoning.root_cause_category == "EXPIRED_SESSION"

    def test_all_proposals_have_risk_assessments(self):
        b = _propose("LIVENESS_FAILURE")
        for item in b.proposals:
            assert item.risk_assessment is not None

    def test_topic_stored_in_bundle(self):
        b = _propose("EXPIRED_SESSION", topic="My_Topic")
        assert b.topic == "My_Topic"

    def test_rule_based_source_when_no_sop(self):
        b = _propose("EXPIRED_SESSION")
        assert b.reasoning.recommendation_source == "rule_based"


# ── Error recovery ────────────────────────────────────────────────────────────

class TestErrorRecovery:
    def test_never_raises_on_none_topic(self):
        b = _engine().propose(
            topic=None,  # type: ignore
            root_cause_category="EXPIRED_SESSION",
            investigation_confidence=0.8,
            investigation_escalate=False,
        )
        assert b is not None

    def test_never_raises_on_none_root_cause(self):
        b = _engine().propose(
            topic="T",
            root_cause_category=None,  # type: ignore
            investigation_confidence=0.8,
            investigation_escalate=False,
        )
        assert b is not None

    def test_never_raises_on_malformed_knowledge_result(self):
        b = _engine().propose(
            topic="T",
            root_cause_category="EXPIRED_SESSION",
            investigation_confidence=0.8,
            investigation_escalate=False,
            knowledge_result={"sop_match_found": True, "recommendation": None},
        )
        assert b is not None

    def test_returns_bundle_type_on_any_input(self):
        b = _engine().propose(
            topic="", root_cause_category="", investigation_confidence=0.0,
            investigation_escalate=False,
        )
        assert isinstance(b, ActionProposalBundle)
