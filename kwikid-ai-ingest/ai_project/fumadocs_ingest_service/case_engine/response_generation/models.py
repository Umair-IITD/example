"""
case_engine/response_generation/models.py

Sprint 2.27.5: Customer Response Layer domain models.

Per blueprint flow_diagram.mermaid — USERRESPONSE node:
  L2CHECK -->|No| USERRESPONSE
  FDUPDATE --> USERRESPONSE
  USERRESPONSE --> LLM
  USERRESPONSE --> FD
  USERRESPONSE --> CLOSECHECK

Design:
  - Frozen dataclasses: immutable, JSON-serializable
  - LLM-injectable: ResponseGenerationService accepts a generator callable
    so the LLM provider can replace deterministic templates without structural changes
  - ResponseContext carries all knowledge a generator needs (SOP steps, root cause,
    resolution outcome, investigation summary)
  - ResponseDraft is the output artifact consumed by the caller
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── ResponseType ──────────────────────────────────────────────────────────────

class ResponseType(str, Enum):
    """
    Category of customer-facing response.

    Per blueprint Section 24:
      - clarification: more information needed from the agent
      - resolution:    issue resolved, ticket can close
      - escalation:    routed to L2 / engineering team
      - status_update: interim update while work is in progress
      - approval_needed: human approver must act before we proceed
    """
    CLARIFICATION    = "clarification"
    RESOLUTION       = "resolution"
    ESCALATION       = "escalation"
    STATUS_UPDATE    = "status_update"
    APPROVAL_NEEDED  = "approval_needed"


# ── ResponseContext ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResponseContext:
    """
    All inputs available to the response generator.

    Blueprint Section 24:
      Uses SOPs, root cause, and resolution outcome.
      LLM responsibilities: Rewrite, Summarize, Humanize.

    Fields that are None indicate the pipeline step did not complete (e.g. no
    investigation because the case was immediately classified + escalated).
    The generator must handle any combination of None values gracefully.
    """
    case_id:              str
    topic:                str
    response_type:        ResponseType
    # Investigation layer outputs
    investigation_result: dict[str, Any] | None = None
    root_cause:           dict[str, Any] | None = None
    # Knowledge layer outputs
    knowledge_result:     dict[str, Any] | None = None
    sop_steps:            tuple[str, ...]        = field(default_factory=tuple)
    citations:            tuple[str, ...]        = field(default_factory=tuple)
    # Reasoning layer output
    reasoning_result:     dict[str, Any] | None = None
    # Execution outcome
    resolution_outcome:   dict[str, Any] | None = None
    action_summary:       str                    = ""
    # Escalation context (set when response_type == ESCALATION)
    escalation_reason:    str                    = ""
    engineering_ticket_id: str | None           = None
    # Clarification context (set when response_type == CLARIFICATION)
    clarification_question: str                 = ""
    # Free-form context for LLM enrichment (Sprint 2.28)
    extra_context:        dict[str, Any]         = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":               self.case_id,
            "topic":                 self.topic,
            "response_type":         self.response_type.value,
            "investigation_result":  self.investigation_result,
            "root_cause":            self.root_cause,
            "knowledge_result":      self.knowledge_result,
            "sop_steps":             list(self.sop_steps),
            "citations":             list(self.citations),
            "reasoning_result":      self.reasoning_result,
            "resolution_outcome":    self.resolution_outcome,
            "action_summary":        self.action_summary,
            "escalation_reason":     self.escalation_reason,
            "engineering_ticket_id": self.engineering_ticket_id,
            "clarification_question": self.clarification_question,
            "extra_context":         dict(self.extra_context),
        }


# ── ResponseMetadata ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResponseMetadata:
    """
    Generation provenance: which template/generator produced this draft, and why.

    This is the audit trail for the response generation step. In Sprint 2.28
    when LLM generation is wired, template_used will be "llm" and
    llm_model will carry the model identifier.
    """
    generator_type: str    # "deterministic_template" | "llm"
    template_used:  str    # template key, e.g. "resolution_success"
    llm_model:      str | None = None
    generation_ms:  int        = 0
    retry_count:    int        = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "generator_type": self.generator_type,
            "template_used":  self.template_used,
            "llm_model":      self.llm_model,
            "generation_ms":  self.generation_ms,
            "retry_count":    self.retry_count,
        }


# ── ResponseDraft ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ResponseDraft:
    """
    Output of ResponseGenerationService.generate().

    This is the USERRESPONSE node output per blueprint flow_diagram.mermaid.
    It is the artifact passed to:
      - FD (Freshdesk) to update the ticket with a customer reply
      - CLOSECHECK decision node (escalation_required drives closure logic)

    Fields:
        draft_id         — UUID for this specific draft
        response_type    — category of this response (matches ResponseContext.response_type)
        subject          — Freshdesk reply subject line
        body_text        — plain-text body (for Freshdesk API + audit)
        body_html        — HTML body for Freshdesk rich reply (same content, HTML-formatted)
        confidence       — 0.0–1.0; how confident we are in this response
        citations        — SOP IDs / document references cited in the response
        escalation_required — True iff this response triggers the L2/engineering flow
        llm_ready        — True iff this draft can be sent to LLM for enrichment (Sprint 2.28)
        generated_at     — ISO timestamp
        metadata         — generation provenance (ResponseMetadata)
    """
    draft_id:            str
    response_type:       ResponseType
    subject:             str
    body_text:           str
    body_html:           str
    confidence:          float
    citations:           tuple[str, ...]
    escalation_required: bool
    llm_ready:           bool
    generated_at:        str
    metadata:            ResponseMetadata

    def to_dict(self) -> dict[str, Any]:
        return {
            "draft_id":            self.draft_id,
            "response_type":       self.response_type.value,
            "subject":             self.subject,
            "body_text":           self.body_text,
            "body_html":           self.body_html,
            "confidence":          self.confidence,
            "citations":           list(self.citations),
            "escalation_required": self.escalation_required,
            "llm_ready":           self.llm_ready,
            "generated_at":        self.generated_at,
            "metadata":            self.metadata.to_dict(),
        }

    @classmethod
    def failure(cls, case_id: str, topic: str, error_msg: str) -> "ResponseDraft":
        """Safe fallback draft when generation fails."""
        meta = ResponseMetadata(
            generator_type="deterministic_template",
            template_used="error_fallback",
        )
        return cls(
            draft_id=_new_id(),
            response_type=ResponseType.ESCALATION,
            subject=f"Support Update — {topic}",
            body_text=(
                "We have received your request and our support team is looking into it. "
                "A specialist will follow up with you shortly."
            ),
            body_html=(
                "<p>We have received your request and our support team is looking into it. "
                "A specialist will follow up with you shortly.</p>"
            ),
            confidence=0.0,
            citations=(),
            escalation_required=True,
            llm_ready=False,
            generated_at=_now_iso(),
            metadata=meta,
        )
