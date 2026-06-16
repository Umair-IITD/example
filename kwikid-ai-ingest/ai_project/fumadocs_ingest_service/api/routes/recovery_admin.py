"""
api/routes/recovery_admin.py

Sprint 2.14: Human recovery and operational control endpoints.

Inspection (read-only):
  GET /admin/action-gateway/actions/{action_id}
      Full action record for a specific action.

  GET /admin/action-gateway/actions/{action_id}/transitions
      Complete state-transition history for an action.

  GET /admin/action-gateway/actions/{action_id}/audit
      All audit events linked to an action.

Recovery (state-mutating — ADMIN only):
  POST /admin/action-gateway/actions/{action_id}/retry
      Retry a DEAD_LETTER or FAILED action.
      Body: {"actor": "...", "reason": "..."}

  POST /admin/action-gateway/actions/{action_id}/cancel
      Cancel a PROPOSED/AWAITING_APPROVAL/APPROVED action.
      Body: {"actor": "...", "reason": "..."}

  POST /admin/action-gateway/actions/{action_id}/expire
      Manually expire a PROPOSED/AWAITING_APPROVAL/APPROVED action.
      Body: {"actor": "...", "reason": "..."}

  POST /admin/action-gateway/actions/{action_id}/rollback
      Trigger rollback for an EXECUTED REVERSIBLE action.
      Body: {"actor": "...", "reason": "..."}

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Stack traces never appear in error responses.
  - actor must be present in mutation request bodies.
  - 503 returned if runtime is not yet initialised.
  - Never 500 on empty data — return 404 for missing actions.
"""
from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/action-gateway/actions", tags=["Recovery Admin"])


def _get_stack(request: Request):
    return getattr(request.app.state, "stack", None)


def _get_recovery(stack: Any):
    return getattr(stack, "recovery", None) if stack else None


def _get_repo(stack: Any):
    return getattr(stack, "repository", None) if stack else None


def _get_audit_repo(stack: Any):
    return getattr(stack, "audit_repository", None) if stack else None


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("SERVICE_UNAVAILABLE", "Runtime not initialised"),
    )


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _action_to_dict(action) -> dict:
    return {
        "action_id":               action.action_id,
        "case_id":                 action.case_id,
        "ticket_id":               action.ticket_id,
        "client":                  action.client,
        "action_type":             action.action_type,
        "action_namespace":        action.action_namespace,
        "risk_level":              action.risk_level.value,
        "current_state":           action.current_state.value,
        "proposed_by":             action.proposed_by,
        "proposed_at":             _iso(action.proposed_at),
        "approval_required":       action.approval_required,
        "expires_at":              _iso(action.expires_at),
        "approver":                action.approver,
        "approved_at":             _iso(action.approved_at),
        "rejected_at":             _iso(action.rejected_at),
        "approval_notes":          action.approval_notes,
        "executor_id":             action.executor_id,
        "execution_started_at":    _iso(action.execution_started_at),
        "execution_completed_at":  _iso(action.execution_completed_at),
        "execution_failed_at":     _iso(action.execution_failed_at),
        "execution_attempt":       action.execution_attempt,
        "max_attempts":            action.max_attempts,
        "failure_code":            action.failure_code,
        "failure_reason":          action.failure_reason,
        "is_rolled_back":          action.is_rolled_back,
        "rollback_action_id":      action.rollback_action_id,
        "rollback_completed_at":   _iso(action.rollback_completed_at),
        "dead_lettered_at":        _iso(action.dead_lettered_at),
        "cancelled_at":            _iso(action.cancelled_at),
        "created_at":              _iso(action.created_at),
        "updated_at":              _iso(action.updated_at),
    }


# ── GET /admin/action-gateway/actions/{action_id} ─────────────────────────────

@router.get("/{action_id}")
def get_action(
    action_id: str,
    request: Request,
    auth=Depends(require_admin),
) -> JSONResponse:
    """Return the full record for a single action."""
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    repo = _get_repo(stack)
    if repo is None:
        return _unavailable()

    try:
        action = repo.get_action(action_id)
    except Exception as exc:
        LOGGER.exception("recovery_admin.get_action: error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve action"),
        )

    if action is None:
        return JSONResponse(
            status_code=404,
            content=error_body("NOT_FOUND", f"Action {action_id} not found"),
        )

    return JSONResponse(status_code=200, content=_action_to_dict(action))


