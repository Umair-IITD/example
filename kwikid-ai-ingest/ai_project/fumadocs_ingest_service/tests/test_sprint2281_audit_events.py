"""
tests/test_sprint2281_audit_events.py

Sprint 2.28.1: Freshdesk audit events and AuditLogger methods tests.

Coverage:
  1. AuditEventType — all 15 new Sprint 2.28.1 values present, total = 95
  2. log_webhook_received — writes correct event type and fields
  3. log_webhook_rejected — reason and error_code set
  4. log_webhook_duplicate — outcome=SKIPPED, idempotency_key set
  5. log_ticket_ingested — case_id, cf_clients in detail
  6. log_customer_reply_received — clarification_resolved flag
  7. log_private_note_added — note_id in detail
  8. log_public_reply_sent — note_id in detail
  9. log_signature_failure — event_type and reason
  10. log_freshdesk_api_error — operation and error in detail
  11. log_conversation_state_updated — new_state and reason
  12. All methods: supabase_client=None → log-only mode, no exception
  13. Sprint 2.28.1 event names match exact string values from the enum
"""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch, call

import pytest

os.environ.setdefault("RAG_API_KEY", "test-audit-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from case_engine.models import AuditEventType
from case_engine.audit import AuditLogger


# ── Section 1: AuditEventType enum completeness ───────────────────────────────

class TestAuditEventTypeCompleteness:
    def test_total_count_is_95(self):
        assert len(AuditEventType) == 95

    def test_sprint2281_events_present(self):
        expected = [
            "WEBHOOK_RECEIVED",
            "WEBHOOK_REJECTED",
            "WEBHOOK_DUPLICATE",
            "TICKET_INGESTED",
            "TICKET_UPDATED",
            "TICKET_SKIPPED",
            "CUSTOMER_REPLY_RECEIVED",
            "AGENT_NOTE_RECEIVED",
            "PRIVATE_NOTE_ADDED",
            "PUBLIC_REPLY_SENT",
            "CONVERSATION_STATE_UPDATED",
            "CLARIFICATION_REPLY_RECEIVED",
            "FRESHDESK_API_ERROR",
            "SIGNATURE_FAILURE",
            "CLARIFICATION_PENDING",
        ]
        all_names = [e.name for e in AuditEventType]
        for name in expected:
            assert name in all_names, f"Missing AuditEventType: {name}"

    def test_sprint2279_events_still_present(self):
        assert AuditEventType.CLIENT_RESOLVED == "CLIENT_RESOLVED"
        assert AuditEventType.TENANT_REGISTRY_VALIDATED == "TENANT_REGISTRY_VALIDATED"

    def test_event_values_are_strings(self):
        for event in AuditEventType:
            assert isinstance(event.value, str)

    def test_webhook_received_value(self):
        assert AuditEventType.WEBHOOK_RECEIVED.value == "WEBHOOK_RECEIVED"

    def test_signature_failure_value(self):
        assert AuditEventType.SIGNATURE_FAILURE.value == "SIGNATURE_FAILURE"

    def test_clarification_reply_received_value(self):
        assert AuditEventType.CLARIFICATION_REPLY_RECEIVED.value == "CLARIFICATION_REPLY_RECEIVED"

    def test_private_note_added_value(self):
        assert AuditEventType.PRIVATE_NOTE_ADDED.value == "PRIVATE_NOTE_ADDED"

    def test_public_reply_sent_value(self):
        assert AuditEventType.PUBLIC_REPLY_SENT.value == "PUBLIC_REPLY_SENT"

    def test_freshdesk_api_error_value(self):
        assert AuditEventType.FRESHDESK_API_ERROR.value == "FRESHDESK_API_ERROR"


# ── Section 2: log_webhook_received ──────────────────────────────────────────

class TestLogWebhookReceived:
    def test_writes_correct_event_type(self):
        audit = AuditLogger(supabase_client=None)
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_received("123", "ticket_created", client_id="unity")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.WEBHOOK_RECEIVED

    def test_ticket_id_in_entry(self):
        audit = AuditLogger(supabase_client=None)
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_received("ticket-456", "ticket_created")
            entry = mock_write.call_args[0][0]
            assert entry.ticket_id == "ticket-456"

    def test_event_type_in_detail(self):
        audit = AuditLogger(supabase_client=None)
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_received("123", "ticket_created")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["event_type"] == "ticket_created"

    def test_outcome_received(self):
        audit = AuditLogger(supabase_client=None)
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_received("123", "ticket_created")
            entry = mock_write.call_args[0][0]
            assert entry.outcome == "RECEIVED"

    def test_idempotency_key_optional(self):
        audit = AuditLogger(supabase_client=None)
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_received("123", "ticket_created", idempotency_key="k1")
            entry = mock_write.call_args[0][0]
            assert entry.idempotency_key == "k1"


# ── Section 3: log_webhook_rejected ──────────────────────────────────────────

class TestLogWebhookRejected:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_rejected("123", "ticket_created", "HMAC_MISMATCH")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.WEBHOOK_REJECTED

    def test_reason_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_rejected("123", "ticket_created", "REPLAY_ATTACK")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["reason"] == "REPLAY_ATTACK"

    def test_outcome_rejected(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_rejected("123", "ticket_created", "reason")
            entry = mock_write.call_args[0][0]
            assert entry.outcome == "REJECTED"

    def test_error_code_set(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_rejected("123", "ticket_created", "SIZE_LIMIT")
            entry = mock_write.call_args[0][0]
            assert entry.error_code == "SIZE_LIMIT"


# ── Section 4: log_webhook_duplicate ─────────────────────────────────────────

class TestLogWebhookDuplicate:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_duplicate("123", "ticket_created", "123:ticket_created:ts")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.WEBHOOK_DUPLICATE

    def test_outcome_skipped(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_duplicate("123", "ticket_created", "k1")
            entry = mock_write.call_args[0][0]
            assert entry.outcome == "SKIPPED"

    def test_idempotency_key_set(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_webhook_duplicate("123", "ticket_created", "key-123")
            entry = mock_write.call_args[0][0]
            assert entry.idempotency_key == "key-123"


# ── Section 5: log_ticket_ingested ────────────────────────────────────────────

class TestLogTicketIngested:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_ticket_ingested("123", "unity_bank", case_id="c1", cf_clients="Unity")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.TICKET_INGESTED

    def test_case_id_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_ticket_ingested("123", "unity_bank", case_id="case-xyz")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["case_id"] == "case-xyz"

    def test_cf_clients_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_ticket_ingested("123", "unity_bank", cf_clients="Unity")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["cf_clients"] == "Unity"

    def test_outcome_success(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_ticket_ingested("123", "unity_bank")
            entry = mock_write.call_args[0][0]
            assert entry.outcome == "SUCCESS"


# ── Section 6: log_customer_reply_received ────────────────────────────────────

class TestLogCustomerReplyReceived:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_customer_reply_received("123", "unity")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.CUSTOMER_REPLY_RECEIVED

    def test_clarification_resolved_flag(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_customer_reply_received("123", "unity", clarification_resolved=True)
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["clarification_resolved"] is True


# ── Section 7: log_private_note_added ────────────────────────────────────────

class TestLogPrivateNoteAdded:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_private_note_added("123", "unity", "note-456")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.PRIVATE_NOTE_ADDED

    def test_note_id_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_private_note_added("123", "unity", "note-789")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["note_id"] == "note-789"


# ── Section 8: log_public_reply_sent ─────────────────────────────────────────

class TestLogPublicReplySent:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_public_reply_sent("123", "unity", "reply-1")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.PUBLIC_REPLY_SENT

    def test_note_id_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_public_reply_sent("123", "unity", "reply-999")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["note_id"] == "reply-999"


# ── Section 9: log_signature_failure ─────────────────────────────────────────

class TestLogSignatureFailure:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_signature_failure("123", "ticket_created", "HMAC_MISMATCH")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.SIGNATURE_FAILURE

    def test_reason_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_signature_failure("123", "ticket_updated", "REPLAY_ATTACK")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["reason"] == "REPLAY_ATTACK"

    def test_error_code_signature_failure(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_signature_failure("123", "ticket_created", "bad")
            entry = mock_write.call_args[0][0]
            assert entry.error_code == "SIGNATURE_FAILURE"


# ── Section 10: log_freshdesk_api_error ──────────────────────────────────────

class TestLogFreshdeskApiError:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_freshdesk_api_error("123", "add_private_note", "HTTP 500")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.FRESHDESK_API_ERROR

    def test_operation_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_freshdesk_api_error("123", "update_ticket", "HTTP 422")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["operation"] == "update_ticket"

    def test_error_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_freshdesk_api_error("123", "get_ticket", "connection timeout")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["error"] == "connection timeout"

    def test_outcome_failure(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_freshdesk_api_error("123", "op", "err")
            entry = mock_write.call_args[0][0]
            assert entry.outcome == "FAILURE"


# ── Section 11: log_conversation_state_updated ───────────────────────────────

class TestLogConversationStateUpdated:
    def test_event_type(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_conversation_state_updated("123", "unity", "CLARIFICATION")
            entry = mock_write.call_args[0][0]
            assert entry.action_type == AuditEventType.CONVERSATION_STATE_UPDATED

    def test_new_state_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_conversation_state_updated("123", "unity", "RESOLVED")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["new_state"] == "RESOLVED"

    def test_reason_in_detail(self):
        audit = AuditLogger()
        with patch.object(audit, "_write") as mock_write:
            audit.log_conversation_state_updated("123", "unity", "ESCALATED", reason="SLA breached")
            entry = mock_write.call_args[0][0]
            assert entry.action_detail["reason"] == "SLA breached"


# ── Section 12: Log-only mode (supabase=None) ─────────────────────────────────

class TestLogOnlyMode:
    def test_log_webhook_received_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_webhook_received("123", "ticket_created")

    def test_log_webhook_rejected_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_webhook_rejected("123", "ticket_created", "HMAC_FAIL")

    def test_log_ticket_ingested_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_ticket_ingested("123", "unity")

    def test_log_signature_failure_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_signature_failure("123", "ticket_created", "mismatch")

    def test_log_customer_reply_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_customer_reply_received("123", "unity")

    def test_log_private_note_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_private_note_added("123", "unity", "note-1")

    def test_log_public_reply_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_public_reply_sent("123", "unity", "reply-1")

    def test_log_api_error_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_freshdesk_api_error("123", "op", "error")

    def test_log_conversation_state_no_error(self):
        audit = AuditLogger(supabase_client=None)
        audit.log_conversation_state_updated("123", "unity", "RESOLVED")
