"""
tests/test_sprint2282_background_reliability.py

Sprint 2.28.2 Part 7: Background Task Reliability — WAL pre-persistence.

Before this sprint, mark_received() was called INSIDE the background task.
If the process crashed after returning 200 OK but before the background task ran,
the event was permanently lost — no record in Supabase, no way to detect it.

After Sprint 2.28.2:
  - ensure_receipt() is called SYNCHRONOUSLY before returning 200 OK (WAL step)
  - Uses INSERT (not upsert) — never overwrites COMPLETED records
  - _db_check() returns True only for COMPLETED status (not RECEIVED)
  - RECEIVED entries act as WAL tombstones that allow background retry

Coverage:
  1. ensure_receipt() writes RECEIVED to Supabase before 200 is returned
  2. ensure_receipt() uses INSERT (not upsert) — preserves COMPLETED records
  3. ensure_receipt() returns True on success, False on key collision
  4. ensure_receipt() returns False (gracefully) when Supabase not configured
  5. _db_check() returns True only for COMPLETED, not for RECEIVED
  6. _db_check() returns False for RECEIVED entries (allows retry)
  7. Route calls _pre_persist before returning 200 (ordering guarantee)
  8. Pre-persist errors don't block the 200 response
  9. WAL key matches idempotency key format
  10. Duplicate RECEIVED inserts → INSERT constraint → ensure_receipt returns False
"""
from __future__ import annotations

import json
import os
from unittest.mock import MagicMock, patch

import pytest

os.environ.setdefault("RAG_API_KEY", "test-2282-bg-reliability")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.idempotency import WebhookIdempotencyStore, IdempotencyStatus


# ── Section 1: ensure_receipt() core semantics ───────────────────────────────

