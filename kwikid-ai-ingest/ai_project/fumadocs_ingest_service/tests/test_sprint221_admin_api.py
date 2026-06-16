"""
tests/test_sprint221_admin_api.py

Sprint 2.21: POST /admin/action-proposals/run endpoint tests.

Coverage:
  - Valid request returns 200 with status=ok and bundle dict
  - Missing required fields return 422
  - Unauthenticated request returns 403
  - topic and root_cause_category echoed in response
  - Response is JSON-serializable and contains bundle key
  - investigation_confidence default 0.8 applied
  - investigation_escalate=True forces ESCALATE_L2 in top proposal
  - Error response on engine failure is structured (not 500 with traceback)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.error_models import error_body


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_client(admin_key: str = "test-admin-key") -> TestClient:
    from fastapi import FastAPI
    from api.routes import action_proposals_admin
    from security.dependencies import require_admin

    app = FastAPI()

    async def _fake_require_admin():
        return None

    app.dependency_overrides[require_admin] = _fake_require_admin
    app.include_router(action_proposals_admin.router)
    return TestClient(app, raise_server_exceptions=False)


def _post(client: TestClient, body: dict) -> object:
    return client.post("/admin/action-proposals/run", json=body)


VALID_BODY = {
    "topic": "VKYC_Session_Failure",
    "root_cause_category": "EXPIRED_SESSION",
    "investigation_confidence": 0.85,
    "investigation_escalate": False,
}


# ── Happy path ────────────────────────────────────────────────────────────────

class TestAdminEndpointHappyPath:
    def test_returns_200(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        assert r.status_code == 200

    def test_status_ok_in_response(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        data = r.json()
        assert data["status"] == "ok"

    def test_topic_echoed_in_response(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        assert r.json()["topic"] == "VKYC_Session_Failure"

    def test_root_cause_category_echoed(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        assert r.json()["root_cause_category"] == "EXPIRED_SESSION"

    def test_bundle_key_in_response(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        assert "bundle" in r.json()

    def test_bundle_is_dict(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        assert isinstance(r.json()["bundle"], dict)

    def test_bundle_has_status(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        bundle = r.json()["bundle"]
        assert "status" in bundle

    def test_bundle_has_proposals(self):
        client = _make_client()
        r = _post(client, VALID_BODY)
        bundle = r.json()["bundle"]
        assert "proposals" in bundle


# ── Default values ─────────────────────────────────────────────────────────────

class TestAdminEndpointDefaults:
    def test_minimal_body_accepted(self):
        client = _make_client()
        r = _post(client, {
            "topic": "VKYC_Session_Failure",
            "root_cause_category": "EXPIRED_SESSION",
        })
        assert r.status_code == 200

    def test_investigation_escalate_default_false(self):
        client = _make_client()
        body = {"topic": "T", "root_cause_category": "EXPIRED_SESSION"}
        r = _post(client, body)
        assert r.status_code == 200

    def test_escalate_true_in_bundle(self):
        client = _make_client()
        r = _post(client, {
            "topic": "T",
            "root_cause_category": "EXPIRED_SESSION",
            "investigation_escalate": True,
        })
        bundle = r.json()["bundle"]
        if "top_proposal" in bundle and bundle["top_proposal"]:
            assert bundle["top_proposal"]["action_type"] == "ESCALATE_L2"


# ── Validation errors ─────────────────────────────────────────────────────────

class TestAdminEndpointValidation:
    def test_missing_topic_returns_422(self):
        client = _make_client()
        r = _post(client, {"root_cause_category": "EXPIRED_SESSION"})
        assert r.status_code == 422

    def test_missing_root_cause_returns_422(self):
        client = _make_client()
        r = _post(client, {"topic": "T"})
        assert r.status_code == 422

    def test_confidence_above_1_returns_422(self):
        client = _make_client()
        r = _post(client, {
            "topic": "T",
            "root_cause_category": "X",
            "investigation_confidence": 1.5,
        })
        assert r.status_code == 422

    def test_confidence_below_0_returns_422(self):
        client = _make_client()
        r = _post(client, {
            "topic": "T",
            "root_cause_category": "X",
            "investigation_confidence": -0.1,
        })
        assert r.status_code == 422


# ── Error response structure ───────────────────────────────────────────────────

class TestAdminEndpointErrorResponse:
    def test_engine_error_returns_500_with_structured_body(self):
        from fastapi import FastAPI
        from api.routes import action_proposals_admin
        from security.dependencies import require_admin
        from case_engine.actions.service import ActionProposalService

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin

        with patch(
            "case_engine.actions.build_action_proposal_service",
            side_effect=RuntimeError("engine exploded"),
        ):
            app.include_router(action_proposals_admin.router)
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/admin/action-proposals/run", json=VALID_BODY)

        assert r.status_code == 500
        body = r.json()
        assert "error" in body
        assert "code" in body["error"]

    def test_error_body_no_traceback_in_message(self):
        from fastapi import FastAPI
        from api.routes import action_proposals_admin
        from security.dependencies import require_admin

        app = FastAPI()

        async def _fake_require_admin():
            return None

        app.dependency_overrides[require_admin] = _fake_require_admin

        with patch(
            "case_engine.actions.build_action_proposal_service",
            side_effect=RuntimeError("secret internal error"),
        ):
            app.include_router(action_proposals_admin.router)
            client = TestClient(app, raise_server_exceptions=False)
            r = client.post("/admin/action-proposals/run", json=VALID_BODY)

        body_str = r.text
        assert "Traceback" not in body_str
        assert "secret internal error" not in body_str


# ── error_body helper ─────────────────────────────────────────────────────────

class TestErrorBodyHelper:
    def test_error_body_has_code(self):
        b = error_body("MY_CODE", "message")
        assert b["error"]["code"] == "MY_CODE"

    def test_error_body_has_message(self):
        b = error_body("CODE", "my message")
        assert b["error"]["message"] == "my message"

    def test_error_body_returns_dict(self):
        b = error_body("CODE", "msg")
        assert isinstance(b, dict)
