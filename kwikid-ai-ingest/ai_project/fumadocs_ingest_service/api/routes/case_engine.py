"""
api/routes/case_engine.py

Sprint 2.15: Case Engine API — foundation endpoints for the Case Intelligence Layer.

Endpoints:
  POST /cases                      — open a new case (or return existing for the ticket)
  GET  /cases/{case_id}            — retrieve case state and metadata
  POST /cases/{case_id}/message    — process an incoming message (slot filling)
  GET  /cases/{case_id}/slots      — retrieve current slot filling status

Design constraints:
  - All endpoints are synchronous via asyncio.to_thread (case engine is sync).
  - 503 if CaseService is not yet initialised.
  - No LLM calls in this layer.
  - No Action Gateway calls — foundation only.
  - PII is masked before entering LLM context (not yet applicable at foundation level).
"""
from __future__ import annotations

import asyncio
import logging
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.error_models import error_body
from case_engine.case_state import CaseState
from case_engine.clarification_engine import ClarificationEngine
from case_engine.models import TopicKey
from case_engine.workflows.models import WorkflowState

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/cases", tags=["Case Engine"])


# ── Pydantic request/response models ──────────────────────────────────────────

class CreateCaseRequest(BaseModel):
    ticket_id:       str  = Field(..., min_length=1, max_length=256)
    client:          str  = Field(..., min_length=1, max_length=128)
    initial_message: str | None = Field(default=None, max_length=4096)


class MessageRequest(BaseModel):
    message_text:   str        = Field(..., min_length=0, max_length=4096)
    slot_name:      str | None = Field(default=None, max_length=128)
    slot_value:     str | None = Field(default=None, max_length=512)


class WorkflowResumeRequest(BaseModel):
    action_id: str = Field(..., min_length=1, max_length=256)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_case_service(request: Request):
    return getattr(request.app.state, "case_service", None)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("SERVICE_UNAVAILABLE", "Case engine not initialised"),
    )


def _case_to_dict(case) -> dict[str, Any]:
    return {
        "case_id":       case.case_id,
        "ticket_id":     case.ticket_id,
        "client":        case.client,
        "state":         case.current_state.value,
        "topic":         case.topic,
        "confidence":    case.confidence,
        "created_at":    case.created_at.isoformat(),
        "updated_at":    case.updated_at.isoformat(),
        "closed_at":     case.closed_at.isoformat() if case.closed_at else None,
        "sla_breach_at": case.sla_breach_at.isoformat() if case.sla_breach_at else None,
        "slot_state":    case.slot_state or {},
        "escalation_reason": case.escalation_reason,
        "failure_code":  case.failure_code,
    }


def _slot_state_summary(case, case_service) -> list[dict[str, Any]]:
    """Build the /slots response from case.slot_state and the topic registry."""
    topic_str = case.topic
    try:
        topic = TopicKey(topic_str) if topic_str else None
    except ValueError:
        topic = None

    slot_values = case_service.get_slot_state(case)

    if topic is None or topic == TopicKey.UNKNOWN:
        return [
            {
                "slot_name": name,
                "is_required": None,
                **sv.to_dict(),
            }
            for name, sv in slot_values.items()
        ]

    from case_engine.topic_registry import get_registry
    registry = get_registry(topic)
    if registry is None:
        return []

    result = []
    for slot_def in registry.all_definitions():
        sv = slot_values.get(slot_def.name)
        result.append({
            "slot_name":   slot_def.name,
            "description": slot_def.description,
            "is_required": slot_def.required,
            "status":      sv.status.value if sv else "EMPTY",
            "value":       sv.value if sv else None,
            "attempt_count": sv.attempt_count if sv else 0,
            "valid_values": sorted(slot_def.valid_values) if slot_def.valid_values else None,
        })
    return result


# ── Endpoints ─────────────────────────────────────────────────────────────────

