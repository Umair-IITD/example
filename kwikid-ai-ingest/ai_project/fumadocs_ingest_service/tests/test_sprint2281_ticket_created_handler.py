"""
tests/test_sprint2281_ticket_created_handler.py

Sprint 2.28.1: FreshdeskTicketCreatedHandler tests.

Coverage:
  1. Happy path — valid payload, no client resolver, idempotency set, state created
  2. Duplicate event — idempotency hit → skipped=True
  3. Missing ticket_id → error_code=MISSING_TICKET_ID
  4. Missing email → error_code=MISSING_EMAIL, idempotency marked failed
  5. Client resolver — unknown client → UNKNOWN_CLIENT, no case opened
  6. Client resolver — success → client_id resolved
  7. TicketOrchestrator integration — case_id returned
  8. TicketOrchestrator — exception → ORCHESTRATOR_ERROR, idempotency marked failed
  9. Parse error (malformed payload) → PARSE_ERROR
  10. Metrics incremented on each path
  11. Audit events written
  12. ConversationState created after successful ticket ingestion
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-handler-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.handlers import FreshdeskTicketCreatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore, IdempotencyStatus
from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _unity_payload(ticket_id: int = 197416, email: str = "vishakha.gangurde@unitybank.co.in") -> dict:
    return {
        "freshdesk_webhook": {
            "id": ticket_id,
            "subject": "Not visible in Auditor Trey",
            "description": "<div>Issue details</div>",
            "description_text": "Issue details",
            "status": 2,
            "priority": 3,
            "ticket_type": "Issues",
            "created_at": "2026-06-19T08:06:12Z",
            "requester_email": email,
            "requester_name": "Vishakha Gangurde",
            "tags": "auto_assigned_client, auto_client_assign_as_unity",
            "ticket_custom_fields": {
                "cf_clients": "Unity",
                "cf_session_ids": "F70a8888-96ee-45bb-b777-6942c2cd8ce3",
                "cf_environment": "Production",
                "cf_issue_area": "Frontend",
                "cf_portal": "Admin",
            },
            "attachments": [],
        }
    }


def _make_handler(
    *,
    client_resolver=None,
    orchestrator=None,
    audit=None,
    metrics=None,
) -> tuple[FreshdeskTicketCreatedHandler, WebhookIdempotencyStore, ConversationStateStore]:
    idem = WebhookIdempotencyStore()
    conv = ConversationStateStore()
    handler = FreshdeskTicketCreatedHandler(
        idempotency_store=idem,
        conversation_store=conv,
        ticket_orchestrator=orchestrator,
        client_resolver=client_resolver,
        audit_logger=audit,
        metrics_collector=metrics,
    )
    return handler, idem, conv


def _make_metrics():
    m = MagicMock()
    m.increment = MagicMock()
    m.record_latency = MagicMock()
    return m


# ── Section 1: Happy path ─────────────────────────────────────────────────────

class TestHappyPath:
    def test_success_returns_success_result(self):
        handler, idem, conv = _make_handler()
        result = handler.handle(_unity_payload())
        assert result.success is True
        assert result.ticket_id == "197416"
        assert result.skipped is False

    def test_conversation_state_created(self):
        handler, idem, conv = _make_handler()
        handler.handle(_unity_payload())
        assert conv.exists("197416")

    def test_idempotency_marked_completed(self):
        handler, idem, conv = _make_handler()
        handler.handle(_unity_payload())
        key = idem.make_key("197416", "ticket_created", "2026-06-19T08:06:12Z")
        entry = idem.get(key)
        assert entry is not None
        assert entry.status == IdempotencyStatus.COMPLETED

    def test_action_is_ticket_ingested(self):
        handler, idem, conv = _make_handler()
        result = handler.handle(_unity_payload())
        assert result.action == "ticket_ingested"


# ── Section 2: Duplicate event ────────────────────────────────────────────────

class TestDuplicateEvent:
    def test_duplicate_returns_skipped(self):
        handler, idem, conv = _make_handler()
        payload = _unity_payload()
        handler.handle(payload)  # first time
        result = handler.handle(payload)  # second time
        assert result.success is True
        assert result.skipped is True
        assert result.skip_reason == "DUPLICATE"

    def test_duplicate_does_not_create_second_conversation_state(self):
        handler, idem, conv = _make_handler()
        payload = _unity_payload()
        handler.handle(payload)
        count_before = conv.count()
        handler.handle(payload)
        assert conv.count() == count_before


# ── Section 3: Missing ticket_id ──────────────────────────────────────────────

class TestMissingTicketId:
    def test_zero_ticket_id_error(self):
        handler, _, _ = _make_handler()
        payload = _unity_payload(ticket_id=0)
        result = handler.handle(payload)
        assert result.success is False
        assert result.error_code == "MISSING_TICKET_ID"

    def test_missing_freshdesk_webhook_key_parse_error(self):
        handler, _, _ = _make_handler()
        result = handler.handle({"unexpected": "structure", "freshdesk_webhook": {"id": 0}})
        assert result.success is False


# ── Section 4: Missing email ──────────────────────────────────────────────────

class TestMissingEmail:
    def test_empty_email_returns_error(self):
        handler, idem, _ = _make_handler()
        payload = _unity_payload(email="")
        result = handler.handle(payload)
        assert result.success is False
        assert result.error_code == "MISSING_EMAIL"

    def test_empty_email_marks_idempotency_failed(self):
        handler, idem, _ = _make_handler()
        payload = _unity_payload(email="")
        handler.handle(payload)
        key = idem.make_key("197416", "ticket_created", "2026-06-19T08:06:12Z")
        entry = idem.get(key)
        assert entry is not None
        assert entry.status == IdempotencyStatus.FAILED


# ── Section 5: Client resolver — unknown client ───────────────────────────────

class TestUnknownClient:
    def test_unknown_client_returns_error(self):
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=Exception("UnknownClientError"))
        handler, idem, _ = _make_handler(client_resolver=resolver)
        result = handler.handle(_unity_payload())
        assert result.success is False
        assert result.error_code == "UNKNOWN_CLIENT"

    def test_unknown_client_domain_in_detail(self):
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=Exception("UnknownClientError"))
        handler, _, _ = _make_handler(client_resolver=resolver)
        result = handler.handle(_unity_payload())
        assert result.detail is not None
        assert "domain" in result.detail

    def test_unknown_client_does_not_create_conversation_state(self):
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=Exception("UnknownClientError"))
        handler, _, conv = _make_handler(client_resolver=resolver)
        handler.handle(_unity_payload())
        assert not conv.exists("197416")


# ── Section 6: Client resolver — success ─────────────────────────────────────

class TestClientResolverSuccess:
    def test_resolved_client_id_used(self):
        resolver = MagicMock()
        ctx = MagicMock()
        ctx.client_id = "unity_bank"
        ctx.client_name = "Unity Bank"
        resolver.resolve = MagicMock(return_value=ctx)
        handler, idem, conv = _make_handler(client_resolver=resolver)
        result = handler.handle(_unity_payload())
        assert result.success is True
        # ConversationState should use resolved client_id
        state = conv.get("197416")
        assert state is not None
        assert state.client_id == "unity_bank"


# ── Section 7: Orchestrator integration ──────────────────────────────────────

class TestOrchestratorIntegration:
    def test_orchestrator_result_case_id_stored(self):
        orch = MagicMock()
        orch_result = MagicMock()
        orch_result.case_id = "case-999"
        orch.process_ticket = MagicMock(return_value=orch_result)
        handler, idem, conv = _make_handler(orchestrator=orch)
        result = handler.handle(_unity_payload())
        assert result.case_id == "case-999"

    def test_orchestrator_case_id_stored_in_conversation(self):
        orch = MagicMock()
        orch_result = MagicMock()
        orch_result.case_id = "case-abc"
        orch.process_ticket = MagicMock(return_value=orch_result)
        handler, idem, conv = _make_handler(orchestrator=orch)
        handler.handle(_unity_payload())
        state = conv.get("197416")
        assert state.case_id == "case-abc"

    def test_orchestrator_called_with_correct_args(self):
        orch = MagicMock()
        orch_result = MagicMock()
        orch_result.case_id = "case-1"
        orch.process_ticket = MagicMock(return_value=orch_result)
        handler, _, _ = _make_handler(orchestrator=orch)
        handler.handle(_unity_payload(ticket_id=197416, email="vishakha@unitybank.co.in"))
        call_kwargs = orch.process_ticket.call_args[1]
        assert call_kwargs["ticket_id"] == "197416"
        assert call_kwargs["requester_email"] == "vishakha@unitybank.co.in"


# ── Section 8: Orchestrator exception ────────────────────────────────────────

class TestOrchestratorException:
    def test_orchestrator_error_returns_failure(self):
        orch = MagicMock()
        orch.process_ticket = MagicMock(side_effect=RuntimeError("db down"))
        handler, idem, _ = _make_handler(orchestrator=orch)
        result = handler.handle(_unity_payload())
        assert result.success is False
        assert result.error_code == "ORCHESTRATOR_ERROR"

    def test_orchestrator_error_marks_idempotency_failed(self):
        orch = MagicMock()
        orch.process_ticket = MagicMock(side_effect=RuntimeError("error"))
        handler, idem, _ = _make_handler(orchestrator=orch)
        handler.handle(_unity_payload())
        key = idem.make_key("197416", "ticket_created", "2026-06-19T08:06:12Z")
        entry = idem.get(key)
        assert entry.status == IdempotencyStatus.FAILED


# ── Section 9: Parse error ────────────────────────────────────────────────────

class TestParseError:
    def test_completely_wrong_structure(self):
        handler, _, _ = _make_handler()
        # Pass a list instead of dict
        result = handler.handle([])  # type: ignore
        assert result.success is False
        assert result.error_code == "PARSE_ERROR"


# ── Section 10: Metrics ───────────────────────────────────────────────────────

class TestMetrics:
    def test_webhooks_received_incremented(self):
        metrics = _make_metrics()
        handler, _, _ = _make_handler(metrics=metrics)
        handler.handle(_unity_payload())
        metrics.increment.assert_called()
        all_calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("webhooks_received" in c for c in all_calls)

    def test_duplicate_counter_incremented(self):
        metrics = _make_metrics()
        handler, _, _ = _make_handler(metrics=metrics)
        payload = _unity_payload()
        handler.handle(payload)
        metrics.increment.reset_mock()
        handler.handle(payload)
        all_calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("duplicate" in c for c in all_calls)

    def test_latency_recorded_on_success(self):
        metrics = _make_metrics()
        handler, _, _ = _make_handler(metrics=metrics)
        handler.handle(_unity_payload())
        metrics.record_latency.assert_called()


# ── Section 11: Audit events ─────────────────────────────────────────────────

class TestAuditEvents:
    def test_ticket_ingested_audit_written(self):
        audit = MagicMock()
        audit._write = MagicMock()
        handler, _, _ = _make_handler(audit=audit)
        handler.handle(_unity_payload())
        audit._write.assert_called()
        from case_engine.models import AuditEventType
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.TICKET_INGESTED in event_types

    def test_unknown_client_audit_written(self):
        audit = MagicMock()
        audit._write = MagicMock()
        resolver = MagicMock()
        resolver.resolve = MagicMock(side_effect=Exception("unknown"))
        handler, _, _ = _make_handler(client_resolver=resolver, audit=audit)
        handler.handle(_unity_payload())
        from case_engine.models import AuditEventType
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CLIENT_RESOLUTION_FAILED in event_types
