"""
tests/test_sprint227_admin_api.py

Sprint 2.27: Tests for POST /admin/adapters/run and GET /admin/adapters/health.
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.adapter_admin import router
from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.adapter_router import AdapterRouter
from security.dependencies import require_admin


# ── Helpers ────────────────────────────────────────────────────────────────────

async def _fake_require_admin():
    return None


def _make_client(with_router: bool = True) -> TestClient:
    app = FastAPI()
    app.dependency_overrides[require_admin] = _fake_require_admin
    app.include_router(router)

    if with_router:
        reg = AdapterRegistry.build_default()
        app.state.adapter_router = AdapterRouter(registry=reg)
    else:
        app.state.adapter_router = None

    return TestClient(app, raise_server_exceptions=False)


def _make_auth_client() -> TestClient:
    """Client without auth bypass — require_admin stays active."""
    app = FastAPI()
    app.include_router(router)
    reg = AdapterRegistry.build_default()
    app.state.adapter_router = AdapterRouter(registry=reg)
    return TestClient(app, raise_server_exceptions=False)


# ── Run endpoint tests ─────────────────────────────────────────────────────────

class TestAdapterRunEndpoint:
    def test_run_returns_200(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        assert resp.status_code == 200

    def test_run_response_has_status_ok(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        assert resp.json()["status"] == "ok"

    def test_run_response_has_request_id(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        data = resp.json()
        assert "request_id" in data

    def test_run_response_has_adapter_type(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        data = resp.json()
        assert "adapter_type" in data

    def test_run_response_has_success_field(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        data = resp.json()
        assert "success" in data

    def test_run_success_is_true_for_routed_action(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        assert resp.json()["success"] is True

    def test_run_with_case_id(self):
        client = _make_client()
        resp = client.post(
            "/admin/adapters/run",
            json={"action_type": "otp_resend", "case_id": "case-test-001"},
        )
        assert resp.status_code == 200

    def test_run_with_action_params(self):
        client = _make_client()
        resp = client.post(
            "/admin/adapters/run",
            json={
                "action_type": "post_ticket_note",
                "action_params": {"ticket_id": "T-123", "body": "test note"},
                "case_id": "case-abc",
            },
        )
        assert resp.status_code == 200

    def test_run_503_without_router(self):
        client = _make_client(with_router=False)
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        assert resp.status_code == 503

    def test_run_503_body_has_error_envelope(self):
        client = _make_client(with_router=False)
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        body = resp.json()
        assert "error" in body
        assert "code" in body["error"]

    def test_run_all_routing_table_actions_return_200(self):
        client = _make_client()
        from case_engine.adapters.execution_adapter import list_routable_action_types
        for action_type in list_routable_action_types():
            resp = client.post(
                "/admin/adapters/run",
                json={"action_type": action_type},
            )
            assert resp.status_code == 200, f"{action_type} failed with {resp.status_code}"

    def test_run_unknown_action_still_returns_200(self):
        client = _make_client()
        resp = client.post(
            "/admin/adapters/run",
            json={"action_type": "completely_unknown_xyz_action"},
        )
        assert resp.status_code == 200

    def test_run_missing_action_type_returns_422(self):
        client = _make_client()
        resp = client.post("/admin/adapters/run", json={})
        assert resp.status_code == 422

    def test_run_requires_auth(self):
        client = _make_auth_client()
        resp = client.post("/admin/adapters/run", json={"action_type": "otp_resend"})
        # Without authenticator wired, auth subsystem returns 503;
        # with authenticator but no key: 401; wrong role: 403.
        assert resp.status_code in (401, 403, 503)

    def test_run_create_l2_ticket_routes_to_asana(self):
        client = _make_client()
        resp = client.post(
            "/admin/adapters/run",
            json={"action_type": "create_l2_ticket"},
        )
        assert resp.status_code == 200
        assert resp.json()["adapter_type"] == "ASANA"

    def test_run_emit_metric_routes_to_monitoring(self):
        client = _make_client()
        resp = client.post(
            "/admin/adapters/run",
            json={"action_type": "emit_metric"},
        )
        assert resp.status_code == 200
        assert resp.json()["adapter_type"] == "MONITORING"


# ── Health endpoint tests ──────────────────────────────────────────────────────

class TestAdapterHealthEndpoint:
    def test_health_returns_200_with_all_healthy(self):
        client = _make_client()
        resp = client.get("/admin/adapters/health")
        assert resp.status_code == 200

    def test_health_body_has_overall_healthy(self):
        client = _make_client()
        resp = client.get("/admin/adapters/health")
        assert "overall_healthy" in resp.json()

    def test_health_overall_healthy_is_true(self):
        client = _make_client()
        resp = client.get("/admin/adapters/health")
        assert resp.json()["overall_healthy"] is True

    def test_health_body_has_adapter_count(self):
        client = _make_client()
        resp = client.get("/admin/adapters/health")
        assert resp.json().get("adapter_count") == 4

    def test_health_body_has_adapters_dict(self):
        client = _make_client()
        resp = client.get("/admin/adapters/health")
        assert "adapters" in resp.json()

    def test_health_503_without_router(self):
        client = _make_client(with_router=False)
        resp = client.get("/admin/adapters/health")
        assert resp.status_code == 503

    def test_health_requires_auth(self):
        client = _make_auth_client()
        resp = client.get("/admin/adapters/health")
        assert resp.status_code in (401, 403, 503)


# ── Route registration tests ───────────────────────────────────────────────────

class TestAdapterAdminRouterRegistration:
    def test_router_prefix_is_correct(self):
        assert router.prefix == "/admin/adapters"

    def test_router_has_run_route(self):
        paths = [r.path for r in router.routes]
        assert any("/run" in p for p in paths)

    def test_router_has_health_route(self):
        paths = [r.path for r in router.routes]
        assert any("/health" in p for p in paths)

    def test_router_tags_include_adapter_admin(self):
        assert "Adapter Admin" in router.tags
