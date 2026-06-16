"""
tests/test_sprint223_admin_api.py

Sprint 2.23: POST /admin/executions/run endpoint tests.

Coverage:
  - Valid request returns 200 with status=ok and bundle keys
  - Missing required fields return 422
  - Unauthenticated request returns 403
  - action_type echoed in response
  - final_status present in response
  - bundle_id present and non-empty in response
  - total_attempts present in response
  - result dict present in response
  - service unavailable returns 503
  - simulate_failure action returns FAILED final_status
  - action_namespace / risk_level / case_id / workflow_id / step_id optional fields accepted
  - Exception in svc.process still returns structured response (not raw traceback)
  - response is JSON-serializable
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.error_models import error_body


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(with_service: bool = True) -> TestClient:
    from fastapi import FastAPI
    from api.routes import execution_admin
    from security.dependencies import require_admin
    from case_engine.execution.service import build_execution_service

    app = FastAPI()

    async def _fake_require_admin():
        return None

    app.dependency_overrides[require_admin] = _fake_require_admin
    app.include_router(execution_admin.router)

    if with_service:
        app.state.execution_service = build_execution_service()

    return TestClient(app, raise_server_exceptions=False)


def _make_auth_client() -> TestClient:
    """Client that does NOT bypass auth -- require_admin stays wired."""
    from fastapi import FastAPI
    from api.routes import execution_admin

    app = FastAPI()
    app.include_router(execution_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict) -> object:
    return client.post("/admin/executions/run", json=body)


VALID_BODY = {
    "action_type": "resend_otp",
    "action_params": {},
}


# ── Happy path ────────────────────────────────────────────────────────────────

class TestAdminRunHappyPath:
    def test_returns_200(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.status_code == 200

    def test_status_ok_in_response(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["status"] == "ok"

    def test_action_type_echoed_in_response(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["action_type"] == "resend_otp"

    def test_bundle_id_present_and_non_empty(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["bundle_id"] != ""

    def test_final_status_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "final_status" in r.json()

    def test_total_attempts_present(self):
        r = _post(_make_client(), VALID_BODY)
        assert "total_attempts" in r.json()

    def test_result_key_present_and_is_dict(self):
        r = _post(_make_client(), VALID_BODY)
        data = r.json()
        assert "result" in data
        assert isinstance(data["result"], dict)

    def test_success_action_returns_SUCCESS_status(self):
        r = _post(_make_client(), VALID_BODY)
        assert r.json()["final_status"] == "SUCCESS"

    def test_response_is_json_serializable(self):
        import json
        r = _post(_make_client(), VALID_BODY)
        json.dumps(r.json())

    def test_result_contains_bundle_id(self):
        r = _post(_make_client(), VALID_BODY)
        result = r.json()["result"]
        assert "bundle_id" in result


# ── Failure action ────────────────────────────────────────────────────────────

class TestAdminRunFailureAction:
    def test_simulate_failure_returns_200(self):
        body = {"action_type": "simulate_failure", "action_params": {}}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_simulate_failure_final_status_failed(self):
        body = {"action_type": "simulate_failure", "action_params": {}}
        r = _post(_make_client(), body)
        assert r.json()["final_status"] == "FAILED"

    def test_simulate_failure_total_attempts_1(self):
        body = {"action_type": "simulate_failure", "action_params": {}}
        r = _post(_make_client(), body)
        assert r.json()["total_attempts"] == 1


# ── Optional fields ───────────────────────────────────────────────────────────

class TestAdminRunOptionalFields:
    def test_action_namespace_accepted(self):
        body = {**VALID_BODY, "action_namespace": "otp"}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_risk_level_safe_accepted(self):
        body = {**VALID_BODY, "risk_level": "SAFE"}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_case_id_accepted(self):
        body = {**VALID_BODY, "case_id": "case-001"}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_workflow_id_accepted(self):
        body = {**VALID_BODY, "workflow_id": "wf-001"}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_step_id_accepted(self):
        body = {**VALID_BODY, "step_id": "step-001"}
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_all_optional_fields_together(self):
        body = {
            **VALID_BODY,
            "action_namespace": "otp",
            "risk_level": "REVERSIBLE",
            "case_id": "case-x",
            "workflow_id": "wf-x",
            "step_id": "step-x",
        }
        r = _post(_make_client(), body)
        assert r.status_code == 200

    def test_action_params_with_values(self):
        body = {
            "action_type": "resend_otp",
            "action_params": {"phone": "1234567890", "channel": "sms"},
        }
        r = _post(_make_client(), body)
        assert r.status_code == 200


# ── Validation errors ─────────────────────────────────────────────────────────

class TestAdminRunValidation:
    def test_missing_action_type_returns_422(self):
        r = _post(_make_client(), {"action_params": {}})
        assert r.status_code == 422

    def test_empty_body_returns_422(self):
        r = _post(_make_client(), {})
        assert r.status_code == 422

    def test_action_type_none_returns_422(self):
        r = _post(_make_client(), {"action_type": None, "action_params": {}})
        assert r.status_code == 422


# ── Authorization ─────────────────────────────────────────────────────────────

def _make_auth_client_with_service() -> TestClient:
    """Client with real auth wired (authenticator + service), to test rejection."""
    from fastapi import FastAPI
    from api.routes import execution_admin
    from case_engine.execution.service import build_execution_service
    from security.auth import ApiKeyAuthenticator
    from security.roles import Role

    app = FastAPI()
    app.include_router(execution_admin.router)
    app.state.execution_service = build_execution_service()
    # Wiring an authenticator with only a known admin key — wrong/missing keys get 401/403
    app.state.authenticator = ApiKeyAuthenticator(
        {"test-admin-secret": ("test-admin", Role.ADMIN)},
        auth_enabled=True,
    )
    return TestClient(app, raise_server_exceptions=False)


class TestAdminRunAuthorization:
    def test_no_api_key_returns_401(self):
        client = _make_auth_client_with_service()
        r = client.post("/admin/executions/run", json=VALID_BODY)
        assert r.status_code == 401

    def test_wrong_api_key_returns_401(self):
        client = _make_auth_client_with_service()
        r = client.post(
            "/admin/executions/run",
            json=VALID_BODY,
            headers={"X-API-Key": "wrong-key"},
        )
        assert r.status_code == 401

    def test_correct_admin_key_returns_200(self):
        client = _make_auth_client_with_service()
        r = client.post(
            "/admin/executions/run",
            json=VALID_BODY,
            headers={"X-API-Key": "test-admin-secret"},
        )
        assert r.status_code == 200


# ── Service unavailable ───────────────────────────────────────────────────────

class TestAdminRunServiceUnavailable:
    def test_no_execution_service_returns_503(self):
        client = _make_client(with_service=False)
        r = _post(client, VALID_BODY)
        assert r.status_code == 503

    def test_503_response_has_error_key(self):
        client = _make_client(with_service=False)
        r = _post(client, VALID_BODY)
        data = r.json()
        assert "error" in data

    def test_503_error_code_is_service_unavailable(self):
        client = _make_client(with_service=False)
        r = _post(client, VALID_BODY)
        data = r.json()
        assert data["error"]["code"] == "SERVICE_UNAVAILABLE"


# ── Exception isolation ───────────────────────────────────────────────────────

class TestAdminRunExceptionIsolation:
    def test_exception_in_service_returns_structured_response(self):
        from fastapi import FastAPI
        from api.routes import execution_admin
        from security.dependencies import require_admin

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin
        app.include_router(execution_admin.router)

        bad_svc = MagicMock()
        bad_svc.process.side_effect = RuntimeError("svc crash")
        app.state.execution_service = bad_svc

        client = TestClient(app, raise_server_exceptions=False)
        r = _post(client, VALID_BODY)
        assert r.status_code in (200, 500)
        data = r.json()
        assert isinstance(data, dict)

    def test_exception_response_has_no_raw_traceback(self):
        from fastapi import FastAPI
        from api.routes import execution_admin
        from security.dependencies import require_admin

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin
        app.include_router(execution_admin.router)

        bad_svc = MagicMock()
        bad_svc.process.side_effect = RuntimeError("boom")
        app.state.execution_service = bad_svc

        client = TestClient(app, raise_server_exceptions=False)
        r = _post(client, VALID_BODY)
        body_str = r.text
        assert "Traceback" not in body_str
        assert "RuntimeError" not in body_str


# ── Result structure ──────────────────────────────────────────────────────────

class TestAdminRunResultStructure:
    def test_result_has_bundle_id_key(self):
        r = _post(_make_client(), VALID_BODY)
        assert "bundle_id" in r.json()["result"]

    def test_result_has_final_status_key(self):
        r = _post(_make_client(), VALID_BODY)
        assert "final_status" in r.json()["result"]

    def test_result_has_action_type_key(self):
        r = _post(_make_client(), VALID_BODY)
        assert "action_type" in r.json()["result"]

    def test_result_has_attempts_key(self):
        r = _post(_make_client(), VALID_BODY)
        assert "attempts" in r.json()["result"]

    def test_result_attempts_is_list_with_one_item(self):
        r = _post(_make_client(), VALID_BODY)
        attempts = r.json()["result"]["attempts"]
        assert isinstance(attempts, list)
        assert len(attempts) == 1

    def test_retry_ocr_returns_success(self):
        body = {"action_type": "retry_ocr", "action_params": {}}
        r = _post(_make_client(), body)
        assert r.json()["final_status"] == "SUCCESS"
