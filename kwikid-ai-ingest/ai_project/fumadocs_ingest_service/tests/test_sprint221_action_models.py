"""
tests/test_sprint221_action_models.py

Sprint 2.21: Domain model tests.

Coverage:
  - ProposedActionType enum values and count
  - ProposalRiskLevel enum values
  - ActionRiskAssessment: construction, to_dict, from_dict
  - ActionReasoning: construction, to_dict, from_dict
  - ActionProposalItem: construction, to_dict, from_dict
  - ActionProposalBundle: construction, to_dict, from_dict
  - WorkflowExecutionResult.action_proposal_result field
  - JSONB round-trip for WorkflowExecutionResult with action_proposal_result
  - All fields JSON-serializable
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from case_engine.actions.models import (
    ActionProposalBundle,
    ActionProposalItem,
    ActionReasoning,
    ActionRiskAssessment,
    ProposedActionType,
    ProposalRiskLevel,
)
from case_engine.models import AuditEventType
from case_engine.workflows.models import WorkflowExecutionResult


# ── ProposedActionType ────────────────────────────────────────────────────────

class TestProposedActionType:
    def test_ask_user_retry_exists(self):
        assert hasattr(ProposedActionType, "ASK_USER_RETRY")

    def test_wait_and_retry_exists(self):
        assert hasattr(ProposedActionType, "WAIT_AND_RETRY")

    def test_resend_otp_exists(self):
        assert hasattr(ProposedActionType, "RESEND_OTP")

    def test_retry_document_capture_exists(self):
        assert hasattr(ProposedActionType, "RETRY_DOCUMENT_CAPTURE")

    def test_reset_session_exists(self):
        assert hasattr(ProposedActionType, "RESET_SESSION")

    def test_retry_callback_exists(self):
        assert hasattr(ProposedActionType, "RETRY_CALLBACK")

    def test_check_server_status_exists(self):
        assert hasattr(ProposedActionType, "CHECK_SERVER_STATUS")

    def test_refresh_portal_exists(self):
        assert hasattr(ProposedActionType, "REFRESH_PORTAL")

    def test_manual_review_exists(self):
        assert hasattr(ProposedActionType, "MANUAL_REVIEW")

    def test_escalate_l2_exists(self):
        assert hasattr(ProposedActionType, "ESCALATE_L2")

    def test_create_asana_ticket_exists(self):
        assert hasattr(ProposedActionType, "CREATE_ASANA_TICKET")

    def test_unknown_action_exists(self):
        assert hasattr(ProposedActionType, "UNKNOWN_ACTION")

    def test_is_str_enum(self):
        assert isinstance(ProposedActionType.RESET_SESSION, str)
        assert ProposedActionType.RESET_SESSION == "RESET_SESSION"

    def test_minimum_12_action_types(self):
        assert len(ProposedActionType) >= 12


# ── ProposalRiskLevel ─────────────────────────────────────────────────────────

class TestProposalRiskLevel:
    def test_safe_exists(self):
        assert ProposalRiskLevel.SAFE.value == "SAFE"

    def test_reversible_exists(self):
        assert ProposalRiskLevel.REVERSIBLE.value == "REVERSIBLE"

    def test_high_risk_exists(self):
        assert ProposalRiskLevel.HIGH_RISK.value == "HIGH_RISK"

    def test_is_str_enum(self):
        assert isinstance(ProposalRiskLevel.SAFE, str)

    def test_three_levels(self):
        assert len(ProposalRiskLevel) == 3


# ── ActionRiskAssessment ──────────────────────────────────────────────────────

class TestActionRiskAssessment:
    def _make(self) -> ActionRiskAssessment:
        return ActionRiskAssessment(
            assessment_id="a-001",
            action_type=ProposedActionType.RESET_SESSION,
            risk_level=ProposalRiskLevel.REVERSIBLE,
            requires_approval=True,
            risk_reason="Session reset terminates active session",
            assessed_at="2024-01-01T00:00:00+00:00",
        )

    def test_construction(self):
        a = self._make()
        assert a.action_type == ProposedActionType.RESET_SESSION

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for key in ("assessment_id", "action_type", "risk_level", "requires_approval", "risk_reason", "assessed_at"):
            assert key in d

    def test_to_dict_action_type_is_string(self):
        d = self._make().to_dict()
        assert isinstance(d["action_type"], str)
        assert d["action_type"] == "RESET_SESSION"

    def test_to_dict_risk_level_is_string(self):
        d = self._make().to_dict()
        assert d["risk_level"] == "REVERSIBLE"

    def test_from_dict_round_trip(self):
        d = self._make().to_dict()
        restored = ActionRiskAssessment.from_dict(d)
        assert restored.action_type == ProposedActionType.RESET_SESSION
        assert restored.risk_level == ProposalRiskLevel.REVERSIBLE

    def test_json_serializable(self):
        s = json.dumps(self._make().to_dict())
        assert len(s) > 0

    def test_frozen(self):
        a = self._make()
        with pytest.raises((AttributeError, TypeError)):
            a.risk_level = ProposalRiskLevel.SAFE  # type: ignore


# ── ActionReasoning ───────────────────────────────────────────────────────────

class TestActionReasoning:
    def _make(self) -> ActionReasoning:
        return ActionReasoning(
            reasoning_id="r-001",
            root_cause_category="EXPIRED_SESSION",
            investigation_confidence=0.85,
            sop_match_found=True,
            sop_entry_id="e-001",
            recommendation_source="sop_backed",
            explanation="SOP match found; RESET_SESSION promoted to primary",
            created_at="2024-01-01T00:00:00+00:00",
        )

    def test_construction(self):
        r = self._make()
        assert r.root_cause_category == "EXPIRED_SESSION"

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for key in ("reasoning_id", "root_cause_category", "investigation_confidence",
                    "sop_match_found", "sop_entry_id", "recommendation_source", "explanation", "created_at"):
            assert key in d

    def test_from_dict_round_trip(self):
        d = self._make().to_dict()
        restored = ActionReasoning.from_dict(d)
        assert restored.root_cause_category == "EXPIRED_SESSION"
        assert restored.sop_match_found is True
        assert restored.sop_entry_id == "e-001"

    def test_from_dict_empty_dict(self):
        r = ActionReasoning.from_dict({})
        assert r.root_cause_category == "UNKNOWN"

    def test_json_serializable(self):
        s = json.dumps(self._make().to_dict())
        assert len(s) > 0


# ── ActionProposalItem ────────────────────────────────────────────────────────

class TestActionProposalItem:
    def _make_risk(self) -> ActionRiskAssessment:
        return ActionRiskAssessment(
            assessment_id="a-001",
            action_type=ProposedActionType.RESET_SESSION,
            risk_level=ProposalRiskLevel.REVERSIBLE,
            requires_approval=True,
            risk_reason="Session reset",
            assessed_at="2024-01-01T00:00:00+00:00",
        )

    def _make(self) -> ActionProposalItem:
        return ActionProposalItem(
            item_id="i-001",
            action_type=ProposedActionType.RESET_SESSION,
            priority=1,
            rationale="Root cause is expired session",
            risk_assessment=self._make_risk(),
            sop_backed=True,
            created_at="2024-01-01T00:00:00+00:00",
        )

    def test_construction(self):
        item = self._make()
        assert item.action_type == ProposedActionType.RESET_SESSION

    def test_to_dict_has_risk_assessment(self):
        d = self._make().to_dict()
        assert "risk_assessment" in d
        assert isinstance(d["risk_assessment"], dict)

    def test_to_dict_action_type_string(self):
        d = self._make().to_dict()
        assert d["action_type"] == "RESET_SESSION"

    def test_to_dict_priority(self):
        d = self._make().to_dict()
        assert d["priority"] == 1

    def test_from_dict_round_trip(self):
        d = self._make().to_dict()
        restored = ActionProposalItem.from_dict(d)
        assert restored.action_type == ProposedActionType.RESET_SESSION
        assert restored.sop_backed is True
        assert restored.risk_assessment.risk_level == ProposalRiskLevel.REVERSIBLE

    def test_json_serializable(self):
        s = json.dumps(self._make().to_dict())
        assert len(s) > 0

    def test_frozen(self):
        item = self._make()
        with pytest.raises((AttributeError, TypeError)):
            item.priority = 2  # type: ignore


# ── ActionProposalBundle ──────────────────────────────────────────────────────

class TestActionProposalBundle:
    def _make_risk(self, action_type=ProposedActionType.RESET_SESSION, risk=ProposalRiskLevel.REVERSIBLE):
        return ActionRiskAssessment(
            assessment_id="a-001",
            action_type=action_type,
            risk_level=risk,
            requires_approval=risk != ProposalRiskLevel.SAFE,
            risk_reason="test",
            assessed_at="2024-01-01T00:00:00+00:00",
        )

    def _make_item(self, action_type=ProposedActionType.RESET_SESSION, priority=1):
        return ActionProposalItem(
            item_id=f"i-{priority}",
            action_type=action_type,
            priority=priority,
            rationale="test rationale",
            risk_assessment=self._make_risk(action_type),
            sop_backed=False,
            created_at="2024-01-01T00:00:00+00:00",
        )

    def _make_reasoning(self):
        return ActionReasoning(
            reasoning_id="r-001",
            root_cause_category="EXPIRED_SESSION",
            investigation_confidence=0.85,
            sop_match_found=False,
            sop_entry_id=None,
            recommendation_source="rule_based",
            explanation="rule-based",
            created_at="2024-01-01T00:00:00+00:00",
        )

    def _make(self) -> ActionProposalBundle:
        item1 = self._make_item(ProposedActionType.RESET_SESSION, 1)
        item2 = self._make_item(ProposedActionType.ASK_USER_RETRY, 2)
        return ActionProposalBundle(
            bundle_id="b-001",
            topic="VKYC_Session_Failure",
            root_cause_category="EXPIRED_SESSION",
            proposals=(item1, item2),
            reasoning=self._make_reasoning(),
            top_proposal=item1,
            all_safe=False,
            requires_approval=True,
            proposal_count=2,
            created_at="2024-01-01T00:00:00+00:00",
        )

    def test_construction(self):
        b = self._make()
        assert b.topic == "VKYC_Session_Failure"

    def test_proposal_count(self):
        b = self._make()
        assert b.proposal_count == 2
        assert len(b.proposals) == 2

    def test_top_proposal(self):
        b = self._make()
        assert b.top_proposal is not None
        assert b.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_to_dict_keys(self):
        d = self._make().to_dict()
        for key in ("bundle_id", "topic", "root_cause_category", "proposals",
                    "reasoning", "top_proposal", "all_safe", "requires_approval",
                    "proposal_count", "created_at"):
            assert key in d

    def test_to_dict_proposals_is_list(self):
        d = self._make().to_dict()
        assert isinstance(d["proposals"], list)
        assert len(d["proposals"]) == 2

    def test_from_dict_round_trip(self):
        d = self._make().to_dict()
        restored = ActionProposalBundle.from_dict(d)
        assert restored.bundle_id == "b-001"
        assert restored.topic == "VKYC_Session_Failure"
        assert len(restored.proposals) == 2
        assert restored.top_proposal.action_type == ProposedActionType.RESET_SESSION

    def test_json_serializable(self):
        s = json.dumps(self._make().to_dict())
        assert len(s) > 0

    def test_from_dict_empty_proposals(self):
        b = ActionProposalBundle.from_dict({"proposals": []})
        assert b.proposal_count == 0


# ── WorkflowExecutionResult integration ──────────────────────────────────────

class TestWorkflowExecutionResultActionProposal:
    def test_default_action_proposal_result_is_none(self):
        r = WorkflowExecutionResult()
        assert r.action_proposal_result is None

    def test_can_set_action_proposal_result(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-001", "status": "COMPLETED"}
        assert r.action_proposal_result["bundle_id"] == "b-001"

    def test_to_dict_includes_action_proposal_result(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-001"}
        d = r.to_dict()
        assert "action_proposal_result" in d
        assert d["action_proposal_result"]["bundle_id"] == "b-001"

    def test_from_dict_restores_action_proposal_result(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-001", "status": "COMPLETED"}
        d = r.to_dict()
        r2 = WorkflowExecutionResult.from_dict(d)
        assert r2.action_proposal_result is not None
        assert r2.action_proposal_result["bundle_id"] == "b-001"

    def test_from_dict_missing_key_defaults_to_none(self):
        d = WorkflowExecutionResult().to_dict()
        del d["action_proposal_result"]
        r = WorkflowExecutionResult.from_dict(d)
        assert r.action_proposal_result is None

    def test_jsonb_full_round_trip(self):
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"bundle_id": "b-001", "proposals": [], "status": "COMPLETED"}
        json_str = json.dumps(r.to_dict())
        restored_dict = json.loads(json_str)
        r2 = WorkflowExecutionResult.from_dict(restored_dict)
        assert r2.action_proposal_result["bundle_id"] == "b-001"


# ── AuditEventType new values ─────────────────────────────────────────────────

class TestAuditEventTypeActionProposal:
    def test_action_proposal_started_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_STARTED")
        assert AuditEventType.ACTION_PROPOSAL_STARTED.value == "ACTION_PROPOSAL_STARTED"

    def test_action_proposal_completed_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_COMPLETED")
        assert AuditEventType.ACTION_PROPOSAL_COMPLETED.value == "ACTION_PROPOSAL_COMPLETED"

    def test_action_proposal_blocked_exists(self):
        assert hasattr(AuditEventType, "ACTION_PROPOSAL_BLOCKED")
        assert AuditEventType.ACTION_PROPOSAL_BLOCKED.value == "ACTION_PROPOSAL_BLOCKED"

    def test_risk_assessment_completed_exists(self):
        assert hasattr(AuditEventType, "RISK_ASSESSMENT_COMPLETED")
        assert AuditEventType.RISK_ASSESSMENT_COMPLETED.value == "RISK_ASSESSMENT_COMPLETED"
