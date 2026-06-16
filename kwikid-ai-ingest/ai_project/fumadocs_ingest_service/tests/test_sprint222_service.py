"""
tests/test_sprint222_service.py

Sprint 2.22: Tests for case_engine/action_gateway/service.py

Coverage:
- ActionGatewayService.process: SAFE->APPROVED, REVERSIBLE->PENDING, HIGH_RISK->PENDING
- BLOCKED path: no proposal, bad proposal
- ERROR path: service never raises
- build_action_gateway_service factory
- Audit events emitted correctly
- Audit events not emitted when case is None
- Audit exception isolation (audit errors don't propagate)
- Result dict structure / JSONB-compatible
- HIGH_RISK guardrail: can_execute False
"""
import pytest
from unittest.mock import MagicMock, call, patch

from case_engine.action_gateway.service import ActionGatewayService, build_action_gateway_service
from case_engine.action_gateway.models import GatewayRiskLevel


def _case(case_id: str = "case-001") -> MagicMock:
    c = MagicMock()
    c.case_id = case_id
    c.ticket_id = "ticket-001"
    c.client = "test_client"
    return c


def _investigation(confidence: float = 0.9) -> dict:
    return {
        "status": "COMPLETED",
        "root_cause": {"category": "TIMEOUT", "confidence": confidence, "escalate": False},
    }


def _proposal(risk_level: str = "SAFE", action_type: str = "RESET_SESSION") -> dict:
    return {
        "status": "COMPLETED",
        "bundle_id": "bundle-001",
        "top_proposal": {
            "action_type": action_type,
            "risk_assessment": {"risk_level": risk_level, "requires_approval": risk_level != "SAFE"},
        },
    }


@pytest.fixture
def svc() -> ActionGatewayService:
    return build_action_gateway_service()


# ── Factory ───────────────────────────────────────────────────────────────────

class TestFactory:
    def test_returns_service(self):
        svc = build_action_gateway_service()
        assert isinstance(svc, ActionGatewayService)

    def test_with_audit_logger(self):
        audit = MagicMock()
        svc = build_action_gateway_service(audit_logger=audit)
        assert svc._audit is audit


# ── APPROVED path (SAFE) ──────────────────────────────────────────────────────

