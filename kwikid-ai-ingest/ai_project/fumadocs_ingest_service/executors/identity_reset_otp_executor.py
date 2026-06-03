"""
executors/identity_reset_otp_executor.py

Sprint 2.5: IdentityResetOtpExecutor — record an OTP reset on a Freshdesk ticket.

Domain design:
  action_namespace : "identity"
  action_type      : "reset_otp"
  risk_level       : IRREVERSIBLE — once an OTP is reset and communicated to the
                     customer, it cannot be un-sent or un-verified. Classifying
                     it as IRREVERSIBLE enforces:
                       • max_attempts = 1 (gateway default for IRREVERSIBLE)
                       • no retry on failure
                       • no rollback path
                       • explicit human approval before execution

  rollback policy  : NOT SUPPORTED. rollback() always raises RollbackError with
                     failure_code="ROLLBACK_NOT_SUPPORTED". This is correct:
                     the gateway's propose_rollback() validates risk_level == REVERSIBLE
                     before creating a compensation action, so rollback() on this
                     executor should never be called in production. The explicit
                     RollbackError guard exists as a defense-in-depth measure.

  Rationale for IRREVERSIBLE:
    An OTP reset triggers a one-time credential that the customer receives via SMS
    or email. The moment the message is dispatched, it is irrevocable — the
    customer sees it, and any attacker intercepting it could use it. Rolling back
    the ticket action does not invalidate the OTP. Human oversight at the approval
    step is therefore mandatory.

Required payload (action_payload / action_params at proposal time):
  account_id : str — customer account identifier (required, non-empty).
  reason     : str — reason for the OTP reset (optional, for audit purposes).

  The executor records the OTP reset as an internal note on the Freshdesk ticket.
  It does NOT call an OTP service directly — the OTP reset is performed by the
  human agent after they see the recorded action in the ticket. This executor's
  job is to create an auditable record that the action was approved and taken.

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

_NOTE_TEMPLATE = (
    "[IDENTITY ACTION — OTP RESET]\n"
    "Account ID: {account_id}\n"
    "Reason: {reason}\n"
    "Action ID: {action_id}\n"
    "This note was generated automatically when an approved OTP reset action "
    "was executed. A support agent must complete the OTP reset via the identity "
    "management console and update this ticket accordingly."
)


class IdentityResetOtpExecutor(_BaseProviderExecutor):
    """
    Records an OTP reset action on a Freshdesk support ticket.

    Irreversibility rationale
    ─────────────────────────
    OTP reset triggers a credential dispatch to the customer (SMS/email). Once
    dispatched, the credential cannot be recalled. Classifying this as IRREVERSIBLE
    enforces mandatory human approval and prevents automatic retries, which could
    dispatch duplicate credentials.

    What this executor actually does
    ─────────────────────────────────
    It adds a private internal note to the ticket, creating an auditable record
    that the OTP reset was approved and should be executed. The human agent then
    performs the actual OTP reset via the identity management console. This
    separation ensures:
      1. The approval trail exists in the ticketing system.
      2. The actual credential dispatch is under human control.
      3. The system never dispatches credentials automatically (security principle).
    """

    @property
    def action_type(self) -> str:
        return "reset_otp"

    @property
    def action_namespace(self) -> str:
        return "identity"

    def execute(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        Record an OTP reset action as a private note on the support ticket.

        Args:
            context: Execution metadata (ticket_id, request_id, deadline, etc.).
            payload: Must contain "account_id" (str, non-empty).
                     Optional: "reason" (str) — audit rationale.

        Returns:
            ExecutionResult with provider_reference=note_id on success.

        Raises:
            PermanentExecutionError: if "account_id" is missing or empty.
            RetryableExecutionError: on transient provider failures.
            PermanentExecutionError: on permanent provider failures.
        """
        account_id: str = str(payload.get("account_id", "")).strip()
        if not account_id:
            raise PermanentExecutionError(
                "IdentityResetOtpExecutor: payload.account_id is required and must be non-empty.",
                failure_code="MISSING_REQUIRED_FIELD",
            )
        reason: str = str(payload.get("reason", "OTP reset requested via support ticket")).strip()

        self._check_deadline(context)

        note_body = _NOTE_TEMPLATE.format(
            account_id=account_id,
            reason=reason,
            action_id=context.action_id,
        )

        request = self._build_request(
            context,
            operation="add_note",
            payload={"body": note_body, "private": True},
        )
        response = self._route(request)

        note_id = response.result.get("note_id", response.provider_request_id or "")
        LOGGER.info(
            "identity_reset_otp: ticket=%s account=%s note=%s action=%s",
            context.ticket_id, account_id, note_id, context.action_id,
        )
        return ExecutionResult(
            success=True,
            provider_reference=str(note_id),
            payload={
                "note_id":    str(note_id),
                "account_id": account_id,
                "ticket_id":  context.ticket_id,
            },
        )

    def rollback(
        self,
        context: ExecutionContext,
        payload: dict[str, Any],
    ) -> ExecutionResult:
        """
        NOT SUPPORTED — OTP reset is irreversible.

        This method exists to satisfy the ActionExecutor ABC. It must never be
        called in production because:
          1. The gateway's propose_rollback() rejects IRREVERSIBLE actions.
          2. max_attempts=1 means there is no retry path that could trigger rollback.

        If called, it raises RollbackError immediately so the runtime records
        the attempt as ROLLBACK_FAILED (terminal state requiring human escalation).

        Raises:
            RollbackError: always, with failure_code="ROLLBACK_NOT_SUPPORTED".
        """
        raise RollbackError(
            "IdentityResetOtpExecutor: OTP reset is an IRREVERSIBLE action — "
            "rollback is not supported. Human escalation is required to assess "
            "the impact of the OTP reset and take appropriate remediation steps.",
            failure_code="ROLLBACK_NOT_SUPPORTED",
        )
