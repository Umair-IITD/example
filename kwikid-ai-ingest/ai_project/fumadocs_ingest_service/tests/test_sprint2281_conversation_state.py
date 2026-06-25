"""
tests/test_sprint2281_conversation_state.py

Sprint 2.28.1: ConversationStateStore and ConversationState tests.

Coverage:
  1. ConversationLifecycle enum
  2. ConversationState dataclass — defaults, fields
  3. ConversationState.to_db_row / from_db_row round-trip
  4. ConversationStateStore.get / get_or_create
  5. ConversationStateStore.update — field updates, partial updates
  6. ConversationStateStore.set_resolved / set_escalated
  7. ConversationStateStore.increment_clarification
  8. ConversationStateStore.exists / count / all_ticket_ids
  9. Supabase persistence path
  10. Unknown ticket_id in update → returns None, logs warning
"""
from __future__ import annotations

import os
import time
from unittest.mock import MagicMock

import pytest

os.environ.setdefault("RAG_API_KEY", "test-conv-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-key")
os.environ.setdefault("AUDIT_BACKEND", "inmemory")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENABLED", "false")
os.environ.setdefault("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
os.environ.setdefault("CORS_ALLOWED_ORIGINS", "http://localhost:3000")

from freshdesk.conversation_state import (
    ConversationLifecycle,
    ConversationState,
    ConversationStateStore,
)


# ── Section 1: ConversationLifecycle enum ────────────────────────────────────

class TestConversationLifecycle:
    def test_all_states_defined(self):
        states = [s.value for s in ConversationLifecycle]
        assert "OPEN" in states
        assert "PENDING" in states
        assert "CLARIFICATION" in states
        assert "RESOLVED" in states
        assert "CLOSED" in states
        assert "ESCALATED" in states

    def test_is_str_enum(self):
        assert ConversationLifecycle.OPEN == "OPEN"
        assert ConversationLifecycle.RESOLVED == "RESOLVED"


# ── Section 2: ConversationState dataclass ────────────────────────────────────

class TestConversationState:
    def test_default_lifecycle_open(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert state.lifecycle_state == ConversationLifecycle.OPEN

    def test_default_flags_false(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert state.clarification_pending is False
        assert state.awaiting_customer is False
        assert state.awaiting_human_approval is False

    def test_default_clarification_count_zero(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert state.clarification_count == 0

    def test_default_case_id_none(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert state.case_id is None

    def test_metadata_default_empty(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert state.metadata == {}

    def test_created_at_is_float(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        assert isinstance(state.created_at, float)
        assert state.created_at > 0


# ── Section 3: to_db_row / from_db_row round-trip ────────────────────────────

class TestDbRowRoundTrip:
    def test_round_trip_basic(self):
        state = ConversationState(ticket_id="t123", client_id="unity_bank")
        row = state.to_db_row()
        restored = ConversationState.from_db_row(row)
        assert restored.ticket_id == "t123"
        assert restored.client_id == "unity_bank"
        assert restored.lifecycle_state == ConversationLifecycle.OPEN

    def test_round_trip_with_case_id(self):
        state = ConversationState(ticket_id="t1", client_id="unity", case_id="case-abc")
        row = state.to_db_row()
        restored = ConversationState.from_db_row(row)
        assert restored.case_id == "case-abc"

    def test_round_trip_with_clarification(self):
        state = ConversationState(
            ticket_id="t1",
            client_id="unity",
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            clarification_pending=True,
            awaiting_customer=True,
            clarification_count=2,
        )
        row = state.to_db_row()
        restored = ConversationState.from_db_row(row)
        assert restored.lifecycle_state == ConversationLifecycle.CLARIFICATION
        assert restored.clarification_pending is True
        assert restored.awaiting_customer is True
        assert restored.clarification_count == 2

    def test_row_contains_all_required_keys(self):
        state = ConversationState(ticket_id="t1", client_id="unity")
        row = state.to_db_row()
        required_keys = [
            "ticket_id", "client_id", "lifecycle_state", "clarification_pending",
            "awaiting_customer", "awaiting_human_approval", "clarification_count",
            "case_id", "created_at", "updated_at", "resolved_at", "metadata",
        ]
        for key in required_keys:
            assert key in row, f"Missing key: {key}"

    def test_row_lifecycle_state_is_string(self):
        state = ConversationState(ticket_id="t1", client_id="u")
        row = state.to_db_row()
        assert isinstance(row["lifecycle_state"], str)
        assert row["lifecycle_state"] == "OPEN"

    def test_from_db_row_missing_optional_fields(self):
        minimal = {"ticket_id": "t1", "client_id": "unity"}
        state = ConversationState.from_db_row(minimal)
        assert state.ticket_id == "t1"
        assert state.lifecycle_state == ConversationLifecycle.OPEN


# ── Section 4: get / get_or_create ────────────────────────────────────────────

class TestGetOrCreate:
    def test_get_unknown_returns_none(self):
        store = ConversationStateStore()
        assert store.get("unknown-ticket") is None

    def test_get_or_create_creates_new(self):
        store = ConversationStateStore()
        state = store.get_or_create("t1", "unity")
        assert state.ticket_id == "t1"
        assert state.client_id == "unity"
        assert state.lifecycle_state == ConversationLifecycle.OPEN

    def test_get_or_create_returns_existing(self):
        store = ConversationStateStore()
        s1 = store.get_or_create("t1", "unity")
        s1.clarification_count = 5
        s2 = store.get_or_create("t1", "unity")
        assert s2.clarification_count == 5

    def test_get_returns_existing(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        state = store.get("t1")
        assert state is not None
        assert state.ticket_id == "t1"


# ── Section 5: update ────────────────────────────────────────────────────────

class TestUpdate:
    def test_update_lifecycle_state(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.update("t1", lifecycle_state=ConversationLifecycle.PENDING)
        assert result.lifecycle_state == ConversationLifecycle.PENDING

    def test_update_lifecycle_state_from_string(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.update("t1", lifecycle_state="RESOLVED")
        assert result.lifecycle_state == ConversationLifecycle.RESOLVED

    def test_update_clarification_pending(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.update("t1", clarification_pending=True)
        assert result.clarification_pending is True

    def test_update_case_id(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.update("t1", case_id="case-xyz")
        assert result.case_id == "case-xyz"

    def test_update_updates_updated_at(self):
        store = ConversationStateStore()
        state = store.get_or_create("t1", "unity")
        original_ts = state.updated_at
        time.sleep(0.01)
        store.update("t1", clarification_count=1)
        assert store.get("t1").updated_at >= original_ts

    def test_update_unknown_ticket_returns_none(self):
        store = ConversationStateStore()
        result = store.update("nonexistent", lifecycle_state="OPEN")
        assert result is None

    def test_update_multiple_fields_at_once(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.update(
            "t1",
            lifecycle_state=ConversationLifecycle.CLARIFICATION,
            clarification_pending=True,
            awaiting_customer=True,
        )
        assert result.lifecycle_state == ConversationLifecycle.CLARIFICATION
        assert result.clarification_pending is True
        assert result.awaiting_customer is True


# ── Section 6: set_resolved / set_escalated ───────────────────────────────────

class TestResolvedEscalated:
    def test_set_resolved(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.set_resolved("t1")
        assert result.lifecycle_state == ConversationLifecycle.RESOLVED
        assert result.clarification_pending is False
        assert result.awaiting_customer is False
        assert result.resolved_at is not None

    def test_set_escalated(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.set_escalated("t1")
        assert result.lifecycle_state == ConversationLifecycle.ESCALATED
        assert result.clarification_pending is False

    def test_set_resolved_unknown_returns_none(self):
        store = ConversationStateStore()
        assert store.set_resolved("nonexistent") is None


# ── Section 7: increment_clarification ────────────────────────────────────────

class TestIncrementClarification:
    def test_increment_from_zero(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        result = store.increment_clarification("t1")
        assert result.clarification_count == 1
        assert result.clarification_pending is True
        assert result.awaiting_customer is True
        assert result.lifecycle_state == ConversationLifecycle.CLARIFICATION

    def test_increment_twice(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        store.increment_clarification("t1")
        result = store.increment_clarification("t1")
        assert result.clarification_count == 2

    def test_increment_unknown_returns_none(self):
        store = ConversationStateStore()
        result = store.increment_clarification("nonexistent")
        assert result is None


# ── Section 8: Introspection ──────────────────────────────────────────────────

class TestIntrospection:
    def test_count_empty(self):
        store = ConversationStateStore()
        assert store.count() == 0

    def test_count_with_entries(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        store.get_or_create("t2", "unity")
        assert store.count() == 2

    def test_exists_true(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        assert store.exists("t1") is True

    def test_exists_false(self):
        store = ConversationStateStore()
        assert store.exists("nonexistent") is False

    def test_all_ticket_ids(self):
        store = ConversationStateStore()
        store.get_or_create("t1", "unity")
        store.get_or_create("t2", "unity")
        ids = store.all_ticket_ids()
        assert "t1" in ids
        assert "t2" in ids


# ── Section 9: Supabase persistence ──────────────────────────────────────────

class TestSupabasePersistence:
    def _make_sb(self, existing_row: dict | None = None):
        sb = MagicMock()
        if existing_row:
            sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = [existing_row]
        else:
            sb.table.return_value.select.return_value.eq.return_value.limit.return_value.execute.return_value.data = []
        sb.table.return_value.upsert.return_value.execute.return_value = MagicMock()
        return sb

    def test_get_or_create_calls_db_upsert(self):
        sb = self._make_sb()
        store = ConversationStateStore(supabase_client=sb)
        store.get_or_create("t1", "unity")
        sb.table.return_value.upsert.assert_called()

    def test_db_get_loads_from_supabase(self):
        existing = {
            "ticket_id": "t1",
            "client_id": "unity",
            "lifecycle_state": "CLARIFICATION",
            "clarification_pending": True,
            "awaiting_customer": True,
            "awaiting_human_approval": False,
            "clarification_count": 2,
            "case_id": "case-abc",
            "created_at": "2026-06-19T10:00:00+00:00",
            "updated_at": "2026-06-19T11:00:00+00:00",
            "resolved_at": None,
            "metadata": {},
        }
        sb = self._make_sb(existing_row=existing)
        store = ConversationStateStore(supabase_client=sb)
        state = store.get("t1")
        assert state is not None
        assert state.lifecycle_state == ConversationLifecycle.CLARIFICATION
        assert state.clarification_count == 2
        assert state.case_id == "case-abc"

    def test_db_upsert_exception_does_not_raise(self):
        sb = MagicMock()
        sb.table.side_effect = RuntimeError("db error")
        store = ConversationStateStore(supabase_client=sb)
        # Should not raise
        store._db_upsert(ConversationState(ticket_id="t1", client_id="unity"))
