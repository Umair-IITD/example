"""
tests/test_sprint227_adapter_router.py

Sprint 2.27: Tests for AdapterRouter — routing, BLOCKED, UNSUPPORTED, audit,
health, never-raises contract.
"""
import pytest
from unittest.mock import MagicMock, patch

from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.freshdesk_adapter import FreshdeskAdapter
from case_engine.adapters.asana_adapter import AsanaAdapter
from case_engine.adapters.portal_adapter import AdminPortalAdapter
from case_engine.adapters.monitoring_adapter import MonitoringAdapter
from case_engine.adapters.models import (
    AdapterOperation,
    AdapterRequest,
    AdapterStatus,
    AdapterType,
)


def _build_full_router() -> AdapterRouter:
    reg = AdapterRegistry.build_default()
    return AdapterRouter(registry=reg)


def _make_request(
    adapter_type: AdapterType = AdapterType.FRESHDESK,
    operation: AdapterOperation = AdapterOperation.READ,
    case_id: str = "c-test",
    action_type: str = "otp_resend",
) -> AdapterRequest:
    return AdapterRequest.create(
        adapter_type=adapter_type,
        operation=operation,
        payload={},
        case_id=case_id,
        action_type=action_type,
    )


class TestAdapterRouterRouting:
    def test_routes_freshdesk_read(self):
        router = _build_full_router()
        result = router.route(_make_request(AdapterType.FRESHDESK, AdapterOperation.READ))
        assert result.success is True

    def test_routes_asana_create(self):
        router = _build_full_router()
        result = router.route(_make_request(AdapterType.ASANA, AdapterOperation.CREATE))
        assert result.success is True

    def test_routes_monitoring_write(self):
        router = _build_full_router()
        result = router.route(_make_request(AdapterType.MONITORING, AdapterOperation.WRITE))
        assert result.success is True

    def test_routes_admin_portal_execute(self):
        router = _build_full_router()
        result = router.route(_make_request(AdapterType.ADMIN_PORTAL, AdapterOperation.EXECUTE))
        assert result.success is True

    def test_result_has_request_id(self):
        router = _build_full_router()
        req    = _make_request()
        result = router.route(req)
        assert result.request.request_id == req.request_id

    def test_result_has_adapter_name(self):
        router = _build_full_router()
        result = router.route(_make_request())
        assert result.adapter_name

    def test_result_has_response(self):
        router = _build_full_router()
        result = router.route(_make_request())
        assert result.response is not None

    def test_result_response_duration_non_negative(self):
        router = _build_full_router()
        result = router.route(_make_request())
        assert result.response.duration_ms >= 0


class TestAdapterRouterBlocked:
    def test_blocked_when_no_adapter_registered(self):
        reg = AdapterRegistry()  # empty registry
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.FRESHDESK))
        assert result.success is False
        assert result.response.status == AdapterStatus.BLOCKED

    def test_blocked_result_not_retryable(self):
        reg = AdapterRegistry()
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.ASANA))
        assert result.retryable is False

    def test_blocked_returns_router_as_adapter_name(self):
        reg = AdapterRegistry()
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.MONITORING))
        assert result.adapter_name == "router"


class TestAdapterRouterUnsupported:
    def test_unsupported_when_op_not_supported(self):
        reg = AdapterRegistry()
        reg.register_adapter(FreshdeskAdapter())  # FRESHDESK does not support SEARCH
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.FRESHDESK, AdapterOperation.SEARCH))
        assert result.success is False
        assert result.response.status == AdapterStatus.UNSUPPORTED

    def test_asana_write_unsupported(self):
        reg = AdapterRegistry()
        reg.register_adapter(AsanaAdapter())
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.ASANA, AdapterOperation.WRITE))
        assert result.response.status == AdapterStatus.UNSUPPORTED


class TestAdapterRouterNeverRaises:
    def test_never_raises_on_any_adapter_type(self):
        router = _build_full_router()
        for at in AdapterType:
            for op in AdapterOperation:
                result = router.route(_make_request(at, op))
                assert result is not None

    def test_never_raises_on_empty_registry(self):
        reg = AdapterRegistry()
        router = AdapterRouter(registry=reg)
        for at in AdapterType:
            result = router.route(_make_request(at))
            assert result is not None

    def test_never_raises_when_adapter_raises(self):
        """If an adapter's execute() raises, router returns FAILED — never propagates."""
        reg = AdapterRegistry()

        class ExplodingAdapter(FreshdeskAdapter):
            def execute(self, request):
                raise RuntimeError("adapter exploded")

        reg.register_adapter(ExplodingAdapter())
        router = AdapterRouter(registry=reg)
        result = router.route(_make_request(AdapterType.FRESHDESK, AdapterOperation.READ))
        assert result.success is False
        assert result.response.status == AdapterStatus.FAILED


class TestAdapterRouterHealthCheck:
    def test_is_healthy_true_for_registered_adapter(self):
        router = _build_full_router()
        assert router.is_healthy(AdapterType.FRESHDESK) is True

    def test_is_healthy_false_for_unregistered(self):
        reg = AdapterRegistry()
        router = AdapterRouter(registry=reg)
        assert router.is_healthy(AdapterType.FRESHDESK) is False

    def test_is_healthy_never_raises(self):
        router = _build_full_router()
        for at in AdapterType:
            router.is_healthy(at)

    def test_health_summary_returns_dict(self):
        router = _build_full_router()
        s = router.health_summary()
        assert isinstance(s, dict)

    def test_health_summary_overall_healthy_true(self):
        router = _build_full_router()
        s = router.health_summary()
        assert s["overall_healthy"] is True

    def test_health_summary_includes_all_adapters(self):
        router = _build_full_router()
        s = router.health_summary()
        assert s["adapter_count"] == 4

    def test_health_summary_never_raises(self):
        reg = AdapterRegistry()
        router = AdapterRouter(registry=reg)
        s = router.health_summary()
        assert s is not None


class TestAdapterRouterAudit:
    def test_audit_started_called_on_route(self):
        mock_audit = MagicMock()
        router = AdapterRouter(
            registry=AdapterRegistry.build_default(),
            audit_logger=mock_audit,
        )
        router.route(_make_request())
        mock_audit.log_adapter_request_started.assert_called_once()

    def test_audit_completed_called_on_route(self):
        mock_audit = MagicMock()
        router = AdapterRouter(
            registry=AdapterRegistry.build_default(),
            audit_logger=mock_audit,
        )
        router.route(_make_request())
        mock_audit.log_adapter_request_completed.assert_called_once()

    def test_audit_routing_failed_called_on_blocked(self):
        mock_audit = MagicMock()
        router = AdapterRouter(
            registry=AdapterRegistry(),  # empty
            audit_logger=mock_audit,
        )
        router.route(_make_request())
        mock_audit.log_adapter_routing_failed.assert_called_once()

    def test_audit_failure_does_not_crash_router(self):
        mock_audit = MagicMock()
        mock_audit.log_adapter_request_started.side_effect = RuntimeError("audit exploded")
        router = AdapterRouter(
            registry=AdapterRegistry.build_default(),
            audit_logger=mock_audit,
        )
        result = router.route(_make_request())
        assert result is not None

    def test_no_audit_when_logger_is_none(self):
        router = AdapterRouter(
            registry=AdapterRegistry.build_default(),
            audit_logger=None,
        )
        result = router.route(_make_request())
        assert result.success is True
