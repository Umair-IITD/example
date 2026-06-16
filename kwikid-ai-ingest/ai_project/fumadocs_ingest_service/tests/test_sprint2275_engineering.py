"""
tests/test_sprint2275_engineering.py

Sprint 2.27.5 Phase 6: EngineeringEscalationService test suite.

Tests: ticket creation, lifecycle, mock adapter, audit events, factory.
"""
import pytest
from unittest.mock import MagicMock

from case_engine.models import Case
from case_engine.engineering.models import (
    EngineeringEscalationResult,
    EngineeringPriority,
    EngineeringStatus,
    EngineeringTicket,
)
from case_engine.engineering.service import (
    EngineeringEscalationService,
    build_engineering_escalation_service,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

def _make_case(
    case_id: str = "case-001",
    ticket_id: str = "fd-001",
    client: str = "unity_bank",
    topic: str = "VKYC_Session_Failure",
) -> Case:
    c = Case(case_id=case_id, ticket_id=ticket_id, client=client)
    c.topic = topic
    return c


# ── EngineeringPriority ───────────────────────────────────────────────────────

class TestEngineeringPriority:
    def test_all_values(self):
        values = {p.value for p in EngineeringPriority}
        assert "CRITICAL" in values
        assert "HIGH" in values
        assert "MEDIUM" in values
        assert "LOW" in values

    def test_string_enum(self):
        assert EngineeringPriority.HIGH == "HIGH"


# ── EngineeringStatus ─────────────────────────────────────────────────────────

class TestEngineeringStatus:
    def test_all_values(self):
        values = {s.value for s in EngineeringStatus}
        assert "PENDING" in values
        assert "IN_PROGRESS" in values
        assert "RESOLVED" in values
        assert "CLOSED" in values
        assert "FAILED" in values


# ── EngineeringTicket ─────────────────────────────────────────────────────────

class TestEngineeringTicket:
    def _make_ticket(self) -> EngineeringTicket:
        return EngineeringTicket(
            ticket_id="t1",
            external_id=None,
            case_id="case-001",
            freshdesk_ticket_id="fd-001",
            title="[L2] VKYC escalation",
            description="root cause: infrastructure",
            priority=EngineeringPriority.HIGH,
            status=EngineeringStatus.PENDING,
            assignee=None,
            asana_project_id=None,
            created_at="2025-01-01T00:00:00+00:00",
            updated_at="2025-01-01T00:00:00+00:00",
        )

    def test_to_dict(self):
        ticket = self._make_ticket()
        d = ticket.to_dict()
        assert d["ticket_id"] == "t1"
        assert d["priority"] == "HIGH"
        assert d["status"] == "PENDING"

    def test_with_status_immutable_update(self):
        ticket = self._make_ticket()
        updated = ticket.with_status(EngineeringStatus.RESOLVED, resolved_at="2025-01-02T00:00:00+00:00")
        assert updated.status == EngineeringStatus.RESOLVED
        assert updated.resolved_at == "2025-01-02T00:00:00+00:00"
        # Original unchanged
        assert ticket.status == EngineeringStatus.PENDING

    def test_immutable(self):
        ticket = self._make_ticket()
        with pytest.raises((AttributeError, TypeError)):
            ticket.status = EngineeringStatus.RESOLVED  # type: ignore[misc]


# ── EngineeringEscalationResult ───────────────────────────────────────────────

class TestEngineeringEscalationResult:
    def test_to_dict(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(
            case=_make_case(),
            topic="VKYC_Session_Failure",
        )
        d = result.to_dict()
        assert "result_id" in d
        assert "ticket" in d
        assert d["operation"] == "create"
        assert d["success"] is True

    def test_failure_classmethod(self):
        result = EngineeringEscalationResult.failure(
            case_id="c1",
            freshdesk_ticket_id="fd-1",
            operation="create",
            error_code="CREATE_FAILED",
            error_msg="something broke",
            duration_ms=5,
        )
        assert result.success is False
        assert result.error_code == "CREATE_FAILED"
        assert result.ticket.status == EngineeringStatus.FAILED


# ── EngineeringEscalationService — create_ticket ──────────────────────────────

class TestEngineeringEscalationServiceCreate:
    def test_create_ticket_success(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(
            case=_make_case(),
            topic="VKYC_Session_Failure",
            freshdesk_ticket_id="fd-001",
            escalation_reason="Backend is down.",
        )
        assert result.success is True
        assert result.ticket.status == EngineeringStatus.PENDING
        assert result.ticket.case_id == "case-001"

    def test_create_ticket_stores_in_memory(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(case=_make_case(), topic="VKYC_Session_Failure")
        ticket_id = result.ticket.ticket_id
        retrieved = svc.get_ticket(ticket_id)
        assert retrieved is not None
        assert retrieved.ticket_id == ticket_id

    def test_create_ticket_infers_priority_from_topic(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(
            case=_make_case(topic="API_Callback_Failure"),
            topic="API_Callback_Failure",
        )
        assert result.ticket.priority in (EngineeringPriority.HIGH, EngineeringPriority.MEDIUM)

    def test_create_ticket_with_root_cause_critical(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(
            case=_make_case(),
            topic="VKYC_Session_Failure",
            root_cause={"category": "outage", "severity": "critical"},
        )
        assert result.ticket.priority == EngineeringPriority.CRITICAL

    def test_create_ticket_description_includes_root_cause(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(
            case=_make_case(),
            topic="VKYC_Session_Failure",
            root_cause={"category": "database", "explanation": "DB connection pool exhausted."},
            sop_steps=["Step 1: Check logs", "Step 2: Restart service"],
        )
        assert "database" in result.ticket.description.lower()

    def test_create_ticket_never_raises(self):
        svc = build_engineering_escalation_service()
        result = svc.create_ticket(case=None, topic="VKYC_Session_Failure")
        assert isinstance(result, EngineeringEscalationResult)

    def test_list_tickets_for_case(self):
        svc = build_engineering_escalation_service()
        case = _make_case()
        svc.create_ticket(case=case, topic="VKYC_Session_Failure")
        svc.create_ticket(case=case, topic="API_Callback_Failure")
        tickets = svc.list_tickets_for_case(case.case_id)
        assert len(tickets) == 2


# ── Update / resolve / sync ───────────────────────────────────────────────────

class TestEngineeringEscalationServiceLifecycle:
    def _create(self, svc=None):
        if svc is None:
            svc = build_engineering_escalation_service()
        result = svc.create_ticket(case=_make_case(), topic="VKYC_Session_Failure")
        return svc, result.ticket.ticket_id

    def test_update_ticket_status(self):
        svc, tid = self._create()
        result = svc.update_ticket(tid, status=EngineeringStatus.IN_PROGRESS)
        assert result.success is True
        assert result.ticket.status == EngineeringStatus.IN_PROGRESS

    def test_update_ticket_assignee(self):
        svc, tid = self._create()
        result = svc.update_ticket(tid, assignee="engineer@kwikid.com")
        assert result.success is True
        assert result.ticket.assignee == "engineer@kwikid.com"

    def test_update_nonexistent_ticket(self):
        svc, _ = self._create()
        result = svc.update_ticket("not-a-ticket", status=EngineeringStatus.RESOLVED)
        assert result.success is False
        assert result.error_code == "ENGINEERING_TICKET_NOT_FOUND"

    def test_resolve_ticket(self):
        svc, tid = self._create()
        result = svc.resolve_ticket(tid)
        assert result.success is True
        assert result.ticket.status == EngineeringStatus.RESOLVED
        assert result.ticket.resolved_at is not None

    def test_resolve_nonexistent_ticket(self):
        svc, _ = self._create()
        result = svc.resolve_ticket("nonexistent")
        assert result.success is False

    def test_sync_status_no_asana(self):
        svc, tid = self._create()
        result = svc.sync_status(tid)
        assert result.success is True
        assert result.ticket.ticket_id == tid

    def test_sync_nonexistent_ticket(self):
        svc, _ = self._create()
        result = svc.sync_status("nonexistent")
        assert result.success is False

    def test_never_raises_update(self):
        svc = build_engineering_escalation_service()
        result = svc.update_ticket("bad-id", status=None)
        assert isinstance(result, EngineeringEscalationResult)


# ── Asana adapter injection ───────────────────────────────────────────────────

class TestEngineeringServiceAsanaInjection:
    def test_real_asana_client_called_on_create(self):
        mock_asana = MagicMock()
        mock_asana.create_task.return_value = {"gid": "asana-123", "project_id": "proj-456"}
        svc = build_engineering_escalation_service(asana_client=mock_asana)
        result = svc.create_ticket(case=_make_case(), topic="API_Callback_Failure")
        mock_asana.create_task.assert_called_once()
        assert result.ticket.external_id == "asana-123"

    def test_asana_failure_fallback_to_mock(self):
        mock_asana = MagicMock()
        mock_asana.create_task.side_effect = RuntimeError("asana API down")
        svc = build_engineering_escalation_service(asana_client=mock_asana)
        result = svc.create_ticket(case=_make_case(), topic="API_Callback_Failure")
        assert result.success is True  # graceful fallback
        assert result.ticket.external_id is None


# ── Audit events ──────────────────────────────────────────────────────────────

class TestEngineeringEscalationAudit:
    def test_audit_emitted_on_create(self):
        mock_audit = MagicMock()
        svc = build_engineering_escalation_service(audit_logger=mock_audit)
        svc.create_ticket(case=_make_case(), topic="VKYC_Session_Failure")
        mock_audit.log_engineering_escalation_created.assert_called_once()

    def test_audit_emitted_on_resolve(self):
        mock_audit = MagicMock()
        svc = build_engineering_escalation_service(audit_logger=mock_audit)
        result = svc.create_ticket(case=_make_case(), topic="VKYC_Session_Failure")
        tid = result.ticket.ticket_id
        svc.resolve_ticket(tid, case=_make_case())
        mock_audit.log_engineering_escalation_resolved.assert_called_once()

    def test_audit_failure_does_not_crash(self):
        mock_audit = MagicMock()
        mock_audit.log_engineering_escalation_created.side_effect = RuntimeError("audit down")
        svc = build_engineering_escalation_service(audit_logger=mock_audit)
        result = svc.create_ticket(case=_make_case(), topic="VKYC_Session_Failure")
        assert result.success is True


# ── Factory ───────────────────────────────────────────────────────────────────

class TestEngineeringEscalationFactory:
    def test_build_with_no_args(self):
        svc = build_engineering_escalation_service()
        assert svc is not None

    def test_build_with_audit(self):
        mock_audit = MagicMock()
        svc = build_engineering_escalation_service(audit_logger=mock_audit)
        assert svc._audit is mock_audit

    def test_build_with_asana(self):
        mock_asana = MagicMock()
        svc = build_engineering_escalation_service(asana_client=mock_asana)
        assert svc._asana is mock_asana
