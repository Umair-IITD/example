"""
audit/repository.py

Sprint 2.9: AuditRepository — persistence abstraction for audit events.

Design:
  AuditRepository is an abstract base class (ABC). All storage operations
  go through this interface. InMemoryAuditRepository is the default
  in-process implementation used in development and tests.

  Future backends (Supabase, PostgreSQL, S3) implement the same interface
  without any changes to the service or API layers.

Thread safety:
  InMemoryAuditRepository uses list/dict append — safe under the GIL
  for single-process CPython. Multi-process deployments must use a
  persistent backend (Sprint 2.10+).

Performance:
  InMemoryAuditRepository maintains four indices (all events, by action,
  by case, by client) for O(1) lookups on the three primary query paths.
  list_events() with arbitrary filters is O(n) — acceptable for in-memory.
"""
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from collections import defaultdict
from typing import Any

from audit.models import AuditEvent, AuditEventType

LOGGER = logging.getLogger(__name__)


class AuditRepository(ABC):
    """
    Abstract storage backend for audit events.

    All implementations must be append-only: once inserted,
    events are never modified or deleted.
    """

    @abstractmethod
    def insert_event(self, event: AuditEvent) -> None:
        """Persist a new audit event. Never raises."""

    @abstractmethod
    def events_for_action(self, action_id: str) -> list[AuditEvent]:
        """Return all events for the given action_id, in insertion order."""

    @abstractmethod
    def events_for_case(self, case_id: str) -> list[AuditEvent]:
        """Return all events for the given case_id, in insertion order."""

    @abstractmethod
    def events_for_client(
        self,
        client: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Return events for the given client, newest last, with pagination."""

    @abstractmethod
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
        """
        Return events matching all supplied filters, with pagination.

        Filters are ANDed. Omit a filter to match all values.
        Results are in insertion order.
        """

    @abstractmethod
    def count_events(
        self,
        *,
        event_type: AuditEventType | None = None,
        client: str | None = None,
        action_id: str | None = None,
        case_id: str | None = None,
    ) -> int:
        """Return the count of events matching all supplied filters."""


# ── In-Memory Implementation ──────────────────────────────────────────────────

class InMemoryAuditRepository(AuditRepository):
    """
    Fully in-process AuditRepository.

    Maintains four indices for fast lookups:
      _events        — all events in insertion order
      _by_action     — action_id → [event, ...]
      _by_case       — case_id   → [event, ...]
      _by_client     — client    → [event, ...]

    insert_event() is O(1).
    events_for_action/case/client() are O(1) dict lookups.
    list_events() with filters is O(n) — acceptable for in-memory usage.
    count_events() with no filter is O(1); with filters O(n).
    """

    def __init__(self) -> None:
        self._events: list[AuditEvent] = []
        self._by_action: dict[str, list[AuditEvent]] = defaultdict(list)
        self._by_case: dict[str, list[AuditEvent]] = defaultdict(list)
        self._by_client: dict[str, list[AuditEvent]] = defaultdict(list)

    def insert_event(self, event: AuditEvent) -> None:
        try:
            self._events.append(event)
            self._by_action[event.action_id].append(event)
            if event.case_id:
                self._by_case[event.case_id].append(event)
            if event.client:
                self._by_client[event.client].append(event)
        except Exception as exc:
            LOGGER.error("audit_repo.insert_event: failed event_id=%s error=%s",
                         getattr(event, "event_id", "?"), exc)

    def events_for_action(self, action_id: str) -> list[AuditEvent]:
        return list(self._by_action.get(action_id, []))

    def events_for_case(self, case_id: str) -> list[AuditEvent]:
        return list(self._by_case.get(case_id, []))

    def events_for_client(
        self,
        client: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        events = self._by_client.get(client, [])
        return list(events[offset: offset + limit])

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
        results = self._events
        if event_type is not None:
            results = [e for e in results if e.event_type == event_type]
        if client is not None:
            results = [e for e in results if e.client == client]
        if action_id is not None:
            results = [e for e in results if e.action_id == action_id]
        if case_id is not None:
            results = [e for e in results if e.case_id == case_id]
        return list(results[offset: offset + limit])

    def count_events(
        self,
        *,
        event_type: AuditEventType | None = None,
        client: str | None = None,
        action_id: str | None = None,
        case_id: str | None = None,
    ) -> int:
        if event_type is None and client is None and action_id is None and case_id is None:
            return len(self._events)
        results = self._events
        if event_type is not None:
            results = [e for e in results if e.event_type == event_type]
        if client is not None:
            results = [e for e in results if e.client == client]
        if action_id is not None:
            results = [e for e in results if e.action_id == action_id]
        if case_id is not None:
            results = [e for e in results if e.case_id == case_id]
        return len(results)
