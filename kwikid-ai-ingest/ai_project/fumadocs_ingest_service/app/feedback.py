"""
app/feedback.py

Phase B3: POST /feedback endpoint.

Receives human agent feedback on AI-generated drafts and routes it through:
  1. FeedbackIngester.record()    → rag_feedback_logs
  2. ReviewQueueManager.enqueue() → rag_review_queue (APPROVED/EDITED only)
  3. KnowledgePipeline.ingest_verified_reply() → rag_knowledge_chunks (APPROVED/EDITED)

Security:
  - Endpoint is protected by X-API-Key middleware (registered in app/main.py)
  - client slug is validated against the known tenant set
  - human_action is strictly validated; any other value is rejected
  - REJECTED signals are recorded but never trigger knowledge ingestion
  - No stack traces are returned to the caller

Poisoning prevention:
  - knowledge_class is NOT accepted from the caller — it is always set to VERIFIED_REPLY
    by the pipeline, not by the client request
  - edit_ratio is computed server-side (not trusted from the caller)
  - The feedback endpoint cannot bypass the requires_human gate
"""
from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException
from pydantic import BaseModel, Field, field_validator

LOGGER = logging.getLogger(__name__)

# Known human actions (strictly validated)
_VALID_HUMAN_ACTIONS = frozenset({"APPROVED", "EDITED", "REJECTED", "ESCALATED"})

# Known client slugs (prevents injection of arbitrary tenant values).
# This set is the union of all clients in the TenantMapper default mapping.
# Add new clients here as they are onboarded.
_KNOWN_CLIENTS: frozenset[str] = frozenset({
    "cbi",
    "unity_bank",
    "rbl_bank",
    "bob_bank",
    "canara_bank",
    "bajaj_finance",
    "fino_bank",
    "nrfsi",
    "tcook",
})


class FeedbackRequest(BaseModel):
    """Payload for POST /feedback."""
    client:            str   = Field(..., description="Tenant client slug (e.g. unity_bank)")
    ticket_id:         str   = Field(..., description="Freshdesk ticket ID or internal reference")
    query_text:        str   = Field(..., max_length=4000, description="Original support query")
    ai_draft:          str   = Field(..., max_length=8000, description="AI-generated draft shown to agent")
    human_action:      str   = Field(..., description="APPROVED | EDITED | REJECTED | ESCALATED")

    # Optional: only relevant when human_action=EDITED
    edited_response:   Optional[str]  = Field(None, max_length=8000)

    # Optional metadata
    agent_id:          Optional[str]  = Field(None, max_length=256)
    rejection_reason:  Optional[str]  = Field(None, max_length=1000)
    query_type:        Optional[str]  = Field(None, max_length=100)
    automation_label:  Optional[str]  = Field(None, max_length=50)
    generation_latency_ms: Optional[float] = None
    retrieved_chunks:  Optional[list[dict]] = None

    @field_validator("client")
    @classmethod
    def validate_client(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("client must not be empty")
        if v not in _KNOWN_CLIENTS:
            raise ValueError(
                f"unknown client {v!r}; must be one of {sorted(_KNOWN_CLIENTS)}"
            )
        return v

    @field_validator("human_action")
    @classmethod
    def validate_human_action(cls, v: str) -> str:
        v = v.strip().upper()
        if v not in _VALID_HUMAN_ACTIONS:
            raise ValueError(
                f"human_action must be one of {sorted(_VALID_HUMAN_ACTIONS)}"
            )
        return v

    @field_validator("ticket_id")
    @classmethod
    def validate_ticket_id(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("ticket_id must not be empty")
        return v

    @field_validator("query_text")
    @classmethod
    def validate_query_text(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("query_text must not be empty")
        return v.strip()


class FeedbackResponse(BaseModel):
    """Response from POST /feedback."""
    status:           str
    log_id:           Optional[str] = None
    queue_id:         Optional[str] = None
    knowledge_queued: bool          = False
    message:          str           = ""


async def handle_feedback(
    request: FeedbackRequest,
    *,
    feedback_ingester: object,    # FeedbackIngester instance
    review_queue: Optional[object] = None,  # ReviewQueueManager instance (optional)
) -> FeedbackResponse:
    """
    Core logic for the /feedback endpoint.

    Separated from the route handler so it can be tested without FastAPI.
    Never raises — returns error details in the response body.
    """
    from rag_engine.feedback.feedback_loop import FeedbackSignal

    signal = FeedbackSignal(
        freshdesk_ticket_id  = request.ticket_id,
        client               = request.client,
        query_text           = request.query_text,
        query_type           = request.query_type,
        automation_label     = request.automation_label or "HUMAN_REVIEW",
        ai_response          = request.ai_draft,
        human_action         = request.human_action,
        edited_response      = request.edited_response,
        rejection_reason     = request.rejection_reason,
        agent_id             = request.agent_id,
        generation_latency_ms= request.generation_latency_ms,
        retrieved_chunks     = request.retrieved_chunks,
    )

    # Step 1: Record in feedback logs
    log_id: Optional[str] = None
    try:
        log_id = feedback_ingester.record(signal)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        LOGGER.error("Feedback recording failed for ticket=%s: %s", request.ticket_id, exc)
        return FeedbackResponse(
            status  = "error",
            message = "Failed to record feedback. Please retry.",
        )

    # Step 2: Enqueue for human review / knowledge ingestion (APPROVED/EDITED only)
    queue_id: Optional[str] = None
    knowledge_queued = False

    if request.human_action in {"APPROVED", "EDITED"}:
        final_response = request.edited_response or request.ai_draft

        if review_queue is not None:
            try:
                queue_id = review_queue.enqueue(  # type: ignore[attr-defined]
                    client          = request.client,
                    ticket_id       = request.ticket_id,
                    query_text      = request.query_text,
                    ai_draft        = request.ai_draft,
                    feedback_log_id = log_id,
                )
                if queue_id:
                    # Trigger synchronous knowledge ingestion (lightweight articles)
                    processed = review_queue.process_reviewed(  # type: ignore[attr-defined]
                        queue_id,
                        human_action   = request.human_action,
                        final_response = final_response,
                        reviewer_id    = request.agent_id,
                    )
                    knowledge_queued = processed
            except Exception as exc:  # noqa: BLE001
                LOGGER.error(
                    "ReviewQueue enqueue failed for ticket=%s: %s",
                    request.ticket_id, exc,
                )
                # Don't fail the whole request — feedback was already recorded

    LOGGER.info(
        "Feedback processed: client=%s ticket=%s action=%s log_id=%s queue_id=%s knowledge=%s",
        request.client, request.ticket_id, request.human_action,
        log_id, queue_id, knowledge_queued,
    )

    return FeedbackResponse(
        status           = "ok",
        log_id           = log_id,
        queue_id         = queue_id,
        knowledge_queued = knowledge_queued,
        message          = f"Feedback recorded (action={request.human_action})",
    )
