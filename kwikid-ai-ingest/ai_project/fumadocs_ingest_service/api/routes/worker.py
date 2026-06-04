"""
api/routes/worker.py

POST /worker/tick — trigger one worker processing cycle.

Protected by: OPERATOR or ADMIN role (require_operator dependency).

Calls worker.process(client) which runs:
  1. tick_rollbacks(client) — execute ROLLING_BACK originals
  2. tick(client)           — execute APPROVED forward actions

Query params:
  client  (required) — the tenant slug to process

Response:
  {
    "client": "...",
    "rollbacks": {"processed": N, "success": N, "failed": N},
    "forward":   {"processed": N, "success": N, "failed": N},
    "total_processed": N
  }
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from security.auth import AuthContext
from security.dependencies import require_operator

LOGGER = logging.getLogger(__name__)

router = APIRouter()


@router.post("/worker/tick")
def trigger_worker_tick(
    request: Request,
    client: str = "",
    auth: AuthContext = Depends(require_operator),
) -> JSONResponse:
    """
    Trigger one worker processing cycle for the given client.

    Protected: requires OPERATOR or ADMIN role.
    The `client` query parameter is required.
    """
    if not client:
        return JSONResponse(
            status_code=400,
            content=error_body(
                "MISSING_PARAMETER",
                "Query parameter 'client' is required",
            ),
        )

    stack = request.app.state.stack

    try:
        rollback_result, forward_result = stack.worker.process(client)
        LOGGER.info(
            "worker.tick: actor=%s client=%s rollbacks(processed=%d success=%d) "
            "forward(processed=%d success=%d)",
            auth.identity, client,
            rollback_result.processed, rollback_result.success_count,
            forward_result.processed, forward_result.success_count,
        )
        return JSONResponse(
            status_code=200,
            content={
                "client": client,
                "actor": auth.identity,
                "rollbacks": {
                    "processed": rollback_result.processed,
                    "success":   rollback_result.success_count,
                    "failed":    rollback_result.failure_count,
                },
                "forward": {
                    "processed": forward_result.processed,
                    "success":   forward_result.success_count,
                    "failed":    forward_result.failure_count,
                },
                "total_processed": rollback_result.processed + forward_result.processed,
            },
        )
    except Exception as exc:
        LOGGER.exception("worker.tick: unexpected error client=%s error=%s", client, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Worker tick failed unexpectedly"),
        )
