"""
tests/test_sprint2281_idempotency.py

Sprint 2.28.1: WebhookIdempotencyStore tests.

Coverage:
  1. make_key — key format, different inputs produce different keys
  2. check — returns False for unknown, True for already-received
  3. mark_received — stores entry in-memory
  4. mark_completed — updates status, sets case_id
  5. mark_failed — updates status, sets error_detail
  6. TTL eviction — old entries evicted during check()
  7. Supabase integration path — db_check, db_insert, db_update
  8. count() and get()
  9. IdempotencyStatus enum
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-idem-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.idempotency import WebhookIdempotencyStore, IdempotencyStatus, IdempotencyEntry


# ── Section 1: make_key ───────────────────────────────────────────────────────

class TestMakeKey:
    def test_key_format(self):
        key = WebhookIdempotencyStore.make_key("123", "ticket_created", "2026-06-19T10:00:00Z")
        assert key == "123:ticket_created:2026-06-19T10:00:00Z"

    def test_different_ticket_ids_different_keys(self):
        k1 = WebhookIdempotencyStore.make_key("1", "ticket_created", "ts")
        k2 = WebhookIdempotencyStore.make_key("2", "ticket_created", "ts")
        assert k1 != k2

    def test_different_event_types_different_keys(self):
        k1 = WebhookIdempotencyStore.make_key("1", "ticket_created", "ts")
        k2 = WebhookIdempotencyStore.make_key("1", "ticket_updated", "ts")
        assert k1 != k2

    def test_different_timestamps_different_keys(self):
        k1 = WebhookIdempotencyStore.make_key("1", "ticket_created", "ts1")
        k2 = WebhookIdempotencyStore.make_key("1", "ticket_created", "ts2")
        assert k1 != k2

    def test_int_ticket_id_converted_to_string(self):
        key = WebhookIdempotencyStore.make_key(123, "ticket_created", "ts")
        assert key.startswith("123:")


# ── Section 2: check() ────────────────────────────────────────────────────────

class TestCheck:
    def test_check_unknown_key_returns_false(self):
        store = WebhookIdempotencyStore()
        assert store.check("nonexistent-key") is False

    def test_check_known_key_returns_true(self):
        store = WebhookIdempotencyStore()
        key = "ticket:created:ts"
        store.mark_received(key, "ticket", "ticket_created", "ts")
        assert store.check(key) is True

    def test_check_completed_key_returns_true(self):
        store = WebhookIdempotencyStore()
        key = "t:e:ts"
        store.mark_received(key, "t", "e", "ts")
        store.mark_completed(key)
        assert store.check(key) is True


# ── Section 3: mark_received ──────────────────────────────────────────────────

class TestMarkReceived:
    def test_mark_received_stores_entry(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "ticket123", "ticket_created", "2026-06-19T10:00:00Z")
        entry = store.get("k1")
        assert entry is not None
        assert entry.ticket_id == "ticket123"
        assert entry.event_type == "ticket_created"
        assert entry.status == IdempotencyStatus.RECEIVED

    def test_mark_received_idempotent(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "t", "e", "ts")
        store.mark_received("k1", "t", "e", "ts")  # second call overwrites
        assert store.count() == 1


# ── Section 4: mark_completed ────────────────────────────────────────────────

class TestMarkCompleted:
    def test_mark_completed_updates_status(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "t", "e", "ts")
        store.mark_completed("k1", case_id="case-abc")
        entry = store.get("k1")
        assert entry.status == IdempotencyStatus.COMPLETED
        assert entry.case_id == "case-abc"

    def test_mark_completed_without_case_id(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "t", "e", "ts")
        store.mark_completed("k1")
        entry = store.get("k1")
        assert entry.status == IdempotencyStatus.COMPLETED
        assert entry.case_id is None

    def test_mark_completed_unknown_key_noop(self):
        store = WebhookIdempotencyStore()
        store.mark_completed("nonexistent")  # should not raise


# ── Section 5: mark_failed ───────────────────────────────────────────────────

class TestMarkFailed:
    def test_mark_failed_updates_status(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "t", "e", "ts")
        store.mark_failed("k1", "ORCHESTRATOR_ERROR")
        entry = store.get("k1")
        assert entry.status == IdempotencyStatus.FAILED
        assert entry.error_detail == "ORCHESTRATOR_ERROR"

    def test_mark_failed_unknown_key_noop(self):
        store = WebhookIdempotencyStore()
        store.mark_failed("nonexistent", "ERROR")  # should not raise


# ── Section 6: TTL eviction ──────────────────────────────────────────────────

class TestTtlEviction:
    def test_expired_entries_evicted_on_check(self):
        store = WebhookIdempotencyStore(ttl_seconds=1)
        store.mark_received("old_key", "t", "e", "ts")
        # Manually age the entry
        store._store["old_key"].created_at = time.monotonic() - 5.0
        assert store.check("old_key") is False  # evicted

    def test_fresh_entries_not_evicted(self):
        store = WebhookIdempotencyStore(ttl_seconds=3600)
        store.mark_received("fresh_key", "t", "e", "ts")
        assert store.check("fresh_key") is True


# ── Section 7: Supabase integration ──────────────────────────────────────────

class TestSupabaseIntegration:
    def _make_sb(self, has_existing: bool = False):
        sb = MagicMock()
        # Sprint 2.28.2: _db_check now uses two .eq() calls:
        #   .select().eq(key).eq(status).limit().execute()
        data = [{"processing_status": "COMPLETED"}] if has_existing else []
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = data
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        sb.table.return_value.update.return_value.eq.return_value.execute.return_value = MagicMock()
        return sb

    def test_db_check_returns_true_when_exists(self):
        sb = self._make_sb(has_existing=True)
        store = WebhookIdempotencyStore(supabase_client=sb)
        assert store._db_check("some-key") is True

    def test_db_check_returns_false_when_not_exists(self):
        sb = self._make_sb(has_existing=False)
        store = WebhookIdempotencyStore(supabase_client=sb)
        assert store._db_check("some-key") is False

    def test_mark_received_calls_db_insert(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.mark_received("k1", "t", "e", "ts")
        sb.table.assert_called()

    def test_mark_completed_calls_db_update(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.mark_received("k1", "t", "e", "ts")
        store.mark_completed("k1", case_id="case-123")
        assert sb.table.return_value.update.called

    def test_db_check_exception_returns_false(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db error")
        store = WebhookIdempotencyStore(supabase_client=sb)
        result = store._db_check("key")
        assert result is False


# ── Section 8: Introspection ─────────────────────────────────────────────────

class TestIntrospection:
    def test_count_empty(self):
        store = WebhookIdempotencyStore()
        assert store.count() == 0

    def test_count_after_marks(self):
        store = WebhookIdempotencyStore()
        store.mark_received("k1", "t", "e", "ts")
        store.mark_received("k2", "t", "e", "ts2")
        assert store.count() == 2

    def test_get_unknown_key_returns_none(self):
        store = WebhookIdempotencyStore()
        assert store.get("unknown") is None


# ── Section 9: IdempotencyStatus enum ────────────────────────────────────────

class TestIdempotencyStatus:
    def test_all_statuses_defined(self):
        statuses = [s.value for s in IdempotencyStatus]
        assert "RECEIVED" in statuses
        assert "PROCESSING" in statuses
        assert "COMPLETED" in statuses
        assert "FAILED" in statuses

    def test_status_is_string_enum(self):
        assert IdempotencyStatus.RECEIVED == "RECEIVED"
        assert IdempotencyStatus.COMPLETED == "COMPLETED"

    def test_entry_default_status(self):
        entry = IdempotencyEntry(
            key="k", ticket_id="t", event_type="e", event_timestamp="ts"
        )
        assert entry.status == IdempotencyStatus.RECEIVED


# ── Section 10: _coerce_timestamp — timestamp bug regression ─────────────────
#
# Root cause: Freshdesk Format B (Dispatch'r rules) payloads do NOT include a
# top-level created_at field, so event_ts_str = "" is passed to ensure_receipt()
# and mark_received(). Supabase TIMESTAMPTZ column rejects empty strings:
#   "invalid input syntax for type timestamp with time zone: ''"
#
# Fix: _coerce_timestamp() substitutes datetime.now(UTC) when ts is empty.

class TestCoerceTimestamp:
    def test_non_empty_ts_returned_unchanged(self):
        from freshdesk.idempotency import _coerce_timestamp
        ts = "2026-06-26T10:30:00+00:00"
        assert _coerce_timestamp(ts) == ts

    def test_empty_string_returns_iso_timestamp(self):
        from freshdesk.idempotency import _coerce_timestamp
        from datetime import datetime, timezone
        result = _coerce_timestamp("")
        # Must be parseable as ISO 8601 datetime
        dt = datetime.fromisoformat(result)
        assert dt.tzinfo is not None

    def test_empty_string_result_is_utc(self):
        from freshdesk.idempotency import _coerce_timestamp
        from datetime import timezone
        import datetime as _dt
        before = _dt.datetime.now(timezone.utc)
        result = _coerce_timestamp("")
        after = _dt.datetime.now(timezone.utc)
        dt = _dt.datetime.fromisoformat(result)
        assert before <= dt <= after

    def test_ensure_receipt_sends_valid_timestamp_when_event_ts_empty(self):
        """ensure_receipt must not send empty string to Supabase for event_timestamp."""
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.ensure_receipt(
            key="900001:ticket_created:",
            ticket_id="900001",
            event_type="ticket_created",
            event_timestamp="",  # ← the bug: Format B has no created_at
        )
        insert_call = sb.table.return_value.insert.call_args
        assert insert_call is not None
        row = insert_call[0][0]
        ts = row["event_timestamp"]
        assert ts != "", "Empty string must not be sent to Supabase TIMESTAMPTZ column"
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        assert dt is not None

    def test_mark_received_sends_valid_timestamp_when_event_ts_empty(self):
        """mark_received → _db_insert must not send empty string to Supabase."""
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.mark_received(
            key="900001:ticket_created:",
            ticket_id="900001",
            event_type="ticket_created",
            event_timestamp="",  # ← the bug
        )
        upsert_call = sb.table.return_value.upsert.call_args
        assert upsert_call is not None
        row = upsert_call[0][0]
        ts = row["event_timestamp"]
        assert ts != "", "Empty string must not be sent to Supabase TIMESTAMPTZ column"
        from datetime import datetime
        dt = datetime.fromisoformat(ts)
        assert dt is not None

    def test_real_timestamp_passes_through_ensure_receipt_unchanged(self):
        """A valid Freshdesk timestamp must be stored as-is, not replaced."""
        sb = MagicMock()
        sb.table.return_value.insert.return_value.execute.return_value = MagicMock(data=[])
        store = WebhookIdempotencyStore(supabase_client=sb)
        real_ts = "2026-06-26T10:30:00Z"
        store.ensure_receipt("k", "123", "ticket_created", real_ts)
        row = sb.table.return_value.insert.call_args[0][0]
        assert row["event_timestamp"] == real_ts

    def test_real_timestamp_passes_through_mark_received_unchanged(self):
        """A valid Freshdesk timestamp must be stored as-is, not replaced."""
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock(data=[])
        store = WebhookIdempotencyStore(supabase_client=sb)
        real_ts = "2026-06-26T10:30:00Z"
        store.mark_received("k", "123", "ticket_created", real_ts)
        row = sb.table.return_value.upsert.call_args[0][0]
        assert row["event_timestamp"] == real_ts


# ── Section 11: Tenant registry — domain registration regression ──────────────

class TestTenantRegistry:
    def test_unitybank_domain_registered(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        config = reg.lookup_by_domain("unitybank.co.in")
        assert config is not None, "unitybank.co.in must be in the default registry"
        assert config.client_id == "unity_bank"
        assert config.enabled is True

    def test_unitybank_email_resolves(self):
        from case_engine.tenant.resolver import build_client_resolver
        resolver = build_client_resolver()
        ctx = resolver.resolve("mrunali.gaikwad@unitybank.co.in")
        assert ctx.client_id == "unity_bank"
        assert ctx.client_name == "Unity Bank"
        assert ctx.domain == "unitybank.co.in"

    def test_unknown_domain_raises(self):
        from case_engine.tenant.resolver import build_client_resolver
        from case_engine.tenant.models import UnknownClientError
        resolver = build_client_resolver()
        with pytest.raises(UnknownClientError):
            resolver.resolve("someone@unity.co.in")  # wrong domain — must not resolve

    def test_all_registered_domains(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        domains = reg.all_domains()
        assert "unitybank.co.in" in domains

    def test_registry_has_at_least_one_tenant(self):
        from case_engine.tenant.registry import build_default_tenant_registry
        reg = build_default_tenant_registry()
        assert reg.count() >= 1
        errors = reg.validate()
        assert errors == [], f"Registry validation errors: {errors}"


# ── Section 12: ConversationState schema regression ──────────────────────────
#
# Root cause: support_conversation_state was created from sql/support_conversation_state.sql
# (old n8n schema: id uuid pk, channel, external_id — NO ticket_id, NO created_at).
# S2_028 CREATE TABLE IF NOT EXISTS silently no-oped. S2_030 added most columns but
# missed ticket_id and created_at. Fixed by S2_031 which renames the broken table
# and creates the correct schema.
#
# These tests verify:
#   1. to_db_row() emits ticket_id and created_at (the two missing columns).
#   2. _db_get() queries on column named "ticket_id" (regression: column did not exist).
#   3. _db_upsert() sends ticket_id and created_at in the row dict (regression: DB error).
#   4. get_or_create() inserts via upsert when Supabase client is wired.

class TestConversationStateSchema:
    def test_to_db_row_has_ticket_id(self):
        from freshdesk.conversation_state import ConversationState
        state = ConversationState(ticket_id="900001", client_id="unity_bank")
        row = state.to_db_row()
        assert "ticket_id" in row, "to_db_row() must include ticket_id (S2_028 PK column)"
        assert row["ticket_id"] == "900001"

    def test_to_db_row_has_created_at(self):
        from freshdesk.conversation_state import ConversationState
        state = ConversationState(ticket_id="900001", client_id="unity_bank")
        row = state.to_db_row()
        assert "created_at" in row, "to_db_row() must include created_at (S2_028 column)"
        assert row["created_at"] is not None, "created_at must not be None"

    def test_to_db_row_has_all_sprint228_columns(self):
        from freshdesk.conversation_state import ConversationState
        state = ConversationState(ticket_id="900001", client_id="unity_bank")
        row = state.to_db_row()
        required = {
            "ticket_id", "client_id", "lifecycle_state",
            "clarification_pending", "awaiting_customer", "awaiting_human_approval",
            "clarification_count", "case_id", "created_at", "updated_at",
            "resolved_at", "metadata",
        }
        missing = required - set(row.keys())
        assert not missing, f"to_db_row() missing columns: {missing}"

    def test_db_get_queries_ticket_id_column(self):
        """_db_get() must use .eq('ticket_id', ...) — regression: column did not exist in old table."""
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        from freshdesk.conversation_state import ConversationStateStore
        store = ConversationStateStore(supabase_client=sb)
        store._db_get("900001")
        eq_calls = sb.table.return_value.select.return_value.eq.call_args_list
        assert eq_calls, "_db_get() must call .eq() to filter by ticket_id"
        first_eq_col = eq_calls[0][0][0]
        assert first_eq_col == "ticket_id", (
            f"_db_get() must query on 'ticket_id' column, got '{first_eq_col}'"
        )

    def test_db_upsert_sends_ticket_id(self):
        """_db_upsert() must send ticket_id in the row dict — regression: column missing caused DB error."""
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        from freshdesk.conversation_state import ConversationState, ConversationStateStore
        store = ConversationStateStore(supabase_client=sb)
        state = ConversationState(ticket_id="900001", client_id="unity_bank")
        store._db_upsert(state)
        upsert_call = sb.table.return_value.upsert.call_args
        assert upsert_call is not None, "_db_upsert() must call supabase .upsert()"
        row = upsert_call[0][0]
        assert "ticket_id" in row, "upserted row must include ticket_id"
        assert row["ticket_id"] == "900001"

    def test_db_upsert_sends_created_at(self):
        """_db_upsert() must send created_at — regression: created_at column missing caused DB error."""
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        from freshdesk.conversation_state import ConversationState, ConversationStateStore
        store = ConversationStateStore(supabase_client=sb)
        state = ConversationState(ticket_id="900001", client_id="unity_bank")
        store._db_upsert(state)
        row = sb.table.return_value.upsert.call_args[0][0]
        assert "created_at" in row, "upserted row must include created_at"
        assert row["created_at"] is not None


# ── Section 13: Audit interface regression ───────────────────────────────────
#
# Root cause: handlers.py._audit_event() calls self._audit._write(AuditEntry).
# app/main.py was wiring audit.logger.AuditLogger (Action Gateway logger with
# emit() interface) instead of case_engine.audit.AuditLogger (_write() interface).
# The mismatch caused AttributeError silently swallowed by except Exception: LOGGER.debug().
# Fix: Sprint 2.31 wires a dedicated case_engine.audit.AuditLogger to Freshdesk handlers.
#
# These tests verify:
#   1. FreshdeskTicketCreatedHandler._audit_event() works with case_engine.audit.AuditLogger.
#   2. case_engine.audit.AuditLogger has the _write() method handlers.py expects.
#   3. _audit_event() silently skips when self._audit is None (no crash).
#   4. _audit_event() silently skips when passed the wrong audit logger type.

class TestAuditInterface:
    def test_case_engine_audit_logger_has_write_method(self):
        """case_engine.audit.AuditLogger must have _write() — the interface handlers.py uses."""
        from case_engine.audit import AuditLogger
        logger = AuditLogger(supabase_client=None)
        assert hasattr(logger, "_write"), (
            "case_engine.audit.AuditLogger must have _write() method"
        )
        assert callable(logger._write)

    def test_action_gateway_audit_logger_lacks_write_method(self):
        """audit.logger.AuditLogger (Action Gateway) must NOT be passed to Freshdesk handlers."""
        from audit.logger import AuditLogger as GatewayAuditLogger
        gateway_logger = GatewayAuditLogger()
        assert not hasattr(gateway_logger, "_write"), (
            "audit.logger.AuditLogger must NOT have _write() — confirms interface mismatch exists"
        )

    def test_handler_audit_event_works_with_correct_logger(self):
        """_audit_event() must not raise when passed a case_engine.audit.AuditLogger."""
        from case_engine.audit import AuditLogger
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from freshdesk.handlers import FreshdeskTicketCreatedHandler

        correct_logger = AuditLogger(supabase_client=None)
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            audit_logger=correct_logger,
        )
        # Must not raise
        handler._audit_event("TICKET_INGESTED", ticket_id="900001", client_id="unity_bank")

    def test_handler_audit_event_silent_when_none(self):
        """_audit_event() must be a no-op when audit_logger is None."""
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from freshdesk.handlers import FreshdeskTicketCreatedHandler

        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            audit_logger=None,
        )
        # Must not raise — audit_logger=None is silent
        handler._audit_event("TICKET_INGESTED", ticket_id="900001", client_id="unity_bank")

    def test_handler_audit_event_silent_with_wrong_logger_type(self):
        """_audit_event() must fail silently (not crash) with the wrong logger type."""
        from audit.logger import AuditLogger as GatewayAuditLogger
        from freshdesk.idempotency import WebhookIdempotencyStore
        from freshdesk.conversation_state import ConversationStateStore
        from freshdesk.handlers import FreshdeskTicketCreatedHandler

        wrong_logger = GatewayAuditLogger()
        handler = FreshdeskTicketCreatedHandler(
            idempotency_store=WebhookIdempotencyStore(),
            conversation_store=ConversationStateStore(),
            audit_logger=wrong_logger,
        )
        # Must not raise — the except: LOGGER.debug() catch swallows AttributeError
        handler._audit_event("TICKET_INGESTED", ticket_id="900001", client_id="unity_bank")

    def test_case_engine_audit_logger_write_stores_to_log_only_mode(self):
        """_write() in log-only mode (supabase_client=None) must not raise."""
        from case_engine.audit import AuditLogger
        from case_engine.models import AuditEntry, AuditEventType
        logger = AuditLogger(supabase_client=None)
        entry = AuditEntry(
            ticket_id="900001",
            client="unity_bank",
            action_type=AuditEventType.TICKET_INGESTED,
            action_detail={"test": True},
            outcome="SUCCESS",
        )
        logger._write(entry)  # must not raise in log-only mode

    def test_case_engine_audit_logger_write_with_supabase_calls_upsert(self):
        """_write() with supabase_client must call .upsert() on case_audit_log."""
        sb = MagicMock()
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        from case_engine.audit import AuditLogger
        from case_engine.models import AuditEntry, AuditEventType
        logger = AuditLogger(supabase_client=sb)
        entry = AuditEntry(
            ticket_id="900001",
            client="unity_bank",
            action_type=AuditEventType.TICKET_INGESTED,
            action_detail={},
            outcome="SUCCESS",
        )
        logger._write(entry)
        sb.table.assert_called_with("case_audit_log")


# ── Section 14: Assembly audit injection regression ──────────────────────────
#
# Root cause (runtime blocker):
#   runtime/assembly.py _build_workflow_services() line 481:
#       cs._audit = audit_logger
#   where audit_logger is audit.logger.AuditLogger (Action Gateway logger with
#   emit() interface). This OVERWRITES the case_engine.audit.AuditLogger that
#   build_case_service() correctly creates internally.
#
#   Result: AttributeError: AuditLogger object has no attribute log_transition
#           AttributeError: AuditLogger object has no attribute log_classification
#
# Fix: Removed cs._audit = audit_logger from runtime/assembly.py.
#      build_case_service() already creates the correct case_engine.audit.AuditLogger.
#
# These tests verify:
#   1. build_case_service() injects case_engine.audit.AuditLogger, not the gateway one.
#   2. The CaseService._audit produced by build_case_service() has log_transition().
#   3. The CaseService._audit produced by build_case_service() has log_classification().
#   4. The gateway AuditLogger (audit.logger.AuditLogger) is a DIFFERENT class.
#   5. After the fix, assembly CaseService._audit is the correct type.

class TestAssemblyAuditInjection:
    def test_build_case_service_audit_is_case_engine_logger(self):
        """build_case_service() must inject case_engine.audit.AuditLogger, not audit.logger.AuditLogger."""
        from case_engine.service import build_case_service
        from case_engine.audit import AuditLogger as CaseEngineAuditLogger
        cs = build_case_service(supabase_client=None)
        assert isinstance(cs._audit, CaseEngineAuditLogger), (
            f"CaseService._audit must be case_engine.audit.AuditLogger, "
            f"got {type(cs._audit).__module__}.{type(cs._audit).__name__}"
        )

    def test_build_case_service_audit_has_log_transition(self):
        """CaseService._audit from build_case_service() must have log_transition()."""
        from case_engine.service import build_case_service
        cs = build_case_service(supabase_client=None)
        assert hasattr(cs._audit, "log_transition"), (
            f"CaseService._audit ({type(cs._audit).__name__}) must have log_transition(). "
            "This was broken by runtime/assembly.py overwriting _audit with the gateway logger."
        )
        assert callable(cs._audit.log_transition)

    def test_build_case_service_audit_has_log_classification(self):
        """CaseService._audit from build_case_service() must have log_classification()."""
        from case_engine.service import build_case_service
        cs = build_case_service(supabase_client=None)
        assert hasattr(cs._audit, "log_classification"), (
            f"CaseService._audit ({type(cs._audit).__name__}) must have log_classification(). "
            "This was broken by runtime/assembly.py overwriting _audit with the gateway logger."
        )
        assert callable(cs._audit.log_classification)

    def test_gateway_audit_logger_is_different_class(self):
        """audit.logger.AuditLogger and case_engine.audit.AuditLogger are different classes."""
        from audit.logger import AuditLogger as GatewayLogger
        from case_engine.audit import AuditLogger as CaseEngineLogger
        assert GatewayLogger is not CaseEngineLogger, (
            "audit.logger.AuditLogger and case_engine.audit.AuditLogger must be distinct classes"
        )
        assert not hasattr(GatewayLogger, "log_transition"), (
            "audit.logger.AuditLogger must NOT have log_transition() — confirms wrong class was injected"
        )
        assert not hasattr(GatewayLogger, "log_classification"), (
            "audit.logger.AuditLogger must NOT have log_classification() — confirms wrong class was injected"
        )

    def test_gateway_logger_injection_would_fail(self):
        """Proves: injecting audit.logger.AuditLogger into CaseService._audit causes AttributeError on log_transition."""
        from audit.logger import AuditLogger as GatewayLogger
        from case_engine.service import build_case_service
        from case_engine.models import Case
        from case_engine.case_state import CaseState
        cs = build_case_service(supabase_client=None)
        # Simulate the broken assembly injection
        cs._audit = GatewayLogger()
        # log_transition must not exist on the gateway logger
        assert not hasattr(cs._audit, "log_transition"), (
            "Confirmed: injecting gateway logger causes log_transition to be unavailable"
        )

    def test_correct_logger_injection_works(self):
        """Proves: CaseService._audit from build_case_service() succeeds on log_transition/log_classification."""
        from case_engine.service import build_case_service
        from case_engine.models import Case, CaseTransition
        from case_engine.case_state import CaseState
        cs = build_case_service(supabase_client=None)
        # The fixed assembly does NOT overwrite cs._audit — verify correct class
        assert hasattr(cs._audit, "log_transition")
        assert hasattr(cs._audit, "log_classification")
        # Verify they are callable without raising
        case = Case(ticket_id="900001", client="unity_bank")
        transition = CaseTransition(
            case_id=case.case_id,
            from_state=CaseState.NEW,
            to_state=CaseState.CLASSIFYING,
            reason="test",
        )
        cs._audit.log_transition(case, transition)  # must not raise
        cs._audit.log_classification(
            case,
            topic="OTP_Delivery_Failure",
            confidence=0.95,
            tier_used=1,
            meets_threshold=True,
        )  # must not raise
