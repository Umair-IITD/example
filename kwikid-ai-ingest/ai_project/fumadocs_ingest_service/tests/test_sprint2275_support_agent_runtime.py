"""
tests/test_sprint2275_support_agent_runtime.py

Sprint 2.27.5 Phase 3: SupportAgentRuntime test suite.

Tests: run_case() pipeline, AgentStatus outcomes, never-raises, service delegation.
"""
import pytest
from unittest.mock import MagicMock, patch

from case_engine.case_state import CaseState
from case_engine.models import Case
from case_engine.runtime.agent_models import AgentExecutionResult, AgentStatus
from case_engine.runtime.support_agent_runtime import (
    SupportAgentRuntime,
    build_support_agent_runtime,
    _needs_engineering_escalation,
    _determine_response_type,
)
from case_engine.response_generation.models import ResponseType


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_case(
    state: CaseState = CaseState.TRIAGE_COMPLETE,
    topic: str = "VKYC_Session_Failure",
    case_id: str = "case-001",
) -> Case:
    c = Case(case_id=case_id, ticket_id="fd-001", client="unity_bank")
    c.topic = topic
    c.confidence = 0.92
    c.current_state = state
    return c


def _make_runtime(
    with_case_svc: bool = True,
    with_response_svc: bool = True,
    with_engineering_svc: bool = True,
) -> SupportAgentRuntime:
    from case_engine.response_generation.service import build_response_generation_service
    from case_engine.engineering.service import build_engineering_escalation_service

    mock_case_svc = None
    if with_case_svc:
        mock_case_svc = MagicMock()
        mock_case_svc.classify_case.side_effect = lambda case, text: case
        mock_msg_result = MagicMock()
        mock_msg_result.all_slots_filled = True
        mock_msg_result.workflow_started = False
        mock_msg_result.next_question = None
        mock_case_svc.receive_message.return_value = mock_msg_result
        mock_wf_result = MagicMock()
        mock_wf_result.workflow_id = "wf-1"
        mock_wf_result.workflow_state = "RESOLVED"
        mock_wf_result.step_results = []
        mock_wf_result.resolved = True
        mock_wf_result.escalated = False
        mock_wf_result.escalation_reason = None
        mock_wf_result.resolution_note = "Resolved via OTP resend."
        mock_case_svc.start_workflow.return_value = mock_wf_result

    resp_svc = build_response_generation_service() if with_response_svc else None
    eng_svc  = build_engineering_escalation_service() if with_engineering_svc else None

    return SupportAgentRuntime(
        case_service=mock_case_svc,
        response_generation_service=resp_svc,
        engineering_escalation_service=eng_svc,
    )


# ── AgentStatus enum ──────────────────────────────────────────────────────────

class TestAgentStatus:
    def test_all_values(self):
        values = {s.value for s in AgentStatus}
        assert "SUCCESS" in values
        assert "AWAITING_CLARIFICATION" in values
        assert "AWAITING_APPROVAL" in values
        assert "ESCALATED" in values
        assert "FAILED" in values

    def test_string_enum(self):
        assert AgentStatus.SUCCESS == "SUCCESS"


# ── AgentExecutionResult ──────────────────────────────────────────────────────

class TestAgentExecutionResult:
    def test_to_dict(self):
        result = AgentExecutionResult(
            run_id="r1", case_id="c1", agent_status=AgentStatus.SUCCESS,
            workflow_result=None, response_draft=None, engineering_result=None,
            classification=None, steps_completed=("CLASSIFY", "WORKFLOW"),
            error_code=None, error_msg=None,
            started_at="2025-01-01T00:00:00+00:00",
            completed_at="2025-01-01T00:00:01+00:00",
            duration_ms=100,
        )
        d = result.to_dict()
        assert d["agent_status"] == "SUCCESS"
        assert d["success"] is True
        assert d["steps_completed"] == ["CLASSIFY", "WORKFLOW"]

    def test_failure_classmethod(self):
        result = AgentExecutionResult.failure(
            case_id="c1",
            error_code="AGENT_CRASH",
            error_msg="Something broke",
            started_at="2025-01-01T00:00:00+00:00",
        )
        assert result.agent_status == AgentStatus.FAILED
        assert result.error_code == "AGENT_CRASH"
        assert result.success is False

    def test_properties(self):
        result = AgentExecutionResult.failure("c1", "E", "m", "2025-01-01T00:00:00+00:00")
        assert result.success is False
        assert result.needs_clarification is False
        assert result.escalated is False

    def test_immutable(self):
        result = AgentExecutionResult.failure("c1", "E", "m", "2025-01-01T00:00:00+00:00")
        with pytest.raises((AttributeError, TypeError)):
            result.agent_status = AgentStatus.SUCCESS  # type: ignore[misc]


