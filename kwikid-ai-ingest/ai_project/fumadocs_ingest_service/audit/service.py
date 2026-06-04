"""
audit/service.py

Sprint 2.9: AuditService — preferred entrypoint for audit operations.

AuditService wraps AuditRepository and provides the application-level
interface for both emitting events and querying history.

Responsibilities:
  emit()               — record an audit event (never raises)
  get_action_history() — full event history for one action
  get_case_history()   — full event history for one case
  get_client_history() — paginated events for one client
  search()             — filtered, paginated event query
  count()              — filtered count

Design:
  Future integrations (worker audit, watchdog audit) should call
  audit_service.emit() rather than repository.insert_event() directly.
  This allows interception (e.g. structured logging, metrics) at a
  single point.

  The repository backend is injected at construction — swap
  InMemoryAuditRepository for a Supabase backend in one line.
"""
from __future__ import annotations

import logging
from typing import Any

from audit.models import AuditEvent, AuditEventType
from audit.repository import AuditRepository

LOGGER = logging.getLogger(__name__)


class AuditService:
    """
    Application-level audit facade.

    All write operations go through emit(). All read operations delegate
    to the injected AuditRepository.
    """

    def __init__(self, repository: AuditRepository) -> None:
        self._repo = repository

    # ── Write ──────────────────────────────────────────────────────────────────

    def emit(self, event: AuditEvent) -> None:
        """
        Record an audit event.

        Never raises. Failures are logged but do not propagate to callers —
        audit failures must never disrupt business operations.
        """
        try:
            self._repo.insert_event(event)
            LOGGER.info(
                "audit: event_type=%s action_id=%s actor=%s event_id=%s",
                event.event_type.value,
                event.action_id,
                event.actor,
                event.event_id,
            )
        except Exception as exc:
            LOGGER.error(
                "audit.service.emit: failed event_type=%s action_id=%s error=%s",
                event.event_type, event.action_id, exc,
            )

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_action_history(self, action_id: str) -> list[AuditEvent]:
        """Return all audit events for the given action, in emission order."""
        return self._repo.events_for_action(action_id)

    def get_case_history(self, case_id: str) -> list[AuditEvent]:
        """Return all audit events for the given case, in emission order."""
        return self._repo.events_for_case(case_id)

    def get_client_history(
        self,
        client: str,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[AuditEvent]:
        """Return paginated audit events for the given client."""
        return self._repo.events_for_client(client, limit=limit, offset=offset)

    def search(
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
        Return events matching all supplied filters.

        All filters are optional and ANDed. Results are paginated.
        """
        return self._repo.list_events(
            event_type=event_type,
            client=client,
            action_id=action_id,
            case_id=case_id,
            limit=limit,
            offset=offset,
        )

    def count(
        self,
        *,
        event_type: AuditEventType | None = None,
        client: str | None = None,
        action_id: str | None = None,
        case_id: str | None = None,
    ) -> int:
        """Return the count of events matching all supplied filters."""
        return self._repo.count_events(
            event_type=event_type,
            client=client,
            action_id=action_id,
            case_id=case_id,
        )
