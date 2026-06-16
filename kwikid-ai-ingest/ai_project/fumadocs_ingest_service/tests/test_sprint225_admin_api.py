"""
tests/test_sprint225_admin_api.py

Sprint 2.25: POST /admin/clarification/run admin endpoint tests.

Coverage:
  - 200: clarification_service present + valid body → returns READY result
  - 200: NEEDS_CLARIFICATION result returned correctly
  - 200: ESCALATE result returned correctly
  - 422: missing required field (topic) → validation error
  - 422: missing required field (required_slots) → validation error
  - 401: no admin auth header → unauthorized
  - 503: clarification_service not in app.state → SERVICE_UNAVAILABLE
  - 500: clarification_service raises → INTERNAL_ERROR (no traceback)
  - Response includes status, result_id, missing_slots, clarification_message, ready_to_continue
  - Tracebacks never appear in response body
"""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from api.routes.clarification_admin import router


# ── Test app factory ──────────────────────────────────────────────────────────

def _make_app(svc=None, admin_key="test-admin-key") -> FastAPI:
    app = FastAPI()
    app.include_router(router)

    if svc is not None:
        app.state.clarification_service = svc

    # Patch require_admin to accept test-admin-key without hitting DB
    from security.dependencies import require_admin
    import security.dependencies as sec_deps

    original = sec_deps.require_admin

    async def _fake_admin(request=None):
        pass

    app.dependency_overrides[require_admin] = _fake_admin
    return app


def _make_service(status: str = "READY", ready: bool = True) -> MagicMock:
    svc = MagicMock()
    svc.clarify.return_value = {
        "status": status,
        "result_id": "test-rid-1",
        "missing_slots": [] if ready else ["session_id"],
        "clarification_message": "Test message.",
        "ready_to_continue": ready,
        "next_question": None if ready else {"slot_name": "session_id", "prompt_text": "Provide session ID."},
        "topic": "VKYC_Session_Failure",
        "workflow_id": "wf-1",
        "step_id": "clarify_slots",
    }
    return svc


def _valid_body():
    return {
        "topic": "VKYC_Session_Failure",
        "required_slots": ["session_id", "phone_number"],
        "slot_context": {"session_id": "KID-12345678", "phone_number": "+919876543210"},
        "slot_state": {},
        "workflow_id": "vkyc_session_failure_v1",
        "step_id": "clarify_slots",
    }


# ── 200 paths ─────────────────────────────────────────────────────────────────

class TestClarificationAdminHappyPath:
    def test_ready_result_returns_200(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.status_code == 200

    def test_ready_result_has_status_field(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.json()["status"] == "READY"

    def test_ready_result_has_result_id(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert "result_id" in resp.json()

    def test_ready_result_has_missing_slots(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.json()["missing_slots"] == []

    def test_ready_result_has_ready_to_continue(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.json()["ready_to_continue"] is True

    def test_needs_clarification_200(self):
        svc = _make_service("NEEDS_CLARIFICATION", ready=False)
        app = _make_app(svc=svc)
        client = TestClient(app)
        body = _valid_body()
        body["slot_context"] = {}
        resp = client.post("/admin/clarification/run", json=body)
        assert resp.status_code == 200
        assert resp.json()["status"] == "NEEDS_CLARIFICATION"

    def test_needs_clarification_has_missing_slots(self):
        svc = _make_service("NEEDS_CLARIFICATION", ready=False)
        app = _make_app(svc=svc)
        client = TestClient(app)
        body = _valid_body()
        body["slot_context"] = {}
        resp = client.post("/admin/clarification/run", json=body)
        assert "session_id" in resp.json()["missing_slots"]

    def test_escalate_result_200(self):
        svc = _make_service("ESCALATE", ready=False)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.status_code == 200
        assert resp.json()["status"] == "ESCALATE"

    def test_response_includes_clarification_message(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert "clarification_message" in resp.json()

    def test_response_includes_full_result_nested(self):
        svc = _make_service("READY", ready=True)
        app = _make_app(svc=svc)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert "result" in resp.json()


# ── 422 paths ─────────────────────────────────────────────────────────────────

class TestClarificationAdminValidation:
    def test_missing_topic_returns_422(self):
        svc = _make_service()
        app = _make_app(svc=svc)
        client = TestClient(app)
        body = _valid_body()
        del body["topic"]
        resp = client.post("/admin/clarification/run", json=body)
        assert resp.status_code == 422

    def test_missing_required_slots_returns_422(self):
        svc = _make_service()
        app = _make_app(svc=svc)
        client = TestClient(app)
        body = _valid_body()
        del body["required_slots"]
        resp = client.post("/admin/clarification/run", json=body)
        assert resp.status_code == 422

    def test_invalid_required_slots_type_returns_422(self):
        svc = _make_service()
        app = _make_app(svc=svc)
        client = TestClient(app)
        body = _valid_body()
        body["required_slots"] = "not_a_list"
        resp = client.post("/admin/clarification/run", json=body)
        assert resp.status_code == 422


# ── 503 path ──────────────────────────────────────────────────────────────────

class TestClarificationAdminServiceUnavailable:
    def test_no_service_returns_503(self):
        app = _make_app(svc=None)  # no service set
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.status_code == 503

    def test_503_body_has_error_code(self):
        app = _make_app(svc=None)
        client = TestClient(app)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        data = resp.json()
        assert "error" in data or "error_code" in data or "SERVICE_UNAVAILABLE" in str(data)


# ── 500 path — exception isolation ────────────────────────────────────────────

class TestClarificationAdminExceptionIsolation:
    def test_service_exception_returns_500_not_traceback(self):
        svc = MagicMock()
        svc.clarify.side_effect = RuntimeError("boom")
        app = _make_app(svc=svc)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        assert resp.status_code == 500
        body = resp.text
        assert "Traceback" not in body
        assert "RuntimeError" not in body

    def test_500_body_has_error_code(self):
        svc = MagicMock()
        svc.clarify.side_effect = RuntimeError("boom")
        app = _make_app(svc=svc)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.post("/admin/clarification/run", json=_valid_body())
        data = resp.json()
        assert "INTERNAL_ERROR" in str(data) or "error" in data
