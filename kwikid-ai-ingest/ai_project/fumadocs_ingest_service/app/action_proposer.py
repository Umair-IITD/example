"""
app/action_proposer.py

Sprint 2.12: Translate a high-confidence RAG result into an ActionGateway proposal.

Entry point: propose_rag_note_action()

This is the bridge between the RAG pipeline and the action execution layer.
It receives a formatted note body (HTML, already assembled by the caller),
builds a SAFE ActionProposal for ("ticket", "add_note"), and submits it to
the gateway.

Design decisions:
  - Risk level is always SAFE: the note body is the RAG answer, already
    confidence-gated by the caller. Auto-approval is intentional and correct.
  - SAFE actions auto-approve immediately in ActionGateway.propose().
  - The background worker loop executes them within the configured poll interval
    (default: ACTION_WORKER_POLL_INTERVAL_S seconds, see app/main.py).
  - This module never calls FreshdeskReplyClient directly; it only writes to the
    gateway. The executor (AddTicketNoteExecutor) owns the provider call.
  - Idempotency: the gateway computes a deterministic idempotency key from
    (case_id, action_type, action_namespace, action_params). Duplicate proposals
    for the same logical action are returned as DuplicateActionError and treated
    as idempotent success here.
  - This module NEVER raises to the caller. All exceptions are caught and logged.
    A None return means "proposal was not submitted" — the caller decides what to do.

Security:
  - The body HTML passed in must already be sanitized by the caller (format_note_html).
  - action_params stores no PII: body may contain a sanitized answer, but Aadhaar
    masking runs upstream in the webhook handler before query_text is used.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any

from case_engine.action_models import ActionProposal
from case_engine.action_state import ActionRiskLevel

LOGGER = logging.getLogger(__name__)


def propose_rag_note_action(
    *,
    case: Any,
    body_html: str,
    stack: Any,
    ticket_id: str,
    client: str,
) -> str | None:
    """
    Submit a SAFE add_note action proposal to the ActionGateway.

    The action auto-approves immediately (SAFE risk level). The background
    worker loop executes it via AddTicketNoteExecutor → FreshdeskProvider.

    Args:
        case:      Case object (or any object with case_id, ticket_id, client attrs).
                   Used to set the action's case correlation fields.
        body_html: Pre-formatted HTML note body (caller must sanitize before passing).
        stack:     ProductionRuntime (app.state.stack). Must have a .gateway attribute.
        ticket_id: Freshdesk ticket ID (string). Used for logging only; the gateway
                   reads ticket_id from the Case object.
        client:    Tenant slug. Used for logging only; read from Case by the gateway.

    Returns:
        action_id (str) on success or idempotent duplicate.
        None on any error (gateway not available, validation failure, etc.).

    Never raises.
    """
    if stack is None:
        LOGGER.warning(
            "action_proposer: stack is None — gateway not available ticket=%s", ticket_id
        )
        return None

    if not body_html or not body_html.strip():
        LOGGER.warning(
            "action_proposer: empty body_html — skipping proposal ticket=%s", ticket_id
        )
        return None

    proposal = ActionProposal(
        action_type="add_note",
        action_namespace="ticket",
        risk_level=ActionRiskLevel.SAFE,
        action_params={
            "body":    body_html,
            "private": True,
        },
        proposed_by="freshdesk_rag_pipeline",
    )

    try:
        from case_engine.action_gateway import DuplicateActionError  # noqa: PLC0415
        action = stack.gateway.propose(case, proposal)
        LOGGER.info(
            "action_proposer: proposed action_id=%s state=%s ticket=%s client=%s",
            action.action_id, action.current_state.value, ticket_id, client,
        )
        return action.action_id

    except Exception as exc:  # noqa: BLE001
        _cls = type(exc).__name__
        # Check without importing to avoid module-level circular risk
        if _cls == "DuplicateActionError":
            existing = getattr(exc, "existing", None)
            existing_id = getattr(existing, "action_id", "unknown")
            LOGGER.info(
                "action_proposer: duplicate proposal — reusing action_id=%s ticket=%s",
                existing_id, ticket_id,
            )
            return existing_id
        LOGGER.warning(
            "action_proposer: propose_failed ticket=%s client=%s error=%s(%s)",
            ticket_id, client, _cls, exc,
        )
        return None


def build_synthetic_case(ticket_id: str, client: str) -> Any:
    """
    Build a minimal Case object from ticket_id + client.

    Used when the CaseService is unavailable (offline mode or case engine error)
    but we still want to submit a gateway proposal for audit trail purposes.
    The case_id is deterministic (UUID v5) so the idempotency key is stable.
    """
    from case_engine.models import Case  # noqa: PLC0415
    case_id = str(
        uuid.uuid5(uuid.NAMESPACE_URL, f"rag-note-case:{client}:{ticket_id}")
    )
    return Case(case_id=case_id, ticket_id=ticket_id, client=client)
