"""
tests/test_sprint2275_ticket_orchestrator.py

Sprint 2.27.5 Phase 4: TicketOrchestrator test suite.

Tests: ticket lifecycle, process_ticket, resume_ticket, close_ticket,
escalate_ticket, and never-raises contracts.
"""
import pytest
from unittest.mock import MagicMock

from case_engine.ticket_orchestration.models import (
    TicketContext,
    TicketLifecycleState,
    TicketOrchestrationResult,
)
from case_engine.ticket_orchestration.orchestrator import (
    TicketOrchestrator,
    build_ticket_orchestrator,
)
from case_engine.runtime.agent_models import AgentStatus


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_context(
    ticket_id: str = "fd-001",
    client: str = "unity_bank",
    subject: str = "VKYC Session Failed",
    description: str = "Unable to complete video KYC.",
) -> TicketContext:
    return TicketContext(
        ticket_id=ticket_id,
        client=client,
        subject=subject,
        description=description,
        requester_email="agent@bank.com",
    )


def _make_mock_agent(agent_status: AgentStatus = AgentStatus.SUCCESS):
    """Return a mock SupportAgentRuntime that returns a given status."""
    from case_engine.runtime.agent_models import AgentExecutionResult
    mock_agent = MagicMock()
    result = AgentExecutionResult(
        run_id="run-1",
        case_id="case-001",
        agent_status=agent_status,
        workflow_result={"workflow_state": "RESOLVED"},
        response_draft={"body_text": "Your issue has been resolved."},
        engineering_result=None,
        classification={"topic": "VKYC_Session_Failure"},
        steps_completed=("CLASSIFY", "WORKFLOW", "USERRESPONSE"),
        error_code=None,
        error_msg=None,
        started_at="2025-01-01T00:00:00+00:00",
        completed_at="2025-01-01T00:00:01+00:00",
        duration_ms=100,
    )
    mock_agent.run_case.return_value = result
    return mock_agent


def _make_mock_case_svc(case_id: str = "case-001"):
    from case_engine.models import Case
    mock_cs = MagicMock()
    case = Case(case_id=case_id, ticket_id="fd-001", client="unity_bank")
    case.topic = "VKYC_Session_Failure"
    mock_cs.open_case.return_value = case
    mock_cs.get_case.return_value = case
    return mock_cs


def _make_orchestrator(agent_status: AgentStatus = AgentStatus.SUCCESS) -> TicketOrchestrator:
    return TicketOrchestrator(
        agent_runtime=_make_mock_agent(agent_status),
        case_service=_make_mock_case_svc(),
    )


# ── TicketContext ─────────────────────────────────────────────────────────────

class TestTicketContext:
    def test_message_text_combines_subject_and_description(self):
        ctx = _make_context()
        text = ctx.message_text()
        assert "VKYC Session Failed" in text
        assert "Unable to complete" in text

    def test_to_dict(self):
        ctx = _make_context()
        d = ctx.to_dict()
        assert d["ticket_id"] == "fd-001"
        assert d["client"] == "unity_bank"
        assert d["requester_email"] == "agent@bank.com"

    def test_immutable(self):
        ctx = _make_context()
        with pytest.raises((AttributeError, TypeError)):
            ctx.ticket_id = "hacked"  # type: ignore[misc]


# ── TicketLifecycleState ──────────────────────────────────────────────────────

class TestTicketLifecycleState:
    def test_all_values(self):
        values = {s.value for s in TicketLifecycleState}
        assert "RECEIVED" in values
        assert "OPEN" in values
        assert "PROCESSING" in values
        assert "WAITING" in values
        assert "ESCALATED" in values
        assert "CLOSED" in values
        assert "FAILED" in values


# ── TicketOrchestrationResult ─────────────────────────────────────────────────

class TestTicketOrchestrationResult:
    def test_to_dict(self):
        r = TicketOrchestrationResult(
            orchestration_id="o1",
            ticket_id="fd-001",
            case_id="case-001",
            lifecycle_state=TicketLifecycleState.CLOSED,
            agent_result=None,
            operation="process",
            success=True,
            error_code=None,
            error_msg=None,
            executed_at="2025-01-01T00:00:00+00:00",
            duration_ms=200,
        )
        d = r.to_dict()
        assert d["lifecycle_state"] == "CLOSED"
        assert d["success"] is True
        assert d["ticket_id"] == "fd-001"

    def test_failure_classmethod(self):
        r = TicketOrchestrationResult.failure(
            ticket_id="fd-001",
            operation="process",
            error_code="FATAL",
            error_msg="crash",
            duration_ms=0,
        )
        assert r.success is False
        assert r.lifecycle_state == TicketLifecycleState.FAILED

    def test_immutable(self):
        r = TicketOrchestrationResult.failure("fd-1", "process", "E", "msg")
        with pytest.raises((AttributeError, TypeError)):
            r.success = True  # type: ignore[misc]


# ── TicketOrchestrator.process_ticket() ───────────────────────────────────────