# ── GET /admin/action-gateway/actions/{action_id}/transitions ─────────────────

@router.get("/{action_id}/transitions")
def get_action_transitions(
    action_id: str,
    request: Request,
    auth=Depends(require_admin),
) -> JSONResponse:
    """Return the full state-transition history for an action."""
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    repo = _get_repo(stack)
    if repo is None:
        return _unavailable()

    try:
        records = repo.get_transitions(action_id)
    except Exception as exc:
        LOGGER.exception(
            "recovery_admin.get_transitions: error action_id=%s error=%s", action_id, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve transitions"),
        )

    items = [
        {
            "transition_id": r.transition_id,
            "action_id":     r.action_id,
            "from_state":    r.from_state.value,
            "to_state":      r.to_state.value,
            "actor":         r.actor,
            "reason":        r.reason,
            "detail":        r.detail,
            "created_at":    _iso(r.created_at),
        }
        for r in records
    ]
    return JSONResponse(status_code=200, content={"action_id": action_id, "transitions": items, "total": len(items)})


# ── GET /admin/action-gateway/actions/{action_id}/audit ───────────────────────

@router.get("/{action_id}/audit")
def get_action_audit(
    action_id: str,
    request: Request,
    auth=Depends(require_admin),
) -> JSONResponse:
    """Return all audit events linked to an action."""
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    audit_repo = _get_audit_repo(stack)
    if audit_repo is None:
        return _unavailable()

    try:
        events = audit_repo.events_for_action(action_id)
    except Exception as exc:
        LOGGER.exception(
            "recovery_admin.get_audit: error action_id=%s error=%s", action_id, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve audit events"),
        )

    items = [
        {
            "event_id":   e.event_id,
            "action_id":  e.action_id,
            "event_type": e.event_type.value,
            "actor":      e.actor,
            "case_id":    e.case_id,
            "client":     e.client,
            "timestamp":  _iso(e.timestamp),
            "metadata":   e.metadata,
        }
        for e in events
    ]
    return JSONResponse(status_code=200, content={"action_id": action_id, "events": items, "total": len(items)})


# ── Shared body parser ─────────────────────────────────────────────────────────

def _parse_recovery_body(body: dict) -> tuple[str, str]:
    """Extract and validate actor/reason from request body. Returns (actor, reason)."""
    actor = (body.get("actor") or "").strip()
    reason = (body.get("reason") or "operator_recovery").strip()
    return actor, reason


def _missing_actor() -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content=error_body("VALIDATION_ERROR", "'actor' is required in the request body"),
    )


def _recovery_to_response(result) -> JSONResponse:
    """Convert a RecoveryResult to a JSONResponse."""
    if result.success:
        return JSONResponse(
            status_code=200,
            content={
                "success":    True,
                "action_id":  result.action_id,
                "old_state":  result.old_state,
                "new_state":  result.new_state,
            },
        )
    code = result.error_code or "INTERNAL_ERROR"
    http_status = _error_code_to_http(code)
    return JSONResponse(
        status_code=http_status,
        content=error_body(code, result.error_message or "Recovery failed"),
    )


def _error_code_to_http(code: str) -> int:
    return {
        "NOT_FOUND":         404,
        "INVALID_STATE":     409,
        "RETRY_EXHAUSTED":   409,
        "NOT_REVERSIBLE":    409,
        "NO_ROLLBACK_SPEC":  409,
        "ALREADY_ROLLED_BACK": 409,
        "LOCK_CONTENTION":   409,
        "VALIDATION_ERROR":  422,
        "INTERNAL_ERROR":    500,
    }.get(code, 500)


# ── POST /admin/action-gateway/actions/{action_id}/retry ──────────────────────

