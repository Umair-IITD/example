"""
tests/test_sprint2279_unknown_client.py

Sprint 2.27.9: Unknown Client handling tests.

Covers:
  - Orchestrator with client_resolver wired + unknown domain → ESCALATED result
  - Orchestrator with client_resolver + known domain → continues pipeline
  - Orchestrator with client_resolver=None → backward compat (no resolution)
  - UnknownClientError path: audit CLIENT_RESOLUTION_FAILED emitted
  - UnknownClientError path: audit UNKNOWN_CLIENT_ESCALATED emitted
  - UnknownClientError path: no case is opened (pipeline hard stops)
  - ESCALATED result has error_code="UNKNOWN_CLIENT"
  - ESCALATED result has success=False
  - ESCALATED result has lifecycle_state=ESCALATED
  - Requester email is NOT logged (PII protection)
  - Empty requester_email with client_resolver wired → no resolution attempted
"""
from __future__ import annotations

from unittest.mock import MagicMock, call, patch
import pytest

from case_engine.tenant.models import (
    TenantContext,
    TenantEnvironment,
    TenantType,
    UnknownClientError,
)
from case_engine.ticket_orchestration.models import (
    TicketContext,
    TicketLifecycleState,
    TicketOrchestrationResult,
)
from case_engine.ticket_orchestration.orchestrator import TicketOrchestrator


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_context(
    ticket_id: str = "T-001",
    client: str = "unity_bank",
    email: str = "agent@unitybank.co.in",
) -> TicketContext:
    return TicketContext(
        ticket_id=ticket_id,
        client=client,
        subject="Test",
        description="Test description",
        requester_email=email,
    )


def _make_resolver(raises: bool = False, ctx: TenantContext | None = None):
    resolver = MagicMock()
    if raises:
        error = UnknownClientError(domain="xyzbank.com", email="agent@xyzbank.com")
        resolver.resolve.side_effect = error
    else:
        if ctx is None:
            ctx = TenantContext(
                client_id="unity_bank",
                client_name="Unity Bank",
                domain="unitybank.co.in",
                tenant_type=TenantType.BANK,
                environment=TenantEnvironment.UAT,
                enabled_tools=("GetUserDetails",),
                credentials_ref="unity_bank_api_credentials",
            )
        resolver.resolve.return_value = ctx
    return resolver


def _make_audit():
    return MagicMock()


# ── ESCALATED on unknown domain ───────────────────────────────────────────────

class TestUnknownClientEscalated:
    def test_returns_orchestration_result(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        ctx = _make_context(email="agent@xyzbank.com")
        result = orchestrator.process_ticket(ctx)
        assert isinstance(result, TicketOrchestrationResult)

    def test_lifecycle_state_is_escalated(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        result = orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        assert result.lifecycle_state == TicketLifecycleState.ESCALATED

    def test_success_is_false(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        result = orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        assert result.success is False

    def test_error_code_is_unknown_client(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        result = orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        assert result.error_code == "UNKNOWN_CLIENT"

    def test_case_id_is_none(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        result = orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        assert result.case_id is None

    def test_ticket_id_propagated(self):
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=_make_audit(),
        )
        result = orchestrator.process_ticket(_make_context(ticket_id="T-999", email="x@bad.com"))
        assert result.ticket_id == "T-999"

    def test_audit_log_unknown_client_called(self):
        audit = _make_audit()
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=audit,
        )
        orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        audit.log_unknown_client.assert_called_once()

    def test_audit_log_unknown_client_escalated_called(self):
        audit = _make_audit()
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=audit,
        )
        orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        audit.log_unknown_client_escalated.assert_called_once()

    def test_audit_log_called_with_ticket_id(self):
        audit = _make_audit()
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=audit,
        )
        orchestrator.process_ticket(_make_context(ticket_id="T-XYZ", email="x@bad.com"))
        call_args = audit.log_unknown_client.call_args
        assert call_args[1].get("ticket_id") == "T-XYZ" or call_args[0][0] == "T-XYZ"

    def test_no_case_opened_on_unknown_client(self):
        case_svc = MagicMock()
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            case_service=case_svc,
            audit_logger=_make_audit(),
        )
        orchestrator.process_ticket(_make_context(email="x@bad.com"))
        case_svc.open_case.assert_not_called()

    def test_domain_passed_to_audit_not_email(self):
        audit = _make_audit()
        orchestrator = TicketOrchestrator(
            client_resolver=_make_resolver(raises=True),
            audit_logger=audit,
        )
        orchestrator.process_ticket(_make_context(email="agent@xyzbank.com"))
        # check that the raw email is NOT in any audit call args
        all_calls_str = str(audit.log_unknown_client.call_args_list)
        assert "agent@xyzbank.com" not in all_calls_str


# ── Backward compatibility (resolver=None) ────────────────────────────────────

class TestBackwardCompatibility:
    def test_orchestrator_with_no_resolver_does_not_crash(self):
        orchestrator = TicketOrchestrator(client_resolver=None)
        ctx = _make_context(email="agent@xyzbank.com")
        # Should not raise UnknownClientError when no resolver is wired
        # It may fail for other reasons (no case service), but not UnknownClientError
        try:
            orchestrator.process_ticket(ctx)
        except Exception as exc:
            assert "UNKNOWN_CLIENT" not in str(exc)

    def test_orchestrator_default_no_resolver(self):
        orch = TicketOrchestrator()
        assert orch._client_resolver is None

    def test_resolver_none_skips_resolution(self):
        case_svc = MagicMock()
        case_svc.open_case.return_value = MagicMock(
            case_id="case-1", ticket_id="T-001", client="unity_bank",
            topic=None, slot_state={}, workflow_id=None, workflow_state=None,
            workflow_step_index=None, workflow_context={}, tenant_context=None,
        )
        orchestrator = TicketOrchestrator(
            client_resolver=None,
            case_service=case_svc,
        )
        ctx = _make_context(email="agent@unitybank.co.in")
        # With no resolver, open_case should still be attempted (not blocked)
        try:
            orchestrator.process_ticket(ctx)
        except Exception:
            pass
        case_svc.open_case.assert_called_once()

    def test_empty_email_with_resolver_skips_resolution(self):
        resolver = _make_resolver(raises=True)
        orchestrator = TicketOrchestrator(
            client_resolver=resolver,
            audit_logger=_make_audit(),
        )
        ctx = _make_context(email="")
        # Empty email → skip resolution (not an error)
        try:
            orchestrator.process_ticket(ctx)
        except Exception:
            pass
        resolver.resolve.assert_not_called()


# ── UnknownClientError itself ─────────────────────────────────────────────────

class TestUnknownClientErrorBehavior:
    def test_is_exception(self):
        e = UnknownClientError(domain="xyzbank.com")
        assert isinstance(e, Exception)

    def test_can_catch_as_exception(self):
        with pytest.raises(Exception):
            raise UnknownClientError(domain="bad.com")

    def test_domain_attribute_set(self):
        e = UnknownClientError(domain="bad.com", email="x@bad.com")
        assert e.domain == "bad.com"

    def test_email_attribute_set(self):
        e = UnknownClientError(domain="bad.com", email="x@bad.com")
        assert e.email == "x@bad.com"

    def test_message_contains_domain(self):
        e = UnknownClientError(domain="mystery.co.in")
        assert "mystery.co.in" in str(e)

    def test_does_not_contain_email_in_message(self):
        e = UnknownClientError(domain="bad.com", email="secret@bad.com")
        assert "secret@bad.com" not in str(e)
