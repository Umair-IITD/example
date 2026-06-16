"""
api/routes/adapter_admin.py

Sprint 2.27: Admin endpoints for the Production Adapter Framework.

Routes:
  POST /admin/adapters/run
      Dispatch a single AdapterRequest through the AdapterRouter and return
      the AdapterExecutionResult. Admin auth required.

  GET /admin/adapters/health
      Return aggregate health status for all registered adapters.

Authorization:
  Requires ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Never 500 — all errors produce structured JSON.
  - Stack traces never appear in response bodies.
  - adapter_router read from request.app.state.adapter_router.
  - 503 if adapter_router not initialised.
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

router = APIRouter(prefix="/admin/adapters", tags=["Adapter Admin"])


# ── Request / Response models ──────────────────────────────────────────────────

class AdapterRunRequest(BaseModel):
    """Body for POST /admin/adapters/run."""
    action_type:   str            = Field(..., description="Action type to route (e.g. otp_resend)")
    action_params: dict[str, Any] = Field(default_factory=dict, description="Action payload")
    case_id:       str            = Field(default="", description="Case ID for audit metadata")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_adapter_router(request: Request) -> Any:
    return getattr(request.app.state, "adapter_router", None)


def _unavailable(component: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body(
            "SERVICE_UNAVAILABLE",
            f"{component} not initialised — check startup logs",
        ),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.post("/run", dependencies=[Depends(require_admin)])
async def run_adapter(
    body: AdapterRunRequest,
    request: Request,
) -> JSONResponse:
    """
    Manually dispatch an action through the AdapterRouter.

    Translates action_type → (AdapterType, AdapterOperation) via the routing
    table in AdapterBackedExecutionAdapter, builds an AdapterRequest, and routes
    it to the registered adapter. Returns AdapterExecutionResult.
    """
    adapter_router = _get_adapter_router(request)
    if adapter_router is None:
        return _unavailable("AdapterRouter")

    LOGGER.info(
        "adapter_admin.run action_type=%s case_id=%s",
        body.action_type, body.case_id,
    )

    try:
        from case_engine.adapters.execution_adapter import get_adapter_for_action  # noqa: PLC0415
        from case_engine.adapters.models import AdapterRequest                      # noqa: PLC0415

        adapter_type, operation = get_adapter_for_action(body.action_type)

        req = AdapterRequest.create(
            adapter_type=adapter_type,
            operation=operation,
            payload=dict(body.action_params),
            case_id=body.case_id,
            action_type=body.action_type,
        )

        result = adapter_router.route(req)

        return JSONResponse(
            status_code=200,
            content={
                "status":        "ok",
                "request_id":    result.request.request_id,
                "adapter_type":  result.response.adapter_type.value,
                "operation":     result.response.operation.value,
                "adapter_name":  result.adapter_name,
                "adapter_status": result.response.status.value,
                "success":       result.success,
                "retryable":     result.retryable,
                "duration_ms":   result.response.duration_ms,
                "data":          result.response.data,
                "error_code":    result.response.error_code,
                "error_message": result.response.error_message,
            },
        )

    except Exception:
        LOGGER.exception(
            "adapter_admin.run.unexpected_error action_type=%s", body.action_type
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Adapter dispatch encountered an unexpected error"),
        )


@router.get("/health", dependencies=[Depends(require_admin)])
async def adapter_health(request: Request) -> JSONResponse:
    """
    Return aggregate health status for all registered adapters.

    Queries each adapter's health_check() and returns a summary.
    """
    adapter_router = _get_adapter_router(request)
    if adapter_router is None:
        return _unavailable("AdapterRouter")

    try:
        summary = adapter_router.health_summary()
        status_code = 200 if summary.get("overall_healthy", False) else 503
        return JSONResponse(status_code=status_code, content={"status": "ok", **summary})
    except Exception:
        LOGGER.exception("adapter_admin.health.unexpected_error")
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Health check encountered an unexpected error"),
        )
