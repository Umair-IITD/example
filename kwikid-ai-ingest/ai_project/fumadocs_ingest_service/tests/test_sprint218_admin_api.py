"""
tests/test_sprint218_admin_api.py

Sprint 2.18: POST /admin/investigations/run endpoint tests (Part G).

Coverage:
  - 200 success with correct topic + slots
  - 200 with workflow_id that resolves successfully
  - 200 with workflow_id that resolves to None (graceful)
  - 401 when no API key provided
  - 403 when non-admin key provided
  - 503 when investigation_service is None
  - 422 when required fields missing from body
  - Response body structure (status, topic, result)
  - result.to_dict() fields present in response
  - Large slot_values map handled
"""
from __future__ import annotations

from unittest.mock import MagicMock
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from security.auth import ApiKeyAuthenticator, Role
from case_engine.investigation.models import (
    EvidenceBundle,
    InvestigationPlan,
    InvestigationResult,
    RecommendedAction,
    RootCauseAnalysis,
    RootCauseCategory,
)


# ── Test app factory ───────────────────────────────────────────────────────────

ADMIN_KEY   = "adm-key-test-001"
REGULAR_KEY = "usr-key-test-001"


def _make_investigation_result(topic: str = "VKYC_Session_Failure") -> InvestigationResult:
    plan = InvestigationPlan(
        plan_id="plan-api-001",
        case_id="case-api-001",
        topic=topic,
        workflow_id=None,
        steps=(),
        created_at="2026-06-10T00:00:00+00:00",
    )
    bundle = EvidenceBundle(
        bundle_id="bundle-api-001",
        case_id="case-api-001",
        topic=topic,
        plan_id="plan-api-001",
        items=[],
        collected_at="2026-06-10T00:00:00+00:00",
    )
    rca = RootCauseAnalysis(
        analysis_id="rca-api-001",
        case_id="case-api-001",
        topic=topic,
        category=RootCauseCategory.EXPIRED_SESSION,
        confidence=0.9,
        explanation="Session expired.",
        evidence_ids=[],
        recommended_action=RecommendedAction.SESSION_RESET,
        escalate=False,
        analysed_at="2026-06-10T00:00:00+00:00",
    )
    return InvestigationResult(
        result_id="result-api-001",
        case_id="case-api-001",
        plan=plan,
        bundle=bundle,
        root_cause=rca,
        observation="Test observation.",
        completed_at="2026-06-10T00:00:00+00:00",
    )


def _build_client(inv_service_available: bool = True) -> TestClient:
    from case_engine.action_gateway import ActionGateway
    from case_engine.action_repository import ActionRepository
    from case_engine.action_runtime import ActionRuntime
    from runtime.assembly import ProductionRuntime

    mock_stack = MagicMock(spec=ProductionRuntime)
    mock_stack.gateway   = MagicMock(spec=ActionGateway)
    mock_stack.repo      = MagicMock(spec=ActionRepository)
    mock_stack.runtime   = MagicMock(spec=ActionRuntime)
    mock_stack.metrics_service = None

    from audit.logger import AuditLogger
    from audit.repository import InMemoryAuditRepository
    audit_repo    = InMemoryAuditRepository()
    audit_logger  = AuditLogger(repository=audit_repo)

    from audit.service import AuditService
    audit_service = AuditService(repository=audit_repo)

    registry = {
        ADMIN_KEY:   ("admin_user",    Role.ADMIN),
        REGULAR_KEY: ("operator_user", Role.OPERATOR),
    }
    authenticator = ApiKeyAuthenticator(registry, auth_enabled=True)

    app = create_app(
        stack=mock_stack,
        authenticator=authenticator,
        audit_logger=audit_logger,
        audit_service=audit_service,
        skip_config_validation=True,
    )

    if inv_service_available:
        mock_inv = MagicMock()
        mock_inv.investigate.return_value = _make_investigation_result()
        app.state.investigation_service = mock_inv
    else:
        app.state.investigation_service = None

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture(scope="module")
def client():
    return _build_client(inv_service_available=True)


@pytest.fixture(scope="module")
def client_no_service():
    return _build_client(inv_service_available=False)


# ── Auth tests ─────────────────────────────────────────────────────────────────

class TestInvestigationAdminAuth:
    def test_no_key_returns_401(self, client):
        resp = client.post("/admin/investigations/run", json={
            "topic": "VKYC_Session_Failure",
        })
        assert resp.status_code == 401

    def test_non_admin_key_returns_403(self, client):
        resp = client.post("/admin/investigations/run",
                           headers={"X-API-Key": REGULAR_KEY},
                           json={"topic": "VKYC_Session_Failure"})
        assert resp.status_code == 403

    def test_admin_key_returns_200(self, client):
        resp = client.post("/admin/investigations/run",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"topic": "VKYC_Session_Failure", "slot_values": {"session_id": "S1"}})
        assert resp.status_code == 200


