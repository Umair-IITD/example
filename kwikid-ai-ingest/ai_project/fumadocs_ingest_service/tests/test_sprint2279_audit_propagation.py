"""
tests/test_sprint2279_audit_propagation.py

Sprint 2.27.9: Audit propagation tests.

Covers:
  - AuditLogger.log_client_resolved() emits CLIENT_RESOLVED event
  - AuditLogger.log_unknown_client() emits CLIENT_RESOLUTION_FAILED event
  - AuditLogger.log_tenant_context_attached() emits TENANT_CONTEXT_ATTACHED event
  - AuditLogger.log_unknown_client_escalated() emits UNKNOWN_CLIENT_ESCALATED event
  - All events include client_id (or "UNKNOWN") in the client field
  - log_unknown_client() does not include email (PII protection)
  - AuditEventType has all 6 new Sprint 2.27.9 values
  - Audit entries have correct action_type values
  - log_client_resolved does not log actual credentials
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from case_engine.audit import AuditLogger
from case_engine.models import AuditEntry, AuditEventType


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def logger() -> AuditLogger:
    return AuditLogger(supabase_client=None)  # log-only mode


@pytest.fixture
def mock_logger() -> AuditLogger:
    logger = AuditLogger(supabase_client=None)
    return logger


# ── AuditEventType — Sprint 2.27.9 values ────────────────────────────────────

class TestAuditEventTypes:
    def test_client_resolved_exists(self):
        assert hasattr(AuditEventType, "CLIENT_RESOLVED")

    def test_client_resolution_failed_exists(self):
        assert hasattr(AuditEventType, "CLIENT_RESOLUTION_FAILED")

    def test_tenant_context_attached_exists(self):
        assert hasattr(AuditEventType, "TENANT_CONTEXT_ATTACHED")

    def test_unknown_client_escalated_exists(self):
        assert hasattr(AuditEventType, "UNKNOWN_CLIENT_ESCALATED")

    def test_tenant_registry_validated_exists(self):
        assert hasattr(AuditEventType, "TENANT_REGISTRY_VALIDATED")

    def test_tenant_registry_validation_failed_exists(self):
        assert hasattr(AuditEventType, "TENANT_REGISTRY_VALIDATION_FAILED")

    def test_client_resolved_value(self):
        assert AuditEventType.CLIENT_RESOLVED.value == "CLIENT_RESOLVED"

    def test_client_resolution_failed_value(self):
        assert AuditEventType.CLIENT_RESOLUTION_FAILED.value == "CLIENT_RESOLUTION_FAILED"

    def test_tenant_context_attached_value(self):
        assert AuditEventType.TENANT_CONTEXT_ATTACHED.value == "TENANT_CONTEXT_ATTACHED"

    def test_unknown_client_escalated_value(self):
        assert AuditEventType.UNKNOWN_CLIENT_ESCALATED.value == "UNKNOWN_CLIENT_ESCALATED"


# ── log_client_resolved() ─────────────────────────────────────────────────────

class TestLogClientResolved:
    def test_method_exists(self, logger):
        assert hasattr(logger, "log_client_resolved")

    def test_does_not_raise_in_log_only_mode(self, logger):
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )

    def test_writes_correct_event_type(self, logger):
        written: list[AuditEntry] = []
        original_write = logger._write

        def capture(entry):
            written.append(entry)

        logger._write = capture
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        assert len(written) == 1
        assert written[0].action_type == AuditEventType.CLIENT_RESOLVED

    def test_client_field_set_to_client_id(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        assert written[0].client == "unity_bank"

    def test_ticket_id_in_entry(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_client_resolved(
            ticket_id="T-CLIENT-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        assert written[0].ticket_id == "T-CLIENT-001"

    def test_action_detail_contains_domain(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        assert written[0].action_detail["domain"] == "unitybank.co.in"

    def test_action_detail_no_credentials(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        detail_str = str(written[0].action_detail)
        assert "password" not in detail_str.lower()
        assert "secret" not in detail_str.lower()
        assert "credentials_ref" not in detail_str or "credentials_ref" not in written[0].action_detail

    def test_outcome_success(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_client_resolved(
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            domain="unitybank.co.in",
        )
        assert written[0].outcome == "SUCCESS"


# ── log_unknown_client() ──────────────────────────────────────────────────────

class TestLogUnknownClient:
    def test_method_exists(self, logger):
        assert hasattr(logger, "log_unknown_client")

    def test_does_not_raise(self, logger):
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")

    def test_writes_correct_event_type(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert written[0].action_type == AuditEventType.CLIENT_RESOLUTION_FAILED

    def test_client_field_is_unknown(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert written[0].client == "UNKNOWN"

    def test_action_detail_contains_domain(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert "domain" in written[0].action_detail
        assert written[0].action_detail["domain"] == "xyzbank.com"

    def test_action_detail_no_email(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert "email" not in written[0].action_detail

    def test_outcome_unknown_client(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert written[0].outcome == "UNKNOWN_CLIENT"

    def test_error_code_set(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client(ticket_id="T-002", domain="xyzbank.com")
        assert written[0].error_code is not None


# ── log_tenant_context_attached() ────────────────────────────────────────────

class TestLogTenantContextAttached:
    def test_method_exists(self, logger):
        assert hasattr(logger, "log_tenant_context_attached")

    def test_does_not_raise(self, logger):
        logger.log_tenant_context_attached(
            case_id="case-001",
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )

    def test_writes_correct_event_type(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_tenant_context_attached(
            case_id="case-001",
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )
        assert written[0].action_type == AuditEventType.TENANT_CONTEXT_ATTACHED

    def test_client_field_set(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_tenant_context_attached(
            case_id="case-001",
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )
        assert written[0].client == "unity_bank"

    def test_case_id_in_entry(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_tenant_context_attached(
            case_id="case-XYZ",
            ticket_id="T-001",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )
        assert written[0].case_id == "case-XYZ"

    def test_action_detail_tool_count(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_tenant_context_attached(
            case_id="c",
            ticket_id="T",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )
        assert written[0].action_detail["tool_count"] == 10

    def test_action_detail_environment(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_tenant_context_attached(
            case_id="c",
            ticket_id="T",
            client_id="unity_bank",
            client_name="Unity Bank",
            environment="uat",
            tool_count=10,
        )
        assert written[0].action_detail["environment"] == "uat"


# ── log_unknown_client_escalated() ───────────────────────────────────────────

class TestLogUnknownClientEscalated:
    def test_method_exists(self, logger):
        assert hasattr(logger, "log_unknown_client_escalated")

    def test_does_not_raise(self, logger):
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")

    def test_writes_correct_event_type(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")
        assert written[0].action_type == AuditEventType.UNKNOWN_CLIENT_ESCALATED

    def test_client_field_is_unknown(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")
        assert written[0].client == "UNKNOWN"

    def test_outcome_escalated(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")
        assert written[0].outcome == "ESCALATED"

    def test_action_detail_has_domain(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")
        assert written[0].action_detail["domain"] == "bad.com"

    def test_custom_reason(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(
            ticket_id="T-003",
            domain="bad.com",
            reason="Custom escalation reason",
        )
        assert written[0].action_detail["reason"] == "Custom escalation reason"

    def test_default_reason_present(self, logger):
        written: list[AuditEntry] = []
        logger._write = written.append
        logger.log_unknown_client_escalated(ticket_id="T-003", domain="bad.com")
        assert "reason" in written[0].action_detail
        assert written[0].action_detail["reason"]  # not empty


# ── No-op in log-only mode ───────────────────────────────────────────────────

class TestLogOnlyMode:
    def test_all_methods_succeed_without_supabase(self):
        logger = AuditLogger(supabase_client=None)
        logger.log_client_resolved("T", "unity_bank", "Unity Bank", "unitybank.co.in")
        logger.log_unknown_client("T", "bad.com")
        logger.log_tenant_context_attached("c", "T", "unity_bank", "Unity Bank", "uat", 5)
        logger.log_unknown_client_escalated("T", "bad.com")
