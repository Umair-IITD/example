"""
freshdesk/idempotency.py

Sprint 2.28.1: WebhookIdempotencyStore — prevent duplicate webhook processing.

Idempotency key format: "{ticket_id}:{event_type}:{event_timestamp}"

Design:
  - In-memory dict as primary store (fast path).
  - Supabase persistence via freshdesk_webhook_events table (durable path).
  - If supabase_client is None → in-memory only (dev/test mode).
  - check() returns True if the event was already processed.
  - mark_received() records the event immediately on arrival.
  - mark_completed() / mark_failed() update final status.
  - TTL: entries older than ttl_seconds are evicted from the in-memory store
    during mark() calls (lazy eviction; not time-critical).
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

LOGGER = logging.getLogger(__name__)

_DEFAULT_TTL = 3600  # 1 hour in-memory retention
_TABLE = "freshdesk_webhook_events"


class IdempotencyStatus(str, Enum):
    RECEIVED   = "RECEIVED"
    PROCESSING = "PROCESSING"
    COMPLETED  = "COMPLETED"
    FAILED     = "FAILED"


@dataclass
class IdempotencyEntry:
    key: str
    ticket_id: str
    event_type: str
    event_timestamp: str
    status: IdempotencyStatus = IdempotencyStatus.RECEIVED
    case_id: str | None = None
    error_detail: str | None = None
    created_at: float = field(default_factory=time.monotonic)


class WebhookIdempotencyStore:
    """
    Prevents duplicate processing of Freshdesk webhook events.

    Usage:
        store = WebhookIdempotencyStore(supabase_client)

        key = store.make_key(ticket_id, event_type, event_timestamp)
        if store.check(key):
            return  # already processed

        store.mark_received(key, ticket_id, event_type, event_timestamp)
        try:
            result = process(...)
            store.mark_completed(key, case_id=result.case_id)
        except Exception as exc:
            store.mark_failed(key, str(exc))
    """

    def __init__(
        self,
        supabase_client: Any = None,
        *,
        ttl_seconds: int = _DEFAULT_TTL,
    ) -> None:
        self._sb = supabase_client
        self._ttl = ttl_seconds
        self._store: dict[str, IdempotencyEntry] = {}

    # ── Key construction ───────────────────────────────────────────────────────

    @staticmethod
    def make_key(ticket_id: str | int, event_type: str, event_timestamp: str) -> str:
        return f"{ticket_id}:{event_type}:{event_timestamp}"

    # ── Check ─────────────────────────────────────────────────────────────────

    def check(self, key: str) -> bool:
        """Return True if this key has already been received/processed."""
        self._evict()
        if key in self._store:
            return True
        if self._sb is not None:
            return self._db_check(key)
        return False

    # ── Mark operations ────────────────────────────────────────────────────────

    def mark_received(
        self,
        key: str,
        ticket_id: str,
        event_type: str,
        event_timestamp: str,
    ) -> None:
        entry = IdempotencyEntry(
            key=key,
            ticket_id=ticket_id,
            event_type=event_type,
            event_timestamp=event_timestamp,
            status=IdempotencyStatus.RECEIVED,
        )
        self._store[key] = entry
        if self._sb is not None:
            self._db_insert(entry)

    def mark_completed(self, key: str, *, case_id: str | None = None) -> None:
        if key in self._store:
            self._store[key].status = IdempotencyStatus.COMPLETED
            self._store[key].case_id = case_id
        if self._sb is not None:
            self._db_update(key, IdempotencyStatus.COMPLETED, case_id=case_id)

    def mark_failed(self, key: str, error_detail: str) -> None:
        if key in self._store:
            self._store[key].status = IdempotencyStatus.FAILED
            self._store[key].error_detail = error_detail
        if self._sb is not None:
            self._db_update(key, IdempotencyStatus.FAILED, error_detail=error_detail)

    # ── Introspection ─────────────────────────────────────────────────────────

    def get(self, key: str) -> IdempotencyEntry | None:
        return self._store.get(key)

    def count(self) -> int:
        return len(self._store)

    # ── Internal ──────────────────────────────────────────────────────────────

    def _evict(self) -> None:
        cutoff = time.monotonic() - self._ttl
        expired = [k for k, v in self._store.items() if v.created_at < cutoff]
        for k in expired:
            del self._store[k]

    def ensure_receipt(
        self,
        key: str,
        ticket_id: str,
        event_type: str,
        event_timestamp: str,
    ) -> bool:
        """
        Durably write a RECEIVED record to Supabase BEFORE returning 200 OK.

        This is the WAL (write-ahead log) step for background task reliability.
        It does NOT update the in-memory store — the idempotency check() is
        unaffected. The Supabase record uses INSERT (not upsert), so an existing
        COMPLETED record is never overwritten.

        Returns True if persisted, False if key already existed or on error.
        """
        if self._sb is None:
            return False
        try:
            row = {
                "idempotency_key":   key,
                "ticket_id":         ticket_id,
                "event_type":        event_type,
                "event_timestamp":   event_timestamp,
                "processing_status": IdempotencyStatus.RECEIVED.value,
            }
            # INSERT — not upsert. If the key already exists (any status), the
            # unique constraint raises. We catch it and return False, which means
            # "already in DB — don't overwrite COMPLETED records".
            self._sb.table(_TABLE).insert(row).execute()
            LOGGER.debug("idempotency.ensure_receipt: persisted key=%s", key)
            return True
        except Exception as exc:
            LOGGER.debug("idempotency.ensure_receipt: key=%s status=%s", key, exc)
            return False

    def _db_check(self, key: str) -> bool:
        """
        Return True only if the key has COMPLETED status in Supabase.

        RECEIVED entries are NOT treated as duplicates — they represent events
        received but not yet fully processed. A RECEIVED entry that was written
        by ensure_receipt (WAL step) allows the background task to process it.
        Only COMPLETED entries suppress reprocessing.
        """
        try:
            result = (
                self._sb.table(_TABLE)
                .select("processing_status")
                .eq("idempotency_key", key)
                .eq("processing_status", IdempotencyStatus.COMPLETED.value)
                .limit(1)
                .execute()
            )
            return bool(result.data)
        except Exception as exc:
            LOGGER.warning("idempotency.db_check: error=%s key=%s", exc, key)
            return False

    def _db_insert(self, entry: IdempotencyEntry) -> None:
        try:
            from datetime import datetime, timezone
            row = {
                "idempotency_key":  entry.key,
                "ticket_id":        entry.ticket_id,
                "event_type":       entry.event_type,
                "event_timestamp":  entry.event_timestamp,
                "processing_status": entry.status.value,
            }
            self._sb.table(_TABLE).upsert(row).execute()
        except Exception as exc:
            LOGGER.warning("idempotency.db_insert: error=%s key=%s", exc, entry.key)

    def _db_update(
        self,
        key: str,
        status: IdempotencyStatus,
        *,
        case_id: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        try:
            updates: dict[str, Any] = {"processing_status": status.value}
            if case_id is not None:
                updates["case_id"] = case_id
            if error_detail is not None:
                updates["error_detail"] = error_detail
            self._sb.table(_TABLE).update(updates).eq("idempotency_key", key).execute()
        except Exception as exc:
            LOGGER.warning("idempotency.db_update: error=%s key=%s", exc, key)
