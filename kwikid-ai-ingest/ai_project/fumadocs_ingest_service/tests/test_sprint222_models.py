"""
tests/test_sprint222_models.py

Sprint 2.22: Tests for case_engine/action_gateway/models.py

Coverage:
- GatewayRiskLevel enum values
- GatewayValidationStatus enum values
- GatewayApprovalStatus enum values
- ActionGatewayDecision: creation, is_valid, to_dict, from_dict
- GatewayApprovalDecision: creation, can_execute, is_pending, to_dict, from_dict
- ActionGatewayResult: creation, to_dict, from_dict, status values
- WorkflowExecutionResult.gateway_result field (Sprint 2.22)
- WorkflowStepType.ACTION_GATEWAY value
- AuditEventType Sprint 2.22 values
"""
import pytest

from case_engine.action_gateway.models import (
    ActionGatewayDecision,
    ActionGatewayResult,
    GatewayApprovalDecision,
    GatewayApprovalStatus,
    GatewayRiskLevel,
    GatewayValidationStatus,
)


# ── GatewayRiskLevel ──────────────────────────────────────────────────────────

class TestGatewayRiskLevel:
    def test_safe_value(self):
        assert GatewayRiskLevel.SAFE.value == "SAFE"

    def test_reversible_value(self):
        assert GatewayRiskLevel.REVERSIBLE.value == "REVERSIBLE"

    def test_high_risk_value(self):
        assert GatewayRiskLevel.HIGH_RISK.value == "HIGH_RISK"

    def test_three_values(self):
        assert len(GatewayRiskLevel) == 3

    def test_is_str_enum(self):
        assert isinstance(GatewayRiskLevel.SAFE, str)

    def test_equality_with_string(self):
        assert GatewayRiskLevel.SAFE == "SAFE"


# ── GatewayValidationStatus ───────────────────────────────────────────────────

class TestGatewayValidationStatus:
    def test_valid(self):
        assert GatewayValidationStatus.VALID.value == "VALID"

    def test_invalid(self):
        assert GatewayValidationStatus.INVALID.value == "INVALID"

    def test_blocked(self):
        assert GatewayValidationStatus.BLOCKED.value == "BLOCKED"

    def test_three_values(self):
        assert len(GatewayValidationStatus) == 3


# ── GatewayApprovalStatus ─────────────────────────────────────────────────────

class TestGatewayApprovalStatus:
    def test_approved(self):
        assert GatewayApprovalStatus.APPROVED.value == "APPROVED"

    def test_pending(self):
        assert GatewayApprovalStatus.PENDING.value == "PENDING"

    def test_rejected(self):
        assert GatewayApprovalStatus.REJECTED.value == "REJECTED"

    def test_bypassed(self):
        assert GatewayApprovalStatus.BYPASSED.value == "BYPASSED"

    def test_four_values(self):
        assert len(GatewayApprovalStatus) == 4


# ── ActionGatewayDecision ─────────────────────────────────────────────────────

