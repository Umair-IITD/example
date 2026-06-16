"""
tests/test_sprint227_audit_events.py

Sprint 2.27: Tests for the 4 new AuditEventType values and 4 new
log_adapter_* methods on case_engine/audit.py::AuditLogger.
"""
import pytest

from case_engine.models import AuditEventType
from case_engine.audit import AuditLogger


class TestAuditEventTypes:
    def test_adapter_request_started_exists(self):
        assert AuditEventType.ADAPTER_REQUEST_STARTED == "ADAPTER_REQUEST_STARTED"

    def test_adapter_request_completed_exists(self):
        assert AuditEventType.ADAPTER_REQUEST_COMPLETED == "ADAPTER_REQUEST_COMPLETED"

    def test_adapter_health_check_exists(self):
        assert AuditEventType.ADAPTER_HEALTH_CHECK == "ADAPTER_HEALTH_CHECK"

    def test_adapter_routing_failed_exists(self):
        assert AuditEventType.ADAPTER_ROUTING_FAILED == "ADAPTER_ROUTING_FAILED"

    def test_total_audit_event_count_increased(self):
        total = len(AuditEventType)
        assert total >= 58, f"Expected at least 58 AuditEventType values, got {total}"


class TestLogAdapterRequestStarted:
    def test_method_exists(self):
        logger = AuditLogger()
        assert hasattr(logger, "log_adapter_request_started")

    def test_does_not_raise_offline(self):
        logger = AuditLogger()
        logger.log_adapter_request_started(
            adapter_type="FRESHDESK",
            operation="READ",
            request_id="req-001",
            case_id="case-001",
            action_type="otp_resend",
        )

    def test_does_not_raise_with_supabase_none(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_adapter_request_started(
            adapter_type="MONITORING",
            operation="WRITE",
            request_id="req-002",
            case_id="",
            action_type="emit_metric",
        )


class TestLogAdapterRequestCompleted:
    def test_method_exists(self):
        logger = AuditLogger()
        assert hasattr(logger, "log_adapter_request_completed")

    def test_does_not_raise_offline_success(self):
        logger = AuditLogger()
        logger.log_adapter_request_completed(
            adapter_type="FRESHDESK",
            operation="READ",
            request_id="req-003",
            case_id="case-001",
            status="SUCCESS",
            success=True,
            duration_ms=15,
            adapter_name="freshdesk-placeholder",
        )

    def test_does_not_raise_offline_failed(self):
        logger = AuditLogger()
        logger.log_adapter_request_completed(
            adapter_type="ASANA",
            operation="CREATE",
            request_id="req-004",
            case_id="case-002",
            status="FAILED",
            success=False,
            duration_ms=0,
            adapter_name="asana-placeholder",
        )


class TestLogAdapterHealthCheck:
    def test_method_exists(self):
        logger = AuditLogger()
        assert hasattr(logger, "log_adapter_health_check")

    def test_does_not_raise_healthy(self):
        logger = AuditLogger()
        logger.log_adapter_health_check(
            adapter_type="FRESHDESK",
            adapter_name="freshdesk-placeholder",
            healthy=True,
        )

    def test_does_not_raise_unhealthy(self):
        logger = AuditLogger()
        logger.log_adapter_health_check(
            adapter_type="MONITORING",
            adapter_name="monitoring-placeholder",
            healthy=False,
        )


class TestLogAdapterRoutingFailed:
    def test_method_exists(self):
        logger = AuditLogger()
        assert hasattr(logger, "log_adapter_routing_failed")

    def test_does_not_raise_offline(self):
        logger = AuditLogger()
        logger.log_adapter_routing_failed(
            adapter_type="ASANA",
            operation="CREATE",
            request_id="req-005",
            case_id="case-001",
            reason="No adapter registered for type: ASANA",
        )

    def test_does_not_raise_with_empty_case_id(self):
        logger = AuditLogger()
        logger.log_adapter_routing_failed(
            adapter_type="FRESHDESK",
            operation="READ",
            request_id="req-006",
            case_id="",
            reason="adapter not found",
        )


class TestAdapterAuditWithMockSupabase:
    def test_started_calls_insert(self):
        mock_sb = _build_mock_supabase()
        logger = AuditLogger(supabase_client=mock_sb)
        logger.log_adapter_request_started(
            adapter_type="FRESHDESK",
            operation="READ",
            request_id="req-x",
            case_id="c1",
            action_type="otp_resend",
        )
        mock_sb.table.assert_called()

    def test_completed_calls_insert(self):
        mock_sb = _build_mock_supabase()
        logger = AuditLogger(supabase_client=mock_sb)
        logger.log_adapter_request_completed(
            adapter_type="FRESHDESK",
            operation="READ",
            request_id="req-x",
            case_id="c1",
            status="SUCCESS",
            success=True,
            duration_ms=10,
            adapter_name="freshdesk-placeholder",
        )
        mock_sb.table.assert_called()

    def test_routing_failed_calls_insert(self):
        mock_sb = _build_mock_supabase()
        logger = AuditLogger(supabase_client=mock_sb)
        logger.log_adapter_routing_failed(
            adapter_type="ASANA",
            operation="CREATE",
            request_id="req-y",
            case_id="c2",
            reason="not registered",
        )
        mock_sb.table.assert_called()

    def test_health_check_calls_insert(self):
        mock_sb = _build_mock_supabase()
        logger = AuditLogger(supabase_client=mock_sb)
        logger.log_adapter_health_check(
            adapter_type="FRESHDESK",
            adapter_name="freshdesk-placeholder",
            healthy=True,
        )
        mock_sb.table.assert_called()


def _build_mock_supabase():
    from unittest.mock import MagicMock
    mock = MagicMock()
    mock.table.return_value.insert.return_value.execute.return_value = None
    return mock
