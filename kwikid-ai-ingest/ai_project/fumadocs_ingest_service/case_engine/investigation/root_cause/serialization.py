"""
case_engine/investigation/root_cause/serialization.py

Sprint 2.43: JSON serialization for Root Cause Engine outputs.

RootCauseSerializer:
  to_json(analysis)   — RootCauseAnalysis → JSON string
  from_json(text)     — JSON string → RootCauseAnalysis (full roundtrip)
  to_dict(analysis)   — delegates to RootCauseAnalysis.to_dict()

Design constraints:
  - Roundtrip correctness: from_json(to_json(x)) reconstructs all fields.
  - Never raises on to_json() — returns error JSON on failure.
  - from_json() raises ValueError on malformed input.
  - No external dependencies beyond stdlib.

Dependency direction:
  serialization.py → root_cause/models.py (all model types)
  serialization.py → case_engine.investigation.models (enum types)
  serialization.py → stdlib (json, typing)
"""
from __future__ import annotations

import json
import logging
from typing import Any

from case_engine.investigation.models import RecommendedAction, RootCauseCategory
from case_engine.investigation.root_cause.models import (
    AuditMetadata,
    ConfidenceAdjustment,
    ConfidenceBreakdown,
    ContradictionRecord,
    ContradictionSeverity,
    DecisionTrace,
    EscalationLevel,
    EscalationRecommendation,
    EvidenceReference,
    RootCauseAnalysis,
    RuleMatch,
    RuleMatchStatus,
)

LOGGER = logging.getLogger(__name__)


class RootCauseSerializer:
    """
    JSON serializer / deserializer for RootCauseAnalysis.

    All methods are stateless — no instance state is mutated.
    """

    def to_dict(self, analysis: RootCauseAnalysis) -> dict[str, Any]:
        """Delegate to RootCauseAnalysis.to_dict()."""
        return analysis.to_dict()

    def to_json(self, analysis: RootCauseAnalysis, indent: int | None = None) -> str:
        """
        Serialize a RootCauseAnalysis to a JSON string.

        Never raises — returns a JSON error envelope on failure.
        """
        try:
            return json.dumps(analysis.to_dict(), indent=indent, ensure_ascii=False)
        except Exception as exc:
            LOGGER.error("RootCauseSerializer.to_json() failed: %s", exc)
            return json.dumps({
                "error":      "serialization_failed",
                "analysis_id": getattr(analysis, "analysis_id", "UNKNOWN"),
                "reason":     str(exc),
            })

    def from_json(self, text: str) -> RootCauseAnalysis:
        """
        Deserialize a JSON string back to a RootCauseAnalysis.

        Raises:
            ValueError: if the JSON is malformed or missing required fields.
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON: {exc}") from exc

        return self._from_dict(data)

    def from_dict(self, data: dict[str, Any]) -> RootCauseAnalysis:
        """Reconstruct a RootCauseAnalysis from its dict representation."""
        return self._from_dict(data)

    # ── Private reconstruction helpers ─────────────────────────────────────────

    def _from_dict(self, data: dict[str, Any]) -> RootCauseAnalysis:
        try:
            return RootCauseAnalysis(
                analysis_id=data["analysis_id"],
                case_id=data["case_id"],
                category=RootCauseCategory(data["category"]),
                confidence=float(data["confidence"]),
                explanation=data["explanation"],
                evidence_references=tuple(
                    self._evidence_ref(e) for e in data.get("evidence_references", [])
                ),
                supporting_evidence=tuple(data.get("supporting_evidence", [])),
                contradicting_evidence=tuple(data.get("contradicting_evidence", [])),
                missing_evidence=tuple(data.get("missing_evidence", [])),
                recommended_action=RecommendedAction(data["recommended_action"]),
                escalation_recommendation=self._escalation(data["escalation_recommendation"]),
                decision_trace=self._decision_trace(data["decision_trace"]),
                confidence_breakdown=self._confidence_breakdown(data["confidence_breakdown"]),
                rule_matches=tuple(
                    self._rule_match(r) for r in data.get("rule_matches", [])
                ),
                version=data.get("version", ""),
                timestamp=data.get("timestamp", ""),
                audit_metadata=self._audit_metadata(data["audit_metadata"]),
            )
        except (KeyError, ValueError, TypeError) as exc:
            raise ValueError(f"Cannot reconstruct RootCauseAnalysis from dict: {exc}") from exc

    def _evidence_ref(self, d: dict[str, Any]) -> EvidenceReference:
        return EvidenceReference(
            evidence_id=d["evidence_id"],
            evidence_type=d["evidence_type"],
            source=d["source"],
            weight=float(d["weight"]),
        )

    def _confidence_adjustment(self, d: dict[str, Any]) -> ConfidenceAdjustment:
        return ConfidenceAdjustment(
            reason=d["reason"],
            delta=float(d["delta"]),
            component=d["component"],
        )

    def _confidence_breakdown(self, d: dict[str, Any]) -> ConfidenceBreakdown:
        return ConfidenceBreakdown(
            base_confidence=float(d["base_confidence"]),
            adjustments=tuple(self._confidence_adjustment(a) for a in d.get("adjustments", [])),
            final_confidence=float(d["final_confidence"]),
        )

    def _rule_match(self, d: dict[str, Any]) -> RuleMatch:
        return RuleMatch(
            rule_id=d["rule_id"],
            rule_name=d["rule_name"],
            status=RuleMatchStatus(d["status"]),
            confidence_contribution=float(d["confidence_contribution"]),
            evidence_used=tuple(d.get("evidence_used", [])),
            explanation=d["explanation"],
            evaluation_order=int(d["evaluation_order"]),
        )

    def _contradiction(self, d: dict[str, Any]) -> ContradictionRecord:
        return ContradictionRecord(
            contradiction_id=d["contradiction_id"],
            description=d["description"],
            evidence_a_id=d.get("evidence_a_id"),
            evidence_b_id=d.get("evidence_b_id"),
            severity=ContradictionSeverity(d["severity"]),
        )

    def _decision_trace(self, d: dict[str, Any]) -> DecisionTrace:
        return DecisionTrace(
            rules_evaluated=tuple(self._rule_match(r) for r in d.get("rules_evaluated", [])),
            confidence_adjustments=tuple(
                self._confidence_adjustment(a) for a in d.get("confidence_adjustments", [])
            ),
            contradictions_detected=tuple(
                self._contradiction(c) for c in d.get("contradictions_detected", [])
            ),
            missing_evidence_types=tuple(d.get("missing_evidence_types", [])),
            evaluation_order=tuple(d.get("evaluation_order", [])),
        )

    def _escalation(self, d: dict[str, Any]) -> EscalationRecommendation:
        return EscalationRecommendation(
            should_escalate=bool(d["should_escalate"]),
            level=EscalationLevel(d["level"]),
            reason=d["reason"],
            urgency_score=float(d["urgency_score"]),
        )

    def _audit_metadata(self, d: dict[str, Any]) -> AuditMetadata:
        return AuditMetadata(
            analysis_id=d["analysis_id"],
            engine_version=d["engine_version"],
            rule_registry_version=d["rule_registry_version"],
            evidence_bundle_id=d["evidence_bundle_id"],
            plan_id=d["plan_id"],
            case_id=d["case_id"],
            analyzed_at=d["analyzed_at"],
            execution_duration_ms=int(d["execution_duration_ms"]),
            rules_evaluated_count=int(d["rules_evaluated_count"]),
        )
