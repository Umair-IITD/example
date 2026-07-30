"""
tests/test_sprint217_admin_api.py

Sprint 2.17 Part F — Admin Visibility API Tests

Covers:
  - GET /admin/tools/ — list all tools
  - GET /admin/tools/{tool_name} — get specific tool
  - GET /admin/workflows/playbooks — list all playbooks
  - GET /admin/workflows/playbooks/{workflow_id} — get specific playbook
  - Auth required for all endpoints
  - 503 when registry not initialised
  - 404 for unknown tool / workflow_id
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.tool_admin import router as tool_router
from api.routes.workflow_admin import router as wf_router
from case_engine.tools.tool_registry import ToolRegistry
from case_engine.workflows.playbook_registry import PlaybookRegistry
from security.dependencies import require_admin


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_app_with_registries(
    tool_registry: ToolRegistry | None = None,
    playbook_registry: PlaybookRegistry | None = None,
) -> FastAPI:
    app = FastAPI()

    async def _noop_auth():
        return True

    app.include_router(tool_router)
    app.include_router(wf_router)
    app.dependency_overrides[require_admin] = _noop_auth

    if tool_registry is not None:
        app.state.tool_registry = tool_registry
    if playbook_registry is not None:
        app.state.playbook_registry = playbook_registry

    return app


@pytest.fixture
def default_tool_registry() -> ToolRegistry:
    return ToolRegistry.build_default()


@pytest.fixture
def default_playbook_registry() -> PlaybookRegistry:
    return PlaybookRegistry.build()


@pytest.fixture
def full_client(default_tool_registry, default_playbook_registry) -> TestClient:
    app = _make_app_with_registries(default_tool_registry, default_playbook_registry)
    return TestClient(app)


@pytest.fixture
def no_registry_client() -> TestClient:
    app = _make_app_with_registries(None, None)
    return TestClient(app)


# ── GET /admin/tools/ ─────────────────────────────────────────────────────────

class TestListTools:
    def test_200_with_all_5_tools(self, full_client):
        resp = full_client.get("/admin/tools/")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert isinstance(data["tools"], list)
        assert len(data["tools"]) == 5

    def test_response_has_tool_fields(self, full_client):
        resp = full_client.get("/admin/tools/")
        tools = resp.json()["tools"]
        for tool in tools:
            assert "tool_name" in tool
            assert "description" in tool
            assert "required_inputs" in tool
            assert "output_schema" in tool
            assert "version" in tool

    def test_503_when_no_registry(self, no_registry_client):
        resp = no_registry_client.get("/admin/tools/")
        assert resp.status_code == 503

    def test_tools_sorted_by_name(self, full_client):
        resp = full_client.get("/admin/tools/")
        names = [t["tool_name"] for t in resp.json()["tools"]]
        assert names == sorted(names)

    def test_all_5_expected_tools_present(self, full_client):
        resp = full_client.get("/admin/tools/")
        names = {t["tool_name"] for t in resp.json()["tools"]}
        expected = {
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
            "GetFailureReasonTool",
            "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
        }
        assert names == expected

    def test_empty_registry_returns_200_with_zero_tools(self):
        app = _make_app_with_registries(ToolRegistry(), None)
        client = TestClient(app)
        resp = client.get("/admin/tools/")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ── GET /admin/tools/{tool_name} ──────────────────────────────────────────────

class TestGetTool:
    def test_200_for_known_tool(self, full_client):
        resp = full_client.get("/admin/tools/GetSessionDetailsTool")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tool_name"] == "GetSessionDetailsTool"
        assert "session_id" in data["required_inputs"]

    def test_response_has_output_schema(self, full_client):
        resp = full_client.get("/admin/tools/GetUserDetailsTool")
        assert resp.status_code == 200
        schema = resp.json()["output_schema"]
        assert "kyc_status" in schema

    def test_404_for_unknown_tool(self, full_client):
        resp = full_client.get("/admin/tools/NonExistentTool")
        assert resp.status_code == 404

    def test_503_when_no_registry(self, no_registry_client):
        resp = no_registry_client.get("/admin/tools/GetSessionDetailsTool")
        assert resp.status_code == 503

    def test_all_5_tools_individually_accessible(self, full_client):
        tools = [
            "GetSessionDetailsTool",
            "GetUserDetailsTool",
            "GetFailureReasonTool",
            "GetCaseHistoryTool",
            "GetOnboardingStatusTool",
        ]
        for name in tools:
            resp = full_client.get(f"/admin/tools/{name}")
            assert resp.status_code == 200, f"Tool {name} returned {resp.status_code}"


# ── GET /admin/workflows/playbooks ────────────────────────────────────────────

class TestListPlaybooks:
    def test_200_with_5_playbooks(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert isinstance(data["playbooks"], list)

    def test_playbooks_have_investigation_metadata(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks")
        for pb in resp.json()["playbooks"]:
            assert "investigation_steps" in pb
            assert "tool_candidates" in pb
            assert "resolution_paths" in pb

    def test_503_when_no_playbook_registry(self, no_registry_client):
        resp = no_registry_client.get("/admin/workflows/playbooks")
        assert resp.status_code == 503

    def test_all_5_topic_names_present(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks")
        topics = {pb["topic"] for pb in resp.json()["playbooks"]}
        expected = {
            "VKYC_Session_Failure",
            "OTP_Delivery_Failure",
            "API_Callback_Failure",
            "Document_OCR_Failure",
            "Agent_Portal_Issue",
        }
        assert topics == expected

    def test_steps_included_in_playbook(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks")
        for pb in resp.json()["playbooks"]:
            assert "steps" in pb
            assert pb["step_count"] >= 4  # all playbooks have ≥4 steps

    def test_vkyc_has_required_slots(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks")
        vkyc = next(pb for pb in resp.json()["playbooks"] if pb["topic"] == "VKYC_Session_Failure")
        assert "session_id" in vkyc["required_slots"]
        assert "phone_number" in vkyc["required_slots"]


# ── GET /admin/workflows/playbooks/{workflow_id} ──────────────────────────────

class TestGetPlaybook:
    def test_200_for_known_workflow_id(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks/vkyc_session_failure_v1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["workflow_id"] == "vkyc_session_failure_v1"
        assert data["topic"] == "VKYC_Session_Failure"

    def test_response_includes_steps(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks/otp_delivery_failure_v2")
        assert resp.status_code == 200
        assert len(resp.json()["steps"]) >= 5

    def test_response_includes_investigation_metadata(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks/vkyc_session_failure_v1")
        data = resp.json()
        assert len(data["investigation_steps"]) >= 1
        assert len(data["tool_candidates"]) >= 1

    def test_404_for_unknown_workflow_id(self, full_client):
        resp = full_client.get("/admin/workflows/playbooks/does_not_exist_v99")
        assert resp.status_code == 404

    def test_503_when_no_registry(self, no_registry_client):
        resp = no_registry_client.get("/admin/workflows/playbooks/vkyc_session_failure_v1")
        assert resp.status_code == 503

    def test_all_5_playbooks_accessible_by_id(self, full_client):
        workflow_ids = [
            "vkyc_session_failure_v1",
            "otp_delivery_failure_v2",
            "api_callback_failure_v1",
            "document_ocr_failure_v1",
            "agent_portal_issue_v1",
        ]
        for wid in workflow_ids:
            resp = full_client.get(f"/admin/workflows/playbooks/{wid}")
            assert resp.status_code == 200, f"Workflow {wid} returned {resp.status_code}"


# ── Auth requirement tests ────────────────────────────────────────────────────

class TestAdminAuthRequired:
    """Endpoints should be guarded — without admin override they must not return 200."""

    def test_tool_list_rejected_without_auth(self):
        app = FastAPI()
        app.include_router(tool_router)
        app.state.tool_registry = ToolRegistry.build_default()
        client = TestClient(app)
        resp = client.get("/admin/tools/")
        assert resp.status_code != 200

    def test_tool_get_rejected_without_auth(self):
        app = FastAPI()
        app.include_router(tool_router)
        app.state.tool_registry = ToolRegistry.build_default()
        client = TestClient(app)
        resp = client.get("/admin/tools/GetSessionDetailsTool")
        assert resp.status_code != 200

    def test_playbook_list_rejected_without_auth(self):
        app = FastAPI()
        app.include_router(wf_router)
        app.state.playbook_registry = PlaybookRegistry.build()
        client = TestClient(app)
        resp = client.get("/admin/workflows/playbooks")
        assert resp.status_code != 200

    def test_playbook_get_rejected_without_auth(self):
        app = FastAPI()
        app.include_router(wf_router)
        app.state.playbook_registry = PlaybookRegistry.build()
        client = TestClient(app)
        resp = client.get("/admin/workflows/playbooks/vkyc_session_failure_v1")
        assert resp.status_code != 200
