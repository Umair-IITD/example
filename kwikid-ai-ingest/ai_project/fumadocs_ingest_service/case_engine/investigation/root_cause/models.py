"""
case_engine/investigation/root_cause/models.py

Sprint 2.43: Rich data types for the Root Cause Engine.

Design principles:
  - All output types are frozen (immutable) — analysis results never mutate.
  - Every field is strongly typed — no bare dict[str, Any] in public results.
  - All types support to_dict() for JSON serialization.
  - RootCauseCategory and RecommendedAction are REUSED from Sprint 2.18
    (case_engine.investigation.models) to maintain a single source of truth.

Dependency direction:
  models.py → case_engine.investigation.models (RootCauseCategory, RecommendedAction)
  models.py → stdlib only (no other case_engine imports)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from case_engine.investigation.models import RecommendedAction, RootCauseCategory

# Re-export for consumers that import from this package
__all__ = [
    "RootCauseCategory",
    "RecommendedAction",
    "RuleMatchStatus",
    "EscalationLevel",
    "ContradictionSeverity",
    "EvidenceReference",
    "ConfidenceAdjustment",
    "ConfidenceBreakdown",
    "RuleMatch",
    "ContradictionRecord",
    "DecisionTrace",
    "EscalationRecommendation",
    "AuditMetadata",
    "RootCauseAnalysis",
]


# ── Enumerations ───────────────────────────────────────────────────────────────

class RuleMatchStatus(str, Enum):
    """Result status of a single rule evaluation."""
    MATCH         = "MATCH"
    PARTIAL_MATCH = "PARTIAL_MATCH"
    NO_MATCH      = "NO_MATCH"
    UNKNOWN       = "UNKNOWN"


class EscalationLevel(str, Enum):
    """Escalation urgency level."""
    NONE        = "NONE"
    L2          = "L2"
    L3          = "L3"
    ENGINEERING = "ENGINEERING"
    URGENT      = "URGENT"


class ContradictionSeverity(str, Enum):
    """Severity of a detected contradiction in evidence."""
    HIGH   = "HIGH"
    MEDIUM = "MEDIUM"
    LOW    = "LOW"


# ── Supporting types ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class EvidenceReference:
    """
    A typed reference to one evidence item and its analytical weight.

    weight: 0.0–1.0 float expressing how much this evidence contributed
            to the final conclusion.
    """
    evidence_id:   str
    evidence_type: str   # EvidenceType.value
    source:        str   # EvidenceSource.value
    weight:        float # contribution to conclusion; 0.0–1.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id":   self.evidence_id,
            "evidence_type": self.evidence_type,
            "source":        self.source,
            "weight":        self.weight,
        }


@dataclass(frozen=True)
class ConfidenceAdjustment:
    """
    A single adjustment applied during confidence computation.

    delta: positive or negative float change applied to base confidence.
    component: which analysis component produced this adjustment.
    """
    reason:    str
    delta:     float   # positive or negative; e.g. -0.10, +0.05
    component: str     # e.g. "contradiction_engine", "evidence_quality"

    def to_dict(self) -> dict[str, Any]:
        return {
            "reason":    self.reason,
            "delta":     self.delta,
            "component": self.component,
        }


@dataclass(frozen=True)
class ConfidenceBreakdown:
    """
    Complete decomposition of the final confidence score.

    final_confidence = clamp(base_confidence + sum(adj.delta for adj in adjustments))
    """
    base_confidence:  float
    adjustments:      tuple[ConfidenceAdjustment, ...]
    final_confidence: float   # clamped to [0.0, 1.0]

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_confidence":  self.base_confidence,
            "adjustments":      [a.to_dict() for a in self.adjustments],
            "final_confidence": self.final_confidence,
        }


@dataclass(frozen=True)
class RuleMatch:
    """
    Complete record of one rule's evaluation result.

    Every rule that was evaluated (MATCH, PARTIAL_MATCH, NO_MATCH, UNKNOWN)
    produces a RuleMatch for the DecisionTrace.
    """
    rule_id:                str
    rule_name:              str
    status:                 RuleMatchStatus
    confidence_contribution: float           # 0.0 if NO_MATCH/UNKNOWN
    evidence_used:          tuple[str, ...]  # evidence_ids examined
    explanation:            str
    evaluation_order:       int              # 0-based; order rule was evaluated

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":                self.rule_id,
            "rule_name":              self.rule_name,
            "status":                 self.status.value,
            "confidence_contribution": self.confidence_contribution,
            "evidence_used":          list(self.evidence_used),
            "explanation":            self.explanation,
            "evaluation_order":       self.evaluation_order,
        }


@dataclass(frozen=True)
class ContradictionRecord:
    """
    A single detected contradiction in the evidence.

    Either between two evidence items (evidence_a_id vs evidence_b_id)
    or a structural inconsistency (evidence_a_id only).
    """
    contradiction_id: str
    description:      str
    evidence_a_id:    str | None
    evidence_b_id:    str | None
    severity:         ContradictionSeverity

    def to_dict(self) -> dict[str, Any]:
        return {
            "contradiction_id": self.contradiction_id,
            "description":      self.description,
            "evidence_a_id":    self.evidence_a_id,
            "evidence_b_id":    self.evidence_b_id,
            "severity":         self.severity.value,
        }


@dataclass(frozen=True)
class DecisionTrace:
    """
    Complete, replayable record of every decision made during analysis.

    Every rule evaluated, every confidence adjustment, every contradiction,
    and every missing evidence type is recorded here.
    """
    rules_evaluated:        tuple[RuleMatch, ...]
    confidence_adjustments: tuple[ConfidenceAdjustment, ...]
    contradictions_detected: tuple[ContradictionRecord, ...]
    missing_evidence_types: tuple[str, ...]    # EvidenceType.value names
    evaluation_order:       tuple[str, ...]    # rule_ids in evaluation order

    @property
    def match_count(self) -> int:
        return sum(1 for r in self.rules_evaluated if r.status == RuleMatchStatus.MATCH)

    @property
    def partial_match_count(self) -> int:
        return sum(1 for r in self.rules_evaluated if r.status == RuleMatchStatus.PARTIAL_MATCH)

    @property
    def no_match_count(self) -> int:
        return sum(1 for r in self.rules_evaluated if r.status == RuleMatchStatus.NO_MATCH)

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_evaluated":        [r.to_dict() for r in self.rules_evaluated],
            "confidence_adjustments": [a.to_dict() for a in self.confidence_adjustments],
            "contradictions_detected": [c.to_dict() for c in self.contradictions_detected],
            "missing_evidence_types": list(self.missing_evidence_types),
            "evaluation_order":       list(self.evaluation_order),
            "match_count":            self.match_count,
            "partial_match_count":    self.partial_match_count,
            "no_match_count":         self.no_match_count,
        }


@dataclass(frozen=True)
class EscalationRecommendation:
    """
    Structured escalation recommendation produced by the engine.
    """
    should_escalate: bool
    level:           EscalationLevel
    reason:          str
    urgency_score:   float   # 0.0 (not urgent) – 1.0 (critical)

    def to_dict(self) -> dict[str, Any]:
        return {
            "should_escalate": self.should_escalate,
            "level":           self.level.value,
            "reason":          self.reason,
            "urgency_score":   self.urgency_score,
        }


@dataclass(frozen=True)
class AuditMetadata:
    """
    Immutable audit record attached to every RootCauseAnalysis.

    Enables complete replay of any analysis from its inputs.
    """
    analysis_id:             str
    engine_version:          str
    rule_registry_version:   str
    evidence_bundle_id:      str
    plan_id:                 str
    case_id:                 str
    analyzed_at:             str   # ISO 8601
    execution_duration_ms:   int
    rules_evaluated_count:   int

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id":           self.analysis_id,
            "engine_version":        self.engine_version,
            "rule_registry_version": self.rule_registry_version,
            "evidence_bundle_id":    self.evidence_bundle_id,
            "plan_id":               self.plan_id,
            "case_id":               self.case_id,
            "analyzed_at":           self.analyzed_at,
            "execution_duration_ms": self.execution_duration_ms,
            "rules_evaluated_count": self.rules_evaluated_count,
        }


# ── Main output ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RootCauseAnalysis:
    """
    Sprint 2.43: Rich, fully auditable root cause analysis output.

    This is the primary output of RootCauseEngine.analyze().

    Invariants:
      - Immutable after construction (frozen=True).
      - confidence is always in [0.0, 1.0].
      - decision_trace records every rule evaluated and every adjustment.
      - analysis_id is globally unique (UUID4).
      - All collections are tuples (not lists) to preserve immutability.

    Consumed by:
      - ObservationGenerator (Sprint 2.44)
      - InvestigationContext (context.root_cause_analysis)
      - Audit log
    """
    analysis_id:              str
    case_id:                  str
    category:                 RootCauseCategory
    confidence:               float                     # 0.0 – 1.0
    explanation:              str
    evidence_references:      tuple[EvidenceReference, ...]
    supporting_evidence:      tuple[str, ...]           # evidence_ids
    contradicting_evidence:   tuple[str, ...]           # evidence_ids
    missing_evidence:         tuple[str, ...]           # EvidenceType.value names
    recommended_action:       RecommendedAction
    escalation_recommendation: EscalationRecommendation
    decision_trace:           DecisionTrace
    confidence_breakdown:     ConfidenceBreakdown
    rule_matches:             tuple[RuleMatch, ...]
    version:                  str                       # engine version string
    timestamp:                str                       # ISO 8601
    audit_metadata:           AuditMetadata

    @property
    def escalate(self) -> bool:
        """Convenience accessor matching the Sprint 2.18 interface."""
        return self.escalation_recommendation.should_escalate

    @property
    def analysed_at(self) -> str:
        """Alias for backward-compatibility with Sprint 2.18 consumers."""
        return self.timestamp

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id":              self.analysis_id,
            "case_id":                  self.case_id,
            "category":                 self.category.value,
            "confidence":               self.confidence,
            "explanation":              self.explanation,
            "evidence_references":      [e.to_dict() for e in self.evidence_references],
            "supporting_evidence":      list(self.supporting_evidence),
            "contradicting_evidence":   list(self.contradicting_evidence),
            "missing_evidence":         list(self.missing_evidence),
            "recommended_action":       self.recommended_action.value,
            "escalation_recommendation": self.escalation_recommendation.to_dict(),
            "decision_trace":           self.decision_trace.to_dict(),
            "confidence_breakdown":     self.confidence_breakdown.to_dict(),
            "rule_matches":             [r.to_dict() for r in self.rule_matches],
            "version":                  self.version,
            "timestamp":                self.timestamp,
            "audit_metadata":           self.audit_metadata.to_dict(),
        }