@router.post("", status_code=201)
async def create_case(body: CreateCaseRequest, request: Request) -> JSONResponse:
    """
    Open a new case for the given ticket and client.

    If a case already exists for (ticket_id, client), returns the existing case.
    Optionally classifies the initial_message if provided.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.open_case, body.ticket_id, body.client)
        if body.initial_message and case.current_state == CaseState.NEW:
            case = await asyncio.to_thread(cs.classify_case, case, body.initial_message)

        status = 200 if case.current_state != CaseState.NEW else 201
        return JSONResponse(status_code=status, content=_case_to_dict(case))

    except Exception as exc:
        LOGGER.exception("create_case failed ticket=%s error=%s", body.ticket_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to create case"),
        )


@router.get("/{case_id}")
async def get_case(case_id: str, request: Request) -> JSONResponse:
    """Retrieve the full case record for the given case_id."""
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.get_case, case_id)
        if case is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Case {case_id} not found"),
            )
        return JSONResponse(status_code=200, content=_case_to_dict(case))

    except Exception as exc:
        LOGGER.exception("get_case failed case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve case"),
        )


@router.post("/{case_id}/message")
async def send_message(case_id: str, body: MessageRequest, request: Request) -> JSONResponse:
    """
    Process an incoming message for a case.

    The message is used for slot filling:
    - If slot_name + slot_value are provided, accepts that slot explicitly.
    - Otherwise, attempts deterministic extraction from message_text (enum slots only).

    Returns the updated slot state and, if slots are still missing, the next
    clarification question to ask the caller.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.get_case, case_id)
        if case is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Case {case_id} not found"),
            )

        result = await asyncio.to_thread(
            cs.receive_message,
            case,
            body.message_text,
            slot_name=body.slot_name,
            slot_value_str=body.slot_value,
        )

        return JSONResponse(
            status_code=200,
            content={
                "case_id":          result.case_id,
                "state":            result.state.value,
                "slot_values":      result.slot_values,
                "next_question":    result.next_question,
                "all_slots_filled": result.all_slots_filled,
                "escalated":        result.escalated,
            },
        )

    except Exception as exc:
        LOGGER.exception("send_message failed case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to process message"),
        )


@router.get("/{case_id}/slots")
async def get_slots(case_id: str, request: Request) -> JSONResponse:
    """
    Return the current slot filling status for a case.

    Lists all required and optional slots for the case's topic, with their
    current fill status, value, and attempt count.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.get_case, case_id)
        if case is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Case {case_id} not found"),
            )

        slots = _slot_state_summary(case, cs)
        return JSONResponse(
            status_code=200,
            content={
                "case_id": case.case_id,
                "topic":   case.topic,
                "state":   case.current_state.value,
                "slots":   slots,
            },
        )

    except Exception as exc:
        LOGGER.exception("get_slots failed case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve slot state"),
        )


@router.get("/{case_id}/workflow")
async def get_workflow(case_id: str, request: Request) -> JSONResponse:
    """
    Return the current workflow execution state for a case.

    Includes workflow_id, workflow_state, current_step_id, pending_action_id,
    step_results, and completion details.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.get_case, case_id)
        if case is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Case {case_id} not found"),
            )

        workflow_ctx = case.workflow_context or {}
        return JSONResponse(
            status_code=200,
            content={
                "case_id":           case.case_id,
                "state":             case.current_state.value,
                "workflow_id":       case.workflow_id,
                "workflow_state":    case.workflow_state,
                "workflow_step_index": case.workflow_step_index,
                "workflow_context":  workflow_ctx,
            },
        )

    except Exception as exc:
        LOGGER.exception("get_workflow failed case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve workflow state"),
        )


@router.post("/{case_id}/workflow/resume")
async def resume_workflow(case_id: str, body: WorkflowResumeRequest, request: Request) -> JSONResponse:
    """
    Resume a paused workflow after its pending action has completed.

    The caller provides the action_id of the completed action. The workflow
    engine reads the action state from the Action Gateway and advances to
    the next step.

    Expected case state: ACTION_PENDING.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        case = await asyncio.to_thread(cs.get_case, case_id)
        if case is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Case {case_id} not found"),
            )

        # Load the action request from the action gateway stack
        action = None
        if hasattr(request.app.state, "stack") and request.app.state.stack is not None:
            try:
                action = await asyncio.to_thread(
                    request.app.state.stack.gateway._repo.get_action, body.action_id
                )
            except Exception:
                pass

        if action is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"Action {body.action_id} not found"),
            )

        slot_values = await asyncio.to_thread(cs.get_slot_state, case)
        result = await asyncio.to_thread(cs.resume_workflow, case, action, slot_values)

        return JSONResponse(
            status_code=200,
            content={
                "case_id":           result.case_id,
                "state":             result.state.value,
                "workflow_id":       result.workflow_id,
                "workflow_state":    result.workflow_state,
                "current_step_id":   result.current_step_id,
                "pending_action_id": result.pending_action_id,
                "resolved":          result.resolved,
                "escalated":         result.escalated,
                "step_results":      result.step_results,
                "resolution_note":   result.resolution_note,
                "escalation_reason": result.escalation_reason,
            },
        )

    except Exception as exc:
        LOGGER.exception("resume_workflow failed case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to resume workflow"),
        )
