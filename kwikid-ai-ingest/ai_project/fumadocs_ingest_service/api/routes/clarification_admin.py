"""
api/routes/clarification_admin.py

Sprint 2.25: POST /admin/clarification/run — admin endpoint to exercise the
Clarification Layer directly without a live case.

Security:
  - Requires admin role via Depends(require_admin).
  - Returns structured error JSON — never exposes raw tracebacks.

Notes:
  - Reads ClarificationService from app.state.clarification_service.
  - Returns 503 if service not initialized.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter()


class ClarificationRunRequest(BaseModel):
    topic:          str
    required_slots: list[str]
    slot_context:   dict[str, Any] | None = None
    slot_state:     dict[str, Any] | None = None
    workflow_id:    str | None = None
    step_id:        str | None = None


@router.post("/admin/clarification/run")
async def run_clarification(
    request: Request,
    body:    ClarificationRunRequest,
    _admin:  Any = Depends(require_admin),
) -> JSONResponse:
    """
    Invoke the Clarification Layer directly.

    Returns the full ClarificationResult bundle as JSON.
    """
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "clarification_service", None) if svc else None

    if svc is None:
        return JSONResponse(
            status_code=503,
            content=error_body("SERVICE_UNAVAILABLE", "clarification_service not initialised"),
        )

    try:
        result = svc.clarify(
            topic=body.topic,
            slot_context=body.slot_context or {},
            required_slots=body.required_slots,
            slot_state=body.slot_state or {},
            case=None,
            workflow_id=body.workflow_id or "",
            step_id=body.step_id or "",
        )
        return JSONResponse(
            status_code=200,
            content={
                "status":               result.get("status", "READY"),
                "result_id":            result.get("result_id", ""),
                "missing_slots":        result.get("missing_slots", []),
                "clarification_message": result.get("clarification_message", ""),
                "ready_to_continue":    result.get("ready_to_continue", True),
                "next_question":        result.get("next_question"),
                "result":               result,
            },
        )
    except Exception as exc:
        LOGGER.exception("admin/clarification/run failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "clarification run failed"),
        )
