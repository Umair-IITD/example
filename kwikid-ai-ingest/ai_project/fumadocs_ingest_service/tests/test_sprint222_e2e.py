"""
tests/test_sprint222_e2e.py

Sprint 2.22: End-to-end pipeline tests for ACTIONPROPOSAL -> ACTIONGW -> RISKCHECK -> APPROVAL.

Coverage:
- Full pipeline: proposal -> gateway -> approved (SAFE)
- Full pipeline: proposal -> gateway -> pending (REVERSIBLE)
- Full pipeline: proposal -> gateway -> pending (HIGH_RISK guardrail)
- Full pipeline: blocked at ACTIONGW (no investigation)
- Full pipeline: blocked at ACTIONGW (proposal not COMPLETED)
- JSONB round-trip for gateway_result in WorkflowExecutionResult
- ProposalGateway + GatewayRiskEngine + ApprovalEngine composed correctly
- All 5 main KwikID topics handled
- Determinism: same input always produces same output
"""
import pytest

from case_engine.action_gateway.service import build_action_gateway_service
from case_engine.workflows.models import WorkflowExecutionResult


def _base_investigation(category: str = "TIMEOUT", confidence: float = 0.9) -> dict:
    return {
        "status": "COMPLETED",
        "bundle_id": "inv-001",
        "root_cause": {
            "category": category,
            "confidence": confidence,
            "escalate": False,
        },
    }


def _base_proposal(
    action_type: str = "RESET_SESSION",
    risk_level: str = "REVERSIBLE",
    bundle_id: str = "bundle-001",
) -> dict:
    return {
        "status": "COMPLETED",
        "bundle_id": bundle_id,
        "top_proposal": {
            "action_type": action_type,
            "risk_assessment": {
                "risk_level": risk_level,
                "requires_approval": risk_level != "SAFE",
            },
        },
        "proposals": [],
        "all_safe": risk_level == "SAFE",
        "requires_approval": risk_level != "SAFE",
    }


def _knowledge(sop: bool = True) -> dict:
    return {"sop_match_found": sop, "result_id": "k-001"}


@pytest.fixture
def svc():
    return build_action_gateway_service()


# ── SAFE path (auto-approved) ─────────────────────────────────────────────────

class TestSafePipeline:
    @pytest.mark.parametrize("action_type", [
        "ASK_USER_RETRY",
        "WAIT_AND_RETRY",
        "RESEND_OTP",
        "RETRY_DOCUMENT_CAPTURE",
        "CHECK_SERVER_STATUS",
        "REFRESH_PORTAL",
    ])
    def test_safe_actions_approved(self, svc, action_type):
        proposal = _base_proposal(action_type=action_type, risk_level="SAFE")
        result = svc.process("OTP_Delivery_Failure", proposal, _base_investigation(), _knowledge())
        assert result["status"] == "APPROVED"
        assert result["can_execute"] is True
        assert result["risk_level"] == "SAFE"

    def test_safe_approval_decision_approved(self, svc):
        proposal = _base_proposal(action_type="CHECK_SERVER_STATUS", risk_level="SAFE")
        result = svc.process("Agent_Portal_Issue", proposal, _base_investigation(), {})
        assert result["approval_decision"]["status"] == "APPROVED"
        assert result["approval_decision"]["approver"] == "auto_approval"


# ── REVERSIBLE path (pending approval) ───────────────────────────────────────

class TestReversiblePipeline:
    @pytest.mark.parametrize("action_type", [
        "RESET_SESSION",
        "RETRY_CALLBACK",
        "MANUAL_REVIEW",
        "ESCALATE_L2",
        "CREATE_ASANA_TICKET",
    ])
    def test_reversible_actions_pending(self, svc, action_type):
        proposal = _base_proposal(action_type=action_type, risk_level="REVERSIBLE")
        result = svc.process("VKYC_Session_Failure", proposal, _base_investigation(), _knowledge())
        assert result["status"] == "PENDING_APPROVAL"
        assert result["can_execute"] is False
        assert result["risk_level"] == "REVERSIBLE"

    def test_reversible_approval_decision_pending(self, svc):
        proposal = _base_proposal(action_type="RESET_SESSION", risk_level="REVERSIBLE")
        result = svc.process("VKYC_Session_Failure", proposal, _base_investigation(), {})
        assert result["approval_decision"]["status"] == "PENDING"
        assert result["approval_decision"]["requires_human"] is True


# ── HIGH_RISK guardrail ───────────────────────────────────────────────────────

class TestHighRiskGuardrail:
    def test_high_risk_is_pending(self, svc):
        proposal = _base_proposal(action_type="UNKNOWN_ACTION", risk_level="HIGH_RISK")
        result = svc.process("VKYC_Session_Failure", proposal, _base_investigation(), {})
        assert result["status"] == "PENDING_APPROVAL"
        assert result["can_execute"] is False

    def test_high_risk_approval_decision_pending(self, svc):
        proposal = _base_proposal(action_type="UNKNOWN_ACTION", risk_level="HIGH_RISK")
        result = svc.process("topic", proposal, _base_investigation(), {})
        assert result["approval_decision"]["status"] == "PENDING"
        assert result["approval_decision"]["requires_human"] is True

    def test_high_risk_never_approved(self, svc):
        """Mechanical guarantee: HIGH_RISK is never auto-approved."""
        for i in range(3):
            proposal = _base_proposal(action_type="UNKNOWN_ACTION", risk_level="HIGH_RISK",
                                       bundle_id=f"b-{i}")
            result = svc.process("topic", proposal, _base_investigation(), {})
            assert result["status"] != "APPROVED", f"HIGH_RISK was approved on iteration {i}"


