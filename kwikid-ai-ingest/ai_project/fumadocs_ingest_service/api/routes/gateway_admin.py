"""
api/routes/gateway_admin.py

Sprint 2.13: Operational admin endpoints for the Action Gateway.

Routes (all under /admin/action-gateway/):

  GET /admin/action-gateway/summary
      Action counts by state + in-process metrics totals.
      Answers: "How many actions are waiting/executing/dead-lettered?"

  GET /admin/action-gateway/dead-letter
      Dead-lettered actions with failure details.
      Answers: "What failed and why?"

  GET /admin/action-gateway/retries
      Actions that have been retried at least once.
      Answers: "What retried, how many times, and what is the current state?"

  GET /admin/action-gateway/stuck
      Actions stuck in EXECUTING or AWAITING_APPROVAL beyond thresholds.
      Answers: "What is blocked and for how long?"

  GET /admin/action-gateway/workers
      Per-worker success/failure statistics derived from recent actions.
      Answers: "Which worker processed what, and when?"

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).

Response models:
  All endpoints return typed Pydantic models (not raw dicts).
  See api/admin_response_models.py.

Design constraints:
  - Read-only: no state mutations.
  - Never 500 on empty data — return empty lists with 200.
  - Stack traces never appear in error responses.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.admin_response_models import (
    DeadLetterActionItem,
    DeadLetterResponse,
    GatewaySummaryResponse,
    RetryResponse,
    RetriedActionItem,
    StateCountItem,
    StaleApprovalItem,
    StuckActionsResponse,
    StuckExecutingItem,
    WorkerResponse,
    WorkerStatsItem,
)
from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/action-gateway", tags=["Gateway Admin"])

_DEFAULT_LIMIT = 100
_MAX_LIMIT = 500
_DEFAULT_WINDOW_HOURS = 24
_DEFAULT_EXECUTING_THRESHOLD_S = 900   # 15 minutes
_DEFAULT_APPROVAL_THRESHOLD_S = 86400  # 24 hours


def _get_ops(request: Request):
    """Extract the operations service from app state. Returns None if unavailable."""
    stack = getattr(request.app.state, "stack", None)
    if stack is None:
        return None
    return getattr(stack, "operations", None)


def _iso(dt) -> str | None:
    return dt.isoformat() if dt is not None else None


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("SERVICE_UNAVAILABLE", "Runtime not initialised"),
    )


# ── GET /admin/action-gateway/summary ─────────────────────────────────────────

@router.get("/summary")
def get_gateway_summary(
    request: Request,
    client: str = "",
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Return action counts by state and in-process metrics totals.

    Query parameters:
      client — filter by tenant slug (optional; empty = all tenants)
    """
    ops = _get_ops(request)
    if ops is None:
        return _unavailable()

    client_filter = client.strip() or None
    try:
        summary = ops.get_state_summary(client=client_filter)
    except Exception as exc:
        LOGGER.exception(
            "gateway_admin.summary: unexpected error client=%s error=%s", client_filter, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve gateway summary"),
        )

    state_items = [
        StateCountItem(state=sc.state, count=sc.count)
        for sc in summary.state_counts
    ]
    response = GatewaySummaryResponse(
        client=summary.client,
        state_counts=state_items,
        total_live=summary.total_live,
        metrics_totals=summary.metrics_totals,
        checked_at=_iso(summary.checked_at),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


# ── GET /admin/action-gateway/dead-letter ─────────────────────────────────────

@router.get("/dead-letter")
def get_dead_letter(
    request: Request,
    client: str = "",
    limit: int = _DEFAULT_LIMIT,
    offset: int = 0,
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Return dead-lettered actions with failure details.

    Query parameters:
      client — filter by tenant slug (optional)
      limit  — page size (default 100, max 500)
      offset — page offset (default 0)
    """
    limit = min(max(1, limit), _MAX_LIMIT)
    offset = max(0, offset)
    ops = _get_ops(request)
    if ops is None:
        return _unavailable()

    client_filter = client.strip() or None
    try:
        summary = ops.get_dead_letter_summary(
            client=client_filter, limit=limit, offset=offset,
        )
    except Exception as exc:
        LOGGER.exception(
            "gateway_admin.dead_letter: unexpected error client=%s error=%s", client_filter, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve dead-letter queue"),
        )

    action_items = [
        DeadLetterActionItem(
            action_id=item.action_id,
            action_type=item.action_type,
            action_namespace=item.action_namespace,
            client=item.client,
            proposed_at=_iso(item.proposed_at),
            dead_lettered_at=_iso(item.dead_lettered_at),
            failure_code=item.failure_code,
            failure_reason=item.failure_reason,
            execution_attempt=item.execution_attempt,
            max_attempts=item.max_attempts,
        )
        for item in summary.items
    ]
    response = DeadLetterResponse(
        client=summary.client,
        actions=action_items,
        total=summary.total,
        limit=limit,
        offset=offset,
        checked_at=_iso(summary.checked_at),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


# ── GET /admin/action-gateway/retries ─────────────────────────────────────────

@router.get("/retries")
def get_retries(
    request: Request,
    client: str = "",
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Return actions that have been retried at least once.

    Covers all final states — the action may have ultimately succeeded,
    failed, or been dead-lettered after retries.

    Query parameters:
      client — filter by tenant slug (optional)
    """
    ops = _get_ops(request)
    if ops is None:
        return _unavailable()

    client_filter = client.strip() or None
    try:
        summary = ops.get_retry_summary(client=client_filter)
    except Exception as exc:
        LOGGER.exception(
            "gateway_admin.retries: unexpected error client=%s error=%s", client_filter, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve retry data"),
        )

    action_items = [
        RetriedActionItem(
            action_id=item.action_id,
            action_type=item.action_type,
            action_namespace=item.action_namespace,
            client=item.client,
            current_state=item.current_state,
            execution_attempt=item.execution_attempt,
            max_attempts=item.max_attempts,
            failure_code=item.failure_code,
            failure_reason=item.failure_reason,
            proposed_at=_iso(item.proposed_at),
        )
        for item in summary.items
    ]
    response = RetryResponse(
        client=summary.client,
        actions=action_items,
        total=summary.total,
        checked_at=_iso(summary.checked_at),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


# ── GET /admin/action-gateway/stuck ───────────────────────────────────────────

@router.get("/stuck")
def get_stuck_actions(
    request: Request,
    client: str = "",
    executing_threshold_seconds: int = _DEFAULT_EXECUTING_THRESHOLD_S,
    approval_threshold_seconds: int = _DEFAULT_APPROVAL_THRESHOLD_S,
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Detect actions stuck in EXECUTING or AWAITING_APPROVAL beyond thresholds.

    This endpoint is read-only — it reports findings without modifying state.

    Query parameters:
      client                      — filter by tenant (optional)
      executing_threshold_seconds — how long EXECUTING before considered stuck (default 900)
      approval_threshold_seconds  — how long AWAITING_APPROVAL before stale (default 86400)
    """
    executing_threshold_seconds = max(60, executing_threshold_seconds)
    approval_threshold_seconds = max(60, approval_threshold_seconds)
    ops = _get_ops(request)
    if ops is None:
        return _unavailable()

    client_filter = client.strip() or None
    try:
        summary = ops.get_stuck_actions(
            executing_threshold_seconds=executing_threshold_seconds,
            approval_threshold_seconds=approval_threshold_seconds,
            client=client_filter,
        )
    except Exception as exc:
        LOGGER.exception(
            "gateway_admin.stuck: unexpected error client=%s error=%s", client_filter, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve stuck actions"),
        )

    exec_items = [
        StuckExecutingItem(
            action_id=item.action_id,
            action_type=item.action_type,
            action_namespace=item.action_namespace,
            client=item.client,
            executor_id=item.executor_id,
            execution_started_at=_iso(item.execution_started_at),
            execution_attempt=item.execution_attempt,
            stuck_for_seconds=item.stuck_for_seconds,
        )
        for item in summary.stuck_executing
    ]
    approval_items = [
        StaleApprovalItem(
            action_id=item.action_id,
            action_type=item.action_type,
            action_namespace=item.action_namespace,
            client=item.client,
            risk_level=item.risk_level,
            proposed_at=_iso(item.proposed_at),
            expires_at=_iso(item.expires_at),
            waiting_for_seconds=item.waiting_for_seconds,
        )
        for item in summary.stale_approval
    ]
    response = StuckActionsResponse(
        client=summary.client,
        stuck_executing=exec_items,
        stale_approval=approval_items,
        total_stuck=len(exec_items) + len(approval_items),
        executing_threshold_seconds=summary.executing_threshold_seconds,
        approval_threshold_seconds=summary.approval_threshold_seconds,
        checked_at=_iso(summary.checked_at),
    )
    return JSONResponse(status_code=200, content=response.model_dump())


# ── GET /admin/action-gateway/workers ─────────────────────────────────────────

@router.get("/workers")
def get_workers(
    request: Request,
    client: str = "",
    window_hours: int = _DEFAULT_WINDOW_HOURS,
    auth=Depends(require_admin),
) -> JSONResponse:
    """
    Return per-worker execution statistics derived from recent actions.

    Statistics are derived from EXECUTED, FAILED, and DEAD_LETTER actions
    where executor_id is set. Bounded by window_hours.

    Query parameters:
      client       — filter by tenant (optional)
      window_hours — look-back window (default 24)
    """
    window_hours = max(1, min(window_hours, 720))
    ops = _get_ops(request)
    if ops is None:
        return _unavailable()

    client_filter = client.strip() or None
    try:
        summary = ops.get_worker_summary(client=client_filter, window_hours=window_hours)
    except Exception as exc:
        LOGGER.exception(
            "gateway_admin.workers: unexpected error client=%s error=%s", client_filter, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve worker statistics"),
        )

    worker_items = [
        WorkerStatsItem(
            worker_id=w.worker_id,
            successful=w.successful,
            failed=w.failed,
            dead_lettered=w.dead_lettered,
            total_processed=w.total_processed,
            last_execution_at=_iso(w.last_execution_at),
        )
        for w in summary.workers
    ]
    response = WorkerResponse(
        client=summary.client,
        workers=worker_items,
        total_workers=summary.total_workers,
        window_hours=summary.window_hours,
        checked_at=_iso(summary.checked_at),
    )
    return JSONResponse(status_code=200, content=response.model_dump())
