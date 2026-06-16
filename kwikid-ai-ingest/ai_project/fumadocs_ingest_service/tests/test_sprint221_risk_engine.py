"""
tests/test_sprint221_risk_engine.py

Sprint 2.21: RiskAssessmentEngine tests.

Coverage:
  - All 6 SAFE action types classified correctly
  - All 5 REVERSIBLE action types classified correctly (plus MANUAL_REVIEW, ESCALATE_L2, CREATE_ASANA_TICKET)
  - UNKNOWN_ACTION classified as HIGH_RISK
  - requires_approval False for SAFE, True for REVERSIBLE/HIGH_RISK
  - assess_bundle returns list of assessments in order
  - highest_risk picks the correct maximum
  - any_requires_approval returns True/False correctly
  - Never raises on unknown action type
  - Default HIGH_RISK for unclassified types
"""
from __future__ import annotations

import pytest

from case_engine.actions.models import (
    ActionRiskAssessment,
    ProposedActionType,
    ProposalRiskLevel,
)
from case_engine.actions.risk import RiskAssessmentEngine


# ── Helpers ───────────────────────────────────────────────────────────────────

def _engine() -> RiskAssessmentEngine:
    return RiskAssessmentEngine()


def _assess(action_type: ProposedActionType) -> ActionRiskAssessment:
    return _engine().assess(action_type)


# ── SAFE action types ─────────────────────────────────────────────────────────

class TestSafeActions:
    @pytest.mark.parametrize("action_type", [
        ProposedActionType.ASK_USER_RETRY,
        ProposedActionType.WAIT_AND_RETRY,
        ProposedActionType.RESEND_OTP,
        ProposedActionType.RETRY_DOCUMENT_CAPTURE,
        ProposedActionType.CHECK_SERVER_STATUS,
        ProposedActionType.REFRESH_PORTAL,
    ])
    def test_classified_as_safe(self, action_type):
        a = _assess(action_type)
        assert a.risk_level == ProposalRiskLevel.SAFE

    @pytest.mark.parametrize("action_type", [
        ProposedActionType.ASK_USER_RETRY,
        ProposedActionType.WAIT_AND_RETRY,
        ProposedActionType.RESEND_OTP,
        ProposedActionType.RETRY_DOCUMENT_CAPTURE,
        ProposedActionType.CHECK_SERVER_STATUS,
        ProposedActionType.REFRESH_PORTAL,
    ])
    def test_does_not_require_approval(self, action_type):
        a = _assess(action_type)
        assert a.requires_approval is False

    def test_ask_user_retry_safe(self):
        assert _assess(ProposedActionType.ASK_USER_RETRY).risk_level == ProposalRiskLevel.SAFE

    def test_resend_otp_safe(self):
        assert _assess(ProposedActionType.RESEND_OTP).risk_level == ProposalRiskLevel.SAFE

    def test_retry_document_capture_safe(self):
        assert _assess(ProposedActionType.RETRY_DOCUMENT_CAPTURE).risk_level == ProposalRiskLevel.SAFE

    def test_check_server_status_safe(self):
        assert _assess(ProposedActionType.CHECK_SERVER_STATUS).risk_level == ProposalRiskLevel.SAFE

    def test_refresh_portal_safe(self):
        assert _assess(ProposedActionType.REFRESH_PORTAL).risk_level == ProposalRiskLevel.SAFE

    def test_wait_and_retry_safe(self):
        assert _assess(ProposedActionType.WAIT_AND_RETRY).risk_level == ProposalRiskLevel.SAFE


# ── REVERSIBLE action types ───────────────────────────────────────────────────

