"""
api/routes/action_proposals_admin.py

Sprint 2.21: Admin endpoint for the Action Proposal Engine.

Routes:
    POST /admin/action-proposals/run
        Run the full proposal pipeline (investigation result → proposals → risk)
        without requiring a live workflow or inbound ticket.
        Returns ActionProposalBundle as JSON.

Authorization:
    Requires ADMIN role (X-API-Key with admin-scoped key).

Design:
    Never 500 — all errors produce structured JSON.
    Stack traces never appear in response bodies.
    Idempotent: each call generates an independent bundle_id.
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

router = APIRouter(prefix="/admin/action-proposals", tags=["Action Proposal Admin"])


# ── Request / Response models ──────────────────────────────────────────────────

class ActionProposalRunRequest(BaseModel):
    """
    Body for POST /admin/action-proposals/run.

    Fields:
        topic:               The ticket topic (e.g. "VKYC_Session_Failure").
        root_cause_category: Root cause from investigation layer (e.g. "EXPIRED_SESSION").
        investigation_confidence: Confidence score [0.0, 1.0].
        investigation_escalate:   Whether investigation flagged for escalation.
        knowledge_match:     Optional dict representing a KnowledgeResult from Sprint 2.20.
                             Must include 'sop_match_found' and 'recommendation' keys.
        slot_values:         Optional slot context for audit metadata.
        case_id:             Optional case_id for audit metadata.
    """
    topic:                    str                  = Field(..., description="Ticket topic key")
    root_cause_category:      str                  = Field(..., description="Root cause category from investigation")
    investigation_confidence: float                = Field(0.8, ge=0.0, le=1.0, description="Investigation confidence [0,1]")
    investigation_escalate:   bool                 = Field(False, description="Whether investigation flagged for escalation")
    knowledge_match:          dict[str, Any] | None = Field(None, description="Optional KnowledgeResult dict from Sprint 2.20")
    slot_values:              dict[str, str]        = Field(default_factory=dict, description="Slot context")
    case_id:                  str | None            = Field(None, description="Optional case_id for audit metadata")


# ── Helpers ────────────────────────────────────────────────────────────────────

def _get_proposal_service(request: Request):
    """Extract ActionProposalService from app state."""
    try:
        return getattr(request.app.state, "action_proposal_service", None)
    except Exception:
        return None


# ── Route ──────────────────────────────────────────────────────────────────────

@router.post(
    "/run",
    summary="Run the Action Proposal Engine",
    description=(
        "Execute the full action proposal pipeline for a given topic and root cause, "
        "optionally incorporating knowledge layer results. "
        "Returns an ActionProposalBundle with risk-assessed proposals."
    ),
)
async def run_action_proposal(
    body:    ActionProposalRunRequest,
    request: Request,
    _auth:   None = Depends(require_admin),
) -> JSONResponse:
    """
    POST /admin/action-proposals/run

    Run the Action Proposal Engine and return the resulting ActionProposalBundle.
    """
    try:
        from case_engine.actions import build_action_proposal_service
        from case_engine.actions.proposal import ActionProposalEngine
        from case_engine.actions.risk import RiskAssessmentEngine

        # Use app-level service if available, else build a fresh one
        svc = _get_proposal_service(request)
        if svc is None:
            svc = build_action_proposal_service()

        # Build synthetic investigation_result from request params
        investigation_result = {
            "result_id": f"admin-run-{body.case_id or 'no-case'}",
            "root_cause": {
                "category":           body.root_cause_category,
                "confidence":         body.investigation_confidence,
                "escalate":           body.investigation_escalate,
                "recommended_action": "",
            },
            "observation": f"Admin-triggered proposal for topic={body.topic}",
        }

        result = svc.propose(
            topic=body.topic,
            investigation_result=investigation_result,
            knowledge_result=body.knowledge_match,
            workflow_id="admin_run",
            step_id="admin_step",
        )

        return JSONResponse(
            status_code=200,
            content={
                "status":              "ok",
                "topic":               body.topic,
                "root_cause_category": body.root_cause_category,
                "bundle":              result,
            },
        )

    except Exception as exc:
        LOGGER.exception(
            "action_proposals_admin.run_action_proposal failed topic=%s error=%s",
            body.topic, exc,
        )
        return JSONResponse(
            status_code=500,
            content=error_body(
                code="PROPOSAL_ENGINE_ERROR",
                message="Action proposal engine failed",
            ),
        )
