"""
case_engine/investigation/root_cause/engine.py

Sprint 2.43: RootCauseEngine — the main analysis orchestrator.

Orchestration:
  1. Validate EvidenceBundle (RootCauseValidator).
  2. Build RuleEvaluationContext.
  3. Evaluate all rules in priority order (RuleRegistry.list_rules()).
  4. Detect contradictions from MATCH/PARTIAL_MATCH results with conflicting categories.
  5. Select winning rule (highest confidence; MATCH preferred over PARTIAL_MATCH).
  6. If no rule matched, invoke fallback rule.
  7. Compute final confidence with adjustments.
  8. Map category → RecommendedAction and EscalationRecommendation.
  9. Build DecisionTrace, ConfidenceBreakdown, AuditMetadata.
  10. Return RootCauseAnalysis.

Design constraints:
  - analyze() never returns None — raises InvalidEvidenceError only on bad bundle.
  - All rule errors are caught; the engine always completes.
  - No LLM, no network, no database, no providers.
  - Thread-safe (no shared mutable state; registry uses its own lock).

Dependency direction:
  engine.py → root_cause/registry.py (RuleRegistry, build_default_registry)
  engine.py → root_cause/validators.py (RootCauseValidator)
  engine.py → root_cause/contracts.py (RuleEvaluationContext, RuleResult)
  engine.py → root_cause/models.py (all result types)
  engine.py → root_cause/metrics.py (EngineMetrics, RuleExecutionMetric)
  engine.py → root_cause/exceptions.py (InvalidEvidenceError)
  engine.py → root_cause/versioning.py (CURRENT_ENGINE_VERSION, CURRENT_REGISTRY_VERSION)
  engine.py → case_engine.investigation.models (RootCauseCategory, RecommendedAction)
  engine.py → stdlib (uuid, datetime, logging, time)
"""
from __future__ import annotations

import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.investigation.models import RecommendedAction, RootCauseCategory
from case_engine.investigation.root_cause.contracts import (
    RuleEvaluationContext,
    RuleResult,
)
from case_engine.investigation.root_cause.exceptions import InvalidEvidenceError
from case_engine.investigation.root_cause.metrics import EngineMetrics, RuleExecutionMetric
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
from case_engine.investigation.root_cause.registry import RuleRegistry, build_default_registry
from case_engine.investigation.root_cause.validators import RootCauseValidator
from case_engine.investigation.root_cause.versioning import (
    CURRENT_ENGINE_VERSION,
    CURRENT_REGISTRY_VERSION,
)

LOGGER = logging.getLogger(__name__)

# ── Category → Action mapping ──────────────────────────────────────────────────

_ACTION_MAP: dict[RootCauseCategory, RecommendedAction] = {
    RootCauseCategory.NETWORK_FAILURE:      RecommendedAction.RETRY,
    RootCauseCategory.TIMEOUT:              RecommendedAction.RETRY,
    RootCauseCategory.EXPIRED_SESSION:      RecommendedAction.SESSION_RESET,
    RootCauseCategory.LIVENESS_FAILURE:     RecommendedAction.MANUAL_REVIEW,
    RootCauseCategory.DOCUMENT_FAILURE:     RecommendedAction.MANUAL_REVIEW,
    RootCauseCategory.KYC_REJECTED:         RecommendedAction.ESCALATE,
    RootCauseCategory.SMS_DELIVERY_FAILURE: RecommendedAction.OTP_RESEND,
    RootCauseCategory.CALLBACK_FAILURE:     RecommendedAction.CALLBACK_RETRY,
    RootCauseCategory.QUOTA_EXCEEDED:       RecommendedAction.RETRY,
    RootCauseCategory.PORTAL_UNAVAILABLE:   RecommendedAction.PORTAL_REFRESH,
    RootCauseCategory.ONBOARDING_BLOCKED:   RecommendedAction.ESCALATE,
    RootCauseCategory.REPEATED_FAILURE:     RecommendedAction.ESCALATE,
    RootCauseCategory.VALIDATION_FAILURE:   RecommendedAction.MANUAL_REVIEW,
    RootCauseCategory.UNKNOWN:              RecommendedAction.MANUAL_REVIEW,
}

# ── Category → Escalation level mapping ───────────────────────────────────────