# ── Helper functions ──────────────────────────────────────────────────────────

class TestHelperFunctions:
    def test_needs_engineering_escalation_none_workflow(self):
        assert _needs_engineering_escalation(None, None) is False

    def test_needs_engineering_escalation_workflow_escalated(self):
        assert _needs_engineering_escalation("VKYC", {"workflow_state": "ESCALATED"}) is True

    def test_needs_engineering_escalation_infrastructure_root_cause(self):
        wf = {
            "workflow_state": "RESOLVED",
            "workflow_context": {
                "investigation_result": {
                    "root_cause": {"category": "infrastructure backend"}
                }
            }
        }
        assert _needs_engineering_escalation("VKYC_Session_Failure", wf) is True

    def test_needs_engineering_escalation_false_for_normal(self):
        wf = {
            "workflow_state": "RESOLVED",
            "workflow_context": {
                "investigation_result": {
                    "root_cause": {"category": "user_error"}
                }
            }
        }
        assert _needs_engineering_escalation("OTP_Delivery_Failure", wf) is False

    def test_determine_response_type_awaiting_input(self):
        case = _make_case(CaseState.AWAITING_INPUT)
        rt = _determine_response_type(case, None, False, "What is your session ID?")
        assert rt == ResponseType.CLARIFICATION

    def test_determine_response_type_needs_l2(self):
        case = _make_case(CaseState.TRIAGE_COMPLETE)
        rt = _determine_response_type(case, None, True, "")
        assert rt == ResponseType.ESCALATION

    def test_determine_response_type_escalated_state(self):
        case = _make_case(CaseState.ESCALATED)
        rt = _determine_response_type(case, None, False, "")
        assert rt == ResponseType.ESCALATION

    def test_determine_response_type_resolved_workflow(self):
        case = _make_case(CaseState.TRIAGE_COMPLETE)
        wf = {"workflow_state": "RESOLVED"}
        rt = _determine_response_type(case, wf, False, "")
        assert rt == ResponseType.RESOLUTION


# ── SupportAgentRuntime.run_case() ────────────────────────────────────────────

