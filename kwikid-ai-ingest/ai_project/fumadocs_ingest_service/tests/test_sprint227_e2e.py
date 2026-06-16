"""
tests/test_sprint227_e2e.py

Sprint 2.27: End-to-end tests exercising the full adapter pipeline:
  action_type → AdapterBackedExecutionAdapter → AdapterRouter → Adapter → response
"""
import pytest

from case_engine.adapters.adapter_registry import AdapterRegistry
from case_engine.adapters.adapter_router import AdapterRouter
from case_engine.adapters.execution_adapter import (
    AdapterBackedExecutionAdapter,
    _ACTION_ROUTING_TABLE,
    _DEFAULT_ROUTING,
)
from case_engine.adapters.models import AdapterStatus, AdapterType, AdapterOperation
from case_engine.execution.service import build_execution_service


@pytest.fixture
def full_router():
    return AdapterRouter(registry=AdapterRegistry.build_default())


@pytest.fixture
def execution_adapter(full_router):
    return AdapterBackedExecutionAdapter(full_router)


class TestE2EActionRouting:
    """Verify every routed action_type produces a successful response."""

    def test_otp_resend_e2e(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {"ticket_id": "T-1"}, "case-e2e-001")
        assert result["success"] is True
        assert result["response_data"]["adapter_type"] == "FRESHDESK"

    def test_vkyc_session_reset_e2e(self, execution_adapter):
        result = execution_adapter.run("vkyc_session_reset", {}, "case-e2e-002")
        assert result["success"] is True
        assert result["response_data"]["adapter_type"] == "ADMIN_PORTAL"

    def test_agent_session_refresh_e2e(self, execution_adapter):
        result = execution_adapter.run("agent_session_refresh", {}, "case-e2e-003")
        assert result["success"] is True

    def test_document_ocr_reprocess_e2e(self, execution_adapter):
        result = execution_adapter.run("document_ocr_reprocess", {}, "case-e2e-004")
        assert result["success"] is True

    def test_api_callback_retry_e2e(self, execution_adapter):
        result = execution_adapter.run("api_callback_retry", {}, "case-e2e-005")
        assert result["success"] is True

    def test_emit_metric_e2e(self, execution_adapter):
        result = execution_adapter.run(
            "emit_metric", {"metric_name": "kwikid.test", "value": 1}, "case-e2e-006"
        )
        assert result["success"] is True
        assert result["response_data"]["adapter_type"] == "MONITORING"

    def test_trigger_alert_e2e(self, execution_adapter):
        result = execution_adapter.run("trigger_alert", {}, "case-e2e-007")
        assert result["success"] is True

    def test_create_l2_ticket_e2e(self, execution_adapter):
        result = execution_adapter.run("create_l2_ticket", {"title": "L2"}, "case-e2e-008")
        assert result["success"] is True
        assert result["response_data"]["adapter_type"] == "ASANA"

    def test_post_ticket_note_e2e(self, execution_adapter):
        result = execution_adapter.run("post_ticket_note", {}, "case-e2e-009")
        assert result["success"] is True

    def test_close_ticket_e2e(self, execution_adapter):
        result = execution_adapter.run("close_ticket", {}, "case-e2e-010")
        assert result["success"] is True

    def test_update_ticket_status_e2e(self, execution_adapter):
        result = execution_adapter.run("update_ticket_status", {}, "case-e2e-011")
        assert result["success"] is True

    def test_all_routing_table_entries_succeed(self, execution_adapter):
        for action_type in _ACTION_ROUTING_TABLE:
            result = execution_adapter.run(action_type, {}, "case-e2e-all")
            assert result["success"] is True, f"{action_type} failed: {result}"


class TestE2EResponseShape:
    def test_response_data_has_adapter_name(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {}, "c1")
        assert "adapter_name" in result["response_data"]

    def test_response_data_has_status(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {}, "c1")
        assert "status" in result["response_data"]

    def test_response_data_status_is_success(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {}, "c1")
        assert result["response_data"]["status"] == "SUCCESS"

    def test_error_code_none_on_success(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {}, "c1")
        assert result["error_code"] is None

    def test_error_message_none_on_success(self, execution_adapter):
        result = execution_adapter.run("otp_resend", {}, "c1")
        assert result["error_message"] is None


class TestE2EExecutionServicePipeline:
    def test_full_pipeline_otp_resend(self, full_router):
        svc = build_execution_service(adapter_router=full_router)
        bundle = svc.process(
            action_type="otp_resend",
            action_params={"ticket_id": "T-1"},
            case_id="case-pipe-001",
        )
        assert bundle.action_type  == "otp_resend"
        assert bundle.bundle_id

    def test_full_pipeline_emit_metric(self, full_router):
        svc = build_execution_service(adapter_router=full_router)
        bundle = svc.process(
            action_type="emit_metric",
            action_params={"metric_name": "kwikid.otp.sent", "value": 1},
            case_id="case-pipe-002",
        )
        assert bundle.action_type == "emit_metric"

    def test_full_pipeline_create_l2_ticket(self, full_router):
        svc = build_execution_service(adapter_router=full_router)
        bundle = svc.process(
            action_type="create_l2_ticket",
            action_params={"title": "L2 escalation"},
            case_id="case-pipe-003",
        )
        assert bundle is not None

    def test_pipeline_never_raises(self, full_router):
        svc = build_execution_service(adapter_router=full_router)
        for action_type in list(_ACTION_ROUTING_TABLE.keys()) + ["unknown_action"]:
            bundle = svc.process(
                action_type=action_type,
                action_params={},
                case_id="case-pipe-all",
            )
            assert bundle is not None


class TestE2EDefaultRouting:
    def test_unknown_action_routes_to_admin_portal(self, full_router):
        ea = AdapterBackedExecutionAdapter(full_router)
        result = ea.run("no_such_action_xyz", {}, "c1")
        assert "success" in result
        if result["success"]:
            assert result["response_data"]["adapter_type"] == "ADMIN_PORTAL"

    def test_default_routing_is_admin_portal_execute(self):
        at, op = _DEFAULT_ROUTING
        assert at  == AdapterType.ADMIN_PORTAL
        assert op  == AdapterOperation.EXECUTE