_ESCALATION_MAP: dict[RootCauseCategory, tuple[bool, EscalationLevel]] = {
    RootCauseCategory.KYC_REJECTED:       (True,  EscalationLevel.L3),
    RootCauseCategory.ONBOARDING_BLOCKED: (True,  EscalationLevel.L3),
    RootCauseCategory.REPEATED_FAILURE:   (True,  EscalationLevel.L3),
    RootCauseCategory.LIVENESS_FAILURE:   (True,  EscalationLevel.L2),
    RootCauseCategory.DOCUMENT_FAILURE:   (True,  EscalationLevel.L2),
    RootCauseCategory.UNKNOWN:            (True,  EscalationLevel.L2),
}


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _clamp(value: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, value))


class RootCauseEngine:
    """
    Deterministic rule-based root cause analysis engine.

    Usage:
        engine = RootCauseEngine()                    # uses default registry
        analysis = engine.analyze(bundle, context)

    The engine is stateless after construction — analyze() can be called
    concurrently from multiple threads.
    """

    def __init__(self, registry: RuleRegistry | None = None) -> None:
        self._registry  = registry or build_default_registry()
        self._validator = RootCauseValidator()

    # ── Public API ─────────────────────────────────────────────────────────────

    def analyze(
        self,
        bundle: Any,
        context: RuleEvaluationContext | None = None,
    ) -> RootCauseAnalysis:
        """
        Analyze an EvidenceBundle and return a RootCauseAnalysis.

        Args:
            bundle:  EvidenceBundle produced by the Evidence Collector.
            context: Optional RuleEvaluationContext. If None, one is built
                     from bundle fields.

        Returns:
            RootCauseAnalysis — never None.

        Raises:
            InvalidEvidenceError: if the bundle fails structural validation.
        """
        analysis_id = str(uuid.uuid4())
        start_time  = time.monotonic()

        # 1. Validate bundle
        ok, reasons = self._validator.validate_bundle(bundle)
        if not ok:
            raise InvalidEvidenceError(
                bundle_id=getattr(bundle, "bundle_id", "UNKNOWN"),
                reason="; ".join(reasons),
            )

        # 2. Build context if not provided
        if context is None:
            context = RuleEvaluationContext(
                case_id=getattr(bundle, "case_id", ""),
                topic=getattr(bundle, "topic", ""),
                evaluation_timestamp=_now_iso(),
            )

        # 3. Evaluate all rules
        rules       = self._registry.list_rules()
        rule_results: list[RuleResult]        = []
        rule_metrics: list[RuleExecutionMetric] = []

        for order, rule in enumerate(rules):
            t0 = time.monotonic()
            try:
                result = rule.evaluate(bundle, context)
            except Exception as exc:
                LOGGER.error("Rule %r raised unexpectedly: %s", rule.rule_id, exc)
                result = RuleResult.unknown(rule.rule_id, f"Unexpected rule error: {exc}")
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            rule_results.append(result)
            rule_metrics.append(RuleExecutionMetric(
                rule_id=rule.rule_id,
                rule_name=rule.name,
                status=result.status.value,
                duration_ms=elapsed_ms,
                confidence=result.confidence,
            ))

        # 4. Separate positive matches from non-matches
        positives = [
            r for r in rule_results
            if r.status in (RuleMatchStatus.MATCH, RuleMatchStatus.PARTIAL_MATCH)
        ]

        # 5. Detect contradictions
        contradictions = self._detect_contradictions(positives)

        # 6. Select winning result
        if positives:
            # MATCH beats PARTIAL_MATCH; within same status, highest confidence wins
            winner = max(
                positives,
                key=lambda r: (r.status == RuleMatchStatus.MATCH, r.confidence),
            )
        else:
            # No rule matched — invoke fallback
            fallback = self._registry.get_fallback()
            winner   = fallback.evaluate(bundle, context)

        winning_category = winner.category_hint or RootCauseCategory.UNKNOWN

        # 7. Compute confidence with adjustments
        all_adjustments: list[ConfidenceAdjustment] = list(winner.confidence_adjustments)

        # Corroboration bonus: other MATCH rules that agree on category
        corroborating = [
            r for r in positives
            if r is not winner
            and r.category_hint == winning_category
            and r.status == RuleMatchStatus.MATCH
        ]
        if corroborating:
            all_adjustments.append(ConfidenceAdjustment(
                reason=f"{len(corroborating)} corroborating rule(s) agree on category",
                delta=min(0.05 * len(corroborating), 0.15),
                component="corroboration",
            ))

        # Contradiction penalty
        high_contradictions = [c for c in contradictions if c.severity == ContradictionSeverity.HIGH]
        med_contradictions  = [c for c in contradictions if c.severity == ContradictionSeverity.MEDIUM]
        if high_contradictions:
            all_adjustments.append(ConfidenceAdjustment(
                reason=f"{len(high_contradictions)} high-severity contradiction(s) detected",
                delta=-0.15 * len(high_contradictions),
                component="contradiction_engine",
            ))
        if med_contradictions:
            all_adjustments.append(ConfidenceAdjustment(
                reason=f"{len(med_contradictions)} medium-severity contradiction(s) detected",
                delta=-0.08 * len(med_contradictions),
                component="contradiction_engine",
            ))

        # Missing evidence penalty
        missing_types = self._detect_missing_evidence(bundle)
        if missing_types:
            all_adjustments.append(ConfidenceAdjustment(
                reason=f"Missing evidence types: {', '.join(missing_types)}",
                delta=-0.05 * min(len(missing_types), 3),
                component="missing_evidence",
            ))

        base_confidence  = winner.confidence
        total_adjustment = sum(a.delta for a in all_adjustments)
        final_confidence = _clamp(base_confidence + total_adjustment)

        confidence_breakdown = ConfidenceBreakdown(
            base_confidence=base_confidence,
            adjustments=tuple(all_adjustments),
            final_confidence=final_confidence,
        )

        # 8. Map to action and escalation
        recommended_action = _ACTION_MAP.get(winning_category, RecommendedAction.MANUAL_REVIEW)
        escalation_rec     = self._build_escalation(winning_category, final_confidence)

        # 9. Build DecisionTrace
        rule_matches = tuple(
            RuleMatch(
                rule_id=result.rule_id,
                rule_name=next(
                    (r.name for r in rules if r.rule_id == result.rule_id),
                    result.rule_id,
                ),
                status=result.status,
                confidence_contribution=result.confidence,
                evidence_used=result.evidence_ids_used,
                explanation=result.explanation,
                evaluation_order=idx,
            )
            for idx, result in enumerate(rule_results)
        )

        decision_trace = DecisionTrace(
            rules_evaluated=rule_matches,
            confidence_adjustments=tuple(all_adjustments),
            contradictions_detected=tuple(contradictions),
            missing_evidence_types=tuple(missing_types),
            evaluation_order=tuple(r.rule_id for r in rules),
        )

        # 10. Build evidence references
        supporting_ids    = list(winner.evidence_ids_used)
        contradicting_ids = list({
            eid
            for c in contradictions
            for eid in filter(None, [c.evidence_a_id, c.evidence_b_id])
        })
        evidence_refs = tuple(
            EvidenceReference(
                evidence_id=eid,
                evidence_type="UNKNOWN",
                source="UNKNOWN",
                weight=1.0 / max(len(supporting_ids), 1),
            )
            for eid in supporting_ids
        )

        # 11. Audit metadata
        elapsed_total_ms = int((time.monotonic() - start_time) * 1000)
        audit_meta = AuditMetadata(
            analysis_id=analysis_id,
            engine_version=CURRENT_ENGINE_VERSION,
            rule_registry_version=CURRENT_REGISTRY_VERSION,
            evidence_bundle_id=getattr(bundle, "bundle_id", ""),
            plan_id=getattr(bundle, "plan_id", ""),
            case_id=getattr(bundle, "case_id", ""),
            analyzed_at=_now_iso(),
            execution_duration_ms=elapsed_total_ms,
            rules_evaluated_count=len(rule_results),
        )

        analysis = RootCauseAnalysis(
            analysis_id=analysis_id,
            case_id=getattr(bundle, "case_id", ""),
            category=winning_category,
            confidence=final_confidence,
            explanation=winner.explanation,
            evidence_references=evidence_refs,
            supporting_evidence=tuple(supporting_ids),
            contradicting_evidence=tuple(contradicting_ids),
            missing_evidence=tuple(missing_types),
            recommended_action=recommended_action,
            escalation_recommendation=escalation_rec,
            decision_trace=decision_trace,
            confidence_breakdown=confidence_breakdown,
            rule_matches=tuple(
                rm for rm in rule_matches
                if rm.status in (RuleMatchStatus.MATCH, RuleMatchStatus.PARTIAL_MATCH)
            ),
            version=CURRENT_ENGINE_VERSION,
            timestamp=_now_iso(),
            audit_metadata=audit_meta,
        )

        LOGGER.info(
            "RootCauseEngine: analysis_id=%s case_id=%s category=%s confidence=%.2f duration_ms=%d",
            analysis_id,
            getattr(bundle, "case_id", ""),
            winning_category.value,
            final_confidence,
            elapsed_total_ms,
        )
        return analysis

    def get_metrics(
        self,
        analysis_id: str,
        analysis: RootCauseAnalysis,
        rule_metrics: list[RuleExecutionMetric] | None = None,
    ) -> EngineMetrics:
        """Build EngineMetrics from a completed RootCauseAnalysis."""
        return EngineMetrics.from_trace(
            analysis_id=analysis_id,
            trace=analysis.decision_trace,
            total_duration_ms=analysis.audit_metadata.execution_duration_ms,
            rule_metrics=rule_metrics or [],
        )

    # ── Private helpers ────────────────────────────────────────────────────────

    def _detect_contradictions(
        self, positives: list[RuleResult]
    ) -> list[ContradictionRecord]:
        """
        Detect contradictions: two positive-result rules with different category_hints.
        """
        contradictions: list[ContradictionRecord] = []

        for i in range(len(positives)):
            for j in range(i + 1, len(positives)):
                a, b = positives[i], positives[j]
                if a.category_hint is None or b.category_hint is None:
                    continue
                if a.category_hint == b.category_hint:
                    continue

                # Both matched but on different categories
                if a.status == RuleMatchStatus.MATCH and b.status == RuleMatchStatus.MATCH:
                    severity = ContradictionSeverity.HIGH
                elif (
                    a.status == RuleMatchStatus.MATCH
                    or b.status == RuleMatchStatus.MATCH
                ):
                    severity = ContradictionSeverity.MEDIUM
                else:
                    severity = ContradictionSeverity.LOW

                evidence_a = a.evidence_ids_used[0] if a.evidence_ids_used else None
                evidence_b = b.evidence_ids_used[0] if b.evidence_ids_used else None

                contradictions.append(ContradictionRecord(
                    contradiction_id=str(uuid.uuid4()),
                    description=(
                        f"Rule {a.rule_id!r} suggests {a.category_hint.value} "
                        f"while rule {b.rule_id!r} suggests {b.category_hint.value}."
                    ),
                    evidence_a_id=evidence_a,
                    evidence_b_id=evidence_b,
                    severity=severity,
                ))

        return contradictions

    def _detect_missing_evidence(self, bundle: Any) -> list[str]:
        """
        Return EvidenceType values not present as successful evidence items.
        """
        try:
            from case_engine.investigation.models import EvidenceType
            successful = [e for e in getattr(bundle, "items", []) if getattr(e, "success", False)]
            present    = {getattr(e, "evidence_type", None) for e in successful}
            missing    = [
                et.value for et in EvidenceType
                if et not in present
            ]
            return missing
        except Exception:
            return []

    def _build_escalation(
        self, category: RootCauseCategory, confidence: float
    ) -> EscalationRecommendation:
        """Map category and confidence to an EscalationRecommendation."""
        should_escalate, level = _ESCALATION_MAP.get(
            category, (False, EscalationLevel.NONE)
        )

        # Low confidence always triggers L2 review
        if not should_escalate and confidence < 0.50:
            should_escalate = True
            level           = EscalationLevel.L2

        if should_escalate:
            urgency = _clamp(1.0 - confidence + 0.20) if level == EscalationLevel.L3 else _clamp(0.50)
            reason  = (
                f"Category {category.value} requires escalation "
                f"(confidence={confidence:.2f}, level={level.value})."
            )
        else:
            urgency = 0.0
            reason  = "Automated resolution expected; no escalation required."

        return EscalationRecommendation(
            should_escalate=should_escalate,
            level=level,
            reason=reason,
            urgency_score=urgency,
        )
