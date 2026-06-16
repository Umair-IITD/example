"""
api/routes/investigation_admin.py

Sprint 2.18: Admin endpoint to manually trigger the Investigation Layer.

Routes:
  POST /admin/investigations/run
      Execute a full investigation pipeline (plan → collect → analyse → observe)
      for a given topic and slot_values, without requiring a live Case or
      inbound Freshdesk ticket.

Authorization:
  Requires ADMIN role (X-API-Key with admin-scoped key).

Design constraints:
  - Never 500 — all errors produce a structured JSON response.
  - Stack traces never appear in response bodies.
  - The endpoint is idempotent: calling it multiple times with the same
    inputs produces independent investigation runs (each gets a new result_id).
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

router = APIRouter(prefix="/admin/investigations", tags=["Investigation Admin"])


# ── Request / Response models ──────────────────────────────────────────────────

class InvestigationRunRequest(BaseModel):
    """
    Body for POST /admin/investigations/run.

    Fields:
        topic:       The ticket topic to investigate (e.g. "VKYC_Session_Failure").
        slot_values: Map of slot_name → value string.
                     Must include the slots required by the topic's investigation plan.
        workflow_id: Optional. If provided, the playbook will be resolved from the
                     PlaybookRegistry and its investigation_steps will take priority
                     over the default topic→tool map.
        case_id:     Optional. Used only to populate audit metadata — no live Case
                     object is looked up.
    """
    topic:        str                  = Field(..., description="Ticket topic")
    slot_values:  dict[str, str]       = Field(default_factory=dict, description="Slot name→value map")
    workflow_id:  str | None           = Field(None, description="Optional playbook workflow_id")
    case_id:      str | None           = Field(None, description="Optional case_id for audit metadata")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_investigation_service(request: Request) -> Any:
    return getattr(request.app.state, "investigation_service", None)


def _get_playbook_registry(request: Request) -> Any:
    return getattr(request.app.state, "playbook_registry", None)


def _unavailable(component: str) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=error_body(
            "SERVICE_UNAVAILABLE",
            f"{component} not initialised — check startup logs",
        ),
    )


# ── Endpoint ───────────────────────────────────────────────────────────────────

@router.post("/run", dependencies=[Depends(require_admin)])
async def run_investigation(
    body: InvestigationRunRequest,
    request: Request,
) -> JSONResponse:
    """
    Manually run a full investigation pipeline for a given topic and slot values.

    Returns the complete InvestigationResult including:
      - The InvestigationPlan (steps selected)
      - The EvidenceBundle (tool results)
      - The RootCauseAnalysis (deterministic finding)
      - The observation note (Freshdesk-ready L1 text)
    """
    inv_service = _get_investigation_service(request)
    if inv_service is None:
        return _unavailable("InvestigationService")

    # Resolve workflow definition if workflow_id was provided
    workflow_def = None
    if body.workflow_id is not None:
        playbook_registry = _get_playbook_registry(request)
        if playbook_registry is not None:
            try:
                workflow_def = playbook_registry.get(body.workflow_id)
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "investigation_admin.playbook_lookup_failed workflow_id=%s error=%s",
                    body.workflow_id, exc,
                )

    # Build slot_values map — include _meta.case_id for audit trail
    slot_values: dict[str, Any] = dict(body.slot_values)
    if body.case_id:
        slot_values["_meta"] = {"case_id": body.case_id}

    LOGGER.info(
        "investigation_admin.run topic=%s workflow_id=%s slots=%s",
        body.topic,
        body.workflow_id,
        list(slot_values.keys()),
    )

    try:
        result = inv_service.investigate(
            topic=body.topic,
            workflow_def=workflow_def,
            slot_values=slot_values,
            case=None,
        )
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("investigation_admin.run.unexpected_error topic=%s", body.topic)
        return JSONResponse(
            status_code=500,
            content=error_body("INTERNAL_ERROR", "Investigation pipeline encountered an unexpected error"),
        )

    return JSONResponse(
        status_code=200,
        content={
            "status":  "ok",
            "topic":   body.topic,
            "result":  result.to_dict(),
        },
    )