def _make_valid_decision(**kwargs):
    defaults = dict(
        decision_id="dec-001",
        validation_status=GatewayValidationStatus.VALID,
        risk_level=GatewayRiskLevel.SAFE,
        requires_approval=False,
        validation_failures=(),
        bundle_id="bundle-001",
        top_action_type="RESET_SESSION",
        confidence_check_passed=True,
        investigation_check_passed=True,
        sop_check_passed=True,
        decided_at="2026-06-12T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return ActionGatewayDecision(**defaults)


class TestActionGatewayDecision:
    def test_is_frozen(self):
        d = _make_valid_decision()
        with pytest.raises((AttributeError, TypeError)):
            d.decision_id = "new"

    def test_is_valid_true(self):
        d = _make_valid_decision(validation_status=GatewayValidationStatus.VALID)
        assert d.is_valid() is True

    def test_is_valid_false_blocked(self):
        d = _make_valid_decision(validation_status=GatewayValidationStatus.BLOCKED)
        assert d.is_valid() is False

    def test_is_valid_false_invalid(self):
        d = _make_valid_decision(validation_status=GatewayValidationStatus.INVALID)
        assert d.is_valid() is False

    def test_to_dict_keys(self):
        d = _make_valid_decision()
        result = d.to_dict()
        assert "decision_id" in result
        assert "validation_status" in result
        assert "risk_level" in result
        assert "requires_approval" in result
        assert "validation_failures" in result
        assert "bundle_id" in result
        assert "top_action_type" in result
        assert "confidence_check_passed" in result
        assert "investigation_check_passed" in result
        assert "sop_check_passed" in result
        assert "decided_at" in result

    def test_to_dict_enum_serialized(self):
        d = _make_valid_decision()
        result = d.to_dict()
        assert result["validation_status"] == "VALID"
        assert result["risk_level"] == "SAFE"

    def test_to_dict_failures_is_list(self):
        d = _make_valid_decision(validation_failures=("err1", "err2"))
        assert d.to_dict()["validation_failures"] == ["err1", "err2"]

    def test_from_dict_roundtrip(self):
        d = _make_valid_decision()
        restored = ActionGatewayDecision.from_dict(d.to_dict())
        assert restored.decision_id == d.decision_id
        assert restored.validation_status == d.validation_status
        assert restored.risk_level == d.risk_level
        assert restored.requires_approval == d.requires_approval
        assert restored.bundle_id == d.bundle_id

    def test_from_dict_defaults(self):
        d = ActionGatewayDecision.from_dict({})
        assert d.validation_status == GatewayValidationStatus.BLOCKED
        assert d.risk_level == GatewayRiskLevel.HIGH_RISK
        assert d.requires_approval is True

    def test_blocked_decision_requires_approval(self):
        d = _make_valid_decision(
            validation_status=GatewayValidationStatus.BLOCKED,
            risk_level=GatewayRiskLevel.HIGH_RISK,
            requires_approval=True,
            validation_failures=("investigation_result_missing",),
        )
        assert d.requires_approval is True
        assert d.is_valid() is False


# ── GatewayApprovalDecision ───────────────────────────────────────────────────

def _make_approval_decision(**kwargs):
    defaults = dict(
        approval_id="appr-001",
        status=GatewayApprovalStatus.APPROVED,
        approver="auto_approval",
        reason="SAFE_action_auto_approved",
        notes=None,
        requires_human=False,
        decided_at="2026-06-12T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return GatewayApprovalDecision(**defaults)


class TestGatewayApprovalDecision:
    def test_is_frozen(self):
        d = _make_approval_decision()
        with pytest.raises((AttributeError, TypeError)):
            d.approval_id = "new"

    def test_can_execute_approved(self):
        d = _make_approval_decision(status=GatewayApprovalStatus.APPROVED)
        assert d.can_execute() is True

    def test_can_execute_pending_false(self):
        d = _make_approval_decision(status=GatewayApprovalStatus.PENDING)
        assert d.can_execute() is False

    def test_can_execute_rejected_false(self):
        d = _make_approval_decision(status=GatewayApprovalStatus.REJECTED)
        assert d.can_execute() is False

    def test_is_pending_true(self):
        d = _make_approval_decision(status=GatewayApprovalStatus.PENDING)
        assert d.is_pending() is True

    def test_is_pending_false_approved(self):
        d = _make_approval_decision(status=GatewayApprovalStatus.APPROVED)
        assert d.is_pending() is False

    def test_to_dict_enum_serialized(self):
        d = _make_approval_decision()
        result = d.to_dict()
        assert result["status"] == "APPROVED"

    def test_to_dict_keys(self):
        d = _make_approval_decision()
        result = d.to_dict()
        for key in ("approval_id", "status", "approver", "reason", "notes", "requires_human", "decided_at"):
            assert key in result

    def test_from_dict_roundtrip(self):
        d = _make_approval_decision()
        restored = GatewayApprovalDecision.from_dict(d.to_dict())
        assert restored.approval_id == d.approval_id
        assert restored.status == d.status
        assert restored.approver == d.approver

    def test_from_dict_defaults_pending(self):
        d = GatewayApprovalDecision.from_dict({})
        assert d.status == GatewayApprovalStatus.PENDING
        assert d.requires_human is True


# ── ActionGatewayResult ───────────────────────────────────────────────────────

def _make_result(**kwargs):
    gateway_decision = _make_valid_decision()
    approval_decision = _make_approval_decision()
    defaults = dict(
        result_id="res-001",
        status="APPROVED",
        gateway_decision=gateway_decision,
        approval_decision=approval_decision,
        risk_level=GatewayRiskLevel.SAFE,
        can_execute=True,
        block_reason=None,
        created_at="2026-06-12T00:00:00+00:00",
    )
    defaults.update(kwargs)
    return ActionGatewayResult(**defaults)


class TestActionGatewayResult:
    def test_is_frozen(self):
        r = _make_result()
        with pytest.raises((AttributeError, TypeError)):
            r.result_id = "new"

    def test_to_dict_keys(self):
        r = _make_result()
        d = r.to_dict()
        for key in ("result_id", "status", "gateway_decision", "approval_decision", "risk_level", "can_execute", "block_reason", "created_at"):
            assert key in d

    def test_to_dict_risk_level_serialized(self):
        r = _make_result()
        assert r.to_dict()["risk_level"] == "SAFE"

    def test_to_dict_nested_dicts(self):
        r = _make_result()
        d = r.to_dict()
        assert isinstance(d["gateway_decision"], dict)
        assert isinstance(d["approval_decision"], dict)

    def test_to_dict_none_approval(self):
        r = _make_result(approval_decision=None)
        assert r.to_dict()["approval_decision"] is None

    def test_status_approved(self):
        r = _make_result(status="APPROVED")
        assert r.to_dict()["status"] == "APPROVED"

    def test_status_pending_approval(self):
        r = _make_result(status="PENDING_APPROVAL", can_execute=False)
        assert r.to_dict()["status"] == "PENDING_APPROVAL"

    def test_status_blocked(self):
        r = _make_result(status="BLOCKED", can_execute=False, block_reason="no_investigation")
        assert r.to_dict()["status"] == "BLOCKED"
        assert r.to_dict()["block_reason"] == "no_investigation"

    def test_status_error(self):
        r = _make_result(status="ERROR", can_execute=False, block_reason="internal_error")
        assert r.to_dict()["status"] == "ERROR"

    def test_from_dict_roundtrip(self):
        r = _make_result()
        restored = ActionGatewayResult.from_dict(r.to_dict())
        assert restored.result_id == r.result_id
        assert restored.status == r.status
        assert restored.risk_level == r.risk_level
        assert restored.can_execute == r.can_execute

    def test_from_dict_defaults(self):
        r = ActionGatewayResult.from_dict({})
        assert r.status == "ERROR"
        assert r.can_execute is False


# ── WorkflowExecutionResult.gateway_result ────────────────────────────────────

class TestWorkflowExecutionResultGatewayResult:
    def test_gateway_result_default_none(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        assert r.gateway_result is None

    def test_gateway_result_set(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        r.gateway_result = {"status": "APPROVED"}
        assert r.gateway_result["status"] == "APPROVED"

    def test_to_dict_includes_gateway_result(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        r.gateway_result = {"status": "PENDING_APPROVAL", "risk_level": "REVERSIBLE"}
        d = r.to_dict()
        assert d["gateway_result"]["status"] == "PENDING_APPROVAL"

    def test_from_dict_restores_gateway_result(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        data = {"gateway_result": {"status": "BLOCKED", "block_reason": "no_investigation"}}
        r = WorkflowExecutionResult.from_dict(data)
        assert r.gateway_result["status"] == "BLOCKED"

    def test_from_dict_missing_gateway_result_is_none(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult.from_dict({})
        assert r.gateway_result is None

    def test_jsonb_roundtrip_with_all_fields(self):
        from case_engine.workflows.models import WorkflowExecutionResult
        r = WorkflowExecutionResult()
        r.action_proposal_result = {"status": "COMPLETED", "bundle_id": "b1"}
        r.gateway_result = {"status": "APPROVED", "can_execute": True}
        restored = WorkflowExecutionResult.from_dict(r.to_dict())
        assert restored.action_proposal_result["bundle_id"] == "b1"
        assert restored.gateway_result["status"] == "APPROVED"


# ── WorkflowStepType.ACTION_GATEWAY ──────────────────────────────────────────

class TestWorkflowStepTypeActionGateway:
    def test_action_gateway_exists(self):
        from case_engine.workflows.models import WorkflowStepType
        assert hasattr(WorkflowStepType, "ACTION_GATEWAY")

    def test_action_gateway_value(self):
        from case_engine.workflows.models import WorkflowStepType
        assert WorkflowStepType.ACTION_GATEWAY.value == "ACTION_GATEWAY"

    def test_nine_step_types(self):
        from case_engine.workflows.models import WorkflowStepType
        # Sprint 2.25 added CLARIFY — now 12 step types
        assert len(WorkflowStepType) == 12

    def test_action_gateway_is_str_enum(self):
        from case_engine.workflows.models import WorkflowStepType
        assert isinstance(WorkflowStepType.ACTION_GATEWAY, str)


# ── AuditEventType Sprint 2.22 values ─────────────────────────────────────────

class TestAuditEventTypeSprint222:
    @pytest.mark.parametrize("event_name", [
        "ACTION_GATEWAY_STARTED",
        "ACTION_GATEWAY_COMPLETED",
        "APPROVAL_REQUESTED",
        "APPROVAL_GRANTED",
        "APPROVAL_REJECTED",
    ])
    def test_event_type_exists(self, event_name: str):
        from case_engine.models import AuditEventType
        assert hasattr(AuditEventType, event_name)

    @pytest.mark.parametrize("event_name", [
        "ACTION_GATEWAY_STARTED",
        "ACTION_GATEWAY_COMPLETED",
        "APPROVAL_REQUESTED",
        "APPROVAL_GRANTED",
        "APPROVAL_REJECTED",
    ])
    def test_event_type_value(self, event_name: str):
        from case_engine.models import AuditEventType
        member = AuditEventType[event_name]
        assert member.value == event_name
