"""
tests/test_sprint216_workflow_api.py

Sprint 2.16: Tests for workflow API endpoints.

Endpoints under test:
  GET  /cases/{case_id}/workflow
  POST /cases/{case_id}/workflow/resume
  GET  /admin/workflows/summary
  GET  /admin/workflows/active
  GET  /admin/workflows/stuck

Scenarios:
  1.  get_workflow_returns_200_with_workflow_fields
  2.  get_workflow_case_not_found_returns_404
  3.  get_workflow_service_unavailable_returns_503
  4.  resume_workflow_returns_200
  5.  resume_workflow_case_not_found_returns_404
  6.  resume_workflow_action_not_found_returns_404
  7.  resume_workflow_service_unavailable_returns_503
  8.  workflow_summary_returns_200_with_totals
  9.  workflow_summary_empty_returns_200_not_500
  10. workflow_summary_service_unavailable_returns_503
  11. active_workflows_returns_200
  12. active_workflows_empty_list_returns_200
  13. active_workflows_service_unavailable_returns_503
  14. stuck_workflows_returns_200
  15. stuck_workflows_detects_old_paused_case
  16. stuck_workflows_skips_recently_updated
  17. stuck_workflows_service_unavailable_returns_503
  18. admin_endpoints_require_admin_auth
  19. get_workflow_response_structure
  20. resume_workflow_response_structure
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from api.routes.case_engine import router as case_router
from api.routes.workflow_admin import router as wf_admin_router
from case_engine.case_state import CaseState
from case_engine.models import Case
from case_engine.service import CaseService, WorkflowStartResult
from case_engine.workflows.models import WorkflowState


# ── Helpers ───────────────────────────────────────────────────────────────────

_ADMIN_API_KEY = "admin-test-key-1234"
_REGULAR_API_KEY = "regular-key-5678"

_UNSET = object()


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _make_case(
    case_id: str | None = None,
    ticket_id: str = "TKT-001",
    client: str = "unity_bank",
    state: CaseState = CaseState.WORKFLOW_ACTIVE,
    topic: str | None = "VKYC_Session_Failure",
    workflow_id: str | None = "vkyc_session_failure_v1",
    workflow_state: str | None = "RUNNING",
    workflow_step_index: int | None = 1,
    workflow_context: dict | None = None,
    updated_at: datetime | None = None,
) -> Case:
    c = Case(ticket_id=ticket_id, client=client)
    if case_id:
        c.case_id = case_id
    c.current_state = state
    c.topic = topic
    c.workflow_id = workflow_id
    c.workflow_state = workflow_state
    c.workflow_step_index = workflow_step_index
    c.workflow_context = workflow_context or {}
    if updated_at:
        c.updated_at = updated_at
    return c


def _make_workflow_result(
    case_id: str = "case-1",
    state: CaseState = CaseState.RESOLVED,
    workflow_id: str = "vkyc_session_failure_v1",
    workflow_state: str = "COMPLETED",
    resolved: bool = True,
    escalated: bool = False,
) -> WorkflowStartResult:
    return WorkflowStartResult(
        case_id=case_id,
        state=state,
        workflow_id=workflow_id,
        workflow_state=workflow_state,
        current_step_id="resolve",
        resolved=resolved,
        escalated=escalated,
        step_results=[{"step_id": "resolve", "outcome": "RESOLVED", "detail": {}}],
    )


def _app_with_case_service(
    case_svc: Any | None = None,
    stack: Any | None = None,
) -> FastAPI:
    app = FastAPI()
    app.include_router(case_router)
    app.state.case_service = case_svc
    app.state.stack = stack
    return app


def _app_with_admin_service(case_svc: Any | None = None) -> FastAPI:
    """App with the workflow admin router and mocked auth."""
    app = FastAPI()

    # Patch require_admin to be a no-op passthrough
    async def _noop_auth():
        return True

    from fastapi import Depends
    from api.routes import workflow_admin as wf_mod

    # Override the dependency
    app.include_router(wf_admin_router)
    app.state.case_service = case_svc

    # Override require_admin globally
    from security.dependencies import require_admin
    app.dependency_overrides[require_admin] = _noop_auth

    return app


def _case_client(case_svc=None, stack=None, raise_server_exceptions=False) -> TestClient:
    app = _app_with_case_service(case_svc, stack)
    return TestClient(app, raise_server_exceptions=raise_server_exceptions)


def _admin_client(case_svc=None) -> TestClient:
    app = _app_with_admin_service(case_svc)
    return TestClient(app, raise_server_exceptions=False)


# ── Mock factory ─────────────────────────────────────────────────────────────

def _mock_case_service(
    get_case_returns: Any = _UNSET,
    resume_workflow_returns: Any = _UNSET,
    list_workflow_cases_returns: list | None = None,
    get_slot_state_returns: dict | None = None,
) -> MagicMock:
    svc = MagicMock(spec=CaseService)
    default_case = _make_case()

    if get_case_returns is _UNSET:
        svc.get_case.return_value = default_case
    else:
        svc.get_case.return_value = get_case_returns

    if resume_workflow_returns is _UNSET:
        svc.resume_workflow.return_value = _make_workflow_result(case_id=default_case.case_id)
    else:
        svc.resume_workflow.return_value = resume_workflow_returns

    svc.list_workflow_cases.return_value = list_workflow_cases_returns or []
    svc.get_slot_state.return_value = get_slot_state_returns or {}

    return svc


# ── Mock action stack ─────────────────────────────────────────────────────────

from case_engine.action_models import ActionRequest
from case_engine.action_state import ActionRiskLevel, ActionState


def _make_action_stack(action: ActionRequest | None = None) -> MagicMock:
    stack = MagicMock()
    repo = MagicMock()
    if action is None:
        repo.get_action.return_value = None
    else:
        repo.get_action.return_value = action
    stack.gateway._repo = repo
    return stack


def _mock_action(case_id: str = "case-1") -> ActionRequest:
    return ActionRequest(
        case_id=case_id,
        ticket_id="TKT-001",
        client="unity_bank",
        action_type="reset_session",
        action_namespace="kwikid.vkyc",
        risk_level=ActionRiskLevel.REVERSIBLE,
        idempotency_key=uuid.uuid4().hex,
        current_state=ActionState.EXECUTED,
    )


# ── Test 1: GET /cases/{case_id}/workflow returns 200 ─────────────────────────

class TestGetWorkflow:
    def test_returns_200(self):
        svc = _mock_case_service()
        cli = _case_client(case_svc=svc)
        case_id = svc.get_case.return_value.case_id
        resp = cli.get(f"/cases/{case_id}/workflow")
        assert resp.status_code == 200

    def test_response_has_case_id(self):
        svc = _mock_case_service()
        case = _make_case()
        svc.get_case.return_value = case
        cli = _case_client(case_svc=svc)
        resp = cli.get(f"/cases/{case.case_id}/workflow")
        assert resp.json()["case_id"] == case.case_id

    def test_response_has_workflow_state(self):
        svc = _mock_case_service()
        cli = _case_client(case_svc=svc)
        case = svc.get_case.return_value
        resp = cli.get(f"/cases/{case.case_id}/workflow")
        data = resp.json()
        assert "workflow_state" in data
        assert data["workflow_state"] == "RUNNING"

    def test_response_has_workflow_id(self):
        svc = _mock_case_service()
        cli = _case_client(case_svc=svc)
        case = svc.get_case.return_value
        resp = cli.get(f"/cases/{case.case_id}/workflow")
        assert resp.json()["workflow_id"] == "vkyc_session_failure_v1"


# ── Test 2: GET workflow case not found ───────────────────────────────────────

class TestGetWorkflowNotFound:
    def test_returns_404(self):
        svc = _mock_case_service(get_case_returns=None)
        cli = _case_client(case_svc=svc)
        resp = cli.get("/cases/nonexistent-case/workflow")
        assert resp.status_code == 404

    def test_error_body_has_code(self):
        svc = _mock_case_service(get_case_returns=None)
        cli = _case_client(case_svc=svc)
        resp = cli.get("/cases/nonexistent/workflow")
        assert "NOT_FOUND" in resp.json()["error"]["code"]


# ── Test 3: GET workflow service unavailable ──────────────────────────────────

class TestGetWorkflowServiceUnavailable:
    def test_returns_503_when_no_service(self):
        cli = _case_client(case_svc=None)
        resp = cli.get("/cases/any-id/workflow")
        assert resp.status_code == 503


# ── Test 4: POST resume returns 200 ──────────────────────────────────────────

class TestResumeWorkflow:
    def test_returns_200(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        action = _mock_action(case.case_id)
        stack = _make_action_stack(action)
        cli = _case_client(case_svc=svc, stack=stack)
        resp = cli.post(
            f"/cases/{case.case_id}/workflow/resume",
            json={"action_id": action.action_id},
        )
        assert resp.status_code == 200

    def test_response_has_resolved_field(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        action = _mock_action(case.case_id)
        stack = _make_action_stack(action)
        cli = _case_client(case_svc=svc, stack=stack)
        resp = cli.post(
            f"/cases/{case.case_id}/workflow/resume",
            json={"action_id": action.action_id},
        )
        assert "resolved" in resp.json()


# ── Test 5: resume case not found ────────────────────────────────────────────

class TestResumeWorkflowCaseNotFound:
    def test_returns_404(self):
        svc = _mock_case_service(get_case_returns=None)
        cli = _case_client(case_svc=svc, stack=_make_action_stack())
        resp = cli.post(
            "/cases/nonexistent/workflow/resume",
            json={"action_id": "some-action"},
        )
        assert resp.status_code == 404


# ── Test 6: resume action not found ──────────────────────────────────────────

class TestResumeWorkflowActionNotFound:
    def test_returns_404_when_action_missing(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        # Stack exists but action is None
        stack = _make_action_stack(action=None)
        cli = _case_client(case_svc=svc, stack=stack)
        resp = cli.post(
            f"/cases/{case.case_id}/workflow/resume",
            json={"action_id": "missing-action-id"},
        )
        assert resp.status_code == 404


# ── Test 7: resume service unavailable ───────────────────────────────────────

class TestResumeWorkflowServiceUnavailable:
    def test_returns_503(self):
        cli = _case_client(case_svc=None)
        resp = cli.post(
            "/cases/any-id/workflow/resume",
            json={"action_id": "any"},
        )
        assert resp.status_code == 503


# ── Test 8: GET /admin/workflows/summary returns 200 ─────────────────────────

class TestWorkflowSummary:
    def test_returns_200(self):
        cases = [
            _make_case(workflow_state="RUNNING"),
            _make_case(workflow_state="PAUSED"),
        ]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/summary")
        assert resp.status_code == 200

    def test_total_count_correct(self):
        cases = [_make_case(), _make_case(), _make_case()]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/summary")
        assert resp.json()["total"] == 3

    def test_by_workflow_state_present(self):
        cases = [
            _make_case(workflow_state="RUNNING"),
            _make_case(workflow_state="PAUSED"),
            _make_case(workflow_state="RUNNING"),
        ]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/summary")
        data = resp.json()
        assert "by_workflow_state" in data
        assert data["by_workflow_state"]["RUNNING"] == 2
        assert data["by_workflow_state"]["PAUSED"] == 1

    def test_by_topic_present(self):
        cases = [
            _make_case(topic="VKYC_Session_Failure"),
            _make_case(topic="OTP_Delivery_Failure"),
        ]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/summary")
        data = resp.json()
        assert "by_topic" in data


# ── Test 9: summary empty list ────────────────────────────────────────────────

class TestWorkflowSummaryEmpty:
    def test_empty_list_returns_200_not_500(self):
        svc = _mock_case_service(list_workflow_cases_returns=[])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/summary")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0


# ── Test 10: summary service unavailable ─────────────────────────────────────

class TestWorkflowSummaryUnavailable:
    def test_returns_503(self):
        cli = _admin_client(case_svc=None)
        resp = cli.get("/admin/workflows/summary")
        assert resp.status_code == 503


# ── Test 11: GET /admin/workflows/active returns 200 ─────────────────────────

class TestActiveWorkflows:
    def test_returns_200(self):
        cases = [_make_case(workflow_state="RUNNING")]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/active")
        assert resp.status_code == 200

    def test_total_count_correct(self):
        cases = [_make_case(), _make_case()]
        svc = _mock_case_service(list_workflow_cases_returns=cases)
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/active")
        assert resp.json()["total"] == 2

    def test_workflow_items_have_case_id(self):
        case = _make_case()
        svc = _mock_case_service(list_workflow_cases_returns=[case])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/active")
        items = resp.json()["workflows"]
        assert len(items) == 1
        assert items[0]["case_id"] == case.case_id

    def test_workflow_items_have_required_fields(self):
        case = _make_case()
        svc = _mock_case_service(list_workflow_cases_returns=[case])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/active")
        item = resp.json()["workflows"][0]
        for field in ("case_id", "ticket_id", "client", "workflow_state", "workflow_id"):
            assert field in item


# ── Test 12: active workflows empty list ─────────────────────────────────────

class TestActiveWorkflowsEmpty:
    def test_empty_returns_200_not_500(self):
        svc = _mock_case_service(list_workflow_cases_returns=[])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/active")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0
        assert resp.json()["workflows"] == []


# ── Test 13: active workflows service unavailable ─────────────────────────────

class TestActiveWorkflowsUnavailable:
    def test_returns_503(self):
        cli = _admin_client(case_svc=None)
        resp = cli.get("/admin/workflows/active")
        assert resp.status_code == 503


# ── Test 14: GET /admin/workflows/stuck returns 200 ──────────────────────────

class TestStuckWorkflows:
    def test_returns_200_with_no_stuck(self):
        # Freshly updated — not stuck
        case = _make_case(workflow_state="PAUSED", updated_at=_now())
        svc = _mock_case_service(list_workflow_cases_returns=[case])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/stuck")
        assert resp.status_code == 200

    def test_stuck_threshold_present_in_response(self):
        svc = _mock_case_service(list_workflow_cases_returns=[])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/stuck")
        assert "stuck_threshold_minutes" in resp.json()

    def test_total_stuck_present_in_response(self):
        svc = _mock_case_service(list_workflow_cases_returns=[])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/stuck")
        assert "total_stuck" in resp.json()


# ── Test 15: stuck workflows detects old PAUSED case ─────────────────────────

class TestStuckWorkflowsDetectsOld:
    def test_old_paused_case_is_stuck(self):
        old = _now() - timedelta(minutes=45)  # >30 min threshold
        case = _make_case(workflow_state="PAUSED", updated_at=old)
        svc = _mock_case_service(list_workflow_cases_returns=[case])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/stuck")
        data = resp.json()
        assert data["total_stuck"] == 1
        assert len(data["workflows"]) == 1
        assert data["workflows"][0]["paused_minutes"] >= 30


# ── Test 16: stuck workflows skips recently updated ───────────────────────────

class TestStuckWorkflowsSkipsRecent:
    def test_recent_paused_not_stuck(self):
        recent = _now() - timedelta(minutes=5)  # <30 min
        case = _make_case(workflow_state="PAUSED", updated_at=recent)
        svc = _mock_case_service(list_workflow_cases_returns=[case])
        cli = _admin_client(svc)
        resp = cli.get("/admin/workflows/stuck")
        assert resp.json()["total_stuck"] == 0


# ── Test 17: stuck workflows service unavailable ──────────────────────────────

class TestStuckWorkflowsUnavailable:
    def test_returns_503(self):
        cli = _admin_client(case_svc=None)
        resp = cli.get("/admin/workflows/stuck")
        assert resp.status_code == 503


# ── Test 18: admin endpoints require admin auth ───────────────────────────────

class TestAdminAuthRequired:
    """
    When require_admin is NOT overridden, verify requests without auth are rejected.

    The raw test app has no security initialization, so _get_authenticator raises,
    producing 500 rather than 401. Either way, the endpoint rejects unauthenticated
    requests — it must not return 200.
    """

    def _raw_admin_app(self) -> FastAPI:
        app = FastAPI()
        app.include_router(wf_admin_router)
        app.state.case_service = _mock_case_service()
        return app

    def test_summary_without_auth_is_not_200(self):
        app = self._raw_admin_app()
        cli = TestClient(app, raise_server_exceptions=False)
        resp = cli.get("/admin/workflows/summary")
        assert resp.status_code != 200

    def test_active_without_auth_is_not_200(self):
        app = self._raw_admin_app()
        cli = TestClient(app, raise_server_exceptions=False)
        resp = cli.get("/admin/workflows/active")
        assert resp.status_code != 200

    def test_stuck_without_auth_is_not_200(self):
        app = self._raw_admin_app()
        cli = TestClient(app, raise_server_exceptions=False)
        resp = cli.get("/admin/workflows/stuck")
        assert resp.status_code != 200


# ── Test 19: GET workflow response structure ───────────────────────────────────

class TestGetWorkflowResponseStructure:
    def test_all_expected_keys_present(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        cli = _case_client(case_svc=svc)
        resp = cli.get(f"/cases/{case.case_id}/workflow")
        data = resp.json()
        for key in ("case_id", "state", "workflow_id", "workflow_state",
                    "workflow_step_index", "workflow_context"):
            assert key in data, f"Missing key: {key}"

    def test_state_is_valid_case_state_value(self):
        case = _make_case(state=CaseState.WORKFLOW_ACTIVE)
        svc = _mock_case_service()
        svc.get_case.return_value = case
        cli = _case_client(case_svc=svc)
        resp = cli.get(f"/cases/{case.case_id}/workflow")
        state = resp.json()["state"]
        CaseState(state)  # must not raise


# ── Test 20: POST resume response structure ───────────────────────────────────

class TestResumeWorkflowResponseStructure:
    def test_all_expected_keys_present(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        action = _mock_action(case.case_id)
        stack = _make_action_stack(action)
        cli = _case_client(case_svc=svc, stack=stack)
        resp = cli.post(
            f"/cases/{case.case_id}/workflow/resume",
            json={"action_id": action.action_id},
        )
        data = resp.json()
        for key in ("case_id", "state", "workflow_id", "workflow_state",
                    "resolved", "escalated", "step_results"):
            assert key in data, f"Missing key: {key}"

    def test_step_results_is_list(self):
        case = _make_case()
        svc = _mock_case_service()
        svc.get_case.return_value = case
        action = _mock_action(case.case_id)
        stack = _make_action_stack(action)
        cli = _case_client(case_svc=svc, stack=stack)
        resp = cli.post(
            f"/cases/{case.case_id}/workflow/resume",
            json={"action_id": action.action_id},
        )
        assert isinstance(resp.json()["step_results"], list)
