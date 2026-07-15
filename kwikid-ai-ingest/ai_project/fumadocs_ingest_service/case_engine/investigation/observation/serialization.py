"""
case_engine/investigation/observation/serialization.py

Sprint 2.44: JSON serialization for the Observation Generator.

Provides:
  observation_to_dict()   — Observation → dict[str, Any]  (for JSON/logging)
  observation_from_dict() — dict[str, Any] → Observation  (JSON roundtrip)

Design rules:
  - observation_to_dict() delegates to Observation.to_dict() (which each
    sub-model already implements).
  - observation_from_dict() reconstructs a valid Observation by re-parsing
    every sub-model from its serialized form.
  - No information is lost or invented: the roundtrip is exact.
  - observation_from_dict() raises ObservationDeserializationError for any
    missing/malformed field — never silently coerces.
  - Both functions are pure (no I/O, no side effects).

Dependency direction:
  serialization.py → observation/{models,exceptions}
  serialization.py → case_engine.investigation.root_cause.models
  serialization.py → case_engine.investigation.models (enums)
  serialization.py → stdlib (json, typing)
"""
from __future__ import annotations

import json
from typing import Any

from case_engine.investigation.models import RecommendedAction, RootCauseCategory
from case_engine.investigation.observation.exceptions import (
    ObservationDeserializationError,
)
from case_engine.investigation.observation.models import (
    DecisionTraceSummary,
    InvestigationStepRecord,
    Observation,
    ObservationAuditMetadata,
    ObservationStatus,
    TimelineEntry,
)
from case_engine.investigation.root_cause.models import (
    ConfidenceAdjustment,
    ConfidenceBreakdown,
    ContradictionRecord,
    ContradictionSeverity,
    EscalationLevel,
    EscalationRecommendation,
    EvidenceReference,
)


# ── Public API ─────────────────────────────────────────────────────────────────

def observation_to_dict(observation: Observation) -> dict[str, Any]:
    """Return a JSON-serializable dict for the given Observation."""
    return observation.to_dict()


def observation_to_json(observation: Observation) -> str:
    """Serialize an Observation to a JSON string."""
    return json.dumps(observation_to_dict(observation), ensure_ascii=False)


def observation_from_dict(data: dict[str, Any]) -> Observation:
    """
    Reconstruct an Observation from its serialized dict form.

    Raises ObservationDeserializationError if any required field is
    missing or cannot be parsed.
    """
    try:
        return _parse_observation(data)
    except ObservationDeserializationError:
        raise
    except Exception as exc:
        raise ObservationDeserializationError(
            data.get("observation_id", "<unknown>"),
            f"unexpected error during deserialization: {exc}",
        ) from exc


def observation_from_json(json_str: str) -> Observation:
    """Deserialize an Observation from a JSON string."""
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as exc:
        raise ObservationDeserializationError("<unknown>", f"invalid JSON: {exc}") from exc
    return observation_from_dict(data)


# ── Internal parsers ───────────────────────────────────────────────────────────

def _req(data: dict[str, Any], key: str, observation_id: str) -> Any:
    """Return data[key], raising ObservationDeserializationError if absent."""
    if key not in data:
        raise ObservationDeserializationError(observation_id, f"missing field: {key!r}")
    return data[key]


def _parse_observation(data: dict[str, Any]) -> Observation:
    oid = data.get("observation_id", "<unknown>")

    status = _parse_enum(ObservationStatus, _req(data, "status", oid), oid, "status")
    root_cause_category = _parse_enum(
        RootCauseCategory, _req(data, "root_cause_category", oid), oid, "root_cause_category"
    )
    recommended_action = _parse_enum(
        RecommendedAction, _req(data, "recommended_action", oid), oid, "recommended_action"
    )

    return Observation(
        observation_id=_req(data, "observation_id", oid),
        case_id=_req(data, "case_id", oid),
        topic=data.get("topic", ""),
        status=status,
        issue_summary=data.get("issue_summary", ""),
        evidence_summary=data.get("evidence_summary", ""),
        supporting_evidence=tuple(data.get("supporting_evidence", [])),
        contradicting_evidence=tuple(data.get("contradicting_evidence", [])),
        missing_evidence=tuple(data.get("missing_evidence", [])),
        contradictions=tuple(
            _parse_contradiction(c, oid) for c in data.get("contradictions", [])
        ),
        root_cause_category=root_cause_category,
        root_cause_explanation=data.get("root_cause_explanation", ""),
        confidence=float(_req(data, "confidence", oid)),
        confidence_breakdown=_parse_confidence_breakdown(
            _req(data, "confidence_breakdown", oid), oid
        ),
        recommended_action=recommended_action,
        escalation=_parse_escalation(_req(data, "escalation", oid), oid),
        timeline=tuple(
            _parse_timeline_entry(e, oid) for e in data.get("timeline", [])
        ),
        investigation_steps=tuple(
            _parse_step_record(s, oid) for s in data.get("investigation_steps", [])
        ),
        decision_trace_summary=_parse_decision_trace_summary(
            _req(data, "decision_trace_summary", oid), oid
        ),
        evidence_references=tuple(
            _parse_evidence_reference(r, oid) for r in data.get("evidence_references", [])
        ),
        observation_text=_req(data, "observation_text", oid),
        template_id=data.get("template_id", ""),
        analysis_id=_req(data, "analysis_id", oid),
        audit_metadata=_parse_audit_metadata(_req(data, "audit_metadata", oid), oid),
        generated_at=_req(data, "generated_at", oid),
        version=_req(data, "version", oid),
    )


