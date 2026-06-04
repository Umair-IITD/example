"""
audit/repository_supabase.py

Sprint 2.10: SupabaseAuditRepository — persistent audit storage via Supabase.

Implements the AuditRepository ABC using the supabase-py client to persist
audit events to the audit_events table (S2_003_dead_letter_and_audit_events.sql).

Design:
  - insert_event() maps AuditEvent fields to DB columns including metadata_json.
  - All query methods apply optional filters and pagination at the DB level
    (no in-process filtering of large result sets).
  - Errors are caught, logged, and never propagated — audit must not disrupt
    business operations (same contract as InMemoryAuditRepository).
  - Thread-safe: the supabase-py client is documented as thread-safe for
    concurrent reads/writes from multiple threads.

Performance:
  - events_for_action: indexed on action_id
  - events_for_case: indexed on case_id
  - events_for_client: indexed on client + timestamp
  - list_events: uses server-side filtering; avoids full table scans
    when at least one indexed filter is supplied.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from audit.models import AuditEvent, AuditEventType
from audit.outbox import AuditOutbox
from audit.repository import AuditRepository
from audit.retry_policy import AuditRetryPolicy

LOGGER = logging.getLogger(__name__)

_TABLE = "audit_events"


def _row_to_event(row: dict[str, Any]) -> AuditEvent:
    """Convert a DB row dict to an AuditEvent."""
    ts_raw = row.get("timestamp")
    if isinstance(ts_raw, str):
        ts = datetime.fromisoformat(ts_raw)
    elif isinstance(ts_raw, datetime):
        ts = ts_raw
    else:
        ts = datetime.now(tz=timezone.utc)

    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return AuditEvent(
        event_id=row["event_id"],
        action_id=row["action_id"],
        case_id=row.get("case_id", ""),
        client=row.get("client", ""),
        event_type=AuditEventType(row["event_type"]),
        actor=row["actor"],
        timestamp=ts,
        metadata=row.get("metadata_json") or {},
    )


class SupabaseAuditRepository(AuditRepository):
    """
    Persistent AuditRepository backed by Supabase (PostgreSQL).

    Requires the audit_events table from S2_003_dead_letter_and_audit_events.sql.
    All writes are append-only: no UPDATE or DELETE operations are issued.
    """

    def __init__(
        self,
        supabase_client: Any,
        *,
        retry_policy: AuditRetryPolicy | None = None,
        outbox: AuditOutbox | None = None,
        metrics: Any = None,
    ) -> None:
        if supabase_client is None:
            raise ValueError("SupabaseAuditRepository requires a non-None supabase_client")
        self._sb = supabase_client
        self._retry = retry_policy or AuditRetryPolicy(metrics=metrics)
        self._outbox = outbox or AuditOutbox(metrics=metrics)

    def insert_event(self, event: AuditEvent) -> None:
        # Flush any pending outbox events before attempting a new write.
        # This piggybacks delivery retries on normal write traffic.
        if self._outbox.size > 0:
            self._outbox.flush(self._write_row)

        result = self._retry.execute_with_retry(lambda: self._write_row(event))
        if not result.success:
            LOGGER.error(
                "supabase_audit.insert_event: all retries failed event_id=%s "
                "event_type=%s — queuing to outbox: %s",
                getattr(event, "event_id", "?"),
                getattr(event, "event_type", "?"),
                result.last_error,
            )
            self._outbox.enqueue(event)

    def _write_row(self, event: AuditEvent) -> None:
        """Execute the raw Supabase insert. Raises on failure."""
        # Normalize timestamp to UTC before persistence.
        # AuditEvent.timestamp defaults to UTC but callers may supply
        # any aware datetime — astimezone(utc) handles all offset variants.
        ts = event.timestamp.astimezone(timezone.utc) if event.timestamp.tzinfo else event.timestamp.replace(tzinfo=timezone.utc)
        row = {
            "event_id":      event.event_id,
            "action_id":     event.action_id,
            "case_id":       event.case_id or "",
            "client":        event.client or "",
            "event_type":    event.event_type.value,
            "actor":         event.actor,
            "timestamp":     ts.isoformat(),
            "metadata_json": event.metadata or {},
        }
        self._sb.table(_TABLE).insert(row).execute()

    def events_for_action(self, action_id: str) -> list[AuditEvent]:
        try:
            result = (
                self._sb.table(_TABLE)
                .select("*")
                .eq("action_id", action_id)
                .order("timestamp", desc=False)
                .execute()
            )
            return [_row_to_event(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "supabase_audit.events_for_action: action_id=%s error=%s", action_id, exc,
            )
            return []

    def events_for_case(self, case_id: str) -> list[AuditEvent]:
        try:
            result = (
                self._sb.table(_TABLE)
                .select("*")
                .eq("case_id", case_id)
                .order("timestamp", desc=False)
                .execute()
            )
            return [_row_to_event(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "supabase_audit.events_for_case: case_id=%s error=%s", case_id, exc,
            )
            return []

    def events_for_client(
        self,
        client: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        try:
            result = (
                self._sb.table(_TABLE)
                .select("*")
                .eq("client", client)
                .order("timestamp", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return [_row_to_event(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error(
                "supabase_audit.events_for_client: client=%s error=%s", client, exc,
            )
            return []

    def list_events(
        self,
        *,
        event_type: AuditEventType | None = None,
        client: str | None = None,
        action_id: str | None = None,
        case_id: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        try:
            query = self._sb.table(_TABLE).select("*")
            if event_type is not None:
                query = query.eq("event_type", event_type.value)
            if client is not None:
                query = query.eq("client", client)
            if action_id is not None:
                query = query.eq("action_id", action_id)
            if case_id is not None:
                query = query.eq("case_id", case_id)
            result = (
                query
                .order("timestamp", desc=False)
                .range(offset, offset + limit - 1)
                .execute()
            )
            return [_row_to_event(r) for r in (result.data or [])]
        except Exception as exc:
            LOGGER.error("supabase_audit.list_events: error=%s", exc)
            return []

    def count_events(
        self,
        *,
        event_type: AuditEventType | None = None,
        client: str | None = None,
        action_id: str | None = None,
        case_id: str | None = None,
    ) -> int:
        try:
            query = self._sb.table(_TABLE).select("event_id", count="exact")
            if event_type is not None:
                query = query.eq("event_type", event_type.value)
            if client is not None:
                query = query.eq("client", client)
            if action_id is not None:
                query = query.eq("action_id", action_id)
            if case_id is not None:
                query = query.eq("case_id", case_id)
            result = query.execute()
            return result.count or 0
        except Exception as exc:
            LOGGER.error("supabase_audit.count_events: error=%s", exc)
            return 0