class TestReversibleActions:
    @pytest.mark.parametrize("action_type", [
        ProposedActionType.RESET_SESSION,
        ProposedActionType.RETRY_CALLBACK,
        ProposedActionType.MANUAL_REVIEW,
        ProposedActionType.ESCALATE_L2,
        ProposedActionType.CREATE_ASANA_TICKET,
    ])
    def test_classified_as_reversible(self, action_type):
        a = _assess(action_type)
        assert a.risk_level == ProposalRiskLevel.REVERSIBLE

    @pytest.mark.parametrize("action_type", [
        ProposedActionType.RESET_SESSION,
        ProposedActionType.RETRY_CALLBACK,
        ProposedActionType.MANUAL_REVIEW,
        ProposedActionType.ESCALATE_L2,
        ProposedActionType.CREATE_ASANA_TICKET,
    ])
    def test_requires_approval(self, action_type):
        a = _assess(action_type)
        assert a.requires_approval is True

    def test_reset_session_reversible(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        assert a.risk_level == ProposalRiskLevel.REVERSIBLE

    def test_escalate_l2_reversible(self):
        a = _assess(ProposedActionType.ESCALATE_L2)
        assert a.risk_level == ProposalRiskLevel.REVERSIBLE


# ── HIGH_RISK action types ────────────────────────────────────────────────────

class TestHighRiskActions:
    def test_unknown_action_high_risk(self):
        a = _assess(ProposedActionType.UNKNOWN_ACTION)
        assert a.risk_level == ProposalRiskLevel.HIGH_RISK

    def test_unknown_action_requires_approval(self):
        a = _assess(ProposedActionType.UNKNOWN_ACTION)
        assert a.requires_approval is True


# ── ActionRiskAssessment shape ────────────────────────────────────────────────

class TestAssessmentShape:
    def test_assessment_has_assessment_id(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        assert a.assessment_id and len(a.assessment_id) > 0

    def test_assessment_id_is_uuid(self):
        import re
        a = _assess(ProposedActionType.RESET_SESSION)
        assert re.match(r"^[0-9a-f-]{36}$", a.assessment_id)

    def test_assessment_has_action_type(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        assert a.action_type == ProposedActionType.RESET_SESSION

    def test_assessment_has_risk_reason(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        assert isinstance(a.risk_reason, str) and len(a.risk_reason) > 0

    def test_assessment_has_assessed_at(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        assert isinstance(a.assessed_at, str) and len(a.assessed_at) > 0

    def test_assessment_is_frozen(self):
        a = _assess(ProposedActionType.RESET_SESSION)
        with pytest.raises((AttributeError, TypeError)):
            a.risk_level = ProposalRiskLevel.SAFE  # type: ignore


# ── assess_bundle ─────────────────────────────────────────────────────────────

class TestAssessBundle:
    def test_empty_list_returns_empty(self):
        assert _engine().assess_bundle([]) == []

    def test_returns_list_same_length(self):
        types = [ProposedActionType.RESET_SESSION, ProposedActionType.ASK_USER_RETRY]
        results = _engine().assess_bundle(types)
        assert len(results) == 2

    def test_order_preserved(self):
        types = [ProposedActionType.RESET_SESSION, ProposedActionType.ASK_USER_RETRY]
        results = _engine().assess_bundle(types)
        assert results[0].action_type == ProposedActionType.RESET_SESSION
        assert results[1].action_type == ProposedActionType.ASK_USER_RETRY

    def test_single_element_bundle(self):
        results = _engine().assess_bundle([ProposedActionType.RESEND_OTP])
        assert len(results) == 1
        assert results[0].risk_level == ProposalRiskLevel.SAFE


# ── highest_risk ──────────────────────────────────────────────────────────────

class TestHighestRisk:
    def _assessments(self, *action_types):
        return [_assess(at) for at in action_types]

    def test_empty_returns_safe(self):
        assert _engine().highest_risk([]) == ProposalRiskLevel.SAFE

    def test_all_safe_returns_safe(self):
        result = _engine().highest_risk(self._assessments(
            ProposedActionType.ASK_USER_RETRY, ProposedActionType.RESEND_OTP,
        ))
        assert result == ProposalRiskLevel.SAFE

    def test_mixed_safe_reversible_returns_reversible(self):
        result = _engine().highest_risk(self._assessments(
            ProposedActionType.ASK_USER_RETRY, ProposedActionType.RESET_SESSION,
        ))
        assert result == ProposalRiskLevel.REVERSIBLE

    def test_high_risk_dominates(self):
        result = _engine().highest_risk(self._assessments(
            ProposedActionType.RESET_SESSION, ProposedActionType.UNKNOWN_ACTION,
        ))
        assert result == ProposalRiskLevel.HIGH_RISK

    def test_single_safe(self):
        result = _engine().highest_risk(self._assessments(ProposedActionType.RESEND_OTP))
        assert result == ProposalRiskLevel.SAFE


# ── any_requires_approval ─────────────────────────────────────────────────────

class TestAnyRequiresApproval:
    def test_empty_returns_false(self):
        assert _engine().any_requires_approval([]) is False

    def test_all_safe_returns_false(self):
        assessments = [_assess(ProposedActionType.ASK_USER_RETRY)]
        assert _engine().any_requires_approval(assessments) is False

    def test_one_reversible_returns_true(self):
        assessments = [
            _assess(ProposedActionType.ASK_USER_RETRY),
            _assess(ProposedActionType.RESET_SESSION),
        ]
        assert _engine().any_requires_approval(assessments) is True

    def test_all_reversible_returns_true(self):
        assessments = [_assess(ProposedActionType.ESCALATE_L2)]
        assert _engine().any_requires_approval(assessments) is True
