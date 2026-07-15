"""
case_engine/investigation/observation/models.py

Sprint 2.44: Rich data types for the Observation Generator.

Design principles:
  - Observation is frozen (immutable) — generated notes never mutate.
  - Every field is strongly typed — no bare dict[str, Any] in public results.
  - All types support to_dict() for JSON serialization.
  - All analytical fields are copied VERBATIM from RootCauseAnalysis —
    the Observation Generator never invents, rewrites, or hides evidence
    (blueprint Section 26 guardrails).

Dependency direction:
  models.py → case_engine.investigation.root_cause.models (analysis types)
  models.py → case_engine.investigation.models (RootCauseCategory, RecommendedAction, Evidence)
  models.py → stdlib only (no other case_engine imports)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from case_engine.investigation.models import (
    Evidence,
    RecommendedAction,
    RootCauseCategory,
)
from case_engine.investigation.root_cause.models import (
    ConfidenceBreakdown,
    ContradictionRecord,
    DecisionTrace,
    EscalationRecommendation,
    EvidenceReference,
    RootCauseAnalysis,
    RuleMatchStatus,
)

__all__ = [
    "ObservationStatus",
    "TimelineEntry",
    "InvestigationStepRecord",
    "DecisionTraceSummary",
    "ObservationAuditMetadata",
    "Observation",
    "ObservationDraft",
    "REQUIRED_SECTION_HEADERS",
]


# ── Section headers (canonical) ────────────────────────────────────────────────

REQUIRED_SECTION_HEADERS: tuple[str, ...] = (
    "=== ISSUE SUMMARY ===",
    "=== EVIDENCE SUMMARY ===",
    "=== SUPPORTING EVIDENCE ===",
    "=== CONTRADICTING EVIDENCE ===",
    "=== MISSING EVIDENCE ===",
    "=== CONTRADICTIONS ===",
    "=== ROOT CAUSE ===",
    "=== CONFIDENCE ===",
    "=== RECOMMENDATION ===",
    "=== ESCALATION ===",
    "=== TIMELINE ===",
    "=== INVESTIGATION STEPS ===",
    "=== DECISION TRACE ===",
    "=== EVIDENCE REFERENCES ===",
    "=== AUDIT ===",
)


# ── Enumerations ───────────────────────────────────────────────────────────────

class ObservationStatus(str, Enum):
    """
    Outcome status of one observation generation run.

    COMPLETE: specific template rendered with a full evidence bundle.
    PARTIAL:  rendered without an evidence bundle (analysis-only inputs).
    FALLBACK: the fallback template was used (no specific template matched
              or the specific template failed to render).
    ERROR:    rendering failed entirely — deterministic error note produced.
    """
    COMPLETE = "COMPLETE"
    PARTIAL  = "PARTIAL"
    FALLBACK = "FALLBACK"
    ERROR    = "ERROR"


# ── Supporting types ───────────────────────────────────────────────────────────

@dataclass(frozen=True)
class TimelineEntry:
    """
    One event in the investigation timeline.

    Derived exclusively from timestamps already present on the inputs
    (evidence collected_at, bundle collected_at, analysis timestamp).
    """
    timestamp:   str          # ISO 8601
    event:       str
    source:      str          # component or tool that produced the event
    evidence_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp":   self.timestamp,
            "event":       self.event,
            "source":      self.source,
            "evidence_id": self.evidence_id,
        }


@dataclass(frozen=True)
class InvestigationStepRecord:
    """
    One investigation step that was performed, as recorded for the note.

    Derived from InvestigationPlan steps (when available) cross-referenced
    with the evidence bundle, or from the bundle items directly.
    """
    step_id:            str
    sequence:           int
    tool_name:          str
    purpose:            str
    evidence_collected: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_id":            self.step_id,
            "sequence":           self.sequence,
            "tool_name":          self.tool_name,
            "purpose":            self.purpose,
            "evidence_collected": self.evidence_collected,
        }


@dataclass(frozen=True)
class DecisionTraceSummary:
    """
    Aggregate summary of the engine's DecisionTrace for the note.

    Pure aggregation of counts already present in the trace — the summary
    never re-derives a winner or re-scores rules (that would be reasoning).
    """
    rules_evaluated:        int
    matches:                int
    partial_matches:        int
    no_matches:             int
    unknowns:               int
    contradictions:         int
    missing_evidence_count: int
    evaluation_order:       tuple[str, ...]

    @classmethod
    def from_trace(cls, trace: DecisionTrace) -> "DecisionTraceSummary":
        """Build a summary from a completed DecisionTrace."""
        unknowns = sum(
            1 for r in trace.rules_evaluated
            if r.status == RuleMatchStatus.UNKNOWN
        )
        return cls(
            rules_evaluated=len(trace.rules_evaluated),
            matches=trace.match_count,
            partial_matches=trace.partial_match_count,
            no_matches=trace.no_match_count,
            unknowns=unknowns,
            contradictions=len(trace.contradictions_detected),
            missing_evidence_count=len(trace.missing_evidence_types),
            evaluation_order=tuple(trace.evaluation_order),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rules_evaluated":        self.rules_evaluated,
            "matches":                self.matches,
            "partial_matches":        self.partial_matches,
            "no_matches":             self.no_matches,
            "unknowns":               self.unknowns,
            "contradictions":         self.contradictions,
            "missing_evidence_count": self.missing_evidence_count,
            "evaluation_order":       list(self.evaluation_order),
        }


@dataclass(frozen=True)
class ObservationAuditMetadata:
    """
    Immutable audit record attached to every Observation.

    Records which inputs produced the note (analysis, bundle, context,
    SOP, playbook) and how it was generated (versions, template, duration).
    Per blueprint Section 28 (audit requirements).
    """
    observation_id:            str
    generator_version:         str
    template_registry_version: str
    template_id:               str
    analysis_id:               str
    evidence_bundle_id:        str | None
    case_id:                   str
    context_id:                str | None
    sop_id:                    str | None
    playbook_id:               str | None
    generated_at:              str   # ISO 8601
    generation_duration_ms:    int

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id":            self.observation_id,
            "generator_version":         self.generator_version,
            "template_registry_version": self.template_registry_version,
            "template_id":               self.template_id,
            "analysis_id":               self.analysis_id,
            "evidence_bundle_id":        self.evidence_bundle_id,
            "case_id":                   self.case_id,
            "context_id":                self.context_id,
            "sop_id":                    self.sop_id,
            "playbook_id":               self.playbook_id,
            "generated_at":              self.generated_at,
            "generation_duration_ms":    self.generation_duration_ms,
        }


# ── Main output ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class Observation:
    """
    Sprint 2.44: Rich, fully typed L1 investigation observation.

    This is the primary output of ObservationGenerator.generate().

    Invariants:
      - Immutable after construction (frozen=True).
      - All analytical fields (supporting/contradicting/missing evidence,
        contradictions, confidence, category, action, escalation,
        explanation, references) are copied VERBATIM from RootCauseAnalysis.
      - observation_id is globally unique (UUID4).
      - All collections are tuples (not lists) to preserve immutability.

    Consumed by:
      - InvestigationContext (context.observation)
      - Freshdesk internal note write-back (observation_text)
      - Reasoning Engine (pending, downstream)
    """
    observation_id:         str
    case_id:                str
    topic:                  str
    status:                 ObservationStatus
    issue_summary:          str
    evidence_summary:       str
    supporting_evidence:    tuple[str, ...]
    contradicting_evidence: tuple[str, ...]
    missing_evidence:       tuple[str, ...]
    contradictions:         tuple[ContradictionRecord, ...]
    root_cause_category:    RootCauseCategory
    root_cause_explanation: str
    confidence:             float
    confidence_breakdown:   ConfidenceBreakdown
    recommended_action:     RecommendedAction
    escalation:             EscalationRecommendation
    timeline:               tuple[TimelineEntry, ...]
    investigation_steps:    tuple[InvestigationStepRecord, ...]
    decision_trace_summary: DecisionTraceSummary
    evidence_references:    tuple[EvidenceReference, ...]
    observation_text:       str
    template_id:            str
    analysis_id:            str
    audit_metadata:         ObservationAuditMetadata
    generated_at:           str   # ISO 8601
    version:                str   # generator version string

    @property
    def escalate(self) -> bool:
        """Convenience accessor mirroring RootCauseAnalysis.escalate."""
        return self.escalation.should_escalate

    def to_dict(self) -> dict[str, Any]:
        return {
            "observation_id":         self.observation_id,
            "case_id":                self.case_id,
            "topic":                  self.topic,
            "status":                 self.status.value,
            "issue_summary":          self.issue_summary,
            "evidence_summary":       self.evidence_summary,
            "supporting_evidence":    list(self.supporting_evidence),
            "contradicting_evidence": list(self.contradicting_evidence),
            "missing_evidence":       list(self.missing_evidence),
            "contradictions":         [c.to_dict() for c in self.contradictions],
            "root_cause_category":    self.root_cause_category.value,
            "root_cause_explanation": self.root_cause_explanation,
            "confidence":             self.confidence,
            "confidence_breakdown":   self.confidence_breakdown.to_dict(),
            "recommended_action":     self.recommended_action.value,
            "escalation":             self.escalation.to_dict(),
            "timeline":               [t.to_dict() for t in self.timeline],
            "investigation_steps":    [s.to_dict() for s in self.investigation_steps],
            "decision_trace_summary": self.decision_trace_summary.to_dict(),
            "evidence_references":    [e.to_dict() for e in self.evidence_references],
            "observation_text":       self.observation_text,
            "template_id":            self.template_id,
            "analysis_id":            self.analysis_id,
            "audit_metadata":         self.audit_metadata.to_dict(),
            "generated_at":           self.generated_at,
            "version":                self.version,
        }


# ── Internal draft (generator → template data bag) ─────────────────────────────

@dataclass
class ObservationDraft:
    """
    Internal data bag passed from the generator to templates.

    Templates read from this draft and format — they never mutate the
    underlying analysis or evidence. Not part of the public output API.
    """
    analysis:               RootCauseAnalysis
    case_id:                str
    topic:                  str
    timeline:               tuple[TimelineEntry, ...]
    investigation_steps:    tuple[InvestigationStepRecord, ...]
    decision_trace_summary: DecisionTraceSummary
    evidence_items:         tuple[Evidence, ...] = field(default_factory=tuple)
    generated_at:           str = ""
    generator_version:      str = ""