@router.post("/{action_id}/retry")
def retry_action(
    action_id: str,
    request: Request,
    body: dict = Body(default={}),
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Retry a DEAD_LETTER or FAILED action.

    DEAD_LETTER: resets execution_attempt=0 and bypasses the terminal-state guard.
    FAILED:      requires remaining retry budget (execution_attempt < max_attempts).

    Body: {"actor": "<identity>", "reason": "<optional note>"}
    """
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    recovery = _get_recovery(stack)
    if recovery is None:
        return _unavailable()

    actor, reason = _parse_recovery_body(body)
    if not actor:
        return _missing_actor()

    try:
        # Inspect the action's current state to pick the right method
        repo = _get_repo(stack)
        if repo is None:
            return _unavailable()
        action = repo.get_action(action_id)
        if action is None:
            from case_engine.action_gateway_recovery import RecoveryResult, NOT_FOUND  # noqa
            result = RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=NOT_FOUND,
                error_message=f"Action {action_id} not found",
            )
            return _recovery_to_response(result)

        from case_engine.action_state import ActionState  # noqa
        if action.current_state == ActionState.DEAD_LETTER:
            result = recovery.retry_dead_letter_action(action_id, actor=actor, reason=reason)
        else:
            result = recovery.retry_failed_action(action_id, actor=actor, reason=reason)
    except Exception as exc:
        LOGGER.exception("recovery_admin.retry: error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retry action"),
        )

    return _recovery_to_response(result)


# ── POST /admin/action-gateway/actions/{action_id}/cancel ─────────────────────

@router.post("/{action_id}/cancel")
def cancel_action(
    action_id: str,
    request: Request,
    body: dict = Body(default={}),
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Cancel a PROPOSED, AWAITING_APPROVAL, or APPROVED action before execution begins.

    Body: {"actor": "<identity>", "reason": "<optional note>"}
    """
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    recovery = _get_recovery(stack)
    if recovery is None:
        return _unavailable()

    actor, reason = _parse_recovery_body(body)
    if not actor:
        return _missing_actor()

    try:
        result = recovery.cancel_action(action_id, actor=actor, reason=reason)
    except Exception as exc:
        LOGGER.exception("recovery_admin.cancel: error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to cancel action"),
        )

    return _recovery_to_response(result)


# ── POST /admin/action-gateway/actions/{action_id}/expire ─────────────────────

@router.post("/{action_id}/expire")
def expire_action(
    action_id: str,
    request: Request,
    body: dict = Body(default={}),
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Manually expire a PROPOSED, AWAITING_APPROVAL, or APPROVED action.

    Semantically distinct from the SLA watchdog's automatic expiry —
    the operator identity is captured in the audit trail.

    Body: {"actor": "<identity>", "reason": "<optional note>"}
    """
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    recovery = _get_recovery(stack)
    if recovery is None:
        return _unavailable()

    actor, reason = _parse_recovery_body(body)
    if not actor:
        return _missing_actor()

    try:
        result = recovery.expire_action_manually(action_id, actor=actor, reason=reason)
    except Exception as exc:
        LOGGER.exception("recovery_admin.expire: error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to expire action"),
        )

    return _recovery_to_response(result)


# ── POST /admin/action-gateway/actions/{action_id}/rollback ───────────────────

@router.post("/{action_id}/rollback")
def rollback_action(
    action_id: str,
    request: Request,
    body: dict = Body(default={}),
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Trigger a rollback for an EXECUTED REVERSIBLE action.

    Creates a compensating action (SAFE, auto-approved) and transitions
    the original to ROLLING_BACK.

    Body: {"actor": "<identity>", "reason": "<optional note>"}
    """
    stack = _get_stack(request)
    if stack is None:
        return _unavailable()

    recovery = _get_recovery(stack)
    if recovery is None:
        return _unavailable()

    actor, reason = _parse_recovery_body(body)
    if not actor:
        return _missing_actor()

    try:
        result = recovery.force_rollback_action(action_id, actor=actor, reason=reason)
    except Exception as exc:
        LOGGER.exception("recovery_admin.rollback: error action_id=%s error=%s", action_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to trigger rollback"),
        )

    return _recovery_to_response(result)
