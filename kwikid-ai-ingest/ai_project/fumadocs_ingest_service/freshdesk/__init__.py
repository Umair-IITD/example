"""
freshdesk — Freshdesk integration package.

Sprint 2.4  — FreshdeskProvider, FreshdeskConfig, exception hierarchy
Sprint 2.28 — webhook receiver, HMAC verifier, idempotency store,
              async FreshdeskClient, response service, handlers,
              conversation state, metrics.
Sprint 2.48 — SOT reconciliation: ClosureFieldGuard, HTML templates,
              ReplySafetyGate; FreshdeskClient extended endpoints
              (list_tickets, search_tickets, list_agents, list_groups,
               add_public_reply_with_cc).

Dependency direction: freshdesk → case_engine (never the reverse).
"""
from freshdesk.freshdesk_exceptions import (
    FreshdeskApiError,
    FreshdeskAuthError,
    FreshdeskConflictError,
    FreshdeskConnectionError,
    FreshdeskError,
    FreshdeskForbiddenError,
    FreshdeskNotFoundError,
    FreshdeskRateLimitError,
    FreshdeskServerError,
    FreshdeskTimeoutException,
    FreshdeskValidationError,
)
from freshdesk.freshdesk_models import (
    FreshdeskConfig,
    FreshdeskConversation,
    FreshdeskCustomFields,
    FreshdeskLatestComment,
    FreshdeskPriority,
    FreshdeskStatus,
    FreshdeskTicketPayload,
    FreshdeskUpdateEvent,
    FreshdeskWebhookPayload,
)
from freshdesk.freshdesk_provider import FreshdeskProvider

# ── Sprint 2.28 additions ─────────────────────────────────────────────────────
from freshdesk.client import FreshdeskClient, build_freshdesk_client
from freshdesk.conversation_state import (
    ConversationLifecycle,
    ConversationState,
    ConversationStateStore,
)
from freshdesk.handlers import (
    FreshdeskTicketCreatedHandler,
    FreshdeskTicketUpdatedHandler,
    HandlerResult,
)
from freshdesk.idempotency import (
    IdempotencyEntry,
    IdempotencyStatus,
    WebhookIdempotencyStore,
)
from freshdesk.response_service import FreshdeskResponseService
from freshdesk.verifier import (
    FreshdeskWebhookVerifier,
    VerificationResult,
    build_webhook_verifier,
)

# ── Sprint 2.48 additions ─────────────────────────────────────────────────────
from freshdesk.closure_guard import (
    AI_READ_ONLY_CUSTOM_FIELDS,
    AI_WRITEABLE_CUSTOM_FIELDS,
    ALLOWED_TICKET_TYPES,
    REQUIRED_CLOSURE_FIELDS,
    STATUS_CLOSED,
    STATUS_RESOLVED,
    ClosureFieldGuard,
    ClosureGuardError,
    GuardDecision,
)
from freshdesk.safety_gate import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    FORCE_ESCALATION_IMPACT_VALUES,
    GateDecision,
    GateOutcome,
    ReplySafetyGate,
)
from freshdesk.templates import (
    DRAFT_HEADER,
    SIGNATURE,
    build_clarification_reply,
    build_diagnostic_note,
    build_draft_reply_note,
    build_escalation_note,
    build_escalation_reply,
    build_resolution_reply,
    build_unknown_tenant_note,
)

# ── Sprint 2.49 additions ─────────────────────────────────────────────────────
from freshdesk.traces import (
    ALL_TRACE_TAGS,
    TRACE_FD_01_WEBHOOK_RECEIVED,
    TRACE_FD_02_PAYLOAD_NORMALIZED,
    TRACE_FD_03_TENANT_RESOLVED,
    TRACE_FD_04_CASE_CREATED,
    TRACE_FD_05_PIPELINE_STARTED,
    TRACE_FD_06_PIPELINE_COMPLETED,
    TRACE_FD_07_NOTE_PREPARED,
    TRACE_FD_08_NOTE_SENT,
    TRACE_FD_09_REPLY_PREPARED,
    TRACE_FD_10_REPLY_SENT,
    emit_trace,
)

__all__ = [
    # Sprint 2.4 core
    "FreshdeskConfig",
    "FreshdeskProvider",
    "FreshdeskError",
    "FreshdeskApiError",
    "FreshdeskConnectionError",
    "FreshdeskTimeoutException",
    "FreshdeskAuthError",
    "FreshdeskForbiddenError",
    "FreshdeskNotFoundError",
    "FreshdeskConflictError",
    "FreshdeskValidationError",
    "FreshdeskRateLimitError",
    "FreshdeskServerError",
    # Sprint 2.28.1 payload models
    "FreshdeskStatus",
    "FreshdeskPriority",
    "FreshdeskCustomFields",
    "FreshdeskTicketPayload",
    "FreshdeskWebhookPayload",
    "FreshdeskConversation",
    "FreshdeskLatestComment",
    "FreshdeskUpdateEvent",
    # Sprint 2.28 async client + supporting services
    "FreshdeskClient",
    "build_freshdesk_client",
    "FreshdeskWebhookVerifier",
    "VerificationResult",
    "build_webhook_verifier",
    "WebhookIdempotencyStore",
    "IdempotencyEntry",
    "IdempotencyStatus",
    "ConversationStateStore",
    "ConversationState",
    "ConversationLifecycle",
    "FreshdeskResponseService",
    "FreshdeskTicketCreatedHandler",
    "FreshdeskTicketUpdatedHandler",
    "HandlerResult",
    # Sprint 2.48 SOT reconciliation
    "ClosureFieldGuard",
    "ClosureGuardError",
    "GuardDecision",
    "REQUIRED_CLOSURE_FIELDS",
    "ALLOWED_TICKET_TYPES",
    "AI_WRITEABLE_CUSTOM_FIELDS",
    "AI_READ_ONLY_CUSTOM_FIELDS",
    "STATUS_RESOLVED",
    "STATUS_CLOSED",
    "ReplySafetyGate",
    "GateDecision",
    "GateOutcome",
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "FORCE_ESCALATION_IMPACT_VALUES",
    "build_resolution_reply",
    "build_clarification_reply",
    "build_escalation_reply",
    "build_diagnostic_note",
    "build_draft_reply_note",
    "build_escalation_note",
    "build_unknown_tenant_note",
    "SIGNATURE",
    "DRAFT_HEADER",
    # Sprint 2.49 runtime traces
    "emit_trace",
    "ALL_TRACE_TAGS",
    "TRACE_FD_01_WEBHOOK_RECEIVED",
    "TRACE_FD_02_PAYLOAD_NORMALIZED",
    "TRACE_FD_03_TENANT_RESOLVED",
    "TRACE_FD_04_CASE_CREATED",
    "TRACE_FD_05_PIPELINE_STARTED",
    "TRACE_FD_06_PIPELINE_COMPLETED",
    "TRACE_FD_07_NOTE_PREPARED",
    "TRACE_FD_08_NOTE_SENT",
    "TRACE_FD_09_REPLY_PREPARED",
    "TRACE_FD_10_REPLY_SENT",
]
