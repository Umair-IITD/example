"""
api/routes/tickets.py

Sprint 2.27.8: Golden Path — Ticket lifecycle API.

This is THE production integration path:
  POST /tickets/process   → TicketOrchestrator.process_ticket()
  POST /tickets/{id}/resume    → TicketOrchestrator.resume_ticket()
  POST /tickets/{id}/close     → TicketOrchestrator.close_ticket()
  POST /tickets/{id}/escalate  → TicketOrchestrator.escalate_ticket()
  GET  /tickets/{id}/status    → TicketOrchestrator.get_status()

Authentication: all endpoints require operator role (X-API-Key header).

Design:
  - Each handler retrieves TicketOrchestrator from app.state
  - Returns 503 if orchestrator is not available
  - Returns 200 with orchestration result dict on success
  - Returns 4xx/5xx on errors
  - Never raises unhandled exceptions

Security:
  - Auth enforced via require_operator (RBAC)
  - No secrets logged or returned
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.error_models import error_body
from security.dependencies import require_operator

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/tickets")


# ── Request models ────────────────────────────────────────────────────────────

class ProcessTicketRequest(BaseModel):
    ticket_id:       str
    client:          str
    subject:         str
    description:     str
    requester_email: str = ""
    freshdesk_url:   str = ""
    metadata:        dict = {}


class ResumeTicketRequest(BaseModel):
    message_text: str


class EscalateTicketRequest(BaseModel):
    reason: str = ""


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_orchestrator(request: Request):
    """Extract TicketOrchestrator from app.state. Returns None if not wired."""
    try:
        return request.app.state.ticket_orchestrator
    except AttributeError:
        return None


def _orchestrator_unavailable(ticket_id: str = "") -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body(
            "SERVICE_UNAVAILABLE",
            "TicketOrchestrator not available — runtime assembly failed or incomplete",
        ),
    )


# ── Golden Path: POST /tickets/process ───────────────────────────────────────

@router.post("/process")
async def process_ticket(
    body: ProcessTicketRequest,
    request: Request,
    _auth=Depends(require_operator),
) -> JSONResponse:
    """
    GOLDEN PATH: Process an inbound Freshdesk ticket through the full
    support agent pipeline.

    Flow:
      TicketContext → TicketOrchestrator.process_ticket()
      → SupportAgentRuntime.run_case()
      → [CLASSIFY → SLOT_EXTRACT → CLARIFY → WORKFLOW → NOTEGEN → L2CHECK → ASANACREATE → USERRESPONSE]
      → TicketOrchestrationResult
    """
    orchestrator = _get_orchestrator(request)
    if orchestrator is None:
        return _orchestrator_unavailable(body.ticket_id)

    try:
        from case_engine.ticket_orchestration import TicketContext  # noqa: PLC0415
        ctx = TicketContext(
            ticket_id=body.ticket_id,
            client=body.client,
            subject=body.subject,
            description=body.description,
            requester_email=body.requester_email,
            freshdesk_url=body.freshdesk_url,
            metadata=body.metadata,
        )
        result = orchestrator.process_ticket(ctx)
        LOGGER.info(
            "tickets.process ticket_id=%s state=%s success=%s",
            body.ticket_id, result.lifecycle_state.value, result.success,
        )
        return JSONResponse(status_code=200, content=result.to_dict())
    except Exception as exc:
        LOGGER.exception("tickets.process failed ticket_id=%s error=%s", body.ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("PROCESSING_ERROR", "Ticket processing failed unexpectedly"),
        )


# ── POST /tickets/{ticket_id}/resume ─────────────────────────────────────────

@router.post("/{ticket_id}/resume")
async def resume_ticket(
    ticket_id: str,
    body: ResumeTicketRequest,
    request: Request,
    _auth=Depends(require_operator),
) -> JSONResponse:
    """
    Resume a WAITING ticket after a customer reply or clarification.

    Typically called when Freshdesk forwards a new message on an existing ticket.
    """
    orchestrator = _get_orchestrator(request)
    if orchestrator is None:
        return _orchestrator_unavailable(ticket_id)

    try:
        result = orchestrator.resume_ticket(
            ticket_id=ticket_id,
            message_text=body.message_text,
        )
        LOGGER.info(
            "tickets.resume ticket_id=%s state=%s success=%s",
            ticket_id, result.lifecycle_state.value, result.success,
        )
        return JSONResponse(status_code=200, content=result.to_dict())
    except Exception as exc:
        LOGGER.exception("tickets.resume failed ticket_id=%s error=%s", ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("RESUME_ERROR", "Ticket resume failed unexpectedly"),
        )


# ── POST /tickets/{ticket_id}/close ──────────────────────────────────────────

@router.post("/{ticket_id}/close")
async def close_ticket(
    ticket_id: str,
    request: Request,
    _auth=Depends(require_operator),
) -> JSONResponse:
    """
    Close a ticket that has been resolved.
    """
    orchestrator = _get_orchestrator(request)
    if orchestrator is None:
        return _orchestrator_unavailable(ticket_id)

    try:
        result = orchestrator.close_ticket(ticket_id=ticket_id)
        LOGGER.info(
            "tickets.close ticket_id=%s state=%s success=%s",
            ticket_id, result.lifecycle_state.value, result.success,
        )
        return JSONResponse(status_code=200, content=result.to_dict())
    except Exception as exc:
        LOGGER.exception("tickets.close failed ticket_id=%s error=%s", ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("CLOSE_ERROR", "Ticket close failed unexpectedly"),
        )


# ── POST /tickets/{ticket_id}/escalate ───────────────────────────────────────

@router.post("/{ticket_id}/escalate")
async def escalate_ticket(
    ticket_id: str,
    body: EscalateTicketRequest,
    request: Request,
    _auth=Depends(require_operator),
) -> JSONResponse:
    """
    Force-escalate a ticket to L2/engineering.

    This endpoint always succeeds (creates a registry entry if needed).
    """
    orchestrator = _get_orchestrator(request)
    if orchestrator is None:
        return _orchestrator_unavailable(ticket_id)

    try:
        result = orchestrator.escalate_ticket(
            ticket_id=ticket_id,
            reason=body.reason,
        )
        LOGGER.info(
            "tickets.escalate ticket_id=%s state=%s success=%s",
            ticket_id, result.lifecycle_state.value, result.success,
        )
        return JSONResponse(status_code=200, content=result.to_dict())
    except Exception as exc:
        LOGGER.exception("tickets.escalate failed ticket_id=%s error=%s", ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("ESCALATE_ERROR", "Ticket escalation failed unexpectedly"),
        )


# ── GET /tickets/{ticket_id}/status ──────────────────────────────────────────

@router.get("/{ticket_id}/status")
async def get_ticket_status(
    ticket_id: str,
    request: Request,
    _auth=Depends(require_operator),
) -> JSONResponse:
    """
    Get the current orchestration status for a ticket.
    """
    orchestrator = _get_orchestrator(request)
    if orchestrator is None:
        return _orchestrator_unavailable(ticket_id)

    try:
        lifecycle_state = orchestrator.get_lifecycle_state(ticket_id=ticket_id)
        if lifecycle_state is None:
            return JSONResponse(
                status_code=404,
                content=error_body("TICKET_NOT_FOUND", f"Ticket {ticket_id!r} not found in orchestrator registry"),
            )
        case_id = orchestrator.get_case_id(ticket_id=ticket_id)
        return JSONResponse(status_code=200, content={
            "ticket_id":       ticket_id,
            "lifecycle_state": lifecycle_state.value,
            "case_id":         case_id,
        })
    except Exception as exc:
        LOGGER.exception("tickets.status failed ticket_id=%s error=%s", ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("STATUS_ERROR", "Ticket status lookup failed unexpectedly"),
        )
