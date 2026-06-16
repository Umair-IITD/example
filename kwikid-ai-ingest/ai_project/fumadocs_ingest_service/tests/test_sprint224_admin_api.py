"""
tests/test_sprint224_admin_api.py

Sprint 2.24: POST /admin/reasoning/run endpoint tests.

Coverage:
  - Valid request returns 200 with status, result_id, outcome, recommended_action
  - Missing required investigation_result returns 422
  - Service unavailable returns 503
  - Unauthenticated returns 401
  - result dict present in response
  - should_escalate present in response
  - confidence present in response
  - Exception in service returns structured response (not raw traceback)
  - response is JSON-serializable
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(with_service: bool = True) -> TestClient:
    from fastapi import FastAPI
    from api.routes import reasoning_admin
    from security.dependencies import require_admin
    from case_engine.reasoning.service import build_reasoning_service

    app = FastAPI()

    async def _fake_require_admin():
        return None

    app.dependency_overrides[require_admin] = _fake_require_admin
    app.include_router(reasoning_admin.router)

    if with_service:
        app.state.reasoning_service = build_reasoning_service()

    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict) -> object:
    return client.post("/admin/reasoning/run", json=body)


VALID_INV = {
    "topic": "VKYC_Session_Failure",
    "evidence_ids": [],
    "root_cause": {
        "category":   "NETWORK_FAILURE",
        "confidence": 0.85,
        "escalate":   False,
    },
}

VALID_BODY = {
    "investigation_result": VALID_INV,
}


# ── Happy path ────────────────────────────────────────────────────────────────

class TestAdminReasoningHappyPath:
    def test_returns_200(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.status_code == 200

    def test_status_ok_in_response(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["status"] == "COMPLETED"

    def test_result_id_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["result_id"] != ""

    def test_outcome_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "outcome" in r.json()

    def test_recommended_action_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "recommended_action" in r.json()

    def test_should_escalate_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "should_escalate" in r.json()

    def test_confidence_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "confidence" in r.json()

    def test_result_dict_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "result" in r.json()
        assert isinstance(r.json()["result"], dict)

    def test_response_is_json_serializable(self):
        import json
        r = _post(_make_client(), VALID_BODY)
        json.dumps(r.json())

    def test_network_failure_returns_reset_session(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["recommended_action"] == "RESET_SESSION"

    def test_kyc_rejected_escalates(self):
        body = {
            "investigation_result": {
                "topic": "KYC",
                "root_cause": {"category": "KYC_REJECTED", "confidence": 0.95, "escalate": False},
            }
        }
        r = _post(_make_client(), body)
        assert r.json()["should_escalate"] is True


# ── Optional fields ───────────────────────────────────────────────────────────

class TestAdminReasoningOptionalFields:
    def test_knowledge_result_accepted(self):
        body = {**VALID_BODY, "knowledge_result": {"sop_match_found": False}}
        r    = _post(_make_client(), body)
        assert r.status_code == 200

    def test_workflow_id_accepted(self):
        body = {**VALID_BODY, "workflow_id": "wf-001"}
        r    = _post(_make_client(), body)
        assert r.status_code == 200

    def test_step_id_accepted(self):
        body = {**VALID_BODY, "step_id": "s-001"}
        r    = _post(_make_client(), body)
        assert r.status_code == 200

    def test_all_optional_fields_together(self):
        body = {
            **VALID_BODY,
            "knowledge_result": {"sop_match_found": True, "sop_match": {"entry": {"entry_id": "sop-1", "title": "session reset"}, "relevance_score": 0.9}},
            "workflow_id": "wf-abc",
            "step_id": "step-xyz",
        }
        r = _post(_make_client(), body)
        assert r.status_code == 200


# ── Validation errors ─────────────────────────────────────────────────────────

class TestAdminReasoningValidation:
    def test_missing_investigation_result_returns_422(self):
        r = _post(_make_client(), {})
        assert r.status_code == 422

    def test_null_investigation_result_returns_422(self):
        r = _post(_make_client(), {"investigation_result": None})
        assert r.status_code == 422


# ── Service unavailable ───────────────────────────────────────────────────────

class TestAdminReasoningServiceUnavailable:
    def test_no_service_returns_503(self):
        client = _make_client(with_service=False)
        r      = _post(client, VALID_BODY)
        assert r.status_code == 503

    def test_503_has_error_key(self):
        client = _make_client(with_service=False)
        r      = _post(client, VALID_BODY)
        assert "error" in r.json()

    def test_503_error_code_is_service_unavailable(self):
        client = _make_client(with_service=False)
        r      = _post(client, VALID_BODY)
        assert r.json()["error"]["code"] == "SERVICE_UNAVAILABLE"


# ── Authorization ─────────────────────────────────────────────────────────────

def _make_auth_client_with_service() -> TestClient:
    from fastapi import FastAPI
    from api.routes import reasoning_admin
    from case_engine.reasoning.service import build_reasoning_service
    from security.auth import ApiKeyAuthenticator
    from security.roles import Role

    app = FastAPI()
    app.include_router(reasoning_admin.router)
    app.state.reasoning_service = build_reasoning_service()
    app.state.authenticator = ApiKeyAuthenticator(
        {"test-admin-key": ("test-admin", Role.ADMIN)},
        auth_enabled=True,
    )
    return TestClient(app, raise_server_exceptions=False)


class TestAdminReasoningAuthorization:
    def test_no_api_key_returns_401(self):
        client = _make_auth_client_with_service()
        r      = client.post("/admin/reasoning/run", json=VALID_BODY)
        assert r.status_code == 401

    def test_wrong_api_key_returns_401(self):
        client = _make_auth_client_with_service()
        r      = client.post(
            "/admin/reasoning/run",
            json=VALID_BODY,
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_correct_admin_key_returns_200(self):
        client = _make_auth_client_with_service()
        r      = client.post(
            "/admin/reasoning/run",
            json=VALID_BODY,
            headers={"X-API-Key": "test-admin-key"},
        )
        assert r.status_code == 200


# ── Exception isolation ───────────────────────────────────────────────────────

class TestAdminReasoningExceptionIsolation:
    def test_exception_in_service_returns_structured_response(self):
        from fastapi import FastAPI
        from api.routes import reasoning_admin
        from security.dependencies import require_admin

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin
        app.include_router(reasoning_admin.router)

        bad_svc = MagicMock()
        bad_svc.reason.side_effect = RuntimeError("svc crash")
        app.state.reasoning_service = bad_svc

        client = TestClient(app, raise_server_exceptions=False)
        r      = _post(client, VALID_BODY)
        assert r.status_code in (200, 500)
        assert isinstance(r.json(), dict)

    def test_exception_response_has_no_raw_traceback(self):
        from fastapi import FastAPI
        from api.routes import reasoning_admin
        from security.dependencies import require_admin

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin
        app.include_router(reasoning_admin.router)

        bad_svc = MagicMock()
        bad_svc.reason.side_effect = RuntimeError("boom")
        app.state.reasoning_service = bad_svc

        client   = TestClient(app, raise_server_exceptions=False)
        r        = _post(client, VALID_BODY)
        body_str = r.text
        assert "Traceback" not in body_str
        assert "RuntimeError" not in body_str
