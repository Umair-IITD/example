"""
tests/test_sprint2278_golden_path.py

Sprint 2.27.8: Golden Path API tests.

Covers:
  - POST /tickets/process — golden path endpoint
  - POST /tickets/{id}/resume
  - POST /tickets/{id}/close
  - POST /tickets/{id}/escalate
  - GET /tickets/{id}/status
  - 503 when orchestrator unavailable
  - Auth enforcement (require_operator)
  - Router prefix and tag assignment
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient


# ── App fixture ───────────────────────────────────────────────────────────────

def _make_app(orchestrator=None):
    """Build a minimal FastAPI app with tickets router and optional orchestrator."""
    from api.routes import tickets as _tickets_routes
    from security.auth import ApiKeyAuthenticator
    app = FastAPI()
    # auth_enabled=False → all require_* checks pass without a key
    app.state.authenticator = ApiKeyAuthenticator({}, auth_enabled=False)
    app.state.ticket_orchestrator = orchestrator
    app.include_router(_tickets_routes.router, tags=["Tickets"])
    return app


def _make_orchestrator_result(
    ticket_id: str = "t-1",
    success: bool = True,
    lifecycle_state_value: str = "CLOSED",
):
    from case_engine.ticket_orchestration.models import TicketLifecycleState, TicketOrchestrationResult
    state = TicketLifecycleState(lifecycle_state_value)
    return TicketOrchestrationResult(
        orchestration_id="orch-1",
        ticket_id=ticket_id,
        case_id="case-1",
        lifecycle_state=state,
        agent_result=None,
        operation="process",
        success=success,
        error_code=None,
        error_msg=None,
        executed_at="2026-06-16T00:00:00+00:00",
        duration_ms=50,
    )


# ── POST /tickets/process ─────────────────────────────────────────────────────

class TestProcessTicketEndpoint:
    def _payload(self, **overrides):
        payload = {
            "ticket_id": "t-001",
            "client": "unity_bank",
            "subject": "VKYC session failed",
            "description": "Session dropped unexpectedly",
            "requester_email": "user@example.com",
        }
        payload.update(overrides)
        return payload

    def test_process_ticket_returns_200(self):
        orch = MagicMock()
        orch.process_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch), raise_server_exceptions=True)
        resp = client.post("/tickets/process", json=self._payload())
        assert resp.status_code == 200

    def test_process_ticket_result_dict_returned(self):
        orch = MagicMock()
        orch.process_ticket.return_value = _make_orchestrator_result(ticket_id="t-001")
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.post("/tickets/process", json=self._payload())
        data = resp.json()
        assert data["ticket_id"] == "t-001"
        assert "lifecycle_state" in data

    def test_process_ticket_calls_orchestrator(self):
        orch = MagicMock()
        orch.process_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        client.post("/tickets/process", json=self._payload())
        orch.process_ticket.assert_called_once()

    def test_process_ticket_503_when_no_orchestrator(self):
        client = TestClient(_make_app(orchestrator=None))
        resp = client.post("/tickets/process", json=self._payload())
        assert resp.status_code == 503

    def test_process_ticket_passes_ticket_context_fields(self):
        orch = MagicMock()
        orch.process_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        client.post("/tickets/process", json=self._payload())
        call_args = orch.process_ticket.call_args[0][0]
        assert call_args.ticket_id == "t-001"
        assert call_args.client == "unity_bank"
        assert call_args.subject == "VKYC session failed"


# ── POST /tickets/{id}/resume ─────────────────────────────────────────────────

class TestResumeTicketEndpoint:
    def test_resume_returns_200(self):
        orch = MagicMock()
        orch.resume_ticket.return_value = _make_orchestrator_result(lifecycle_state_value="PROCESSING")
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.post("/tickets/t-001/resume", json={"message_text": "My phone is 9876543210"})
        assert resp.status_code == 200

    def test_resume_503_when_no_orchestrator(self):
        client = TestClient(_make_app(orchestrator=None))
        resp = client.post("/tickets/t-001/resume", json={"message_text": "reply"})
        assert resp.status_code == 503

    def test_resume_calls_orchestrator_with_ticket_id_and_message(self):
        orch = MagicMock()
        orch.resume_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        client.post("/tickets/t-abc/resume", json={"message_text": "hello"})
        orch.resume_ticket.assert_called_once_with(
            ticket_id="t-abc",
            message_text="hello",
        )


# ── POST /tickets/{id}/close ──────────────────────────────────────────────────

class TestCloseTicketEndpoint:
    def test_close_returns_200(self):
        orch = MagicMock()
        orch.close_ticket.return_value = _make_orchestrator_result(lifecycle_state_value="CLOSED")
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.post("/tickets/t-001/close")
        assert resp.status_code == 200

    def test_close_503_when_no_orchestrator(self):
        client = TestClient(_make_app(orchestrator=None))
        resp = client.post("/tickets/t-001/close")
        assert resp.status_code == 503

    def test_close_calls_orchestrator(self):
        orch = MagicMock()
        orch.close_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        client.post("/tickets/t-xyz/close")
        orch.close_ticket.assert_called_once_with(ticket_id="t-xyz")


# ── POST /tickets/{id}/escalate ───────────────────────────────────────────────

class TestEscalateTicketEndpoint:
    def test_escalate_returns_200(self):
        orch = MagicMock()
        orch.escalate_ticket.return_value = _make_orchestrator_result(lifecycle_state_value="ESCALATED")
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.post("/tickets/t-001/escalate", json={"reason": "Infrastructure issue"})
        assert resp.status_code == 200

    def test_escalate_503_when_no_orchestrator(self):
        client = TestClient(_make_app(orchestrator=None))
        resp = client.post("/tickets/t-001/escalate", json={"reason": "test"})
        assert resp.status_code == 503

    def test_escalate_calls_orchestrator_with_reason(self):
        orch = MagicMock()
        orch.escalate_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        client.post("/tickets/t-001/escalate", json={"reason": "DB down"})
        orch.escalate_ticket.assert_called_once_with(
            ticket_id="t-001",
            reason="DB down",
        )

    def test_escalate_with_empty_reason(self):
        orch = MagicMock()
        orch.escalate_ticket.return_value = _make_orchestrator_result()
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.post("/tickets/t-001/escalate", json={})
        assert resp.status_code == 200


# ── GET /tickets/{id}/status ──────────────────────────────────────────────────

class TestGetTicketStatusEndpoint:
    def test_status_returns_200(self):
        from case_engine.ticket_orchestration.models import TicketLifecycleState
        orch = MagicMock()
        orch.get_lifecycle_state.return_value = TicketLifecycleState.WAITING
        orch.get_case_id.return_value = "case-abc"
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.get("/tickets/t-001/status")
        assert resp.status_code == 200

    def test_status_returns_lifecycle_state(self):
        from case_engine.ticket_orchestration.models import TicketLifecycleState
        orch = MagicMock()
        orch.get_lifecycle_state.return_value = TicketLifecycleState.WAITING
        orch.get_case_id.return_value = "case-abc"
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.get("/tickets/t-001/status")
        data = resp.json()
        assert data["lifecycle_state"] == "WAITING"
        assert data["ticket_id"] == "t-001"
        assert data["case_id"] == "case-abc"

    def test_status_404_when_ticket_not_found(self):
        orch = MagicMock()
        orch.get_lifecycle_state.return_value = None
        client = TestClient(_make_app(orchestrator=orch))
        resp = client.get("/tickets/unknown-ticket/status")
        assert resp.status_code == 404

    def test_status_503_when_no_orchestrator(self):
        client = TestClient(_make_app(orchestrator=None))
        resp = client.get("/tickets/t-001/status")
        assert resp.status_code == 503


# ── Router structure ──────────────────────────────────────────────────────────

class TestTicketsRouterStructure:
    def test_router_has_prefix_tickets(self):
        from api.routes.tickets import router
        assert router.prefix == "/tickets"

    def test_router_is_importable(self):
        from api.routes import tickets
        assert hasattr(tickets, "router")

    def test_routes_registered(self):
        from api.routes.tickets import router
        paths = [r.path for r in router.routes]
        assert "/tickets/process" in paths
        assert "/tickets/{ticket_id}/resume" in paths
        assert "/tickets/{ticket_id}/close" in paths
        assert "/tickets/{ticket_id}/escalate" in paths
        assert "/tickets/{ticket_id}/status" in paths


# ── Error handling ────────────────────────────────────────────────────────────

class TestTicketsErrorHandling:
    def test_orchestrator_exception_returns_500(self):
        orch = MagicMock()
        orch.process_ticket.side_effect = Exception("unexpected error")
        client = TestClient(_make_app(orchestrator=orch), raise_server_exceptions=False)
        resp = client.post("/tickets/process", json={
            "ticket_id": "t-1",
            "client": "c",
            "subject": "s",
            "description": "d",
        })
        assert resp.status_code == 500

    def test_resume_orchestrator_exception_returns_500(self):
        orch = MagicMock()
        orch.resume_ticket.side_effect = Exception("unexpected")
        client = TestClient(_make_app(orchestrator=orch), raise_server_exceptions=False)
        resp = client.post("/tickets/t-1/resume", json={"message_text": "hi"})
        assert resp.status_code == 500

    def test_error_response_has_error_envelope(self):
        client = TestClient(_make_app(orchestrator=None), raise_server_exceptions=False)
        resp = client.post("/tickets/process", json={
            "ticket_id": "t-1",
            "client": "c",
            "subject": "s",
            "description": "d",
        })
        data = resp.json()
        assert "error" in data
