"""
case_engine/investigation/root_cause/metrics.py

Sprint 2.43: Execution metrics for the Root Cause Engine.

Captures per-rule and aggregate statistics for observability.

Dependency direction:
  metrics.py → root_cause/models.py (RuleMatchStatus, DecisionTrace)
  metrics.py → stdlib (dataclasses, typing)
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.investigation.root_cause.models import DecisionTrace, RuleMatchStatus

if TYPE_CHECKING:
    pass


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


@dataclass(frozen=True)
class RuleExecutionMetric:
    """
    Per-rule execution metric snapshot.

    duration_ms: wall-clock time taken to evaluate this rule.
    confidence:  the confidence value returned by the rule (0.0 if no match).
    """
    rule_id:     str
    rule_name:   str
    status:      str    # RuleMatchStatus.value
    duration_ms: int
    confidence:  float

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id":     self.rule_id,
            "rule_name":   self.rule_name,
            "status":      self.status,
            "duration_ms": self.duration_ms,
            "confidence":  self.confidence,
        }


@dataclass
class EngineMetrics:
    """
    Aggregate execution metrics for one analyze() call.

    Built from a DecisionTrace after engine execution completes.
    """
    analysis_id:          str
    total_rules_evaluated: int
    matches:              int
    partial_matches:      int
    no_matches:           int
    unknowns:             int
    contradictions:       int
    total_duration_ms:    int
    execution_timestamp:  str
    rule_metrics:         list[RuleExecutionMetric] = field(default_factory=list)

    @classmethod
    def from_trace(
        cls,
        analysis_id: str,
        trace: DecisionTrace,
        total_duration_ms: int,
        rule_metrics: list[RuleExecutionMetric] | None = None,
    ) -> "EngineMetrics":
        """Build EngineMetrics from a completed DecisionTrace."""
        matches        = trace.match_count
        partial        = trace.partial_match_count
        no_matches     = trace.no_match_count
        unknowns       = sum(
            1 for r in trace.rules_evaluated
            if r.status == RuleMatchStatus.UNKNOWN
        )
        contradictions = len(trace.contradictions_detected)
        return cls(
            analysis_id=analysis_id,
            total_rules_evaluated=len(trace.rules_evaluated),
            matches=matches,
            partial_matches=partial,
            no_matches=no_matches,
            unknowns=unknowns,
            contradictions=contradictions,
            total_duration_ms=total_duration_ms,
            execution_timestamp=_now_iso(),
            rule_metrics=rule_metrics or [],
        )

    @property
    def total_steps(self) -> int:
        return self.total_rules_evaluated

    @property
    def match_rate(self) -> float:
        if self.total_rules_evaluated == 0:
            return 0.0
        return (self.matches + self.partial_matches) / self.total_rules_evaluated

    def slowest_rule(self) -> RuleExecutionMetric | None:
        if not self.rule_metrics:
            return None
        return max(self.rule_metrics, key=lambda m: m.duration_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "analysis_id":           self.analysis_id,
            "total_rules_evaluated": self.total_rules_evaluated,
            "matches":               self.matches,
            "partial_matches":       self.partial_matches,
            "no_matches":            self.no_matches,
            "unknowns":              self.unknowns,
            "contradictions":        self.contradictions,
            "total_duration_ms":     self.total_duration_ms,
            "execution_timestamp":   self.execution_timestamp,
            "match_rate":            self.match_rate,
            "rule_metrics":          [m.to_dict() for m in self.rule_metrics],
        }