class TestApprovedPath:
    def test_safe_returns_approved(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["status"] == "APPROVED"

    def test_safe_can_execute(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["can_execute"] is True

    def test_safe_no_block_reason(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["block_reason"] is None

    def test_safe_risk_level(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["risk_level"] == "SAFE"

    def test_safe_result_has_approval_decision(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["approval_decision"] is not None

    def test_safe_approval_decision_approved(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["approval_decision"]["status"] == "APPROVED"

    def test_safe_result_id_set(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert result["result_id"] is not None


# ── PENDING_APPROVAL path (REVERSIBLE) ───────────────────────────────────────

class TestPendingApprovalPath:
    def test_reversible_returns_pending_approval(self, svc):
        result = svc.process("topic", _proposal("REVERSIBLE"), _investigation(), {})
        assert result["status"] == "PENDING_APPROVAL"

    def test_reversible_cannot_execute(self, svc):
        result = svc.process("topic", _proposal("REVERSIBLE"), _investigation(), {})
        assert result["can_execute"] is False

    def test_reversible_risk_level(self, svc):
        result = svc.process("topic", _proposal("REVERSIBLE"), _investigation(), {})
        assert result["risk_level"] == "REVERSIBLE"

    def test_reversible_approval_decision_pending(self, svc):
        result = svc.process("topic", _proposal("REVERSIBLE"), _investigation(), {})
        assert result["approval_decision"]["status"] == "PENDING"


# ── PENDING_APPROVAL path (HIGH_RISK guardrail) ───────────────────────────────

class TestHighRiskGuardrail:
    def test_high_risk_returns_pending_approval(self, svc):
        result = svc.process("topic", _proposal("HIGH_RISK", "UNKNOWN_ACTION"), _investigation(), {})
        assert result["status"] == "PENDING_APPROVAL"

    def test_high_risk_cannot_execute(self, svc):
        """GUARDRAIL: HIGH_RISK can_execute MUST be False."""
        result = svc.process("topic", _proposal("HIGH_RISK"), _investigation(), {})
        assert result["can_execute"] is False

    def test_high_risk_risk_level(self, svc):
        result = svc.process("topic", _proposal("HIGH_RISK"), _investigation(), {})
        assert result["risk_level"] == "HIGH_RISK"


# ── BLOCKED path ──────────────────────────────────────────────────────────────

class TestBlockedPath:
    def test_no_proposal_returns_blocked(self, svc):
        result = svc.process("topic", None, _investigation(), {})
        assert result["status"] == "BLOCKED"

    def test_empty_proposal_returns_blocked(self, svc):
        result = svc.process("topic", {}, _investigation(), {})
        assert result["status"] == "BLOCKED"

    def test_blocked_cannot_execute(self, svc):
        result = svc.process("topic", None, _investigation(), {})
        assert result["can_execute"] is False

    def test_blocked_has_block_reason(self, svc):
        result = svc.process("topic", None, _investigation(), {})
        assert result["block_reason"] is not None
        assert len(result["block_reason"]) > 0

    def test_no_investigation_returns_blocked(self, svc):
        result = svc.process("topic", _proposal(), None, {})
        assert result["status"] == "BLOCKED"

    def test_bad_proposal_status_returns_blocked(self, svc):
        p = {"status": "FAILED", "bundle_id": "b1", "top_proposal": {"action_type": "RESET_SESSION"}}
        result = svc.process("topic", p, _investigation(), {})
        assert result["status"] == "BLOCKED"

    def test_blocked_risk_level_high_risk(self, svc):
        result = svc.process("topic", None, None, None)
        assert result["risk_level"] == "HIGH_RISK"


# ── ERROR path ────────────────────────────────────────────────────────────────

class TestErrorPath:
    def test_process_never_raises(self, svc):
        """process() must never raise under any circumstances."""
        try:
            result = svc.process(None, None, None, None)  # type: ignore
            assert "status" in result
        except Exception as e:
            pytest.fail(f"ActionGatewayService.process raised: {e}")

    def test_internal_error_returns_error_or_blocked(self):
        """If internal components crash, process returns ERROR or BLOCKED."""
        mock_gw = MagicMock(side_effect=RuntimeError("boom"))
        svc = ActionGatewayService(proposal_gateway=mock_gw)
        result = svc.process("topic", _proposal(), _investigation(), {})
        assert result["status"] in ("ERROR", "BLOCKED")
        assert result["can_execute"] is False


# ── Result dict structure ─────────────────────────────────────────────────────

class TestResultStructure:
    def test_result_has_all_keys(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        for key in ("result_id", "status", "gateway_decision", "approval_decision",
                    "risk_level", "can_execute", "block_reason", "created_at"):
            assert key in result, f"Missing key: {key}"

    def test_gateway_decision_is_dict(self, svc):
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        assert isinstance(result["gateway_decision"], dict)

    def test_result_is_json_serializable(self, svc):
        import json
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {})
        json.dumps(result)  # should not raise


# ── Audit events ──────────────────────────────────────────────────────────────

class TestAuditEvents:
    def test_gateway_started_emitted_with_case(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=case)
        audit.log_action_gateway_started.assert_called_once()

    def test_gateway_completed_emitted_with_case(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=case)
        audit.log_action_gateway_completed.assert_called_once()

    def test_approval_granted_emitted_for_safe(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=case)
        audit.log_approval_granted.assert_called_once()

    def test_approval_requested_emitted_for_reversible(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("REVERSIBLE"), _investigation(), {}, case=case)
        audit.log_approval_requested.assert_called_once()

    def test_approval_requested_emitted_for_high_risk(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("HIGH_RISK"), _investigation(), {}, case=case)
        audit.log_approval_requested.assert_called_once()

    def test_no_audit_when_no_case(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=None)
        audit.log_action_gateway_started.assert_not_called()
        audit.log_action_gateway_completed.assert_not_called()

    def test_audit_exception_does_not_propagate(self):
        audit = MagicMock()
        audit.log_action_gateway_started.side_effect = RuntimeError("audit exploded")
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        result = svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=case)
        assert result["status"] == "APPROVED"

    def test_workflow_id_and_step_id_passed_to_audit(self):
        audit = MagicMock()
        svc = ActionGatewayService(audit_logger=audit)
        case = _case()
        svc.process("topic", _proposal("SAFE"), _investigation(), {}, case=case,
                    workflow_id="wf-001", step_id="step-gateway")
        call_kwargs = audit.log_action_gateway_started.call_args
        assert call_kwargs.kwargs["workflow_id"] == "wf-001"
        assert call_kwargs.kwargs["step_id"] == "step-gateway"