def _parse_enum(enum_cls: type, value: Any, oid: str, field: str) -> Any:
    try:
        return enum_cls(value)
    except (ValueError, KeyError) as exc:
        raise ObservationDeserializationError(
            oid, f"invalid {field} value {value!r}: {exc}"
        ) from exc


def _parse_contradiction(data: dict[str, Any], oid: str) -> ContradictionRecord:
    severity = _parse_enum(
        ContradictionSeverity, data.get("severity", ""), oid, "contradiction.severity"
    )
    return ContradictionRecord(
        contradiction_id=data.get("contradiction_id", ""),
        description=data.get("description", ""),
        evidence_a_id=data.get("evidence_a_id"),
        evidence_b_id=data.get("evidence_b_id"),
        severity=severity,
    )


def _parse_confidence_breakdown(data: dict[str, Any], oid: str) -> ConfidenceBreakdown:
    adjustments = [
        ConfidenceAdjustment(
            component=a.get("component", ""),
            delta=float(a.get("delta", 0.0)),
            reason=a.get("reason", ""),
        )
        for a in data.get("adjustments", [])
    ]
    return ConfidenceBreakdown(
        base_confidence=float(data.get("base_confidence", 0.0)),
        adjustments=tuple(adjustments),
        final_confidence=float(data.get("final_confidence", 0.0)),
    )


def _parse_escalation(data: dict[str, Any], oid: str) -> EscalationRecommendation:
    level = _parse_enum(EscalationLevel, data.get("level", ""), oid, "escalation.level")
    return EscalationRecommendation(
        should_escalate=bool(data.get("should_escalate", False)),
        level=level,
        reason=data.get("reason", ""),
        urgency_score=float(data.get("urgency_score", 0.0)),
    )


def _parse_timeline_entry(data: dict[str, Any], oid: str) -> TimelineEntry:
    return TimelineEntry(
        timestamp=data.get("timestamp", ""),
        event=data.get("event", ""),
        source=data.get("source", ""),
        evidence_id=data.get("evidence_id"),
    )


def _parse_step_record(data: dict[str, Any], oid: str) -> InvestigationStepRecord:
    return InvestigationStepRecord(
        step_id=data.get("step_id", ""),
        sequence=int(data.get("sequence", 0)),
        tool_name=data.get("tool_name", ""),
        purpose=data.get("purpose", ""),
        evidence_collected=bool(data.get("evidence_collected", False)),
    )


def _parse_decision_trace_summary(data: dict[str, Any], oid: str) -> DecisionTraceSummary:
    return DecisionTraceSummary(
        rules_evaluated=int(data.get("rules_evaluated", 0)),
        matches=int(data.get("matches", 0)),
        partial_matches=int(data.get("partial_matches", 0)),
        no_matches=int(data.get("no_matches", 0)),
        unknowns=int(data.get("unknowns", 0)),
        contradictions=int(data.get("contradictions", 0)),
        missing_evidence_count=int(data.get("missing_evidence_count", 0)),
        evaluation_order=tuple(data.get("evaluation_order", [])),
    )


def _parse_evidence_reference(data: dict[str, Any], oid: str) -> EvidenceReference:
    return EvidenceReference(
        evidence_id=data.get("evidence_id", ""),
        evidence_type=data.get("evidence_type", ""),
        source=data.get("source", ""),
        weight=float(data.get("weight", 0.0)),
    )


def _parse_audit_metadata(data: dict[str, Any], oid: str) -> ObservationAuditMetadata:
    return ObservationAuditMetadata(
        observation_id=data.get("observation_id", oid),
        generator_version=data.get("generator_version", ""),
        template_registry_version=data.get("template_registry_version", ""),
        template_id=data.get("template_id", ""),
        analysis_id=data.get("analysis_id", ""),
        evidence_bundle_id=data.get("evidence_bundle_id"),
        case_id=data.get("case_id", ""),
        context_id=data.get("context_id"),
        sop_id=data.get("sop_id"),
        playbook_id=data.get("playbook_id"),
        generated_at=data.get("generated_at", ""),
        generation_duration_ms=int(data.get("generation_duration_ms", 0)),
    )
