"""
case_engine/investigation/orchestrator/serialization.py

Sprint 2.46: Serialization for OrchestratorInvestigationResult.

Supports dict and JSON roundtrip. Heavy payloads (EvidenceBundle, Observation)
are summarised structurally — full payload is not serialized for safety.

Dependency direction:
  serialization.py → orchestrator/models.py (OrchestratorInvestigationResult) via TYPE_CHECKING
  serialization.py → stdlib only
"""
from __future__ import annotations

import json
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from case_engine.investigation.orchestrator.models import OrchestratorInvestigationResult


def result_to_dict(result: "OrchestratorInvestigationResult") -> dict[str, Any]:
    """
    Serialize OrchestratorInvestigationResult to a JSON-serializable dict.

    Heavy sub-objects (plan, evidence, root_cause, observation) are summarized
    by their IDs and counts — not fully expanded — to keep the output suitable
    for logging and API responses.
    """
    plan_dict: dict[str, Any] | None = None
    if result.plan is not None:
        try:
            plan_dict = {
                "plan_id":    result.plan.plan_id,
                "topic":      result.plan.topic,
                "step_count": len(result.plan.steps),
                "case_id":    result.plan.case_id,
            }
        except Exception:  # noqa: BLE001
            plan_dict = {"error": "serialization_failed"}

    evidence_dict: dict[str, Any] | None = None
    if result.evidence is not None:
        try:
            evidence_dict = {
                "bundle_id":    result.evidence.bundle_id,
                "item_count":   len(result.evidence.items),
                "topic":        result.evidence.topic,
                "collected_at": result.evidence.collected_at,
            }
        except Exception:  # noqa: BLE001
            evidence_dict = {"error": "serialization_failed"}

    root_cause_dict: dict[str, Any] | None = None
    if result.root_cause is not None:
        try:
            rc = result.root_cause
            root_cause_dict = {
                "analysis_id": getattr(rc, "analysis_id", None),
                "category":    str(getattr(rc, "category", "unknown")),
                "confidence":  getattr(rc, "confidence", None),
            }
        except Exception:  # noqa: BLE001
            root_cause_dict = {"error": "serialization_failed"}

    observation_dict: dict[str, Any] | None = None
    if result.observation is not None:
        try:
            obs = result.observation
            status = getattr(obs, "status", None)
            observation_dict = {
                "observation_id": getattr(obs, "observation_id", None),
                "status":         getattr(status, "value", str(status)) if status else None,
                "topic":          getattr(obs, "topic", None),
            }
        except Exception:  # noqa: BLE001
            observation_dict = {"error": "serialization_failed"}

    return {
        "result_id":             result.result_id,
        "session_id":            result.session_id,
        "case_id":               result.case_id,
        "topic":                 result.topic,
        "status":                result.status,
        "plan":                  plan_dict,
        "evidence":              evidence_dict,
        "knowledge_entry_count": len(result.knowledge_entries),
        "root_cause":            root_cause_dict,
        "observation":           observation_dict,
        "metrics":               result.metrics.to_dict(),
        "audit_timeline":        result.audit_timeline.to_dict(),
        "errors":                list(result.errors),
        "stage_timings":         dict(result.stage_timings),
        "pipeline_duration_ms":  result.pipeline_duration_ms,
        "started_at":            result.started_at,
        "completed_at":          result.completed_at,
        "cancellation_reason":   result.cancellation_reason,
        "recovery_hint":         result.recovery_hint,
    }


def result_to_json(result: "OrchestratorInvestigationResult") -> str:
    return json.dumps(result_to_dict(result), default=str)