# ── BLOCKED path ──────────────────────────────────────────────────────────────

class TestBlockedPipeline:
    def test_no_investigation_blocked(self, svc):
        result = svc.process("topic", _base_proposal(), None, {})
        assert result["status"] == "BLOCKED"
        assert result["can_execute"] is False

    def test_no_proposal_blocked(self, svc):
        result = svc.process("topic", None, _base_investigation(), {})
        assert result["status"] == "BLOCKED"

    def test_proposal_not_completed_blocked(self, svc):
        p = {**_base_proposal(), "status": "BLOCKED"}
        result = svc.process("topic", p, _base_investigation(), {})
        assert result["status"] == "BLOCKED"

    def test_low_confidence_investigation_blocked(self, svc):
        result = svc.process("topic", _base_proposal(), _base_investigation(confidence=0.1), {})
        assert result["status"] == "BLOCKED"

    def test_blocked_has_gateway_decision(self, svc):
        result = svc.process("topic", None, None, None)
        assert result["gateway_decision"] is not None

    def test_blocked_block_reason_present(self, svc):
        result = svc.process("topic", None, None, None)
        assert result["block_reason"] is not None


# ── JSONB round-trip ──────────────────────────────────────────────────────────

class TestJsonbRoundtrip:
    def test_gateway_result_stores_in_workflow_execution_result(self, svc):
        proposal = _base_proposal(risk_level="SAFE")
        gateway_result = svc.process("topic", proposal, _base_investigation(), _knowledge())

        wer = WorkflowExecutionResult(workflow_id="wf-001")
        wer.action_proposal_result = proposal
        wer.gateway_result = gateway_result

        d = wer.to_dict()
        restored = WorkflowExecutionResult.from_dict(d)

        assert restored.gateway_result["status"] == "APPROVED"
        assert restored.gateway_result["risk_level"] == "SAFE"
        assert restored.action_proposal_result["bundle_id"] == "bundle-001"

    def test_pending_approval_survives_jsonb_roundtrip(self, svc):
        proposal = _base_proposal(risk_level="REVERSIBLE")
        gateway_result = svc.process("topic", proposal, _base_investigation(), {})

        wer = WorkflowExecutionResult()
        wer.gateway_result = gateway_result
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())

        assert restored.gateway_result["status"] == "PENDING_APPROVAL"
        assert restored.gateway_result["can_execute"] is False

    def test_blocked_survives_jsonb_roundtrip(self, svc):
        gateway_result = svc.process("topic", None, None, None)
        wer = WorkflowExecutionResult()
        wer.gateway_result = gateway_result
        restored = WorkflowExecutionResult.from_dict(wer.to_dict())
        assert restored.gateway_result["status"] == "BLOCKED"


# ── Determinism ───────────────────────────────────────────────────────────────

class TestDeterminism:
    def test_same_safe_input_always_approved(self, svc):
        proposal = _base_proposal(risk_level="SAFE")
        results = [
            svc.process("topic", proposal, _base_investigation(), _knowledge())
            for _ in range(5)
        ]
        statuses = {r["status"] for r in results}
        assert statuses == {"APPROVED"}

    def test_same_reversible_input_always_pending(self, svc):
        proposal = _base_proposal(risk_level="REVERSIBLE")
        results = [
            svc.process("topic", proposal, _base_investigation(), {})
            for _ in range(5)
        ]
        statuses = {r["status"] for r in results}
        assert statuses == {"PENDING_APPROVAL"}

    def test_same_blocked_input_always_blocked(self, svc):
        results = [svc.process("topic", None, None, None) for _ in range(5)]
        statuses = {r["status"] for r in results}
        assert statuses == {"BLOCKED"}


# ── All 5 KwikID topics ───────────────────────────────────────────────────────

class TestAllTopics:
    @pytest.mark.parametrize("topic,action_type,risk_level,expected_status", [
        ("OTP_Delivery_Failure",  "RESEND_OTP",            "SAFE",       "APPROVED"),
        ("VKYC_Session_Failure",  "RESET_SESSION",         "REVERSIBLE", "PENDING_APPROVAL"),
        ("Document_OCR_Failure",  "RETRY_DOCUMENT_CAPTURE","SAFE",       "APPROVED"),
        ("Agent_Portal_Issue",    "CHECK_SERVER_STATUS",   "SAFE",       "APPROVED"),
        ("API_Callback_Failure",  "RETRY_CALLBACK",        "REVERSIBLE", "PENDING_APPROVAL"),
    ])
    def test_topic_pipeline(self, svc, topic, action_type, risk_level, expected_status):
        proposal = _base_proposal(action_type=action_type, risk_level=risk_level)
        result = svc.process(topic, proposal, _base_investigation(), _knowledge())
        assert result["status"] == expected_status, \
            f"topic={topic} action={action_type} risk={risk_level}: expected {expected_status} got {result['status']}"
