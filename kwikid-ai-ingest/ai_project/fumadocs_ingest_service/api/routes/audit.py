"""
api/routes/audit.py

Sprint 2.9: Audit query API — ADMIN only.

Endpoints:
  GET /audit/actions/{action_id}  — full event history for one action
  GET /audit/cases/{case_id}      — full event history for one case
  GET /audit/clients/{client}     — paginated events for one client
  GET /audit/events               — global search with filters and pagination

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).
  APPROVER and OPERATOR roles receive 403.

Query parameters for GET /audit/events:
  event_type  — filter by AuditEventType value (e.g. ACTION_APPROVED)
  client      — filter by client/tenant slug
  action_id   — filter by action_id
  case_id     — filter by case_id
  limit       — page size (default 100, max 500)
  offset      — page start (default 0)

Response envelope:
  {
    "events": [...],
    "total":  N,
    "limit":  N,
    "offset": N
  }

Each event:
  {
    "event_id":   "...",
    "action_id":  "...",
    "case_id":    "...",
    "client":     "...",
    "event_type": "ACTION_APPROVED",
    "actor":      "human:alice",
    "timestamp":  "2026-06-03T12:34:56.789012+00:00",
    "metadata":   {...}
  }

Security:
  - No secrets in event metadata.
  - No stack traces in error responses.
  - 401 / 403 use the standard error envelope.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from audit.models import AuditEventType
from security.auth import AuthContext
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter()

_MAX_LIMIT = 500
_DEFAULT_LIMIT = 100


def _audit_service(request: Request):
    return request.app.state.audit_service


def _serialise_events(events) -> list[dict]:
    return [e.to_dict() for e in events]


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/audit/actions/{action_id}")
def get_action_audit(
    action_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
) -> JSONResponse:
    """
    Return the complete audit history for a single action.

    Requires ADMIN role.
    """
    svc = _audit_service(request)
    try:
        events = svc.get_action_history(action_id)
        serialised = _serialise_events(events)
        return JSONResponse(
            status_code=200,
            content={
                "events": serialised,
                "total":  len(serialised),
                "action_id": action_id,
            },
        )
    except Exception as exc:
        LOGGER.exception("audit.get_action_audit: action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve audit events"),
        )


@router.get("/audit/cases/{case_id}")
def get_case_audit(
    case_id: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
) -> JSONResponse:
    """
    Return the complete audit history for a case (all actions in the case).

    Requires ADMIN role.
    """
    svc = _audit_service(request)
    try:
        events = svc.get_case_history(case_id)
        serialised = _serialise_events(events)
        return JSONResponse(
            status_code=200,
            content={
                "events": serialised,
                "total":  len(serialised),
                "case_id": case_id,
            },
        )
    except Exception as exc:
        LOGGER.exception("audit.get_case_audit: case_id=%s error=%s", case_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve audit events"),
        )


@router.get("/audit/clients/{client}")
def get_client_audit(
    client: str,
    request: Request,
    auth: AuthContext = Depends(require_admin),
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> JSONResponse:
    """
    Return paginated audit events for a tenant/client.

    Requires ADMIN role.
    """
    svc = _audit_service(request)
    limit = min(max(1, limit), _MAX_LIMIT)
    offset = max(0, offset)
    try:
        events = svc.get_client_history(client, limit=limit, offset=offset)
        total = svc.count(client=client)
        serialised = _serialise_events(events)
        return JSONResponse(
            status_code=200,
            content={
                "events": serialised,
                "total":  total,
                "limit":  limit,
                "offset": offset,
                "client": client,
            },
        )
    except Exception as exc:
        LOGGER.exception("audit.get_client_audit: client=%s error=%s", client, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve audit events"),
        )


@router.get("/audit/events")
def list_audit_events(
    request: Request,
    auth: AuthContext = Depends(require_admin),
    event_type: str = "",
    client: str = "",
    action_id: str = "",
    case_id: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
) -> JSONResponse:
    """
    Search audit events with optional filters and pagination.

    All filters are optional and ANDed.
    Requires ADMIN role.
    """
    svc = _audit_service(request)
    limit = min(max(1, limit), _MAX_LIMIT)
    offset = max(0, offset)

    # Parse event_type filter
    parsed_event_type: AuditEventType | None = None
    if event_type:
        try:
            parsed_event_type = AuditEventType(event_type)
        except ValueError:
            return JSONResponse(
                status_code=400,
                content=error_body(
                    "INVALID_FILTER",
                    f"Unknown event_type: {event_type!r}. "
                    f"Valid values: {[e.value for e in AuditEventType]}",
                ),
            )

    try:
        events = svc.search(
            event_type=parsed_event_type,
            client=client or None,
            action_id=action_id or None,
            case_id=case_id or None,
            limit=limit,
            offset=offset,
        )
        total = svc.count(
            event_type=parsed_event_type,
            client=client or None,
            action_id=action_id or None,
            case_id=case_id or None,
        )
        serialised = _serialise_events(events)
        return JSONResponse(
            status_code=200,
            content={
                "events": serialised,
                "total":  total,
                "limit":  limit,
                "offset": offset,
            },
        )
    except Exception as exc:
        LOGGER.exception("audit.list_audit_events: error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve audit events"),
        )
