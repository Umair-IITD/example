"""
api/routes/tool_admin.py

Sprint 2.17: Admin visibility endpoints for the investigation tool layer.

Routes (all under /admin/tools/):

  GET /admin/tools/
      List all registered investigation tools with their definitions.

  GET /admin/tools/{tool_name}
      Return full definition for a single tool.

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Read-only: no tool execution via these endpoints.
  - Never 500 on empty tool registry — return empty list with 200.
  - Stack traces never appear in error responses.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/tools", tags=["Tool Admin"])


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_tool_registry(request: Request) -> Any:
    return getattr(request.app.state, "tool_registry", None)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("SERVICE_UNAVAILABLE", "Tool registry not initialised"),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/")
async def list_tools(
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return all registered investigation tools with their full definitions.

    Use this to understand what tools are available for a given topic and
    what inputs they require.
    """
    registry = _get_tool_registry(request)
    if registry is None:
        return _unavailable()

    try:
        definitions = registry.list_tools()
        return JSONResponse(
            status_code=200,
            content={
                "total": len(definitions),
                "tools": [d.to_dict() for d in definitions],
            },
        )
    except Exception as exc:
        LOGGER.exception("list_tools failed error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to list tools"),
        )


@router.get("/{tool_name}")
async def get_tool(
    tool_name: str,
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return the full definition for a single investigation tool.

    Includes required_inputs, output_schema, version, and tags.
    """
    registry = _get_tool_registry(request)
    if registry is None:
        return _unavailable()

    try:
        tool = registry.get(tool_name)
        if tool is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"No tool registered with name '{tool_name}'"),
            )
        return JSONResponse(status_code=200, content=tool.definition.to_dict())
    except Exception as exc:
        LOGGER.exception("get_tool failed tool_name=%s error=%s", tool_name, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve tool definition"),
        )
