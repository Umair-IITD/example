"""
audit/logger.py

Sprint 2.9: AuditLogger — repository-backed audit event facade.

Sprint 2.8 backward compatibility is fully preserved:
  - AuditLogger() with no args creates an InMemoryAuditRepository internally.
  - All Sprint 2.8 public methods (emit, events_for_action, all_events,
    count, count_by_type) retain identical signatures and semantics.

Sprint 2.9 additions:
  - AuditLogger(repository=repo) — inject a shared repository so that
    AuditLogger and AuditService operate on the same event store.
  - AuditLogger.repository property — exposes the backing store so the
    app factory can share it with AuditService.

Thread safety:
  Delegated to the repository implementation. InMemoryAuditRepository
  is safe under the GIL for single-process CPython.
"""
from __future__ import annotations

import logging
from typing import Sequence

from audit.models import AuditEvent, AuditEventType
from audit.repository import AuditRepository, InMemoryAuditRepository

LOGGER = logging.getLogger(__name__)


class AuditLogger:
    """
    Repository-backed audit event logger.

    Primary write path for route handlers (approve/reject) — delegates
    all storage to the injected AuditRepository.

    Usage (Sprint 2.8 style — still works):
        logger = AuditLogger()
        logger.emit(event)
        events = logger.events_for_action(action_id)

    Usage (Sprint 2.9 shared-repo style):
        repo   = InMemoryAuditRepository()
        logger = AuditLogger(repository=repo)
        svc    = AuditService(repository=repo)
        # logger and svc now read/write the same event store
    """

    def __init__(self, repository: AuditRepository | None = None) -> None:
        self._repo: AuditRepository = (
            repository if repository is not None else InMemoryAuditRepository()
        )

    @property
    def repository(self) -> AuditRepository:
        """Expose the backing repository for shared-store wiring."""
        return self._repo

    def emit(self, event: AuditEvent) -> None:
        """
        Record an audit event.

        Never raises. Exceptions are caught and logged.
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
            LOGGER.error("audit.emit: failed to record event type=%s error=%s",
                         event.event_type, exc)

    def events_for_action(self, action_id: str) -> list[AuditEvent]:
        """Return all audit events for the given action, in emission order."""
        return self._repo.events_for_action(action_id)

    def all_events(self) -> list[AuditEvent]:
        """Return all recorded events in emission order."""
        return self._repo.list_events(limit=100_000)

    def count(self) -> int:
        return self._repo.count_events()

    def count_by_type(self, event_type: AuditEventType) -> int:
        return self._repo.count_events(event_type=event_type)
