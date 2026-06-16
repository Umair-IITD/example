"""
api/routes/execution_admin.py

Sprint 2.23: Admin endpoint to manually trigger the Execution Layer.

Routes:
  POST /admin/executions/run
      Execute a full EXECUTE --> VERIFY --> RECOVERY --> RESOLUTION pipeline
      for a given action_type and params, without requiring a live Case or
      inbound Freshdesk ticket.

Authorization:
  Requires ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Never 500 -- all errors produce a structured JSON response.
  - Stack traces never appear in response bodies.
  - No external API calls -- uses MockExecutionAdapter.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/executions", tags=["Execution Admin"])


# ── Request / Response models ──────────────────────────────────────────────────

class ExecutionRunRequest(BaseModel):
    """Body for POST /admin/executions/run."""
    action_type:      str                  = Field(..., description="Action type to execute")
    action_params:    dict[str, str]       = Field(default_factory=dict, description="Action parameters")
    action_namespace: str                  = Field(default="", description="Action namespace")
    risk_level:       str                  = Field(default="SAFE", description="Risk level: SAFE | REVERSIBLE | HIGH_RISK")
    case_id:          str | None           = Field(None, description="Optional case_id for audit metadata")
    workflow_id:      str | None           = Field(None, description="Optional workflow_id for audit metadata")
    step_id:          str | None           = Field(None, description="Optional step_id for audit metadata")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_execution_service(request: Request) -> Any:
    return getattr(request.app.state, "execution_service", None)


def _unavailable(component: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body(
            "SERVICE_UNAVAILABLE",
            f"{component} not initialised -- check startup logs",
        ),
    )


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/run", dependencies=[Depends(require_admin)])
async def run_execution(
    body: ExecutionRunRequest,
    request: Request,
) -> JSONResponse:
    """
    Manually run a full execution pipeline for a given action_type and params.

    Returns the complete ExecutionBundle including:
      - Execution result (adapter response)
      - Verification result (VERIFIED_SUCCESS / VERIFIED_FAILURE / UNCERTAIN)
      - Recovery result (if verification failed)
      - Final execution status
    """
    svc = _get_execution_service(request)
    if svc is None:
        return _unavailable("ExecutionService")

    LOGGER.info(
        "execution_admin.run action_type=%s namespace=%s risk=%s case_id=%s",
        body.action_type,
        body.action_namespace,
        body.risk_level,
        body.case_id,
    )

    try:
        bundle = svc.process(
            action_type=body.action_type,
            action_params=dict(body.action_params),
            case_id=body.case_id or "",
            action_namespace=body.action_namespace,
            risk_level=body.risk_level,
            audit=None,
            case=None,
            workflow_id=body.workflow_id,
            step_id=body.step_id,
        )
    except Exception:  # noqa: BLE001
        LOGGER.exception("execution_admin.run.unexpected_error action_type=%s", body.action_type)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Execution pipeline encountered an unexpected error"),
        )

    return JSONResponse(
        status_code=200,
        content={
            "status":        "ok",
            "bundle_id":     bundle.bundle_id,
            "final_status":  bundle.final_status.value,
            "total_attempts": bundle.total_attempts,
            "action_type":   bundle.action_type,
            "result":        bundle.to_dict(),
        },
    )
