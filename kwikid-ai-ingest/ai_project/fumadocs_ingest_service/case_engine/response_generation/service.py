"""
case_engine/response_generation/service.py

Sprint 2.27.5: ResponseGenerationService — USERRESPONSE node implementation.

Per blueprint flow_diagram.mermaid:
  L2CHECK -->|No| USERRESPONSE
  FDUPDATE --> USERRESPONSE
  USERRESPONSE --> LLM
  USERRESPONSE --> FD
  USERRESPONSE --> CLOSECHECK

Blueprint Section 24 (Customer Response Generation):
  "Uses SOPs, root cause, and resolution outcome."
  "LLM responsibilities: Rewrite, Summarize, Humanize."
  "LLM never executes actions."

Sprint 2.27.5: deterministic templates (no LLM).
Sprint 2.28:   inject llm_generator callable — zero structural changes.

Design:
  - Never raises: all paths return ResponseDraft (failure draft on exception)
  - LLM-injectable: `generator` parameter accepts any callable(context) → str
  - Audit: emits RESPONSE_GENERATED event when audit_logger is provided
  - All output is JSON-serializable
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, TYPE_CHECKING

from case_engine.response_generation.models import (
    ResponseContext,
    ResponseDraft,
    ResponseMetadata,
    ResponseType,
)

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Deterministic Templates ───────────────────────────────────────────────────

_TEMPLATES: dict[str, dict[str, str]] = {
    "resolution_success": {
        "subject": "Your Support Request Has Been Resolved",
        "body_text": (
            "Hello,\n\n"
            "We have investigated your request and have successfully resolved the issue.\n\n"
            "{action_summary}\n\n"
            "If you experience this issue again or have any questions, please don't hesitate "
            "to contact us.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>We have investigated your request and have successfully resolved the issue.</p>"
            "<p>{action_summary}</p>"
            "<p>If you experience this issue again or have any questions, please don't hesitate "
            "to contact us.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
    "escalation_l2": {
        "subject": "Your Request Has Been Escalated to Our Engineering Team",
        "body_text": (
            "Hello,\n\n"
            "Thank you for reaching out to us. After reviewing your case, we have determined "
            "that this issue requires attention from our engineering team.\n\n"
            "{escalation_reason}\n\n"
            "Our team will investigate and provide an update as soon as possible. "
            "You will receive a notification once the issue is resolved.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>Thank you for reaching out to us. After reviewing your case, we have determined "
            "that this issue requires attention from our engineering team.</p>"
            "<p>{escalation_reason}</p>"
            "<p>Our team will investigate and provide an update as soon as possible. "
            "You will receive a notification once the issue is resolved.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
    "clarification_needed": {
        "subject": "Additional Information Required",
        "body_text": (
            "Hello,\n\n"
            "Thank you for contacting us. To resolve your issue as quickly as possible, "
            "we need some additional information.\n\n"
            "{clarification_question}\n\n"
            "Please reply to this message with the requested information and we will "
            "continue working on your case.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>Thank you for contacting us. To resolve your issue as quickly as possible, "
            "we need some additional information.</p>"
            "<p>{clarification_question}</p>"
            "<p>Please reply to this message with the requested information and we will "
            "continue working on your case.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
    "status_update": {
        "subject": "Update on Your Support Request",
        "body_text": (
            "Hello,\n\n"
            "We wanted to provide you with an update on your support request. "
            "Our team is actively working on your case.\n\n"
            "{action_summary}\n\n"
            "We will notify you as soon as there is a resolution.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>We wanted to provide you with an update on your support request. "
            "Our team is actively working on your case.</p>"
            "<p>{action_summary}</p>"
            "<p>We will notify you as soon as there is a resolution.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
    "approval_needed": {
        "subject": "Your Request is Awaiting Authorization",
        "body_text": (
            "Hello,\n\n"
            "Your support request requires authorization before we can proceed. "
            "Our compliance team has been notified and will review your case.\n\n"
            "{action_summary}\n\n"
            "You will be notified once the authorization decision is made.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>Your support request requires authorization before we can proceed. "
            "Our compliance team has been notified and will review your case.</p>"
            "<p>{action_summary}</p>"
            "<p>You will be notified once the authorization decision is made.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
    "error_fallback": {
        "subject": "We Are Looking Into Your Request",
        "body_text": (
            "Hello,\n\n"
            "We have received your request and our support team is looking into it. "
            "A specialist will follow up with you shortly.\n\n"
            "Best regards,\nKwikID Support Team"
        ),
        "body_html": (
            "<p>Hello,</p>"
            "<p>We have received your request and our support team is looking into it. "
            "A specialist will follow up with you shortly.</p>"
            "<p>Best regards,<br>KwikID Support Team</p>"
        ),
    },
}

_RESPONSE_TYPE_TO_TEMPLATE: dict[ResponseType, str] = {
    ResponseType.RESOLUTION:     "resolution_success",
    ResponseType.ESCALATION:     "escalation_l2",
    ResponseType.CLARIFICATION:  "clarification_needed",
    ResponseType.STATUS_UPDATE:  "status_update",
    ResponseType.APPROVAL_NEEDED: "approval_needed",
}

_RESPONSE_TYPE_CONFIDENCE: dict[ResponseType, float] = {
    ResponseType.RESOLUTION:     0.90,
    ResponseType.ESCALATION:     0.85,
    ResponseType.CLARIFICATION:  0.80,
    ResponseType.STATUS_UPDATE:  0.75,
    ResponseType.APPROVAL_NEEDED: 0.80,
}


def _resolve_template_vars(template: str, context: ResponseContext) -> str:
    """Fill template placeholders from ResponseContext."""
    action_summary      = context.action_summary or "Our team has completed the necessary steps."
    escalation_reason   = context.escalation_reason or "This issue requires engineering involvement."
    clarification_question = context.clarification_question or "Could you please provide more details about your issue?"

    # Enrich action_summary from resolution_outcome if not explicitly set
    if not context.action_summary and context.resolution_outcome:
        outcome_summary = context.resolution_outcome.get("summary", "")
        if outcome_summary:
            action_summary = outcome_summary

    return (
        template
        .replace("{action_summary}", action_summary)
        .replace("{escalation_reason}", escalation_reason)
        .replace("{clarification_question}", clarification_question)
    )


# ── ResponseGenerationService ─────────────────────────────────────────────────

class ResponseGenerationService:
    """
    Implements the USERRESPONSE node: generate the customer-facing reply.

    Sprint 2.27.5: deterministic templates.
    Sprint 2.28:   inject llm_generator(context: ResponseContext) → str
                   to replace body_text with LLM-enriched content.

    Never raises. All exceptions return ResponseDraft.failure().
    """

    def __init__(
        self,
        audit_logger:  "AuditLogger | None" = None,
        llm_generator: "Callable[[ResponseContext], str] | None" = None,
    ) -> None:
        self._audit         = audit_logger
        self._llm_generator = llm_generator

    # ── Primary API ───────────────────────────────────────────────────────────

    def generate(
        self,
        context: ResponseContext,
        case:    "Case | None" = None,
    ) -> ResponseDraft:
        """
        Generate a customer response draft for the given context.

        Per blueprint: uses SOPs, root cause, and resolution outcome.
        Sprint 2.27.5: deterministic templates.
        Sprint 2.28: LLM generator injected via constructor.

        Returns ResponseDraft. Never raises.
        """
        started_ms = int(time.monotonic() * 1000)
        try:
            draft = self._generate(context, started_ms)
            self._emit_generated(draft, case)
            return draft
        except Exception as exc:
            LOGGER.exception(
                "response_generation.fatal_error case_id=%s topic=%s error=%s",
                context.case_id, context.topic, exc,
            )
            return ResponseDraft.failure(
                case_id=context.case_id,
                topic=context.topic,
                error_msg=str(exc),
            )

    # ── Private pipeline ──────────────────────────────────────────────────────

    def _generate(self, context: ResponseContext, started_ms: int) -> ResponseDraft:
        response_type = context.response_type
        template_key  = _RESPONSE_TYPE_TO_TEMPLATE.get(response_type, "error_fallback")
        template      = _TEMPLATES[template_key]

        subject   = template["subject"]
        body_text = _resolve_template_vars(template["body_text"], context)
        body_html = _resolve_template_vars(template["body_html"], context)

        # Sprint 2.28 injection point: LLM can rewrite body_text + body_html
        if self._llm_generator is not None:
            try:
                llm_text = self._llm_generator(context)
                if llm_text:
                    body_text = llm_text
                    body_html = f"<p>{llm_text.replace(chr(10), '</p><p>')}</p>"
                    generator_type = "llm"
                else:
                    generator_type = "deterministic_template"
            except Exception as exc:
                LOGGER.warning(
                    "response_generation.llm_failed case_id=%s error=%s — using template",
                    context.case_id, exc,
                )
                generator_type = "deterministic_template"
        else:
            generator_type = "deterministic_template"

        duration_ms = max(0, int(time.monotonic() * 1000) - started_ms)
        meta = ResponseMetadata(
            generator_type=generator_type,
            template_used=template_key,
            generation_ms=duration_ms,
        )

        confidence = _RESPONSE_TYPE_CONFIDENCE.get(response_type, 0.75)
        escalation_required = response_type == ResponseType.ESCALATION

        LOGGER.info(
            "response_generation.complete case_id=%s type=%s template=%s confidence=%.2f "
            "escalation=%s generator=%s",
            context.case_id, response_type.value, template_key, confidence,
            escalation_required, generator_type,
        )

        return ResponseDraft(
            draft_id=_new_id(),
            response_type=response_type,
            subject=subject,
            body_text=body_text,
            body_html=body_html,
            confidence=confidence,
            citations=context.citations,
            escalation_required=escalation_required,
            llm_ready=True,
            generated_at=_now_iso(),
            metadata=meta,
        )

    # ── Audit ─────────────────────────────────────────────────────────────────

    def _emit_generated(self, draft: ResponseDraft, case: "Case | None") -> None:
        """Emit RESPONSE_GENERATED audit event. Never raises."""
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_response_generated(
                case,
                draft_id=draft.draft_id,
                response_type=draft.response_type.value,
                confidence=draft.confidence,
                escalation_required=draft.escalation_required,
                generator_type=draft.metadata.generator_type,
            )
        except Exception as exc:
            LOGGER.debug("response_generation: audit emit failed: %s", exc)


# ── Factory ───────────────────────────────────────────────────────────────────

def build_response_generation_service(
    audit_logger:  Any = None,
    llm_generator: "Callable[[ResponseContext], str] | None" = None,
) -> ResponseGenerationService:
    """
    Factory: build a ResponseGenerationService.

    Args:
        audit_logger:  Optional AuditLogger for RESPONSE_GENERATED events.
        llm_generator: Optional callable(ResponseContext) → str.
                       None = Sprint 2.27.5 deterministic template mode.
                       Non-None = Sprint 2.28 LLM-enriched mode.

    Returns:
        ResponseGenerationService ready to generate response drafts.
    """
    return ResponseGenerationService(
        audit_logger=audit_logger,
        llm_generator=llm_generator,
    )
