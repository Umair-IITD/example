"""
case_engine/reasoning/models.py

Sprint 2.24: Investigation Reasoning domain models.

Distinct from reasoning_models.py (Sprint 2.17) which holds the meta-workflow
reasoning models (ReasoningContext, ReasoningDecision, NextStepType).

These models represent the output of the Investigation Reasoning Engine —
the component that combines RootCauseAnalysis + Knowledge Layer output
into a deterministic, auditable reasoning result that becomes the ONLY
input to ActionProposalService.

Blueprint alignment:
  Section 13 — Reasoning Engine:
    Input:  Ticket, Logs, Summary, Tool results, SOP knowledge
    Output: Root Cause, Confidence, Recommended Action, Recommended Escalation
  Principle 2 — Evidence before reasoning
  Principle 3 — Reasoning before execution
  flow_diagram: ROOTCAUSE --> HYBRIDRAG --> REASONING --> GUARDRAILS --> ACTIONPROPOSAL

All models:
  - Frozen dataclasses (immutable after construction)
  - JSON serializable
  - Support to_dict() / from_dict()
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


# ── Outcome enum ──────────────────────────────────────────────────────────────

class ReasoningOutcome(str, Enum):
    """
    High-level outcome of the Investigation Reasoning Engine.

    RECOMMEND_ACTION       : Engine produced a confident action recommendation.
    ESCALATE               : Confidence too low or root cause requires human judgment.
    UNCERTAIN              : Evidence is ambiguous; escalate for safety (fail-closed).
    WAIT_FOR_MORE_EVIDENCE : Investigation incomplete; more evidence needed.
    """
    RECOMMEND_ACTION       = "RECOMMEND_ACTION"
    ESCALATE               = "ESCALATE"
    UNCERTAIN              = "UNCERTAIN"
    WAIT_FOR_MORE_EVIDENCE = "WAIT_FOR_MORE_EVIDENCE"


# ── Recommendation ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReasoningRecommendation:
    """
    A single action recommendation produced by the Reasoning Engine.

    action_type        : canonical action identifier (e.g., "RESET_SESSION")
    confidence         : 0.0–1.0 recommendation confidence
    rationale          : human-readable explanation (audit-safe)
    sop_ids_used       : SOP entry IDs that supported this recommendation
    knowledge_ids_used : knowledge article IDs referenced
    """
    action_type:        str
    confidence:         float
    rationale:          str
    sop_ids_used:       tuple[str, ...] = field(default_factory=tuple)
    knowledge_ids_used: tuple[str, ...] = field(default_factory=tuple)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type":        self.action_type,
            "confidence":         self.confidence,
            "rationale":          self.rationale,
            "sop_ids_used":       list(self.sop_ids_used),
            "knowledge_ids_used": list(self.knowledge_ids_used),
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReasoningRecommendation":
        return cls(
            action_type=d.get("action_type", ""),
            confidence=float(d.get("confidence", 0.0)),
            rationale=d.get("rationale", ""),
            sop_ids_used=tuple(d.get("sop_ids_used") or []),
            knowledge_ids_used=tuple(d.get("knowledge_ids_used") or []),
        )


# ── Trace ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReasoningTrace:
    """
    Audit-safe record of every decision made during reasoning.

    Compliance teams must be able to reconstruct every reasoning decision
    from this trace. It records which rules fired, which evidence was used,
    and what the final recommendation was.

    rule_applied         : name of the rule that determined the outcome
    evidence_ids_used    : IDs of evidence items considered
    sop_ids_used         : SOP IDs used in reasoning
    knowledge_ids_used   : knowledge article IDs used
    decision_path        : ordered list of rule evaluations performed
    final_recommendation : the action_type ultimately recommended
    created_at           : ISO timestamp when trace was generated
    """
    rule_applied:         str
    evidence_ids_used:    tuple[str, ...]
    sop_ids_used:         tuple[str, ...]
    knowledge_ids_used:   tuple[str, ...]
    decision_path:        tuple[str, ...]
    final_recommendation: str
    created_at:           str

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_applied":         self.rule_applied,
            "evidence_ids_used":    list(self.evidence_ids_used),
            "sop_ids_used":         list(self.sop_ids_used),
            "knowledge_ids_used":   list(self.knowledge_ids_used),
            "decision_path":        list(self.decision_path),
            "final_recommendation": self.final_recommendation,
            "created_at":           self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReasoningTrace":
        return cls(
            rule_applied=d.get("rule_applied", ""),
            evidence_ids_used=tuple(d.get("evidence_ids_used") or []),
            sop_ids_used=tuple(d.get("sop_ids_used") or []),
            knowledge_ids_used=tuple(d.get("knowledge_ids_used") or []),
            decision_path=tuple(d.get("decision_path") or []),
            final_recommendation=d.get("final_recommendation", ""),
            created_at=d.get("created_at", _now_iso()),
        )


# ── Bundle ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReasoningBundle:
    """
    Complete output of one reasoning engine invocation.

    bundle_id              : unique ID for this reasoning run
    topic                  : support ticket topic (e.g., VKYC_Session_Failure)
    root_cause_category    : root cause category from investigation (e.g., NETWORK_FAILURE)
    root_cause_confidence  : confidence in the root cause (0.0–1.0)
    outcome                : high-level ReasoningOutcome
    recommendation         : primary action recommendation
    trace                  : full audit trace of reasoning decisions
    should_escalate        : True when human intervention is recommended
    escalate_reason        : why escalation was recommended (if applicable)
    created_at             : ISO timestamp
    """
    bundle_id:             str
    topic:                 str
    root_cause_category:   str
    root_cause_confidence: float
    outcome:               ReasoningOutcome
    recommendation:        ReasoningRecommendation
    trace:                 ReasoningTrace
    should_escalate:       bool
    escalate_reason:       str | None
    created_at:            str

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id":             self.bundle_id,
            "topic":                 self.topic,
            "root_cause_category":   self.root_cause_category,
            "root_cause_confidence": self.root_cause_confidence,
            "outcome":               self.outcome.value,
            "recommendation":        self.recommendation.to_dict(),
            "trace":                 self.trace.to_dict(),
            "should_escalate":       self.should_escalate,
            "escalate_reason":       self.escalate_reason,
            "created_at":            self.created_at,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReasoningBundle":
        return cls(
            bundle_id=d.get("bundle_id", _new_id()),
            topic=d.get("topic", ""),
            root_cause_category=d.get("root_cause_category", "UNKNOWN"),
            root_cause_confidence=float(d.get("root_cause_confidence", 0.0)),
            outcome=ReasoningOutcome(d.get("outcome", ReasoningOutcome.UNCERTAIN.value)),
            recommendation=ReasoningRecommendation.from_dict(d.get("recommendation") or {}),
            trace=ReasoningTrace.from_dict(d.get("trace") or {}),
            should_escalate=bool(d.get("should_escalate", False)),
            escalate_reason=d.get("escalate_reason"),
            created_at=d.get("created_at", _now_iso()),
        )


# ── Result ────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class ReasoningResult:
    """
    Top-level result returned by ReasoningService.reason().

    This is the AUTHORITATIVE input to ActionProposalService.
    No Action Proposal may be generated without a ReasoningResult.

    result_id  : unique ID for this result
    bundle     : the reasoning bundle (contains all decision data)
    created_at : ISO timestamp

    Convenience properties delegate to bundle for common access patterns.
    """
    result_id:  str
    bundle:     ReasoningBundle
    created_at: str

    # ── Convenience properties ─────────────────────────────────────────────────

    @property
    def recommended_action(self) -> str:
        return self.bundle.recommendation.action_type

    @property
    def root_cause_category(self) -> str:
        return self.bundle.root_cause_category

    @property
    def confidence(self) -> float:
        return self.bundle.recommendation.confidence

    @property
    def should_escalate(self) -> bool:
        return self.bundle.should_escalate

    @property
    def outcome(self) -> ReasoningOutcome:
        return self.bundle.outcome

    # ── Serialization ─────────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "result_id":         self.result_id,
            "bundle":            self.bundle.to_dict(),
            "created_at":        self.created_at,
            # Flattened convenience keys for easy dict access in service.py
            "recommended_action":        self.recommended_action,
            "root_cause_category":       self.root_cause_category,
            "confidence":                self.confidence,
            "should_escalate":           self.should_escalate,
            "outcome":                   self.bundle.outcome.value,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "ReasoningResult":
        return cls(
            result_id=d.get("result_id", _new_id()),
            bundle=ReasoningBundle.from_dict(d.get("bundle") or {}),
            created_at=d.get("created_at", _now_iso()),
        )