class TestTicketOrchestratorProcessTicket:
    def test_process_ticket_success(self):
        orc = _make_orchestrator(AgentStatus.SUCCESS)
        result = orc.process_ticket(_make_context())
        assert result.success is True
        assert result.lifecycle_state == TicketLifecycleState.CLOSED

    def test_process_ticket_stores_case_id(self):
        orc = _make_orchestrator()
        ctx = _make_context()
        result = orc.process_ticket(ctx)
        assert result.case_id == "case-001"

    def test_process_ticket_awaiting_clarification(self):
        orc = _make_orchestrator(AgentStatus.AWAITING_CLARIFICATION)
        result = orc.process_ticket(_make_context())
        assert result.lifecycle_state == TicketLifecycleState.WAITING

    def test_process_ticket_escalated(self):
        orc = _make_orchestrator(AgentStatus.ESCALATED)
        result = orc.process_ticket(_make_context())
        assert result.lifecycle_state == TicketLifecycleState.ESCALATED

    def test_process_ticket_agent_result_included(self):
        orc = _make_orchestrator()
        result = orc.process_ticket(_make_context())
        assert result.agent_result is not None
        assert "agent_status" in result.agent_result

    def test_process_ticket_never_raises(self):
        orc = TicketOrchestrator(agent_runtime=None, case_service=None)
        result = orc.process_ticket(_make_context())
        assert isinstance(result, TicketOrchestrationResult)

    def test_process_ticket_without_case_service(self):
        orc = TicketOrchestrator(
            agent_runtime=_make_mock_agent(),
            case_service=None,
        )
        result = orc.process_ticket(_make_context())
        # No case opened → agent skipped → state depends on implementation
        assert isinstance(result, TicketOrchestrationResult)

    def test_process_ticket_registers_in_registry(self):
        orc = _make_orchestrator()
        ctx = _make_context(ticket_id="fd-999")
        orc.process_ticket(ctx)
        state = orc.get_lifecycle_state("fd-999")
        assert state is not None

    def test_process_different_tickets_independent(self):
        orc = _make_orchestrator()
        orc.process_ticket(_make_context(ticket_id="fd-001"))
        orc.process_ticket(_make_context(ticket_id="fd-002"))
        assert orc.get_lifecycle_state("fd-001") is not None
        assert orc.get_lifecycle_state("fd-002") is not None


# ── TicketOrchestrator.resume_ticket() ────────────────────────────────────────

class TestTicketOrchestratorResumeTicket:
    def test_resume_known_ticket(self):
        orc = _make_orchestrator(AgentStatus.SUCCESS)
        ctx = _make_context(ticket_id="fd-001")
        orc.process_ticket(ctx)
        result = orc.resume_ticket("fd-001", "Here is the session ID.")
        assert result.success is True
        assert result.lifecycle_state == TicketLifecycleState.CLOSED

    def test_resume_unknown_ticket(self):
        orc = _make_orchestrator()
        result = orc.resume_ticket("not-registered", "message")
        assert result.success is False
        assert result.error_code == "TICKET_NOT_FOUND"

    def test_resume_never_raises(self):
        orc = TicketOrchestrator(agent_runtime=None, case_service=None)
        result = orc.resume_ticket("nonexistent", "message")
        assert isinstance(result, TicketOrchestrationResult)


# ── TicketOrchestrator.close_ticket() ────────────────────────────────────────

class TestTicketOrchestratorCloseTicket:
    def test_close_processed_ticket(self):
        orc = _make_orchestrator()
        orc.process_ticket(_make_context(ticket_id="fd-001"))
        result = orc.close_ticket("fd-001")
        assert result.success is True
        assert result.lifecycle_state == TicketLifecycleState.CLOSED
        assert orc.get_lifecycle_state("fd-001") == TicketLifecycleState.CLOSED

    def test_close_unregistered_ticket(self):
        orc = _make_orchestrator()
        result = orc.close_ticket("not-registered")
        assert result.success is False
        assert result.error_code == "TICKET_NOT_FOUND"

    def test_close_never_raises(self):
        orc = TicketOrchestrator()
        result = orc.close_ticket("anything")
        assert isinstance(result, TicketOrchestrationResult)


# ── TicketOrchestrator.escalate_ticket() ─────────────────────────────────────

class TestTicketOrchestratorEscalateTicket:
    def test_escalate_registered_ticket(self):
        orc = _make_orchestrator()
        orc.process_ticket(_make_context(ticket_id="fd-001"))
        result = orc.escalate_ticket("fd-001", reason="Manual escalation")
        assert result.success is True
        assert result.lifecycle_state == TicketLifecycleState.ESCALATED
        assert orc.get_lifecycle_state("fd-001") == TicketLifecycleState.ESCALATED

    def test_escalate_unregistered_ticket(self):
        orc = _make_orchestrator()
        result = orc.escalate_ticket("not-registered", reason="manual")
        assert result.success is True
        assert result.lifecycle_state == TicketLifecycleState.ESCALATED

    def test_escalate_never_raises(self):
        orc = TicketOrchestrator()
        result = orc.escalate_ticket("any", "reason")
        assert isinstance(result, TicketOrchestrationResult)


# ── get_case_id ───────────────────────────────────────────────────────────────

class TestGetCaseId:
    def test_get_case_id_after_process(self):
        orc = _make_orchestrator()
        orc.process_ticket(_make_context(ticket_id="fd-001"))
        case_id = orc.get_case_id("fd-001")
        assert case_id == "case-001"

    def test_get_case_id_unregistered(self):
        orc = TicketOrchestrator()
        assert orc.get_case_id("unknown") is None


# ── Factory ───────────────────────────────────────────────────────────────────

class TestTicketOrchestratorFactory:
    def test_build_with_no_args(self):
        orc = build_ticket_orchestrator()
        assert orc is not None

    def test_build_with_services(self):
        mock_agent = _make_mock_agent()
        mock_cs    = _make_mock_case_svc()
        orc = build_ticket_orchestrator(agent_runtime=mock_agent, case_service=mock_cs)
        assert orc._agent is mock_agent
        assert orc._case_svc is mock_cs