class TestEnsureReceipt:
    def _make_sb(self, insert_raises: bool = False) -> MagicMock:
        sb = MagicMock()
        if insert_raises:
            sb.table.return_value.insert.return_value.execute.side_effect = Exception(
                "duplicate key value violates unique constraint"
            )
        else:
            sb.table.return_value.insert.return_value.execute.return_value = MagicMock()
        return sb

    def test_returns_true_on_success(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        result = store.ensure_receipt("k1", "55001", "ticket_created", "2026-06-19T10:00:00Z")
        assert result is True

    def test_returns_false_without_supabase(self):
        store = WebhookIdempotencyStore()
        result = store.ensure_receipt("k1", "55001", "ticket_created", "2026-06-19T10:00:00Z")
        assert result is False

    def test_returns_false_on_duplicate_insert(self):
        sb = self._make_sb(insert_raises=True)
        store = WebhookIdempotencyStore(supabase_client=sb)
        result = store.ensure_receipt("k1", "55001", "ticket_created", "2026-06-19T10:00:00Z")
        assert result is False

    def test_calls_insert_not_upsert(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.ensure_receipt("k1", "55001", "ticket_created", "ts")
        assert sb.table.return_value.insert.called
        assert not sb.table.return_value.upsert.called

    def test_insert_row_has_received_status(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.ensure_receipt("k1", "55001", "ticket_created", "ts")
        inserted_row = sb.table.return_value.insert.call_args[0][0]
        assert inserted_row["processing_status"] == IdempotencyStatus.RECEIVED.value

    def test_insert_row_contains_correct_key(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.ensure_receipt("mykey", "55001", "ticket_created", "ts")
        inserted_row = sb.table.return_value.insert.call_args[0][0]
        assert inserted_row["idempotency_key"] == "mykey"

    def test_insert_row_contains_ticket_id(self):
        sb = self._make_sb()
        store = WebhookIdempotencyStore(supabase_client=sb)
        store.ensure_receipt("k1", "55001", "ticket_created", "ts")
        inserted_row = sb.table.return_value.insert.call_args[0][0]
        assert inserted_row["ticket_id"] == "55001"

    def test_exception_does_not_propagate(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("connection error")
        store = WebhookIdempotencyStore(supabase_client=sb)
        result = store.ensure_receipt("k1", "55001", "ticket_created", "ts")
        assert result is False  # no exception raised


# ── Section 2: _db_check() COMPLETED-only semantics ──────────────────────────

class TestDbCheckCompletedOnly:
    def _make_sb_with_status(self, status: str) -> MagicMock:
        sb = MagicMock()
        if status == "COMPLETED":
            data = [{"processing_status": "COMPLETED"}]
        else:
            data = []  # RECEIVED is NOT returned by the COMPLETED filter
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = data
        return sb

    def test_db_check_returns_true_for_completed(self):
        sb = self._make_sb_with_status("COMPLETED")
        store = WebhookIdempotencyStore(supabase_client=sb)
        assert store._db_check("key") is True

    def test_db_check_returns_false_for_received(self):
        sb = self._make_sb_with_status("RECEIVED")
        store = WebhookIdempotencyStore(supabase_client=sb)
        assert store._db_check("key") is False

    def test_db_check_returns_false_for_not_found(self):
        sb = self._make_sb_with_status("not_found")
        store = WebhookIdempotencyStore(supabase_client=sb)
        assert store._db_check("key") is False

    def test_db_check_queries_completed_status(self):
        """Verify the second .eq() filter is for COMPLETED status."""
        sb = MagicMock()
        sb.table.return_value.select.return_value.eq.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        store = WebhookIdempotencyStore(supabase_client=sb)
        store._db_check("k1")
        # Second .eq() should filter on processing_status=COMPLETED
        second_eq_call = sb.table.return_value.select.return_value.eq.return_value.eq.call_args
        assert second_eq_call is not None
        args = second_eq_call[0]
        assert args[0] == "processing_status"
        assert args[1] == "COMPLETED"


# ── Section 3: WAL ordering guarantee (route-level) ──────────────────────────

class TestWalOrderingGuarantee:
    def _make_app(self, idem_store: WebhookIdempotencyStore):
        from fastapi import FastAPI
        from api.routes.webhooks.freshdesk import router
        app = FastAPI()
        app.include_router(router)
        app.state.freshdesk_idempotency_store = idem_store
        return app

    def test_ensure_receipt_called_before_200(self):
        """ensure_receipt is called synchronously before the 200 response is sent."""
        from fastapi.testclient import TestClient

        receipts: list[str] = []
        sb = MagicMock()

        def capture_insert(row):
            receipts.append(row["idempotency_key"])
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        sb.table.return_value.insert.side_effect = capture_insert
        store = WebhookIdempotencyStore(supabase_client=sb)

        app = self._make_app(store)
        client = TestClient(app)

        payload = {
            "freshdesk_webhook": {
                "id": 12001,
                "subject": "Test",
                "description": "d",
                "description_text": "d",
                "status": 2,
                "priority": 2,
                "ticket_type": "Issues",
                "created_at": "2026-06-19T10:00:00Z",
                "requester_email": "x@y.com",
                "requester_name": "User",
                "tags": "",
                "ticket_custom_fields": {},
                "attachments": [],
            }
        }
        resp = client.post("/webhooks/freshdesk/ticket-created", json=payload)
        assert resp.status_code == 200
        assert len(receipts) == 1
        expected_key = WebhookIdempotencyStore.make_key("12001", "ticket_created", "2026-06-19T10:00:00Z")
        assert receipts[0] == expected_key

    def test_pre_persist_error_does_not_block_200(self):
        """If ensure_receipt raises, the 200 is still returned."""
        from fastapi.testclient import TestClient

        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db unavailable")
        store = WebhookIdempotencyStore(supabase_client=sb)

        app = self._make_app(store)
        client = TestClient(app)

        payload = {
            "freshdesk_webhook": {
                "id": 12002,
                "subject": "Test",
                "description": "d",
                "description_text": "d",
                "status": 2,
                "priority": 2,
                "ticket_type": "Issues",
                "created_at": "2026-06-19T10:00:00Z",
                "requester_email": "x@y.com",
                "requester_name": "User",
                "tags": "",
                "ticket_custom_fields": {},
                "attachments": [],
            }
        }
        resp = client.post("/webhooks/freshdesk/ticket-created", json=payload)
        assert resp.status_code == 200


# ── Section 4: WAL key format ────────────────────────────────────────────────

class TestWalKeyFormat:
    def test_wal_key_matches_idempotency_key_format(self):
        """ensure_receipt uses the same key format as make_key."""
        sb = MagicMock()
        inserted_keys: list[str] = []

        def capture(row):
            inserted_keys.append(row["idempotency_key"])
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        sb.table.return_value.insert.side_effect = capture
        store = WebhookIdempotencyStore(supabase_client=sb)

        expected = WebhookIdempotencyStore.make_key("42", "ticket_created", "2026-06-19T12:00:00Z")
        store.ensure_receipt(expected, "42", "ticket_created", "2026-06-19T12:00:00Z")
        assert inserted_keys[0] == expected
        assert inserted_keys[0] == "42:ticket_created:2026-06-19T12:00:00Z"

    def test_wal_key_for_updated_event(self):
        sb = MagicMock()
        inserted_keys: list[str] = []

        def capture(row):
            inserted_keys.append(row["idempotency_key"])
            m = MagicMock()
            m.execute.return_value = MagicMock()
            return m

        sb.table.return_value.insert.side_effect = capture
        store = WebhookIdempotencyStore(supabase_client=sb)

        key = WebhookIdempotencyStore.make_key("99", "ticket_updated", "2026-06-19T15:00:00Z")
        store.ensure_receipt(key, "99", "ticket_updated", "2026-06-19T15:00:00Z")
        assert "ticket_updated" in inserted_keys[0]