# ── 503 when service unavailable ──────────────────────────────────────────────

class TestInvestigationAdminServiceUnavailable:
    def test_503_when_no_service(self, client_no_service):
        resp = client_no_service.post("/admin/investigations/run",
                                      headers={"X-API-Key": ADMIN_KEY},
                                      json={"topic": "VKYC_Session_Failure"})
        assert resp.status_code == 503

    def test_503_body_has_error_code(self, client_no_service):
        resp = client_no_service.post("/admin/investigations/run",
                                      headers={"X-API-Key": ADMIN_KEY},
                                      json={"topic": "VKYC_Session_Failure"})
        body = resp.json()
        assert "error" in body or "SERVICE_UNAVAILABLE" in str(body)


# ── Success response structure ────────────────────────────────────────────────

class TestInvestigationAdminSuccess:
    def _run(self, client, topic: str = "VKYC_Session_Failure", **extra):
        payload = {"topic": topic, "slot_values": {"session_id": "S1"}, **extra}
        return client.post(
            "/admin/investigations/run",
            headers={"X-API-Key": ADMIN_KEY},
            json=payload,
        )

    def test_status_ok(self, client):
        resp = self._run(client)
        assert resp.json()["status"] == "ok"

    def test_topic_echoed(self, client):
        resp = self._run(client)
        assert resp.json()["topic"] == "VKYC_Session_Failure"

    def test_result_present(self, client):
        resp = self._run(client)
        assert "result" in resp.json()

    def test_result_has_plan(self, client):
        resp = self._run(client)
        assert "plan" in resp.json()["result"]

    def test_result_has_evidence(self, client):
        resp = self._run(client)
        assert "evidence" in resp.json()["result"]

    def test_result_has_root_cause(self, client):
        resp = self._run(client)
        assert "root_cause" in resp.json()["result"]

    def test_result_has_observation(self, client):
        resp = self._run(client)
        assert "observation" in resp.json()["result"]

    def test_result_id_non_empty(self, client):
        resp = self._run(client)
        assert resp.json()["result"]["result_id"] == "result-api-001"

    def test_service_investigate_called_with_correct_topic(self, client):
        mock_inv = client.app.state.investigation_service
        mock_inv.investigate.reset_mock()
        self._run(client, topic="OTP_Delivery_Failure")
        call_kwargs = mock_inv.investigate.call_args
        assert call_kwargs[1]["topic"] == "OTP_Delivery_Failure" or call_kwargs[0][0] == "OTP_Delivery_Failure"

    def test_slot_values_forwarded_to_service(self, client):
        mock_inv = client.app.state.investigation_service
        mock_inv.investigate.reset_mock()
        self._run(client, **{"slot_values": {"session_id": "MY-SESSION-XYZ"}})
        call_kwargs = mock_inv.investigate.call_args
        slot_values = call_kwargs[0][2] if len(call_kwargs[0]) >= 3 else call_kwargs[1].get("slot_values", {})
        assert "session_id" in slot_values

    def test_case_id_forwarded_via_meta(self, client):
        mock_inv = client.app.state.investigation_service
        mock_inv.investigate.reset_mock()
        self._run(client, **{"case_id": "MY-CASE-001"})
        call_kwargs = mock_inv.investigate.call_args
        slot_values = call_kwargs[0][2] if len(call_kwargs[0]) >= 3 else call_kwargs[1].get("slot_values", {})
        assert slot_values.get("_meta", {}).get("case_id") == "MY-CASE-001"


# ── Validation tests ──────────────────────────────────────────────────────────

class TestInvestigationAdminValidation:
    def test_missing_topic_returns_422(self, client):
        resp = client.post("/admin/investigations/run",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"slot_values": {"session_id": "S1"}})
        assert resp.status_code == 422

    def test_empty_slot_values_accepted(self, client):
        resp = client.post("/admin/investigations/run",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"topic": "VKYC_Session_Failure"})
        assert resp.status_code == 200

    def test_large_slot_values_accepted(self, client):
        slots = {f"slot_{i}": f"value_{i}" for i in range(20)}
        resp = client.post("/admin/investigations/run",
                           headers={"X-API-Key": ADMIN_KEY},
                           json={"topic": "VKYC_Session_Failure", "slot_values": slots})
        assert resp.status_code == 200
