"""
tests/test_sprint227_execution_integration.py

Sprint 2.27: Tests for AdapterBackedExecutionAdapter and its integration
with ExecutionService via build_execution_service(adapter_router=...).
"""
import pytest

from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.execution_adapter import (
    AdapterBackedExecutionAdapter,
    get_adapter_for_action,
    list_routable_action_types,
)
from case_engine.adapters.models import AdapterOperation, AdapterType
from case_engine.execution.service import build_execution_service


def _build_router() -> AdapterRouter:
    return AdapterRouter(registry=AdapterRegistry.build_default())


class TestAdapterBackedExecutionAdapter:
    def test_adapter_name(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        assert a.adapter_name == "adapter-router-backed"

    def test_run_otp_resend_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("otp_resend", {}, "case-001")
        assert result["success"] is True

    def test_run_vkyc_session_reset_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("vkyc_session_reset", {}, "case-001")
        assert result["success"] is True

    def test_run_document_ocr_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("document_ocr_reprocess", {}, "case-001")
        assert result["success"] is True

    def test_run_agent_session_refresh_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("agent_session_refresh", {}, "case-001")
        assert result["success"] is True

    def test_run_api_callback_retry_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("api_callback_retry", {}, "case-001")
        assert result["success"] is True

    def test_run_emit_metric_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("emit_metric", {}, "case-001")
        assert result["success"] is True

    def test_run_trigger_alert_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("trigger_alert", {}, "case-001")
        assert result["success"] is True

    def test_run_create_l2_ticket_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("create_l2_ticket", {}, "case-001")
        assert result["success"] is True

    def test_run_post_ticket_note_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("post_ticket_note", {}, "case-001")
        assert result["success"] is True

    def test_run_close_ticket_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("close_ticket", {}, "case-001")
        assert result["success"] is True

    def test_run_update_ticket_status_success(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("update_ticket_status", {}, "case-001")
        assert result["success"] is True

    def test_run_unknown_action_falls_to_default(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("totally_unknown_action", {}, "case-001")
        assert "success" in result

    def test_run_returns_required_keys(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("otp_resend", {}, "case-001")
        assert "success"       in result
        assert "response_data" in result
        assert "error_code"    in result
        assert "error_message" in result

    def test_run_response_data_has_adapter_name(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        result = a.run("otp_resend", {}, "case-001")
        assert "adapter_name" in result["response_data"]

    def test_run_never_raises(self):
        a = AdapterBackedExecutionAdapter(_build_router())
        action_types = [
            "otp_resend", "vkyc_session_reset", "emit_metric",
            "create_l2_ticket", "unknown_xyz",
        ]
        for at in action_types:
            result = a.run(at, {}, "c1")
            assert result is not None

    def test_run_never_raises_when_router_empty(self):
        empty_router = AdapterRouter(registry=AdapterRegistry())
        a = AdapterBackedExecutionAdapter(empty_router)
        result = a.run("otp_resend", {}, "case-001")
        assert result["success"] is False
        assert result["error_code"] is not None


class TestRoutingTable:
    def test_get_freshdesk_for_otp_resend(self):
        at, op = get_adapter_for_action("otp_resend")
        assert at == AdapterType.FRESHDESK

    def test_get_asana_for_l2_ticket(self):
        at, op = get_adapter_for_action("create_l2_ticket")
        assert at == AdapterType.ASANA
        assert op == AdapterOperation.CREATE

    def test_get_monitoring_for_emit_metric(self):
        at, op = get_adapter_for_action("emit_metric")
        assert at == AdapterType.MONITORING
        assert op == AdapterOperation.WRITE

    def test_default_routing_for_unknown(self):
        at, op = get_adapter_for_action("totally_unknown_xyz_action")
        assert at == AdapterType.ADMIN_PORTAL
        assert op == AdapterOperation.EXECUTE

    def test_list_routable_action_types_not_empty(self):
        actions = list_routable_action_types()
        assert len(actions) > 0

    def test_list_routable_includes_known_actions(self):
        actions = list_routable_action_types()
        assert "otp_resend"         in actions
        assert "create_l2_ticket"   in actions
        assert "emit_metric"        in actions
        assert "vkyc_session_reset" in actions


class TestBuildExecutionServiceWithRouter:
    def test_build_with_adapter_router(self):
        router = _build_router()
        svc = build_execution_service(adapter_router=router)
        assert svc is not None

    def test_build_without_router_uses_mock(self):
        svc = build_execution_service()
        assert svc is not None

    def test_process_with_router_succeeds(self):
        router = _build_router()
        svc = build_execution_service(adapter_router=router)
        bundle = svc.process(
            action_type="otp_resend",
            action_params={},
            case_id="case-test",
        )
        assert bundle is not None
        assert bundle.action_type == "otp_resend"

    def test_process_with_router_returns_bundle(self):
        router = _build_router()
        svc = build_execution_service(adapter_router=router)
        bundle = svc.process(
            action_type="create_l2_ticket",
            action_params={"title": "L2"},
            case_id="case-test",
        )
        assert bundle.bundle_id

    def test_explicit_adapter_takes_priority_over_router(self):
        from case_engine.execution.executor import MockExecutionAdapter
        router = _build_router()
        mock = MockExecutionAdapter()
        svc = build_execution_service(adapter=mock, adapter_router=router)
        bundle = svc.process(action_type="otp_resend", action_params={}, case_id="c1")
        assert bundle is not None
