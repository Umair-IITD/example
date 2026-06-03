"""
executors/add_ticket_note_executor.py

Sprint 2.5: AddTicketNoteExecutor — add a private/public note to a Freshdesk ticket.

Domain design:
  action_namespace : "ticket"
  action_type      : "add_note"
  risk_level       : REVERSIBLE — a note can be contextually negated by adding
                     a compensation note explaining the original was in error.
  rollback         : Add a compensation note to the same ticket (see rollback()).

  NOTE: Freshdesk does not support deleting or editing notes after creation.
  The rollback strategy therefore adds a follow-up correction note rather than
  removing the original — this is the standard support operations pattern for
  audit-trail fidelity.

Required payload (action_payload / action_params at proposal time):
  body     : str  — note body text. Required; must be non-empty.
  private  : bool — True = internal note (agents only). Default True.

Required rollback_params (captured at proposal time):
  compensation_note : str — text of the correction note to add during rollback.
                     Optional; if absent a generic correction message is used.

Provider operation used: "add_note" → POST /api/v2/tickets/{ticket_id}/notes
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

_DEFAULT_COMPENSATION_NOTE = (
    "CORRECTION: A previous note on this ticket was added in error and should "
    "be disregarded. This note is the official record of the correction."
)


class AddTicketNoteExecutor(_BaseProviderExecutor):
    """
    Adds a note to a Freshdesk support ticket.

    Reversibility
    ─────────────
    Freshdesk does not allow note deletion. Rollback adds a clearly-marked
    correction note. This preserves the audit trail while providing operators
    with a visible correction record.

    Idempotency
    ───────────
    Freshdesk does not natively deduplicate notes by idempotency key. The
    executor uses context.request_id as the idempotency key in ProviderRequest,
    which the router forwards to the provider. For strict idempotency across
    retries, operators should configure the Freshdesk instance to use webhook
    deduplication or implement it at the gateway level (Sprint 2.X+).
    """

    @property
    def action_type(self) -> str:
        return "add_note"

    @property
    def action_namespace(self) -> str:
        return "ticket"

    def execute(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Add a note to the ticket identified by context.ticket_id.

        Args:
            context: Execution metadata (ticket_id, request_id, deadline, etc.).
            payload: Must contain "body" (str). Optional: "private" (bool, default True).

        Returns:
            ExecutionResult with provider_reference=note_id on success.

        Raises:
            PermanentExecutionError: if "body" is missing or empty.
            RetryableExecutionError: on transient provider failures.
            PermanentExecutionError: on permanent provider failures.
        """
        body: str = payload.get("body", "")
        if not body or not str(body).strip():
            raise PermanentExecutionError(
                "AddTicketNoteExecutor: payload.body is required and must be non-empty.",
                failure_code="MISSING_REQUIRED_FIELD",
            )
        private: bool = bool(payload.get("private", True))

        self._check_deadline(context)

        request = self._build_request(
            context,
            operation="add_note",
            payload={"body": str(body), "private": private},
        )
        response = self._route(request)

        note_id = response.result.get("note_id", response.provider_request_id or "")
        LOGGER.info(
            "add_ticket_note: ticket=%s note=%s attempt=%s",
            context.ticket_id, note_id, context.action_id,
        )
        return ExecutionResult(
            success=True,
            provider_reference=str(note_id),
            payload={"note_id": str(note_id), "ticket_id": context.ticket_id},
        )

    def rollback(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Add a compensation note to negate the original note.

        Args:
            context:  Execution metadata for the compensation action.
            payload:  rollback_params from the original ActionRequest.
                      Key "compensation_note" (str, optional) — text of the
                      correction note. If absent, a default message is used.

        Returns:
            ExecutionResult with provider_reference=compensation_note_id.

        Raises:
            RollbackError: if the compensation note could not be added.
        """
        compensation_note: str = str(
            payload.get("compensation_note", _DEFAULT_COMPENSATION_NOTE)
        ).strip() or _DEFAULT_COMPENSATION_NOTE

        request = self._build_request(
            context,
            operation="add_note",
            payload={"body": compensation_note, "private": True},
        )
        response = self._route_for_rollback(request)

        note_id = response.result.get("note_id", response.provider_request_id or "")
        LOGGER.info(
            "add_ticket_note: rollback compensation_note=%s ticket=%s",
            note_id, context.ticket_id,
        )
        return ExecutionResult(
            success=True,
            provider_reference=str(note_id),
            payload={
                "compensation_note_id": str(note_id),
                "ticket_id": context.ticket_id,
            },
        )