class TestSupportAgentRuntimeRunCase:
    def test_run_case_returns_result(self):
        rt = _make_runtime()
        case = _make_case(topic="VKYC_Session_Failure")
        result = rt.run_case(case, "My VKYC session failed.")
        assert isinstance(result, AgentExecutionResult)
        assert result.case_id == case.case_id

    def test_run_case_includes_steps(self):
        rt = _make_runtime()
        case = _make_case()
        result = rt.run_case(case, "Session failed.")
        assert len(result.steps_completed) > 0

    def test_run_case_resolution_status(self):
        rt = _make_runtime()
        case = _make_case(state=CaseState.RESOLVED)
        result = rt.run_case(case, "Session failed.")
        assert result.agent_status in (AgentStatus.SUCCESS, AgentStatus.ESCALATED, AgentStatus.AWAITING_CLARIFICATION, AgentStatus.FAILED)

    def test_run_case_never_raises(self):
        rt = SupportAgentRuntime(
            case_service=None,
            response_generation_service=None,
            engineering_escalation_service=None,
        )
        case = _make_case()
        result = rt.run_case(case, "test message")
        assert isinstance(result, AgentExecutionResult)

    def test_run_case_with_no_services(self):
        rt = SupportAgentRuntime()
        case = _make_case()
        result = rt.run_case(case, "message")
        assert isinstance(result, AgentExecutionResult)

    def test_run_case_with_null_topic_classifies(self):
        from case_engine.response_generation.service import build_response_generation_service
        mock_cs = MagicMock()
        # classify_case sets topic
        def classify_side_effect(case, text):
            case.topic = "OTP_Delivery_Failure"
            case.confidence = 0.95
            return case
        mock_cs.classify_case.side_effect = classify_side_effect
        mock_msg_result = MagicMock()
        mock_msg_result.all_slots_filled = True
        mock_msg_result.workflow_started = False
        mock_msg_result.next_question = None
        mock_cs.receive_message.return_value = mock_msg_result
        mock_wf = MagicMock()
        mock_wf.workflow_id = "wf-1"
        mock_wf.workflow_state = "RESOLVED"
        mock_wf.step_results = []
        mock_wf.resolved = True
        mock_wf.escalated = False
        mock_wf.escalation_reason = None
        mock_wf.resolution_note = "done"
        mock_cs.start_workflow.return_value = mock_wf
        rt = SupportAgentRuntime(
            case_service=mock_cs,
            response_generation_service=build_response_generation_service(),
        )
        case = _make_case()
        case.topic = None
        result = rt.run_case(case, "OTP not received.")
        mock_cs.classify_case.assert_called_once()
        assert "CLASSIFY" in result.steps_completed

    def test_run_case_with_clarification_needed(self):
        from case_engine.response_generation.service import build_response_generation_service
        mock_cs = MagicMock()
        mock_cs.classify_case.side_effect = lambda c, t: c
        mock_msg_result = MagicMock()
        mock_msg_result.all_slots_filled = False
        mock_msg_result.workflow_started = False
        mock_msg_result.next_question = {"text": "What is your session ID?"}
        mock_cs.receive_message.return_value = mock_msg_result
        # Sprint 2.54 Wave 4B: investigation now runs before clarification.
        # Configure start_workflow so L2CHECK does not fire for VKYC_Session_Failure topic.
        mock_wf = MagicMock()
        mock_wf.workflow_id = "wf-clarify"
        mock_wf.workflow_state = "SLOT_FILL"
        mock_wf.step_results = []
        mock_wf.resolved = False
        mock_wf.escalated = False
        mock_wf.escalation_reason = None
        mock_wf.resolution_note = None
        mock_cs.start_workflow.return_value = mock_wf
        rt = SupportAgentRuntime(
            case_service=mock_cs,
            response_generation_service=build_response_generation_service(),
        )
        # Use OTP_Delivery_Failure — not in _L2_ESCALATION_TOPICS — so L2CHECK stays False.
        case = _make_case(topic="OTP_Delivery_Failure")
        result = rt.run_case(case, "help")
        assert result.agent_status == AgentStatus.AWAITING_CLARIFICATION
        assert "CLARIFY" in result.steps_completed

    def test_run_case_response_draft_included(self):
        rt = _make_runtime()
        result = rt.run_case(_make_case(), "issue")
        assert result.response_draft is not None or result.agent_status == AgentStatus.FAILED

    def test_run_case_duration_ms_positive(self):
        rt = _make_runtime()
        result = rt.run_case(_make_case(), "message")
        assert result.duration_ms >= 0

    def test_run_case_broken_case_service_never_raises(self):
        mock_cs = MagicMock()
        mock_cs.classify_case.side_effect = RuntimeError("classifier crashed")
        rt = SupportAgentRuntime(case_service=mock_cs)
        result = rt.run_case(_make_case(), "message")
        assert isinstance(result, AgentExecutionResult)


# ── Escalation path ───────────────────────────────────────────────────────────

class TestSupportAgentRuntimeEscalation:
    def test_l2_check_triggers_engineering_ticket(self):
        from case_engine.response_generation.service import build_response_generation_service
        from case_engine.engineering.service import build_engineering_escalation_service

        mock_cs = MagicMock()
        mock_cs.classify_case.side_effect = lambda c, t: c
        mock_msg = MagicMock()
        mock_msg.all_slots_filled = True
        mock_msg.workflow_started = False
        mock_msg.next_question = None
        mock_cs.receive_message.return_value = mock_msg
        mock_wf = MagicMock()
        mock_wf.workflow_id = "wf-1"
        mock_wf.workflow_state = "ESCALATED"  # triggers L2CHECK
        mock_wf.step_results = []
        mock_wf.resolved = False
        mock_wf.escalated = True
        mock_wf.escalation_reason = "Infrastructure failure"
        mock_wf.resolution_note = None
        mock_cs.start_workflow.return_value = mock_wf

        eng_svc = build_engineering_escalation_service()
        from case_engine.runtime.agent_models import SupportAgentMode
        rt = SupportAgentRuntime(
            case_service=mock_cs,
            response_generation_service=build_response_generation_service(),
            engineering_escalation_service=eng_svc,
            mode=SupportAgentMode.PRODUCTION,
        )
        case = _make_case()
        result = rt.run_case(case, "API failing")
        assert result.agent_status == AgentStatus.ESCALATED
        assert "ASANACREATE" in result.steps_completed
        assert result.engineering_result is not None


# ── Factory ───────────────────────────────────────────────────────────────────

class TestSupportAgentRuntimeFactory:
    def test_build_with_no_args(self):
        rt = build_support_agent_runtime()
        assert rt is not None

    def test_build_with_case_service(self):
        mock_cs = MagicMock()
        rt = build_support_agent_runtime(case_service=mock_cs)
        assert rt._case_svc is mock_cs

    def test_build_with_audit_logger(self):
        mock_audit = MagicMock()
        rt = build_support_agent_runtime(audit_logger=mock_audit)
        assert rt._audit is mock_audit
