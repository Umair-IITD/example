"""
tests/test_sprint2275_router_service.py

Sprint 2.27.5 Phase 1: RouterService test suite.

Tests: RouterService universal action dispatch, capability checks,
health aggregation, and factory wiring.
"""
import pytest
from unittest.mock import MagicMock, patch


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_adapter_router(success: bool = True, retryable: bool = False):
    """Build a mock AdapterRouter that returns configurable results."""
    from case_engine.adapters.models import (
        AdapterExecutionResult,
        AdapterRequest,
        AdapterResponse,
        AdapterStatus,
        AdapterType,
        AdapterOperation,
    )
    mock_request = AdapterRequest.create(
        adapter_type=AdapterType.FRESHDESK,
        operation=AdapterOperation.EXECUTE,
        payload={"key": "value"},
        case_id="case-001",
        action_type="otp_resend",
    )
    mock_response = AdapterResponse.from_dict({
        "request_id": mock_request.request_id,
        "adapter_type": AdapterType.FRESHDESK.value,
        "operation": AdapterOperation.EXECUTE.value,
        "status": (AdapterStatus.SUCCESS if success else AdapterStatus.FAILED).value,
        "data": {"result": "ok"} if success else {},
        "error_code": None if success else "TEST_ERROR",
        "error_message": None,
    })
    exec_result = AdapterExecutionResult.from_response(
        request=mock_request,
        response=mock_response,
        adapter_name="FreshdeskAdapter",
    )
    mock_router = MagicMock()
    mock_router.route.return_value = exec_result
    mock_router.health_summary.return_value = {"overall_healthy": True, "adapters": {}}
    mock_router.is_healthy.return_value = True
    return mock_router


# ── RouterResult ──────────────────────────────────────────────────────────────

class TestRouterResult:
    def test_to_dict_completeness(self):
        from case_engine.integrations import RouterResult
        r = RouterResult(
            result_id="r1", request_id="req1", action_type="otp_resend",
            adapter_type="FRESHDESK", operation="EXECUTE", success=True,
            retryable=False, status="SUCCESS", data={"ok": True},
            error_code=None, adapter_name="FreshdeskAdapter",
            executed_at="2025-01-01T00:00:00+00:00", duration_ms=12,
        )
        d = r.to_dict()
        assert d["result_id"] == "r1"
        assert d["success"] is True
        assert d["action_type"] == "otp_resend"
        assert d["data"] == {"ok": True}
        assert "metadata" in d

    def test_failure_classmethod(self):
        from case_engine.integrations import RouterResult
        r = RouterResult.failure(
            action_type="otp_resend",
            error_code="ROUTING_ERROR",
            error_msg="something broke",
            duration_ms=5,
        )
        assert r.success is False
        assert r.error_code == "ROUTING_ERROR"
        assert r.adapter_type == "UNKNOWN"
        assert r.duration_ms == 5

    def test_immutable(self):
        from case_engine.integrations import RouterResult
        r = RouterResult(
            result_id="r1", request_id="req1", action_type="x",
            adapter_type="Y", operation="Z", success=True, retryable=False,
            status="SUCCESS", data={}, error_code=None, adapter_name="a",
            executed_at="2025-01-01T00:00:00+00:00", duration_ms=0,
        )
        with pytest.raises((AttributeError, TypeError)):
            r.success = False  # type: ignore[misc]


# ── RouterService.route() ─────────────────────────────────────────────────────

