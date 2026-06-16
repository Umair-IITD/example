"""
tests/test_sprint215_case_engine_api.py

Sprint 2.15: Tests for Case Engine API endpoints.
Covers POST /cases, GET /cases/{id}, POST /cases/{id}/message, GET /cases/{id}/slots.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.case_engine import router
from case_engine.case_state import CaseState
from case_engine.models import Case, TopicKey
from case_engine.service import CaseService, ReceiveMessageResult

_UNSET = object()  # sentinel to distinguish "not provided" from explicit None


# ── Test infrastructure ───────────────────────────────────────────────────────

def _make_case(
    ticket_id: str = "TKT-001",
    client: str = "bank_alpha",
    state: CaseState = CaseState.NEW,
    topic: str | None = None,
) -> Case:
    c = Case(ticket_id=ticket_id, client=client)
    c.current_state = state
    c.topic = topic
    return c


def _make_client(
    case: Case | None = None,
    *,
    open_returns: Case | None = None,
    classify_returns: Case | None = None,
    get_case_returns: Any = _UNSET,  # None means "not found"; _UNSET means "use default case"
    receive_returns: ReceiveMessageResult | None = None,
    service_none: bool = False,
) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    svc: Any
    if service_none:
        svc = None
    else:
        svc = MagicMock(spec=CaseService)
        _case = case or _make_case()

        svc.open_case.return_value = open_returns or _case
        svc.classify_case.return_value = classify_returns or _case
        svc.get_case.return_value = _case if get_case_returns is _UNSET else get_case_returns
        svc.get_slot_state.return_value = {}

        default_receive = ReceiveMessageResult(
            case_id=_case.case_id,
            state=_case.current_state,
            slot_values={},
            next_question=None,
        )
        svc.receive_message.return_value = receive_returns or default_receive

    app.state.case_service = svc
    return TestClient(app, raise_server_exceptions=False)


# ── POST /cases ───────────────────────────────────────────────────────────────

class TestCreateCase:
    def test_creates_case_returns_201(self):
        case = _make_case(state=CaseState.NEW)
        cli = _make_client(case=case)
        resp = cli.post("/cases", json={"ticket_id": "TKT-001", "client": "bank_alpha"})
        assert resp.status_code == 201
        body = resp.json()
        assert body["ticket_id"] == "TKT-001"
        assert body["state"] == "NEW"

    def test_existing_case_returns_200(self):
        case = _make_case(state=CaseState.TRIAGE_COMPLETE)
        cli = _make_client(open_returns=case)
        resp = cli.post("/cases", json={"ticket_id": "TKT-001", "client": "bank_alpha"})
        assert resp.status_code == 200

    def test_initial_message_triggers_classify(self):
        new_case = _make_case(state=CaseState.NEW)
        classified = _make_case(state=CaseState.TRIAGE_COMPLETE, topic=TopicKey.VKYC_SESSION_FAILURE.value)
        cli = _make_client(open_returns=new_case, classify_returns=classified)
        resp = cli.post(
            "/cases",
            json={"ticket_id": "TKT-001", "client": "bank_alpha", "initial_message": "VKYC failed"},
        )
        assert resp.status_code in (200, 201)

    def test_missing_ticket_id_returns_422(self):
        cli = _make_client()
        resp = cli.post("/cases", json={"client": "bank_alpha"})
        assert resp.status_code == 422

    def test_missing_client_returns_422(self):
        cli = _make_client()
        resp = cli.post("/cases", json={"ticket_id": "TKT-001"})
        assert resp.status_code == 422

    def test_service_not_ready_returns_503(self):
        cli = _make_client(service_none=True)
        resp = cli.post("/cases", json={"ticket_id": "TKT-001", "client": "bank_alpha"})
        assert resp.status_code == 503

    def test_response_contains_expected_fields(self):
        cli = _make_client()
        resp = cli.post("/cases", json={"ticket_id": "TKT-001", "client": "bank_alpha"})
        body = resp.json()
        assert "case_id" in body
        assert "state" in body
        assert "ticket_id" in body
        assert "client" in body


# ── GET /cases/{id} ───────────────────────────────────────────────────────────

class TestGetCase:
    def test_returns_case(self):
        case = _make_case(state=CaseState.AWAITING_INPUT)
        cli = _make_client(get_case_returns=case)
        resp = cli.get(f"/cases/{case.case_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["case_id"] == case.case_id
        assert body["state"] == "AWAITING_INPUT"

    def test_not_found_returns_404(self):
        cli = _make_client(get_case_returns=None)
        resp = cli.get("/cases/nonexistent-id")
        assert resp.status_code == 404

    def test_503_when_service_not_ready(self):
        cli = _make_client(service_none=True)
        resp = cli.get("/cases/some-id")
        assert resp.status_code == 503

    def test_response_contains_slot_state(self):
        case = _make_case()
        case.slot_state = {"session_id": {"status": "FILLED", "value": "KID-X", "attempt_count": 0}}
        cli = _make_client(get_case_returns=case)
        resp = cli.get(f"/cases/{case.case_id}")
        body = resp.json()
        assert body["slot_state"]["session_id"]["status"] == "FILLED"


# ── POST /cases/{id}/message ──────────────────────────────────────────────────

class TestSendMessage:
    def test_returns_200_with_message_result(self):
        case = _make_case(state=CaseState.AWAITING_INPUT)
        result = ReceiveMessageResult(
            case_id=case.case_id,
            state=CaseState.AWAITING_INPUT,
            slot_values={"session_id": {"status": "FILLED", "value": "KID-X", "attempt_count": 0}},
            next_question={"slot_name": "phone_number", "prompt_text": "Provide last 4 digits.", "is_required": True},
        )
        cli = _make_client(get_case_returns=case, receive_returns=result)
        resp = cli.post(
            f"/cases/{case.case_id}/message",
            json={"message_text": "My session is KID-X"},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["case_id"] == case.case_id
        assert body["next_question"]["slot_name"] == "phone_number"

    def test_case_not_found_returns_404(self):
        cli = _make_client(get_case_returns=None)
        resp = cli.post("/cases/bad-id/message", json={"message_text": "hello"})
        assert resp.status_code == 404

    def test_explicit_slot_name_and_value(self):
        case = _make_case(state=CaseState.AWAITING_INPUT)
        cli = _make_client(get_case_returns=case)
        resp = cli.post(
            f"/cases/{case.case_id}/message",
            json={"message_text": "", "slot_name": "channel", "slot_value": "SMS"},
        )
        assert resp.status_code == 200

    def test_503_when_service_not_ready(self):
        cli = _make_client(service_none=True)
        resp = cli.post("/cases/x/message", json={"message_text": "hi"})
        assert resp.status_code == 503

    def test_all_slots_filled_flag_in_response(self):
        case = _make_case(state=CaseState.WORKFLOW_ACTIVE)
        result = ReceiveMessageResult(
            case_id=case.case_id,
            state=CaseState.WORKFLOW_ACTIVE,
            slot_values={},
            next_question=None,
            all_slots_filled=True,
        )
        cli = _make_client(get_case_returns=case, receive_returns=result)
        resp = cli.post(f"/cases/{case.case_id}/message", json={"message_text": "ok"})
        body = resp.json()
        assert body["all_slots_filled"] is True
        assert body["next_question"] is None

    def test_escalated_flag_in_response(self):
        case = _make_case(state=CaseState.ESCALATED)
        result = ReceiveMessageResult(
            case_id=case.case_id,
            state=CaseState.ESCALATED,
            slot_values={},
            next_question=None,
            escalated=True,
        )
        cli = _make_client(get_case_returns=case, receive_returns=result)
        resp = cli.post(f"/cases/{case.case_id}/message", json={"message_text": "bad"})
        body = resp.json()
        assert body["escalated"] is True

    def test_missing_message_text_returns_422(self):
        cli = _make_client()
        resp = cli.post("/cases/x/message", json={})
        assert resp.status_code == 422


# ── GET /cases/{id}/slots ─────────────────────────────────────────────────────

class TestGetSlots:
    def test_returns_slot_list_for_known_topic(self):
        case = _make_case(state=CaseState.AWAITING_INPUT, topic=TopicKey.VKYC_SESSION_FAILURE.value)
        cli = _make_client(get_case_returns=case)
        resp = cli.get(f"/cases/{case.case_id}/slots")
        assert resp.status_code == 200
        body = resp.json()
        assert body["case_id"] == case.case_id
        assert body["topic"] == TopicKey.VKYC_SESSION_FAILURE.value
        slots = {s["slot_name"]: s for s in body["slots"]}
        assert "session_id" in slots
        assert "phone_number" in slots
        assert slots["session_id"]["is_required"] is True

    def test_returns_empty_for_unknown_topic(self):
        case = _make_case(state=CaseState.NEW, topic=None)
        cli = _make_client(get_case_returns=case)
        resp = cli.get(f"/cases/{case.case_id}/slots")
        assert resp.status_code == 200
        body = resp.json()
        assert body["slots"] == []

    def test_case_not_found_returns_404(self):
        cli = _make_client(get_case_returns=None)
        resp = cli.get("/cases/bad-id/slots")
        assert resp.status_code == 404

    def test_503_when_service_not_ready(self):
        cli = _make_client(service_none=True)
        resp = cli.get("/cases/x/slots")
        assert resp.status_code == 503

    def test_slot_valid_values_returned_for_enum_slot(self):
        case = _make_case(state=CaseState.AWAITING_INPUT, topic=TopicKey.OTP_DELIVERY_FAILURE.value)
        cli = _make_client(get_case_returns=case)
        resp = cli.get(f"/cases/{case.case_id}/slots")
        body = resp.json()
        slots = {s["slot_name"]: s for s in body["slots"]}
        assert "channel" in slots
        assert slots["channel"]["valid_values"] is not None
        assert "SMS" in slots["channel"]["valid_values"]
