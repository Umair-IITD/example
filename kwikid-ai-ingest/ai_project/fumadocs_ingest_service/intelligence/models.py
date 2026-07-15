"""
intelligence/models.py

Wave 3: Canonical, strongly-typed models for the Enterprise Intelligence Layer.

All models are frozen dataclasses with `to_dict()` for JSON safety. No
`Optional` overuse — sentinel values (empty tuple/dict/string) where the
field is semantically always present.

Model inventory
---------------
    LLMContext                  — what the LLM sees (Context Builder output)
    RetrievedChunk              — one hybrid-RAG chunk with source metadata
    EvidenceHint                — condensed evidence pointer for the prompt
    ReasoningResult             — validated structured output from the LLM
    ClarificationDecision       — should we ask the customer for more info?
    ObservationDraft            — private-note payload for Freshdesk
    CustomerReplyDraft          — public-reply payload for Freshdesk
    ActionProposal              — proposed action + risk + approval
    IntelligenceResult          — final container returned to the runtime

Dependency direction: models.py → stdlib only.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enums ────────────────────────────────────────────────────────────────────

class ConfidenceLevel(str, Enum):
    """Bucketed confidence label used across reply / observation / action."""
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"

    @classmethod
    def from_float(cls, value: float | None) -> "ConfidenceLevel":
        if value is None:
            return cls.LOW
        try:
            v = float(value)
        except (TypeError, ValueError):
            return cls.LOW
        if v >= 0.75:
            return cls.HIGH
        if v >= 0.45:
            return cls.MEDIUM
        return cls.LOW


class RiskLevel(str, Enum):
    """Risk classification per Blueprint §Enterprise Safety Principles."""
    SAFE       = "SAFE"       # side-effect-free, auto-executable
    MEDIUM     = "MEDIUM"     # confidence-gated
    HIGH       = "HIGH"       # requires human approval
    CRITICAL   = "CRITICAL"   # always requires approval


class ActionKind(str, Enum):
    """
    Canonical action taxonomy. New actions must be added as new enum
    members — never invented ad-hoc by the LLM.
    """
    SEND_REPLY               = "SEND_REPLY"
    POST_INTERNAL_NOTE       = "POST_INTERNAL_NOTE"
    ASK_CLARIFICATION        = "ASK_CLARIFICATION"
    UPDATE_TICKET_FIELDS     = "UPDATE_TICKET_FIELDS"
    RESOLVE_TICKET           = "RESOLVE_TICKET"
    ESCALATE_TO_ENGINEERING  = "ESCALATE_TO_ENGINEERING"
    NO_ACTION                = "NO_ACTION"

    @classmethod
    def from_str(cls, value: str | None) -> "ActionKind":
        if not value:
            return cls.NO_ACTION
        low = str(value).strip().upper()
        try:
            return cls(low)
        except ValueError:
            return cls.NO_ACTION


class ReasoningOutcome(str, Enum):
    """Final outcome the reasoning layer produced."""
    RESOLVED               = "RESOLVED"
    NEEDS_CLARIFICATION    = "NEEDS_CLARIFICATION"
    ESCALATE               = "ESCALATE"
    INSUFFICIENT_EVIDENCE  = "INSUFFICIENT_EVIDENCE"


# ── Context Builder output ───────────────────────────────────────────────────

@dataclass(frozen=True)
class RetrievedChunk:
    """One SOP / knowledge chunk from the Hybrid RAG retrieval."""
    chunk_id:  str
    source:    str       # e.g. "stackoverflow", "fumadocs", "freshdesk_history"
    title:     str
    content:   str
    score:     float
    url:       str = ""
    metadata:  dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "source":   self.source,
            "title":    self.title,
            "content":  self.content[:2000],
            "score":    self.score,
            "url":      self.url,
            "metadata": dict(self.metadata),
        }


@dataclass(frozen=True)
class EvidenceHint:
    """
    Condensed pointer to an EvidenceBundle item. The full evidence stays in
    the InvestigationContext; the prompt sees only these hints so token
    count stays bounded.
    """
    source:       str      # e.g. "MetricTool", "GetSessionDetailsTool"
    kind:         str      # EvidenceType value: SESSION / USER / LOG / METRIC / SUMMARY
    summary:      str      # human-readable one-liner
    is_available: bool = True
    ref_id:       str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source":       self.source,
            "kind":         self.kind,
            "summary":      self.summary,
            "is_available": self.is_available,
            "ref_id":       self.ref_id,
        }


@dataclass(frozen=True)
class LLMContext:
    """
    Fully-assembled context object handed to the PromptBuilder.

    This is the sole boundary through which the LLM sees the world — no
    other information reaches the prompt. Deterministic construction (given
    identical inputs → identical LLMContext); no LLM logic here.
    """
    case_id:            str
    ticket_id:          str
    tenant_id:          str
    topic:              str
    workflow_id:        str = ""
    classification:     str = ""
    ticket_subject:     str = ""
    ticket_description: str = ""
    customer_display:   str = ""       # masked customer identifier — safe to render

    evidence_hints:     tuple[EvidenceHint, ...] = field(default_factory=tuple)
    retrieved_chunks:   tuple[RetrievedChunk, ...] = field(default_factory=tuple)
    conversation:       tuple[dict[str, str], ...] = field(default_factory=tuple)
    tool_results:       dict[str, Any] = field(default_factory=dict)

    trace_id:           str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":            self.case_id,
            "ticket_id":          self.ticket_id,
            "tenant_id":          self.tenant_id,
            "topic":              self.topic,
            "workflow_id":        self.workflow_id,
            "classification":     self.classification,
            "ticket_subject":     self.ticket_subject,
            "ticket_description": self.ticket_description[:2000],
            "customer_display":   self.customer_display,
            "evidence_hints":     [e.to_dict() for e in self.evidence_hints],
            "retrieved_chunks":   [c.to_dict() for c in self.retrieved_chunks],
            "conversation":       list(self.conversation),
            "tool_results":       dict(self.tool_results),
            "trace_id":           self.trace_id,
        }


# ── Reasoning Result (LLM output) ────────────────────────────────────────────

@dataclass(frozen=True)
class ReasoningResult:
    """
    Validated structured output from the LLM. Every field is required by
    the schema; missing / invalid values produce a `ReasoningParseError`.
    """
    outcome:              ReasoningOutcome
    summary:              str
    root_cause:           str
    confidence:           float
    confidence_level:     ConfidenceLevel
    evidence_used:        tuple[str, ...]
    missing_information:  tuple[str, ...]
    clarification_required: bool
    reasoning_notes:      str = ""
    prompt_version:       str = ""
    llm_model:            str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome":              self.outcome.value,
            "summary":              self.summary,
            "root_cause":           self.root_cause,
            "confidence":           self.confidence,
            "confidence_level":     self.confidence_level.value,
            "evidence_used":        list(self.evidence_used),
            "missing_information":  list(self.missing_information),
            "clarification_required": self.clarification_required,
            "reasoning_notes":      self.reasoning_notes,
            "prompt_version":       self.prompt_version,
            "llm_model":            self.llm_model,
        }


# ── Clarification Decision ───────────────────────────────────────────────────

@dataclass(frozen=True)
class ClarificationDecision:
    """Whether to ask the customer for more information — and what to ask."""
    should_clarify:    bool
    questions:         tuple[str, ...] = field(default_factory=tuple)
    reason:            str = ""
    required_slots:    tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_clarify":  self.should_clarify,
            "questions":       list(self.questions),
            "reason":          self.reason,
            "required_slots":  list(self.required_slots),
        }


# ── Observation Draft (private note) ─────────────────────────────────────────

@dataclass(frozen=True)
class ObservationDraft:
    """
    L1-style Freshdesk internal observation. This is the private-note
    payload the Execution Layer will pass to `FreshdeskResponseService.add_internal_note`.

    Content is already HTML-formatted per Sprint 2.48 templates convention.
    """
    issue_summary:     str
    evidence:          str        # HTML block
    root_cause:        str
    recommended_action: str
    escalation:        str        # "None" | "L2 Engineering" | "Human Review"
    confidence_level:  ConfidenceLevel
    body_html:         str        # final ready-to-post HTML

    def to_dict(self) -> dict[str, Any]:
        return {
            "issue_summary":     self.issue_summary,
            "evidence":          self.evidence,
            "root_cause":        self.root_cause,
            "recommended_action": self.recommended_action,
            "escalation":        self.escalation,
            "confidence_level":  self.confidence_level.value,
            "body_html":         self.body_html,
        }


# ── Customer Reply Draft (public reply) ──────────────────────────────────────

@dataclass(frozen=True)
class CustomerReplyDraft:
    """
    Public-reply payload. Callers pass `body_html` to
    `ReplySafetyGate.check(...)` before invoking
    `FreshdeskResponseService.send_customer_reply`.
    """
    reply_kind:        str        # "resolution" | "clarification" | "escalation"
    body_html:         str
    confidence_level:  ConfidenceLevel
    confidence:        float
    citations:         tuple[str, ...] = field(default_factory=tuple)  # SOP URLs

    def to_dict(self) -> dict[str, Any]:
        return {
            "reply_kind":       self.reply_kind,
            "body_html":        self.body_html,
            "confidence_level": self.confidence_level.value,
            "confidence":       self.confidence,
            "citations":        list(self.citations),
        }


# ── Action Proposal ──────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ActionProposal:
    """
    Proposed action — NEVER executed by the Intelligence Layer. The
    Action Gateway (or a future Wave 4 approval flow) decides whether to
    run it.
    """
    action_kind:        ActionKind
    parameters:         dict[str, Any] = field(default_factory=dict)
    confidence:         float = 0.0
    risk:               RiskLevel = RiskLevel.SAFE
    approval_required:  bool = False
    rationale:          str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_kind":       self.action_kind.value,
            "parameters":        dict(self.parameters),
            "confidence":        self.confidence,
            "risk":              self.risk.value,
            "approval_required": self.approval_required,
            "rationale":         self.rationale,
        }


# ── Top-level intelligence result ────────────────────────────────────────────

@dataclass(frozen=True)
class IntelligenceResult:
    """
    The single object returned by `IntelligenceOrchestrator.orchestrate()`
    to the runtime. Contains everything downstream layers need to decide
    what to do next — but takes no action itself.
    """
    case_id:              str
    ticket_id:            str
    reasoning:            ReasoningResult
    clarification:        ClarificationDecision
    observation:          ObservationDraft
    customer_reply:       CustomerReplyDraft | None
    action_proposals:     tuple[ActionProposal, ...] = field(default_factory=tuple)
    llm_model:            str = ""
    duration_ms:          int = 0
    trace_id:             str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "case_id":          self.case_id,
            "ticket_id":        self.ticket_id,
            "reasoning":        self.reasoning.to_dict(),
            "clarification":    self.clarification.to_dict(),
            "observation":      self.observation.to_dict(),
            "customer_reply":   None if self.customer_reply is None else self.customer_reply.to_dict(),
            "action_proposals": [p.to_dict() for p in self.action_proposals],
            "llm_model":        self.llm_model,
            "duration_ms":      self.duration_ms,
            "trace_id":         self.trace_id,
        }
