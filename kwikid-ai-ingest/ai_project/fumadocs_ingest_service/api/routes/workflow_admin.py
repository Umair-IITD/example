"""
api/routes/workflow_admin.py

Sprint 2.16: Operational visibility endpoints for the workflow orchestration layer.

Routes (all under /admin/workflows/):

  GET /admin/workflows/summary
      Workflow counts by state + topic breakdown.
      Answers: "How many workflows are running / paused / completed?"

  GET /admin/workflows/active
      Cases with RUNNING or PAUSED workflows.
      Answers: "What workflows are currently in-flight?"

  GET /admin/workflows/stuck
      Cases stuck in PAUSED (ACTION_PENDING) beyond a threshold.
      Answers: "What workflows are blocked waiting on action approval?"

Authorization:
  All endpoints require ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Read-only: no state mutations.
  - Never 500 on empty data — return empty lists with 200.
  - Stack traces never appear in error responses.
  - Extends the Sprint 2.13 gateway_admin.py pattern.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse

from api.error_models import error_body
from security.dependencies import require_admin

LOGGER = logging.getLogger(__name__)

router = APIRouter(prefix="/admin/workflows", tags=["Workflow Admin"])

_STUCK_PAUSED_THRESHOLD_MINUTES = 30
_STUCK_RUNNING_THRESHOLD_MINUTES = 15


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_case_service(request: Request) -> Any:
    return getattr(request.app.state, "case_service", None)


def _get_registry(request: Request) -> Any:
    return getattr(request.app.state, "playbook_registry", None)


def _unavailable() -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body("SERVICE_UNAVAILABLE", "Case engine not initialised"),
    )


# ── Endpoints ──────────────────────────────────────────────────────────────────

@router.get("/summary")
async def workflow_summary(
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return aggregate workflow counts by workflow_state and topic.

    Suitable for dashboards and SLA monitoring.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        import asyncio
        cases = await asyncio.to_thread(cs.list_workflow_cases)

        state_counts: dict[str, int] = {}
        topic_counts: dict[str, int] = {}
        workflow_counts: dict[str, int] = {}

        for case in cases:
            ws = case.workflow_state or "UNKNOWN"
            state_counts[ws] = state_counts.get(ws, 0) + 1

            topic = case.topic or "UNKNOWN"
            topic_counts[topic] = topic_counts.get(topic, 0) + 1

            wid = case.workflow_id or "UNKNOWN"
            workflow_counts[wid] = workflow_counts.get(wid, 0) + 1

        return JSONResponse(
            status_code=200,
            content={
                "total": len(cases),
                "by_workflow_state": state_counts,
                "by_topic":          topic_counts,
                "by_workflow_id":    workflow_counts,
            },
        )

    except Exception as exc:
        LOGGER.exception("workflow_summary failed error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve workflow summary"),
        )


@router.get("/active")
async def active_workflows(
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return all cases with RUNNING or PAUSED workflow state.

    Useful for operational dashboards monitoring in-flight workflows.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        import asyncio
        cases = await asyncio.to_thread(cs.list_workflow_cases, states=["RUNNING", "PAUSED"])

        items = []
        for case in cases:
            ctx = case.workflow_context or {}
            items.append({
                "case_id":           case.case_id,
                "ticket_id":         case.ticket_id,
                "client":            case.client,
                "topic":             case.topic,
                "case_state":        case.current_state.value,
                "workflow_id":       case.workflow_id,
                "workflow_state":    case.workflow_state,
                "workflow_step_index": case.workflow_step_index,
                "current_step_id":   ctx.get("current_step_id"),
                "pending_action_id": ctx.get("pending_action_id"),
                "started_at":        ctx.get("started_at"),
                "updated_at":        case.updated_at.isoformat(),
            })

        return JSONResponse(
            status_code=200,
            content={"total": len(items), "workflows": items},
        )

    except Exception as exc:
        LOGGER.exception("active_workflows failed error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve active workflows"),
        )


@router.get("/stuck")
async def stuck_workflows(
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return cases whose workflow has been PAUSED beyond the stuck threshold.

    A PAUSED workflow is stuck if it has been waiting for an action
    approval/execution for more than _STUCK_PAUSED_THRESHOLD_MINUTES.

    Use this to identify workflows that need manual intervention.
    """
    cs = _get_case_service(request)
    if cs is None:
        return _unavailable()

    try:
        import asyncio
        cases = await asyncio.to_thread(cs.list_workflow_cases, states=["PAUSED"])

        now = datetime.now(tz=timezone.utc)
        stuck_threshold = timedelta(minutes=_STUCK_PAUSED_THRESHOLD_MINUTES)
        stuck: list[dict[str, Any]] = []

        for case in cases:
            ctx = case.workflow_context or {}
            updated = case.updated_at
            if updated.tzinfo is None:
                updated = updated.replace(tzinfo=timezone.utc)
            paused_duration = now - updated
            if paused_duration >= stuck_threshold:
                stuck.append({
                    "case_id":           case.case_id,
                    "ticket_id":         case.ticket_id,
                    "client":            case.client,
                    "topic":             case.topic,
                    "workflow_id":       case.workflow_id,
                    "workflow_state":    case.workflow_state,
                    "current_step_id":   ctx.get("current_step_id"),
                    "pending_action_id": ctx.get("pending_action_id"),
                    "paused_minutes":    int(paused_duration.total_seconds() // 60),
                    "updated_at":        case.updated_at.isoformat(),
                })

        return JSONResponse(
            status_code=200,
            content={
                "stuck_threshold_minutes": _STUCK_PAUSED_THRESHOLD_MINUTES,
                "total_stuck":             len(stuck),
                "workflows":               stuck,
            },
        )

    except Exception as exc:
        LOGGER.exception("stuck_workflows failed error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve stuck workflows"),
        )


@router.get("/playbooks")
async def list_playbooks(
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return the list of all registered YAML playbooks from the PlaybookRegistry.

    Includes investigation_steps, tool_candidates, and resolution_paths
    added in Sprint 2.17.
    """
    registry = _get_registry(request)
    if registry is None:
        return _unavailable()

    try:
        playbooks = registry.list_all()
        return JSONResponse(
            status_code=200,
            content={
                "total": len(playbooks),
                "playbooks": [p.to_dict() for p in playbooks],
            },
        )
    except Exception as exc:
        LOGGER.exception("list_playbooks failed error=%s", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to list playbooks"),
        )


@router.get("/playbooks/{workflow_id}")
async def get_playbook(
    workflow_id: str,
    request: Request,
    _auth: Any = Depends(require_admin),
) -> JSONResponse:
    """
    Return a single playbook definition by workflow_id.

    Includes all steps, conditions, investigation metadata, and resolution paths.
    """
    registry = _get_registry(request)
    if registry is None:
        return _unavailable()

    try:
        defn = registry.get_by_id(workflow_id)
        if defn is None:
            return JSONResponse(
                status_code=404,
                content=error_body("NOT_FOUND", f"No playbook found with workflow_id='{workflow_id}'"),
            )
        return JSONResponse(status_code=200, content=defn.to_dict())
    except Exception as exc:
        LOGGER.exception("get_playbook failed workflow_id=%s error=%s", workflow_id, exc)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Failed to retrieve playbook"),
        )
