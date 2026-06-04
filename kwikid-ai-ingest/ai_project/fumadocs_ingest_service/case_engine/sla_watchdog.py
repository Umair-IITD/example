"""
case_engine/sla_watchdog.py

Sprint 2.8: SLAWatchdog — approval expiration engine.

Responsibility:
  Scan AWAITING_APPROVAL actions whose expires_at is in the past,
  transition them to EXPIRED, and record the state change.

What this is NOT:
  - Not a background thread or scheduled job
  - Not responsible for notifying approvers
  - Not responsible for HTTP routing

Usage:
    watchdog = SLAWatchdog(gateway=gateway, repository=repository)
    result = watchdog.run(client="unity_bank")
    # result.expired_actions  — count of actions successfully expired
    # result.processed        — count of actions inspected

Design:
  Uses ActionGateway.expire() which uses ActionStateMachine — no direct
  state mutation. Audit trail is preserved via the existing on_transition
  callback in ActionStateMachine.

  Idempotent: running twice on the same set of actions is safe —
  already-EXPIRED actions cannot transition again (terminal state),
  so the second run finds an empty list.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from audit.models import AuditEvent, AuditEventType
from case_engine.action_gateway import ActionGateway
from case_engine.action_repository import ActionRepository
from case_engine.action_state import ActionTransitionError

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class WatchdogResult:
    """Result of one SLA watchdog run."""
    expired_actions: int
    processed: int
    errors: int = 0


class SLAWatchdog:
    """
    Scans for expired approval windows and transitions affected actions to EXPIRED.

    Relies on:
      - ActionRepository.list_expired_actions() — finds AWAITING_APPROVAL rows
        whose expires_at is in the past (already implemented in Sprint 2.6).
      - ActionGateway.expire() — applies AWAITING_APPROVAL→EXPIRED via state machine.
    """

    def __init__(
        self,
        gateway: ActionGateway,
        repository: ActionRepository,
        audit_service: Any = None,
        metrics_service: Any = None,
    ) -> None:
        self._gateway = gateway
        self._repo = repository
        self._audit = audit_service
        self._metrics = metrics_service

    def run(self, client: str | None = None) -> WatchdogResult:
        """
        Expire all AWAITING_APPROVAL actions whose SLA has elapsed.

        Args:
            client: Optional tenant filter. None → process all clients.

        Returns:
            WatchdogResult with counts of processed and expired actions.

        Never raises. All per-action errors are caught and counted.
        """
        try:
            candidates = self._repo.list_expired_actions(client)
        except Exception as exc:
            LOGGER.error("sla_watchdog.run: failed to fetch candidates client=%s error=%s",
                         client, exc)
            return WatchdogResult(expired_actions=0, processed=0, errors=1)

        expired = 0
        errors = 0

        for action in candidates:
            try:
                self._gateway.expire(action, reason="sla_deadline_elapsed")
                expired += 1
                LOGGER.info(
                    "sla_watchdog: expired action_id=%s ticket_id=%s client=%s expires_at=%s",
                    action.action_id, action.ticket_id, action.client,
                    action.expires_at.isoformat() if action.expires_at else "None",
                )
                if self._audit is not None:
                    self._audit.emit(AuditEvent(
                        action_id=action.action_id,
                        case_id=action.case_id,
                        client=action.client,
                        event_type=AuditEventType.ACTION_EXPIRED,
                        actor="watchdog:sla",
                        metadata={
                            "reason": "sla_deadline_elapsed",
                            "expires_at": action.expires_at.isoformat() if action.expires_at else None,
                            "ticket_id": action.ticket_id,
                        },
                    ))
                if self._metrics is not None:
                    try:
                        self._metrics.record_action_expired()
                    except Exception:
                        pass
            except ActionTransitionError as exc:
                # Already expired or in a terminal state — skip silently
                LOGGER.debug(
                    "sla_watchdog: transition blocked action_id=%s reason=%s",
                    action.action_id, exc,
                )
            except Exception as exc:
                LOGGER.error(
                    "sla_watchdog: unexpected error action_id=%s error=%s",
                    action.action_id, exc,
                )
                errors += 1

        LOGGER.info(
            "sla_watchdog.run: client=%s processed=%d expired=%d errors=%d",
            client, len(candidates), expired, errors,
        )
        return WatchdogResult(
            expired_actions=expired,
            processed=len(candidates),
            errors=errors,
        )
