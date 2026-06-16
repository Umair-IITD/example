"""
api/routes/reasoning_admin.py

Sprint 2.24: POST /admin/reasoning/run — admin endpoint to exercise the
Investigation Reasoning Engine directly.

Security:
  - Requires admin role via Depends(require_admin).
  - Returns structured error JSON — never exposes raw tracebacks.

Notes:
  - Reads ReasoningService from app.state.reasoning_service.
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


class ReasoningRunRequest(BaseModel):
    investigation_result: dict[str, Any]
    knowledge_result:     dict[str, Any] | None = None
    workflow_id:          str | None = None
    step_id:              str | None = None


@router.post("/admin/reasoning/run")
async def run_reasoning(
    request: Request,
    body:    ReasoningRunRequest,
    _admin:  Any = Depends(require_admin),
) -> JSONResponse:
    """
    Invoke the Investigation Reasoning Engine directly.

    Returns the full ReasoningResult bundle as JSON.
    """
    svc = getattr(getattr(request, "app", None), "state", None)
    svc = getattr(svc, "reasoning_service", None) if svc else None

    if svc is None:
        return JSONResponse(
            status_code=503,
            content=error_body("SERVICE_UNAVAILABLE", "reasoning_service not initialised"),
        )

    try:
        result = svc.reason(
            investigation_result=body.investigation_result,
            knowledge_result=body.knowledge_result,
            workflow_id=body.workflow_id or "",
            step_id=body.step_id or "",
        )
        # Flatten top-level fields for easy inspection
        return JSONResponse(
            status_code=200,
            content={
                "status":             result.get("status", "COMPLETED"),
                "result_id":          result.get("result_id", ""),
                "outcome":            result.get("outcome", ""),
                "recommended_action": result.get("recommended_action", ""),
                "should_escalate":    result.get("should_escalate", False),
                "confidence":         result.get("confidence", 0.0),
                "result":             result,
            },
        )
    except Exception as exc:
        LOGGER.exception("admin/reasoning/run failed: %s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "reasoning run failed"),
        )