class TestRouterServiceRoute:
    def test_route_known_action_success(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        svc = RouterService(adapter_router=mock_router)
        result = svc.route("otp_resend", payload={"uid": "123"}, case_id="case-1")
        assert result.success is True
        assert result.action_type == "otp_resend"
        assert result.duration_ms >= 0

    def test_route_returns_router_result_on_adapter_failure(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=False)
        svc = RouterService(adapter_router=mock_router)
        result = svc.route("otp_resend", payload={}, case_id="case-1")
        assert result.success is False
        assert result.error_code == "TEST_ERROR"

    def test_route_never_raises(self):
        from case_engine.integrations import RouterService
        broken_router = MagicMock()
        broken_router.route.side_effect = RuntimeError("adapter exploded")
        svc = RouterService(adapter_router=broken_router)
        result = svc.route("otp_resend", payload={}, case_id="case-1")
        assert result.success is False
        assert result.error_code == "ROUTER_SERVICE_INTERNAL_ERROR"

    def test_route_with_metadata(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        svc = RouterService(adapter_router=mock_router)
        result = svc.route(
            "otp_resend",
            payload={"uid": "abc"},
            case_id="case-99",
            metadata={"source": "webhook"},
        )
        assert result.metadata.get("source") == "webhook"

    def test_route_unknown_action_falls_to_default(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        svc = RouterService(adapter_router=mock_router)
        result = svc.route("totally_unknown_action", payload={}, case_id="case-x")
        assert isinstance(result.success, bool)


# ── RouterService capability checks ──────────────────────────────────────────

class TestRouterServiceCapabilityChecks:
    def test_can_route_known_action(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        svc = RouterService(adapter_router=mock_router)
        result = svc.can_route("otp_resend")
        assert isinstance(result, bool)

    def test_can_route_never_raises(self):
        from case_engine.integrations import RouterService
        broken_router = MagicMock()
        broken_router.is_healthy.side_effect = RuntimeError("broken")
        svc = RouterService(adapter_router=broken_router)
        assert svc.can_route("otp_resend") is False

    def test_is_explicitly_routed_known(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        explicitly = svc.is_explicitly_routed("otp_resend")
        assert isinstance(explicitly, bool)

    def test_is_explicitly_routed_never_raises(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        result = svc.is_explicitly_routed("xxx_totally_unknown")
        assert result is False

    def test_capability_check_valid(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router()
        mock_registry = MagicMock()
        mock_adapter = MagicMock()
        mock_adapter.supports.return_value = True
        mock_registry.get_adapter.return_value = mock_adapter
        mock_router._registry = mock_registry
        svc = RouterService(adapter_router=mock_router)
        result = svc.capability_check("FRESHDESK", "EXECUTE")
        assert isinstance(result, bool)

    def test_capability_check_invalid_adapter_type(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        result = svc.capability_check("NOT_A_REAL_ADAPTER", "EXECUTE")
        assert result is False

    def test_list_routable_actions(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        actions = svc.list_routable_actions()
        assert isinstance(actions, list)

    def test_list_routable_actions_never_raises(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        with patch("case_engine.adapters.execution_adapter.list_routable_action_types", side_effect=RuntimeError("oops")):
            actions = svc.list_routable_actions()
        assert actions == []


# ── RouterService health ──────────────────────────────────────────────────────

class TestRouterServiceHealth:
    def test_health_summary_returns_dict(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router()
        svc = RouterService(adapter_router=mock_router)
        health = svc.health_summary()
        assert "overall_healthy" in health

    def test_health_summary_never_raises(self):
        from case_engine.integrations import RouterService
        broken_router = MagicMock()
        broken_router.health_summary.side_effect = RuntimeError("health broken")
        svc = RouterService(adapter_router=broken_router)
        health = svc.health_summary()
        assert health["overall_healthy"] is False

    def test_is_healthy_valid_adapter(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router()
        svc = RouterService(adapter_router=mock_router)
        result = svc.is_healthy("FRESHDESK")
        assert isinstance(result, bool)

    def test_is_healthy_invalid_adapter(self):
        from case_engine.integrations import RouterService
        svc = RouterService(adapter_router=MagicMock())
        assert svc.is_healthy("NOT_A_REAL_ONE") is False


# ── Factory ───────────────────────────────────────────────────────────────────

class TestRouterServiceFactory:
    def test_build_router_service_with_router(self):
        from case_engine.integrations import build_router_service
        mock_router = _make_adapter_router()
        svc = build_router_service(adapter_router=mock_router)
        assert svc is not None

    def test_build_router_service_no_router_builds_default(self):
        from case_engine.integrations import build_router_service
        svc = build_router_service()
        assert svc is not None

    def test_build_router_service_with_audit(self):
        from case_engine.integrations import build_router_service
        mock_audit = MagicMock()
        svc = build_router_service(audit_logger=mock_audit)
        assert svc is not None


# ── Audit integration ─────────────────────────────────────────────────────────

class TestRouterServiceAudit:
    def test_audit_emit_called_on_route(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        mock_audit = MagicMock()
        svc = RouterService(adapter_router=mock_router, audit_logger=mock_audit)
        svc.route("otp_resend", payload={}, case_id="case-audit")
        mock_audit.log_action_routed.assert_called_once()

    def test_audit_failure_does_not_raise(self):
        from case_engine.integrations import RouterService
        mock_router = _make_adapter_router(success=True)
        mock_audit = MagicMock()
        mock_audit.log_action_routed.side_effect = RuntimeError("audit down")
        svc = RouterService(adapter_router=mock_router, audit_logger=mock_audit)
        result = svc.route("otp_resend", payload={}, case_id="case-audit")
        assert result.success is True
