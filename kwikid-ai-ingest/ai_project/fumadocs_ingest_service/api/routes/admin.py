"""
api/routes/admin.py

Sprint 2.11 B6: Admin endpoints — ADMIN role required.

Routes:
  GET /admin/dead-letter — paginated list of dead-lettered actions

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).
  Returns 401/403 for missing/insufficient credentials.

Response envelope for GET /admin/dead-letter:
  {
    "actions": [...],
    "total":   N,
    "limit":   N,
    "offset":  N,
    "client":  "<filter>" | null
  }

Each action in the list includes full action fields plus dead_lettered_at.

Metrics:
  Increments audit_events_read_total for each successful read.

Audit:
  Each admin read is logged as ACTION_AUDIT_READ event.

Security:
  - No secrets in action payloads (action_payload may contain PII — included
    since ADMIN has full data access, consistent with existing audit endpoints).
  - Stack traces never appear in error responses.
  - Request ID propagated from middleware.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from audit.models import AuditEvent, AuditEventType
from security.auth import AuthContext
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin")

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100


def _action_to_dict(action) -> dict:
    def _iso(dt):
        return dt.isoformat() if dt else None

    return {
        "action_id":              action.action_id,
        "case_id":                action.case_id,
        "ticket_id":              action.ticket_id,
        "client":                 action.client,
        "action_type":            action.action_type,
        "action_namespace":       action.action_namespace,
        "risk_level":             action.risk_level.value,
        "current_state":          action.current_state.value,
        "proposed_by":            action.proposed_by,
        "proposed_at":            _iso(action.proposed_at),
        "failure_code":           action.failure_code,
        "failure_reason":         action.failure_reason,
        "execution_attempt":      action.execution_attempt,
        "max_attempts":           action.max_attempts,
        "dead_lettered_at":       _iso(getattr(action, "dead_lettered_at", None)),
        "created_at":             _iso(getattr(action, "created_at", None)),
        "updated_at":             _iso(getattr(action, "updated_at", None)),
    }


@router.get("/dead-letter")
def list_dead_letter(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    client: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> JSONResponse:
    """
    Return a paginated list of dead-lettered actions.

    Requires ADMIN role.

    Query parameters:
      client — filter by client/tenant slug (optional)
      limit  — page size (default 100, max 500)
      offset — page offset (default 0)
    """
    limit = min(max(1, limit), _MAX_LIMIT)
    offset = max(0, offset)

    stack = request.app.state.stack
    if stack is None:
        return JSONResponse(
            status_code=503,
            content=error_body("SERVICE_UNAVAILABLE", "Runtime not initialised"),
        )

    try:
        client_filter = client.strip() or None
        actions = stack.repository.list_dead_letter_actions(client=client_filter)

        # Apply pagination in-process (repository may not support server-side pagination)
        total = len(actions)
        page = actions[offset: offset + limit]

        serialised = [_action_to_dict(a) for a in page]

        # Emit audit event for compliance
        _emit_audit(request, auth, client_filter)

        # Increment metrics
        _record_metric(request)

        return JSONResponse(
            status_code=200,
            content={
                "actions": serialised,
                "total":   total,
                "limit":   limit,
                "offset":  offset,
                "client":  client_filter,
            },
        )
    except Exception as exc:
        LOGGER.exception(
            "admin.list_dead_letter: client=%s error=%s",
            client,
            exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve dead-letter queue"),
        )


def _emit_audit(request: Request, auth: AuthContext, client_filter: str | None) -> None:
    try:
        audit_logger = getattr(request.app.state, "audit_logger", None)
        if audit_logger is None:
            return
        event = AuditEvent(
            action_id="admin-dead-letter-read",
            event_type=AuditEventType.ACTION_AUDIT_READ,
            actor=f"admin:{auth.identity}",
            case_id="",
            client=client_filter or "",
            metadata={"endpoint": "/admin/dead-letter", "client_filter": client_filter},
        )
        audit_logger.emit(event)
    except Exception:
        pass


def _record_metric(request: Request) -> None:
    try:
        ms = getattr(request.app.state, "metrics_service", None)
        if ms is not None:
            ms.record_audit_event_read()
    except Exception:
        pass
