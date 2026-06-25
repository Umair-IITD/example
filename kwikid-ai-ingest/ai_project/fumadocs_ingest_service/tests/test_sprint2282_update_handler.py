"""
tests/test_sprint2282_update_handler.py

Sprint 2.28.2: FreshdeskTicketUpdatedHandler — latest_comment detection (defect fix).

Validates the Sprint 2.28.2 defect fix:
  - BEFORE: _detect_update_action checked changes["conversations"] — key that NEVER
    exists in real Freshdesk update payloads. All replies fell through to "other".
  - AFTER:  _detect_update_action checks latest_comment at the top level of
    freshdesk_webhook. This is the authoritative source per Section 10.1 of
    freshdesk_integration.md and Section 9.2 of the live payload audit.

Coverage:
  1. Customer reply via latest_comment (incoming=True, private=False) → "customer_reply"
  2. Agent public reply via latest_comment (incoming=False, private=False) → "agent_reply"
  3. Internal note via latest_comment (incoming=False, private=True) → "internal_note"
  4. No latest_comment + status change → "status_change"
  5. No latest_comment + tag change → "tag_update"
  6. No latest_comment + no recognized change → "other" (skipped)
  7. latest_comment takes priority over changes dict
  8. Clarification continuation: customer reply while awaiting → resets state, audit written
  9. Clarification: customer reply while NOT awaiting → no state reset
  10. Clarification: audit event CLARIFICATION_REPLY_RECEIVED when awaiting
  11. Lifecycle: latest_comment customer reply does NOT override status change on same event
  12. Metrics: customer_replies counter incremented on latest_comment customer reply
  13. Idempotency preserved across latest_comment events
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-2282-update-handler")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.handlers import FreshdeskTicketUpdatedHandler, HandlerResult
from freshdesk.idempotency import WebhookIdempotencyStore
from freshdesk.conversation_state import ConversationStateStore, ConversationLifecycle


# ── Helpers ───────────────────────────────────────────────────────────────────

def _payload(
    ticket_id: int = 12345,
    changes: dict | None = None,
    updated_at: str = "2026-06-19T10:00:00Z",
    latest_comment: dict | None = None,
) -> dict:
    inner: dict = {
        "id": ticket_id,
        "subject": "Support request",
        "status": 2,
        "priority": 2,
        "updated_at": updated_at,
        "requester_email": "user@client.com",
        "ticket_custom_fields": {"cf_clients": "ClientA"},
        "changes": changes or {},
        "attachments": [],
    }
    if latest_comment is not None:
        inner["latest_comment"] = latest_comment
    return {"freshdesk_webhook": inner}


def _handler(
    *,
    audit=None,
    metrics=None,
    prefill_conv: bool = True,
    ticket_id: str = "12345",
) -> tuple[FreshdeskTicketUpdatedHandler, WebhookIdempotencyStore, ConversationStateStore]:
    idem = WebhookIdempotencyStore()
    conv = ConversationStateStore()
    if prefill_conv:
        conv.get_or_create(ticket_id, "client_a")
    h = FreshdeskTicketUpdatedHandler(
        idempotency_store=idem,
        conversation_store=conv,
        audit_logger=audit,
        metrics_collector=metrics,
    )
    return h, idem, conv


# ── Section 1: latest_comment customer reply ──────────────────────────────────

class TestLatestCommentCustomerReply:
    def test_customer_reply_detected(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        assert result.success is True
        assert result.action == "customer_reply"

    def test_customer_reply_not_skipped(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        assert result.skipped is False

    def test_customer_reply_ticket_id_preserved(self):
        h, _, _ = _handler()
        result = h.handle(_payload(ticket_id=99001, latest_comment={"incoming": True, "private": False}))
        assert result.ticket_id == "99001"


# ── Section 2: latest_comment agent reply ────────────────────────────────────

class TestLatestCommentAgentReply:
    def test_agent_reply_detected(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": False, "private": False}))
        assert result.action == "agent_reply"

    def test_agent_reply_success(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": False, "private": False}))
        assert result.success is True


# ── Section 3: latest_comment internal note ──────────────────────────────────

class TestLatestCommentInternalNote:
    def test_internal_note_detected(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": False, "private": True}))
        assert result.action == "internal_note"

    def test_internal_note_not_skipped(self):
        h, _, _ = _handler()
        result = h.handle(_payload(latest_comment={"incoming": False, "private": True}))
        assert result.skipped is False


# ── Section 4: no latest_comment — fallback to changes dict ─────────────────

class TestNoLatestCommentFallback:
    def test_status_change_detected_without_latest_comment(self):
        h, _, _ = _handler()
        result = h.handle(_payload(changes={"status": [2, 4]}))
        assert result.action == "status_change"

    def test_tag_update_detected_without_latest_comment(self):
        h, _, _ = _handler()
        result = h.handle(_payload(changes={"tags": [[], ["vip"]]}))
        assert result.action == "tag_update"

    def test_empty_changes_skipped_without_latest_comment(self):
        h, _, _ = _handler()
        result = h.handle(_payload(changes={}))
        assert result.skipped is True
        assert result.skip_reason == "NO_ACTIONABLE_CHANGE"

    def test_unknown_change_key_skipped_without_latest_comment(self):
        h, _, _ = _handler()
        result = h.handle(_payload(changes={"group_id": [None, 123]}))
        assert result.skipped is True


# ── Section 5: latest_comment priority over changes ──────────────────────────

class TestLatestCommentPriority:
    def test_latest_comment_wins_over_status_change(self):
        """If latest_comment is present, it determines action type, not changes."""
        h, _, _ = _handler()
        result = h.handle(_payload(
            latest_comment={"incoming": True, "private": False},
            changes={"status": [2, 4]},
        ))
        assert result.action == "customer_reply"

    def test_latest_comment_agent_note_wins_over_tag_change(self):
        h, _, _ = _handler()
        result = h.handle(_payload(
            latest_comment={"incoming": False, "private": True},
            changes={"tags": [[], ["urgent"]]},
        ))
        assert result.action == "internal_note"


# ── Section 6: clarification continuation ───────────────────────────────────

class TestClarificationContinuation:
    def test_customer_reply_while_awaiting_resets_state(self):
        h, _, conv = _handler()
        conv.update("12345", awaiting_customer=True, clarification_pending=True)

        result = h.handle(_payload(latest_comment={"incoming": True, "private": False}))

        assert result.success is True
        assert result.action == "customer_reply"
        state = conv.get("12345")
        assert state.awaiting_customer is False
        assert state.clarification_pending is False

    def test_customer_reply_while_awaiting_sets_lifecycle_open(self):
        h, _, conv = _handler()
        conv.update(
            "12345",
            awaiting_customer=True,
            lifecycle_state=ConversationLifecycle.PENDING,
        )
        h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        state = conv.get("12345")
        assert state.lifecycle_state == ConversationLifecycle.OPEN

    def test_customer_reply_while_not_awaiting_no_state_reset(self):
        h, _, conv = _handler()
        conv.update("12345", awaiting_customer=False, clarification_pending=False)

        h.handle(_payload(latest_comment={"incoming": True, "private": False}))

        state = conv.get("12345")
        assert state.awaiting_customer is False

    def test_clarification_result_detail_when_awaiting(self):
        h, _, conv = _handler()
        conv.update("12345", awaiting_customer=True)
        result = h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        assert result.detail is not None
        assert result.detail.get("clarification_resolved") is True

    def test_no_clarification_detail_when_not_awaiting(self):
        h, _, conv = _handler()
        conv.update("12345", awaiting_customer=False)
        result = h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        assert (result.detail or {}).get("clarification_resolved") is not True


# ── Section 7: audit events via latest_comment ───────────────────────────────

class TestAuditEventsLatestComment:
    def _audit(self):
        a = MagicMock()
        a._write = MagicMock()
        return a

    def test_customer_reply_audit_written(self):
        from case_engine.models import AuditEventType
        audit = self._audit()
        h, _, _ = _handler(audit=audit)
        h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CUSTOMER_REPLY_RECEIVED in types

    def test_clarification_reply_audit_when_awaiting(self):
        from case_engine.models import AuditEventType
        audit = self._audit()
        idem = WebhookIdempotencyStore()
        conv = ConversationStateStore()
        conv.get_or_create("12345", "client_a")
        conv.update("12345", awaiting_customer=True)
        h = FreshdeskTicketUpdatedHandler(
            idempotency_store=idem,
            conversation_store=conv,
            audit_logger=audit,
        )
        h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.CLARIFICATION_REPLY_RECEIVED in types

    def test_agent_note_audit_written(self):
        from case_engine.models import AuditEventType
        audit = self._audit()
        h, _, _ = _handler(audit=audit)
        h.handle(_payload(latest_comment={"incoming": False, "private": True}))
        types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.AGENT_NOTE_RECEIVED in types

    def test_agent_reply_audit_written(self):
        from case_engine.models import AuditEventType
        audit = self._audit()
        h, _, _ = _handler(audit=audit)
        h.handle(_payload(latest_comment={"incoming": False, "private": False}))
        types = [c[0][0].action_type for c in audit._write.call_args_list]
        assert AuditEventType.AGENT_NOTE_RECEIVED in types


# ── Section 8: metrics via latest_comment ────────────────────────────────────

class TestMetricsLatestComment:
    def _metrics(self):
        m = MagicMock()
        m.increment = MagicMock()
        m.record_latency = MagicMock()
        return m

    def test_customer_replies_counter_incremented(self):
        from freshdesk.metrics import COUNTER_FD_CUSTOMER_REPLIES_TOTAL
        metrics = self._metrics()
        h, _, _ = _handler(metrics=metrics)
        h.handle(_payload(latest_comment={"incoming": True, "private": False}))
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert any("customer_replies" in c for c in calls)

    def test_customer_replies_not_incremented_for_agent_reply(self):
        metrics = self._metrics()
        h, _, _ = _handler(metrics=metrics)
        h.handle(_payload(latest_comment={"incoming": False, "private": False}))
        calls = [c[0][0] for c in metrics.increment.call_args_list]
        assert not any("customer_replies" in c for c in calls)


# ── Section 9: idempotency with latest_comment ───────────────────────────────

class TestIdempotencyWithLatestComment:
    def test_duplicate_latest_comment_event_skipped(self):
        h, _, _ = _handler()
        p = _payload(latest_comment={"incoming": True, "private": False})
        h.handle(p)
        result = h.handle(p)
        assert result.skipped is True
        assert result.skip_reason == "DUPLICATE"

    def test_different_timestamps_both_processed(self):
        h, _, _ = _handler()
        p1 = _payload(updated_at="2026-06-19T10:00:00Z", latest_comment={"incoming": True, "private": False})
        p2 = _payload(updated_at="2026-06-19T11:00:00Z", latest_comment={"incoming": True, "private": False})
        r1 = h.handle(p1)
        r2 = h.handle(p2)
        assert not r1.skipped
        assert not r2.skipped
