"""
executors/update_ticket_status_executor.py

Sprint 2.5: UpdateTicketStatusExecutor — update the status field of a Freshdesk ticket.

Domain design:
  action_namespace : "ticket"
  action_type      : "update_status"
  risk_level       : REVERSIBLE — the previous status is captured at proposal time
                     and stored in rollback_params; rollback restores it exactly.
  rollback         : Restore the ticket to the status it had before this action.

  This executor exists as a distinct action type from the generic "update_ticket"
  provider operation because status changes carry specific risk semantics (they
  affect SLA tracking, routing, and customer visibility) and deserve a dedicated
  audit trail and rollback path.

Required payload (action_payload / action_params at proposal time):
  status : int — Freshdesk status code. Standard values:
                 2=Open  3=Pending  4=Resolved  5=Closed
                 Custom status IDs > 5 are also accepted.
                 Must be a positive integer.

Required rollback_params (captured at proposal time by the AI proposer):
  status : int — the status to restore (the ticket's status BEFORE this action).
                 Must be a positive integer.

  IMPORTANT: The AI proposer must look up the current ticket status before
  proposing this action, capture it as rollback_params={"status": current_status},
  and pass it at proposal time. The executor cannot look up the previous status
  during rollback — it relies entirely on what was captured at proposal time.

Provider operation used: "update_ticket" → PUT /api/v2/tickets/{ticket_id}
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.action_executor import (
    ExecutionContext,
    ExecutionResult,
    PermanentExecutionError,
    RollbackError,
)
from executors._base import _BaseProviderExecutor

LOGGER = logging.getLogger(__name__)


class UpdateTicketStatusExecutor(_BaseProviderExecutor):
    """
    Updates the status of a Freshdesk support ticket.

    Reversibility
    ─────────────
    Status rollback is fully reversible: the AI proposer captures the previous
    status at proposal time (rollback_params={"status": original_status}), and
    rollback() restores it by sending another update_ticket call.

    Idempotency
    ───────────
    PUT /api/v2/tickets/{ticket_id} with the same status is idempotent on the
    Freshdesk side — setting status=4 when it is already 4 succeeds without error.
    The executor still uses context.request_id as the idempotency key for the
    provider layer, enabling deduplication if the provider implements it.
    """

    @property
    def action_type(self) -> str:
        return "update_status"

    @property
    def action_namespace(self) -> str:
        return "ticket"

    def execute(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Update the ticket status to payload["status"].

        Args:
            context: Execution metadata (ticket_id, request_id, deadline, etc.).
            payload: Must contain "status" (int, positive).

        Returns:
            ExecutionResult with provider_reference=ticket_id on success.

        Raises:
            PermanentExecutionError: if "status" is missing, not an integer, or <= 0.
            RetryableExecutionError: on transient provider failures.
            PermanentExecutionError: on permanent provider failures.
        """
        status = payload.get("status")
        if status is None:
            raise PermanentExecutionError(
                "UpdateTicketStatusExecutor: payload.status is required.",
                failure_code="MISSING_REQUIRED_FIELD",
            )
        if not isinstance(status, int) or status <= 0:
            raise PermanentExecutionError(
                f"UpdateTicketStatusExecutor: payload.status must be a positive integer, "
                f"got {status!r}.",
                failure_code="INVALID_FIELD_VALUE",
            )

        self._check_deadline(context)

        request = self._build_request(
            context,
            operation="update_ticket",
            payload={"status": status},
        )
        response = self._route(request)

        LOGGER.info(
            "update_ticket_status: ticket=%s status=%s action=%s",
            context.ticket_id, status, context.action_id,
        )
        return ExecutionResult(
            success=True,
            provider_reference=response.provider_request_id or context.ticket_id,
            payload={"ticket_id": context.ticket_id, "status": status},
        )

    def rollback(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Restore the ticket to its previous status.

        Args:
            context: Execution metadata for the compensation action.
            payload: rollback_params captured at proposal time.
                     Must contain "status" (int, positive) — the previous status.

        Returns:
            ExecutionResult with provider_reference=ticket_id on success.

        Raises:
            RollbackError: if "status" is missing/invalid or the provider call fails.
        """
        status = payload.get("status")
        if status is None:
            raise RollbackError(
                "UpdateTicketStatusExecutor.rollback: rollback_params.status is required "
                "but was not captured at proposal time.",
                failure_code="ROLLBACK_MISSING_PARAMS",
            )
        if not isinstance(status, int) or status <= 0:
            raise RollbackError(
                f"UpdateTicketStatusExecutor.rollback: rollback_params.status must be a "
                f"positive integer, got {status!r}.",
                failure_code="ROLLBACK_INVALID_PARAMS",
            )

        request = self._build_request(
            context,
            operation="update_ticket",
            payload={"status": status},
        )
        response = self._route_for_rollback(request)

        LOGGER.info(
            "update_ticket_status: rollback ticket=%s restored_status=%s",
            context.ticket_id, status,
        )
        return ExecutionResult(
            success=True,
            provider_reference=response.provider_request_id or context.ticket_id,
            payload={"ticket_id": context.ticket_id, "restored_status": status},
        )
