"""
case_engine/investigation/root_cause/contracts.py

Sprint 2.43: Contracts (protocols and shared data) for the Root Cause Engine.

RuleEvaluationContext — lightweight data bag passed to every rule.
RuleResult            — what a rule returns from evaluate().
RuleEvaluator         — Protocol all rules must satisfy.

Dependency direction:
  contracts.py → root_cause/models.py (RuleMatchStatus, ConfidenceAdjustment)
  contracts.py → case_engine.investigation.models (RootCauseCategory)
  contracts.py → stdlib (typing, dataclasses)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from case_engine.investigation.models import RootCauseCategory
from case_engine.investigation.root_cause.models import (
    ConfidenceAdjustment,
    RuleMatchStatus,
)


@dataclass
class RuleEvaluationContext:
    """
    Lightweight context passed to every rule during evaluation.

    Rules must not modify this object. It is a read-only data bag
    that gives rules access to topic, slots, and playbook context
    without coupling them to InvestigationContext.
    """
    case_id:              str
    topic:                str
    slots:                dict[str, Any] = field(default_factory=dict)
    playbook_id:          str | None = None
    sop_id:               str | None = None
    evaluation_timestamp: str = ""

    def get_slot(self, name: str, default: Any = None) -> Any:
        return self.slots.get(name, default)


@dataclass(frozen=True)
class RuleResult:
    """
    What a rule returns from its evaluate() call.

    status:               MATCH | PARTIAL_MATCH | NO_MATCH | UNKNOWN
    confidence:           0.0–1.0 for MATCH; 0.0 for NO_MATCH/UNKNOWN
    evidence_ids_used:    IDs of evidence items this rule examined
    explanation:          Human-readable explanation of this rule's finding
    category_hint:        The RootCauseCategory this rule suggests (None if NO_MATCH)
    confidence_adjustments: Fine-grained adjustments this rule recommends
    """
    rule_id:               str
    status:                RuleMatchStatus
    confidence:            float
    evidence_ids_used:     tuple[str, ...]
    explanation:           str
    category_hint:         RootCauseCategory | None
    confidence_adjustments: tuple[ConfidenceAdjustment, ...] = ()

    @classmethod
    def no_match(cls, rule_id: str, reason: str = "") -> "RuleResult":
        return cls(
            rule_id=rule_id,
            status=RuleMatchStatus.NO_MATCH,
            confidence=0.0,
            evidence_ids_used=(),
            explanation=reason or "Rule conditions not met.",
            category_hint=None,
        )

    @classmethod
    def unknown(cls, rule_id: str, reason: str = "") -> "RuleResult":
        return cls(
            rule_id=rule_id,
            status=RuleMatchStatus.UNKNOWN,
            confidence=0.0,
            evidence_ids_used=(),
            explanation=reason or "Evidence insufficient to evaluate rule.",
            category_hint=None,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":               self.rule_id,
            "status":                self.status.value,
            "confidence":            self.confidence,
            "evidence_ids_used":     list(self.evidence_ids_used),
            "explanation":           self.explanation,
            "category_hint":         self.category_hint.value if self.category_hint else None,
            "confidence_adjustments": [a.to_dict() for a in self.confidence_adjustments],
        }


@runtime_checkable
class RuleEvaluator(Protocol):
    """
    Protocol that all concrete rules must satisfy.

    Rules are stateless — evaluate() must be idempotent and side-effect free.
    Rules must NEVER raise — catch all exceptions internally and return
    RuleResult.unknown() on failure.
    """

    @property
    def rule_id(self) -> str: ...

    @property
    def name(self) -> str: ...

    @property
    def priority(self) -> int:
        """Lower value = higher priority; evaluated first."""
        ...

    @property
    def description(self) -> str: ...

    def evaluate(
        self,
        bundle: Any,   # EvidenceBundle — typed loosely to avoid circular import
        context: RuleEvaluationContext,
    ) -> RuleResult: ...
