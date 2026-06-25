"""
tests/test_sprint2281_ticket_updated_handler.py

Sprint 2.28.1: FreshdeskTicketUpdatedHandler tests.

Coverage:
  1. Customer reply detection → action="customer_reply"
  2. Agent reply detection → action="agent_reply"
  3. Internal note detection → action="internal_note"
  4. Status change detection → action="status_change", lifecycle updated
  5. Tag update detection → action="tag_update"
  6. No actionable change → skipped=True
  7. Duplicate event → skipped=True
  8. Missing ticket_id → error
  9. Clarification continuation path — awaiting_customer=True → state reset
  10. Status → lifecycle mapping (2=OPEN, 3=PENDING, 4=RESOLVED, 5=CLOSED)
  11. Metrics incremented
  12. Audit events written
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-updated-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.handlers import FreshdeskTicketUpdatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore, IdempotencyStatus
from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_updated_payload(
    ticket_id: int = 197416,
    changes: dict | None = None,
    updated_at: str = "2026-06-19T10:00:00Z",
    latest_comment: dict | None = None,
) -> dict:
    inner: dict = {
        "id": ticket_id,
        "subject": "Test ticket",
        "status": 2,
        "priority": 2,
        "updated_at": updated_at,
        "requester_email": "test@unitybank.co.in",
        "ticket_custom_fields": {"cf_clients": "Unity"},
        "changes": changes or {},
        "attachments": [],
    }
    if latest_comment is not None:
        inner["latest_comment"] = latest_comment
    return {"freshdesk_webhook": inner}


def _make_handler(
    *,
    audit=None,
    metrics=None,
    prefill_conv: bool = False,
    client_id: str = "unity_bank",
) -> tuple[FreshdeskTicketUpdatedHandler, WebhookIdempotencyStore, ConversationStateStore]:
    idem = WebhookIdempotencyStore()
    conv = ConversationStateStore()
    if prefill_conv:
        conv.get_or_create("197416", client_id)
    handler = FreshdeskTicketUpdatedHandler(
        idempotency_store=idem,
        conversation_store=conv,
        audit_logger=audit,
        metrics_collector=metrics,
    )
    return handler, idem, conv


def _make_metrics():
    m = MagicMock()
    m.increment = MagicMock()
    m.record_latency = MagicMock()
    return m


# ── Section 1: Customer reply detection ──────────────────────────────────────

class TestCustomerReplyDetection:
    def test_conversations_change_incoming_true(self):
        handler, idem, conv = _make_handler(prefill_conv=True)
        # Sprint 2.28.2: latest_comment (not changes.conversations) is the authoritative source
        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        result = handler.handle(payload)
        assert result.success is True
        assert result.action == "customer_reply"

    def test_conversations_change_not_incoming(self):
        handler, idem, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": False, "private": False}
        )
        result = handler.handle(payload)
        assert result.action == "agent_reply"

    def test_conversations_change_private_note(self):
        handler, idem, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": False, "private": True}
        )
        result = handler.handle(payload)
        assert result.action == "internal_note"


# ── Section 2: Status change ──────────────────────────────────────────────────

class TestStatusChange:
    def test_status_change_detected(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 4]})
        result = handler.handle(payload)
        assert result.action == "status_change"

    def test_status_4_sets_resolved_lifecycle(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 4]})
        handler.handle(payload)
        state = conv.get("197416")
        assert state.lifecycle_state == ConversationLifecycle.RESOLVED

    def test_status_5_sets_closed_lifecycle(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [4, 5]})
        handler.handle(payload)
        state = conv.get("197416")
        assert state.lifecycle_state == ConversationLifecycle.CLOSED

    def test_status_3_sets_pending_lifecycle(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 3]})
        handler.handle(payload)
        state = conv.get("197416")
        assert state.lifecycle_state == ConversationLifecycle.PENDING

    def test_status_2_sets_open_lifecycle(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [3, 2]})
        handler.handle(payload)
        state = conv.get("197416")
        assert state.lifecycle_state == ConversationLifecycle.OPEN


# ── Section 3: Tag update ─────────────────────────────────────────────────────

class TestTagUpdate:
    def test_tag_update_detected(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"tags": [[], ["new_tag"]]})
        result = handler.handle(payload)
        assert result.action == "tag_update"


# ── Section 4: No actionable change ──────────────────────────────────────────

class TestNoActionableChange:
    def test_empty_changes_skipped(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={})
        result = handler.handle(payload)
        assert result.skipped is True
        assert result.skip_reason == "NO_ACTIONABLE_CHANGE"

    def test_unknown_change_key_skipped(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"some_unknown_field": "value"})
        result = handler.handle(payload)
        assert result.skipped is True


# ── Section 5: Duplicate event ────────────────────────────────────────────────

class TestDuplicateEvent:
    def test_duplicate_skipped(self):
        handler, idem, conv = _make_handler(prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 4]})
        handler.handle(payload)
        result = handler.handle(payload)
        assert result.skipped is True
        assert result.skip_reason == "DUPLICATE"


# ── Section 6: Missing ticket_id ─────────────────────────────────────────────

class TestMissingTicketId:
    def test_zero_ticket_id_error(self):
        handler, _, _ = _make_handler()
        payload = _make_updated_payload(ticket_id=0)
        result = handler.handle(payload)
        assert result.success is False
        assert result.error_code == "MISSING_TICKET_ID"

    def test_invalid_payload_parse_error(self):
        handler, _, _ = _make_handler()
        result = handler.handle(None)  # type: ignore
        assert result.success is False
        assert result.error_code == "PARSE_ERROR"


# ── Section 7: Clarification continuation ────────────────────────────────────

class TestClarificationContinuation:
    def test_customer_reply_while_awaiting_resets_state(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        # Set up awaiting customer state
        conv.update("197416", awaiting_customer=True, clarification_pending=True)

        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        result = handler.handle(payload)
        assert result.success is True
        assert result.action == "customer_reply"

        state = conv.get("197416")
        assert state.awaiting_customer is False
        assert state.clarification_pending is False

    def test_customer_reply_while_not_awaiting_does_not_reset(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        # Not awaiting customer — regular reply
        conv.update("197416", awaiting_customer=False, clarification_pending=False)

        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        result = handler.handle(payload)
        assert result.success is True
        state = conv.get("197416")
        # State unchanged (not in clarification mode)
        assert state.awaiting_customer is False

    def test_clarification_detail_in_result_when_awaiting(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        conv.update("197416", awaiting_customer=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        result = handler.handle(payload)
        assert result.detail is not None
        assert result.detail.get("clarification_resolved") is True


# ── Section 8: Multiple different timestamps (unique events) ─────────────────

class TestMultipleEvents:
    def test_different_timestamps_not_duplicate(self):
        handler, _, conv = _make_handler(prefill_conv=True)
        payload1 = _make_updated_payload(changes={"status": [2, 3]}, updated_at="2026-06-19T10:00:00Z")
        payload2 = _make_updated_payload(changes={"status": [3, 4]}, updated_at="2026-06-19T11:00:00Z")
        r1 = handler.handle(payload1)
        r2 = handler.handle(payload2)
        assert not r1.skipped
        assert not r2.skipped


# ── Section 9: Metrics ────────────────────────────────────────────────────────

class TestMetrics:
    def test_webhooks_received_incremented(self):
        metrics = _make_metrics()
        handler, _, conv = _make_handler(metrics=metrics, prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 4]})
        handler.handle(payload)
        all_calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("webhooks_received" in c for c in all_calls)

    def test_customer_replies_counter_incremented(self):
        metrics = _make_metrics()
        handler, _, conv = _make_handler(metrics=metrics, prefill_conv=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        handler.handle(payload)
        all_calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("customer_replies" in c for c in all_calls)

    def test_duplicate_counter_incremented(self):
        metrics = _make_metrics()
        handler, _, conv = _make_handler(metrics=metrics, prefill_conv=True)
        payload = _make_updated_payload(changes={"status": [2, 4]})
        handler.handle(payload)
        metrics.increment.reset_mock()
        handler.handle(payload)
        all_calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("duplicate" in c for c in all_calls)


# ── Section 10: Audit events ─────────────────────────────────────────────────

class TestAuditEvents:
    def test_customer_reply_audit_written(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        handler, _, conv = _make_handler(audit=audit, prefill_conv=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        handler.handle(payload)
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CUSTOMER_REPLY_RECEIVED in event_types

    def test_agent_note_audit_written(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        handler, _, conv = _make_handler(audit=audit, prefill_conv=True)
        payload = _make_updated_payload(
            latest_comment={"incoming": False, "private": True}
        )
        handler.handle(payload)
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.AGENT_NOTE_RECEIVED in event_types

    def test_clarification_reply_audit_when_awaiting(self):
        from case_engine.models import AuditEventType
        audit = MagicMock()
        audit._write = MagicMock()
        _, _, conv = _make_handler(prefill_conv=True)
        conv.update("197416", awaiting_customer=True)
        handler = FreshdeskTicketUpdatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=conv,
            audit_logger=audit,
        )
        payload = _make_updated_payload(
            latest_comment={"incoming": True, "private": False}
        )
        handler.handle(payload)
        event_types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CLARIFICATION_REPLY_RECEIVED in event_types
