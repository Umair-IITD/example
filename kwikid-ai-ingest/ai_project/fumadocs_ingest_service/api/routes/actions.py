"""
api/routes/actions.py

Action inspection and approval endpoints.

Routes:
  GET  /actions/{action_id}         — inspect an action (public)
  POST /actions/{action_id}/approve — approve (APPROVER or ADMIN role required)
  POST /actions/{action_id}/reject  — reject  (APPROVER or ADMIN role required)

Error codes:
  UNAUTHORIZED           — 401: missing or invalid API key
  FORBIDDEN              — 403: insufficient role
  ACTION_NOT_FOUND       — 404: action_id does not exist
  INVALID_TRANSITION     — 400: state machine rejects the transition
  INVALID_REQUEST_BODY   — 400: JSON body parsing failed
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from audit.models import AuditEvent, AuditEventType
from case_engine.action_state import ActionTransitionError
from security.auth import AuthContext
from security.dependencies import require_approver

LOGGER = logging.getLogger(__name__)

router = APIRouter()


# ── Helpers ────────────────────────────────────────────────────────────────────

def _action_to_dict(action) -> dict:
    """Serialise an ActionRequest to a JSON-safe dict."""
    def _iso(dt) -> str | None:
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
        "approval_required":      action.approval_required,
        "expires_at":             _iso(action.expires_at),
        "approver":               action.approver,
        "approved_at":            _iso(action.approved_at),
        "rejected_at":            _iso(action.rejected_at),
        "approval_notes":         action.approval_notes,
        "executor_id":            action.executor_id,
        "execution_started_at":   _iso(action.execution_started_at),
        "execution_completed_at": _iso(action.execution_completed_at),
        "execution_failed_at":    _iso(action.execution_failed_at),
        "execution_attempt":      action.execution_attempt,
        "max_attempts":           action.max_attempts,
        "execution_result":       action.execution_result,
        "failure_code":           action.failure_code,
        "failure_reason":         action.failure_reason,
        "is_rolled_back":         action.is_rolled_back,
        "rollback_action_id":     action.rollback_action_id,
        "created_at":             _iso(action.created_at),
        "updated_at":             _iso(action.updated_at),
    }


def _get_action_or_404(action_id: str, stack) -> tuple[object, JSONResponse | None]:
    """Fetch action by ID. Returns (action, None) on success or (None, 404_response) on miss."""
    action = stack.repository.get_action(action_id)
    if action is None:
        return None, JSONResponse(
            status_code=404,
            content=error_body("ACTION_NOT_FOUND", f"Action {action_id!r} not found"),
        )
    return action, None


def _emit_audit(request: Request, event: AuditEvent) -> None:
    """Emit an audit event. Never raises."""
    try:
        audit_logger = getattr(request.app.state, "audit_logger", None)
        if audit_logger is not None:
            audit_logger.emit(event)
    except Exception:
        pass


# ── Routes ─────────────────────────────────────────────────────────────────────

@router.get("/actions/{action_id}")
def get_action(action_id: str, request: Request) -> JSONResponse:
    """Return all fields of the requested action. Public endpoint — no auth required."""
    stack = request.app.state.stack
    action, err = _get_action_or_404(action_id, stack)
    if err is not None:
        return err
    return JSONResponse(status_code=200, content=_action_to_dict(action))


@router.post("/actions/{action_id}/approve")
async def approve_action(
    action_id: str,
    request: Request,
    auth: AuthContext = Depends(require_approver),
) -> JSONResponse:
    """
    Approve an action that is AWAITING_APPROVAL.

    Requires APPROVER or ADMIN role (X-API-Key header).
    """
    stack = request.app.state.stack
    action, err = _get_action_or_404(action_id, stack)
    if err is not None:
        return err

    # Parse optional body for notes
    body = {}
    try:
        raw = await request.body()
        if raw:
            import json
            body = json.loads(raw)
    except Exception:
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_REQUEST_BODY", "Request body is not valid JSON"),
        )

    notes = str(body.get("notes") or "")

    try:
        updated = stack.gateway.approve(action, approved_by=auth.identity, notes=notes)
        LOGGER.info(
            "actions.approve: action_id=%s actor=%s role=%s",
            action_id, auth.identity, auth.role.value,
        )
        _emit_audit(request, AuditEvent(
            action_id=action_id,
            event_type=AuditEventType.ACTION_APPROVED,
            actor=f"human:{auth.identity}",
            metadata={
                "role": auth.role.value,
                "notes": notes,
                "action_type": action.action_type,
                "client": action.client,
            },
        ))
        return JSONResponse(status_code=200, content=_action_to_dict(updated))
    except ActionTransitionError as exc:
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_TRANSITION", str(exc)),
        )
    except Exception as exc:
        LOGGER.exception("actions.approve: unexpected error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to approve action"),
        )


@router.post("/actions/{action_id}/reject")
async def reject_action(
    action_id: str,
    request: Request,
    auth: AuthContext = Depends(require_approver),
) -> JSONResponse:
    """
    Reject an action that is AWAITING_APPROVAL.

    Requires APPROVER or ADMIN role (X-API-Key header).
    """
    stack = request.app.state.stack
    action, err = _get_action_or_404(action_id, stack)
    if err is not None:
        return err

    body = {}
    try:
        raw = await request.body()
        if raw:
            import json
            body = json.loads(raw)
    except Exception:
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_REQUEST_BODY", "Request body is not valid JSON"),
        )

    notes = str(body.get("notes") or "")

    try:
        updated = stack.gateway.reject(action, rejected_by=auth.identity, notes=notes)
        LOGGER.info(
            "actions.reject: action_id=%s actor=%s role=%s",
            action_id, auth.identity, auth.role.value,
        )
        _emit_audit(request, AuditEvent(
            action_id=action_id,
            event_type=AuditEventType.ACTION_REJECTED,
            actor=f"human:{auth.identity}",
            metadata={
                "role": auth.role.value,
                "notes": notes,
                "action_type": action.action_type,
                "client": action.client,
            },
        ))
        return JSONResponse(status_code=200, content=_action_to_dict(updated))
    except ActionTransitionError as exc:
        return JSONResponse(
            status_code=400,
            content=error_body("INVALID_TRANSITION", str(exc)),
        )
    except Exception as exc:
        LOGGER.exception("actions.reject: unexpected error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to reject action"),
        )
