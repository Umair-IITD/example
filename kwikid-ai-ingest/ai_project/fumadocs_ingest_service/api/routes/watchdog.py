"""
api/routes/watchdog.py

POST /watchdog/run — trigger one SLA watchdog sweep.

Protected by: OPERATOR or ADMIN role (require_watchdog dependency).

Calls SLAWatchdog.run(client) which:
  1. Fetches AWAITING_APPROVAL actions whose expires_at is in the past
  2. Transitions each to EXPIRED via ActionGateway.expire()
  3. Returns counts

Query params:
  client  (optional) — tenant slug. If omitted, sweeps all clients.

Response:
  {
    "expired_actions": N,
    "processed": N,
    "errors": N
  }

Idempotent: running multiple times on the same set of actions is safe.
Already-EXPIRED actions are terminal and cannot be transitioned again.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from audit.models import AuditEvent, AuditEventType
from security.auth import AuthContext
from security.dependencies import require_watchdog

LOGGER = logging.getLogger(__name__)

router = APIRouter()


@router.post("/watchdog/run")
def run_watchdog(
    request: Request,
    client: str = "",
    auth: AuthContext = Depends(require_watchdog),
) -> JSONResponse:
    """
    Trigger one SLA watchdog sweep.

    Transitions all AWAITING_APPROVAL actions with elapsed SLA to EXPIRED.
    Protected: requires OPERATOR or ADMIN role.
    """
    stack = request.app.state.stack
    audit_logger = request.app.state.audit_logger

    client_filter = client.strip() or None

    try:
        result = stack.watchdog.run(client=client_filter)

        # Emit audit events for the sweep
        if audit_logger is not None:
            # Fetch the expired actions to emit individual audit events
            # We emit a summary event here; individual expiry events are emitted
            # from within the watchdog via gateway.expire()
            _emit_expiry_audit(audit_logger, result, auth, client_filter)

        LOGGER.info(
            "watchdog.run: actor=%s client=%s expired=%d processed=%d",
            auth.identity, client_filter or "all", result.expired_actions, result.processed,
        )

        return JSONResponse(
            status_code=200,
            content={
                "expired_actions": result.expired_actions,
                "processed":       result.processed,
                "errors":          result.errors,
                "client":          client_filter or "all",
                "actor":           auth.identity,
            },
        )
    except Exception as exc:
        LOGGER.exception("watchdog.run: unexpected error client=%s error=%s", client_filter, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Watchdog run failed unexpectedly"),
        )


def _emit_expiry_audit(audit_logger, result, auth: AuthContext, client: str | None) -> None:
    """Emit a summary audit event for the watchdog sweep."""
    try:
        if result.expired_actions > 0:
            audit_logger.emit(AuditEvent(
                action_id="WATCHDOG_SWEEP",
                event_type=AuditEventType.ACTION_EXPIRED,
                actor=f"watchdog:{auth.identity}",
                metadata={
                    "expired_count": result.expired_actions,
                    "processed":     result.processed,
                    "client":        client or "all",
                },
            ))
    except Exception:
        pass  # Audit failure must never affect the main flow
