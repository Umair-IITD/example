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
