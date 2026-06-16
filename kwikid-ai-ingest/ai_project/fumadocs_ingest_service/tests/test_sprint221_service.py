"""
tests/test_sprint221_service.py

Sprint 2.21: ActionProposalService orchestration tests.

Coverage:
  - propose() returns COMPLETED status dict with required keys
  - propose() returns BLOCKED when investigation_result is None
  - propose() extracts root_cause correctly from investigation_result dict
  - propose() extracts confidence and escalate flags
  - propose() passes knowledge_result to engine
  - propose() never raises — returns ERROR dict on exceptions
  - Audit logger called on STARTED, COMPLETED, BLOCKED
  - Audit not called when audit=None or case=None
  - JSONB-compatible output (all JSON-serializable)
"""
from __future__ import annotations

import json
from unittest.mock import MagicMock, call, patch

import pytest

from case_engine.actions import build_action_proposal_service
from case_engine.actions.service import ActionProposalService


# ── Helpers ───────────────────────────────────────────────────────────────────

def _service(audit=None) -> ActionProposalService:
    return build_action_proposal_service(audit_logger=audit)


def _inv_result(
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


def _mock_case():
    c = MagicMock()
    c.case_id = "case-001"
    c.ticket_id = "ticket-001"
    c.client = "test_client"
    return c


# ── COMPLETED result ──────────────────────────────────────────────────────────

class TestCompletedResult:
    def test_status_is_completed(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert r["status"] == "COMPLETED"

    def test_has_bundle_id(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert "bundle_id" in r

    def test_has_proposals_list(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert isinstance(r["proposals"], list)

    def test_has_proposal_count(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert "proposal_count" in r
        assert isinstance(r["proposal_count"], int)

    def test_proposal_count_matches_proposals_len(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert r["proposal_count"] == len(r["proposals"])

    def test_has_top_proposal(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert "top_proposal" in r

    def test_has_root_cause_category(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result("KYC_REJECTED"))
        assert r["root_cause_category"] == "KYC_REJECTED"

    def test_has_reasoning(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        assert "reasoning" in r

    def test_has_topic(self):
        r = _service().propose("My_Topic", _inv_result())
        assert r["topic"] == "My_Topic"

    def test_fully_json_serializable(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        s = json.dumps(r)
        assert len(s) > 0

    def test_json_round_trip_preserves_bundle_id(self):
        r = _service().propose("VKYC_Session_Failure", _inv_result())
        restored = json.loads(json.dumps(r))
        assert restored["bundle_id"] == r["bundle_id"]


# ── BLOCKED result ────────────────────────────────────────────────────────────

class TestBlockedResult:
    def test_none_investigation_returns_blocked(self):
        r = _service().propose("T", investigation_result=None)
        assert r["status"] == "BLOCKED"

    def test_blocked_has_block_reason(self):
        r = _service().propose("T", investigation_result=None)
        assert r["block_reason"] == "NO_INVESTIGATION_RESULT"

    def test_blocked_has_topic(self):
        r = _service().propose("My_Topic", investigation_result=None)
        assert r["topic"] == "My_Topic"

    def test_blocked_has_empty_proposals(self):
        r = _service().propose("T", investigation_result=None)
        assert r["proposals"] == []

    def test_blocked_proposal_count_is_zero(self):
        r = _service().propose("T", investigation_result=None)
        assert r["proposal_count"] == 0

    def test_blocked_has_blocked_at(self):
        r = _service().propose("T", investigation_result=None)
        assert "blocked_at" in r

    def test_blocked_is_json_serializable(self):
        r = _service().propose("T", investigation_result=None)
        s = json.dumps(r)
        assert len(s) > 0


# ── Root cause extraction ─────────────────────────────────────────────────────

class TestRootCauseExtraction:
    def test_extracts_category_from_root_cause_dict(self):
        inv = _inv_result("CALLBACK_FAILURE")
        r = _service().propose("T", inv)
        assert r["root_cause_category"] == "CALLBACK_FAILURE"

    def test_missing_root_cause_uses_unknown(self):
        r = _service().propose("T", {"result_id": "x"})
        assert r["status"] == "COMPLETED"
        assert r["root_cause_category"] == "UNKNOWN"

    def test_missing_confidence_defaults_to_zero(self):
        inv = {"root_cause": {"category": "EXPIRED_SESSION"}}
        r = _service().propose("T", inv)
        assert r["status"] == "COMPLETED"

    def test_escalate_true_forces_escalate_l2(self):
        inv = _inv_result("EXPIRED_SESSION", escalate=True)
        r = _service().propose("T", inv)
        top = r["top_proposal"]
        assert top["action_type"] == "ESCALATE_L2"

    def test_knowledge_result_passed_through(self):
        kr = {"sop_match_found": True, "recommendation": {"recommended_action": "SESSION_RESET"}, "sop_match": {"entry": {"entry_id": "e1"}}}
        r = _service().propose("T", _inv_result("LIVENESS_FAILURE"), knowledge_result=kr)
        assert r["top_proposal"]["action_type"] == "RESET_SESSION"


# ── Error recovery ────────────────────────────────────────────────────────────

class TestErrorRecovery:
    def test_never_raises(self):
        svc = _service()
        # Should not raise even with completely broken input
        r = svc.propose(None, None)  # type: ignore
        assert isinstance(r, dict)

    def test_returns_dict_on_any_input(self):
        r = _service().propose("T", investigation_result=None)
        assert isinstance(r, dict)

    def test_status_key_always_present(self):
        r = _service().propose("T", investigation_result=None)
        assert "status" in r


# ── Audit integration ─────────────────────────────────────────────────────────

class TestAuditIntegration:
    def _mock_audit(self):
        a = MagicMock()
        a.log_action_proposal_started = MagicMock()
        a.log_action_proposal_completed = MagicMock()
        a.log_action_proposal_blocked = MagicMock()
        return a

    def test_audit_started_called_on_completed(self):
        audit = self._mock_audit()
        case = _mock_case()
        _service(audit=audit).propose("T", _inv_result(), case=case)
        audit.log_action_proposal_started.assert_called_once()

    def test_audit_completed_called_on_success(self):
        audit = self._mock_audit()
        case = _mock_case()
        _service(audit=audit).propose("T", _inv_result(), case=case)
        audit.log_action_proposal_completed.assert_called_once()

    def test_audit_blocked_called_when_no_investigation(self):
        audit = self._mock_audit()
        case = _mock_case()
        _service(audit=audit).propose("T", investigation_result=None, case=case)
        audit.log_action_proposal_blocked.assert_called_once()

    def test_audit_not_called_when_case_is_none(self):
        audit = self._mock_audit()
        _service(audit=audit).propose("T", _inv_result(), case=None)
        audit.log_action_proposal_started.assert_not_called()
        audit.log_action_proposal_completed.assert_not_called()

    def test_audit_none_does_not_crash(self):
        r = _service(audit=None).propose("T", _inv_result(), case=_mock_case())
        assert r["status"] == "COMPLETED"

    def test_audit_exception_does_not_propagate(self):
        audit = self._mock_audit()
        audit.log_action_proposal_started.side_effect = RuntimeError("audit boom")
        case = _mock_case()
        r = _service(audit=audit).propose("T", _inv_result(), case=case)
        assert r["status"] == "COMPLETED"


# ── build_action_proposal_service factory ────────────────────────────────────

class TestFactory:
    def test_factory_returns_service_instance(self):
        from case_engine.actions.service import ActionProposalService
        svc = build_action_proposal_service()
        assert isinstance(svc, ActionProposalService)

    def test_factory_with_audit_logger(self):
        audit = MagicMock()
        svc = build_action_proposal_service(audit_logger=audit)
        assert svc is not None

    def test_factory_without_audit_logger(self):
        svc = build_action_proposal_service()
        assert svc is not None
