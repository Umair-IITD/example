"""
case_engine/reasoning/service.py

Sprint 2.24: ReasoningService — orchestration layer for the Investigation
Reasoning Engine.

flow_diagram path:
    ROOTCAUSE --> HYBRIDRAG --> REASONING --> GUARDRAILS --> ACTIONPROPOSAL

Responsibilities:
  - Validate that investigation_result is present (blueprint Principle 2)
  - Invoke InvestigationReasoningEngine.reason()
  - Emit REASONING_STARTED / REASONING_COMPLETED audit events
  - Return a JSONB-compatible dict
  - Never raise exceptions

Blueprint alignment:
  Principle 3 — Reasoning before execution (enforced upstream in WorkflowEngine)
  Principle 2 — Evidence before reasoning (enforced here: no investigation_result → BLOCKED)
  Section 13  — Reasoning Engine input/output contract
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.reasoning.engine import InvestigationReasoningEngine, build_reasoning_engine

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ReasoningService:
    """
    Orchestration layer for InvestigationReasoningEngine.

    reason() accepts the JSONB context dicts that live in WorkflowExecutionResult
    and returns a JSONB-compatible result dict.

    Never raises — all exceptions produce a BLOCKED/error dict.
    """

    def __init__(
        self,
        engine:       InvestigationReasoningEngine | None = None,
        audit_logger: Any = None,
    ) -> None:
        self._engine = engine or build_reasoning_engine()
        self._audit  = audit_logger

    def reason(
        self,
        investigation_result: dict[str, Any] | None,
        knowledge_result:     dict[str, Any] | None = None,
        case:                 Any = None,
        workflow_id:          str = "",
        step_id:              str = "",
    ) -> dict[str, Any]:
        """
        Run the reasoning pipeline and return a JSONB-compatible dict.

        If investigation_result is None, returns a BLOCKED result dict
        (blueprint Principle 2: Evidence before reasoning).

        Never raises.
        """
        try:
            return self._reason(
                investigation_result=investigation_result,
                knowledge_result=knowledge_result,
                case=case,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            LOGGER.exception(
                "reasoning_service.reason failed workflow=%s step=%s", workflow_id, step_id
            )
            return self._error_result("internal_error", workflow_id)

    # ── Private ────────────────────────────────────────────────────────────────

    def _reason(
        self,
        investigation_result: dict[str, Any] | None,
        knowledge_result:     dict[str, Any] | None,
        case:                 Any,
        workflow_id:          str,
        step_id:              str,
    ) -> dict[str, Any]:
        # Guard: Evidence Before Reasoning (blueprint Principle 2)
        if investigation_result is None:
            LOGGER.warning(
                "reasoning_service: reason called without investigation_result"
                " — workflow=%s step=%s (blueprint violation)",
                workflow_id, step_id,
            )
            self._emit_blocked(case, workflow_id, step_id)
            return {
                "status":       "BLOCKED",
                "block_reason": "NO_INVESTIGATION_RESULT",
                "bundle_id":    str(uuid.uuid4()),
                "blocked_at":   _now_iso(),
                "workflow_id":  workflow_id,
                "step_id":      step_id,
            }

        topic = investigation_result.get("topic", "")
        root_cause = investigation_result.get("root_cause") or {}
        category   = root_cause.get("category", "UNKNOWN")

        self._emit_started(case, topic, category, workflow_id, step_id)

        reasoning_result = self._engine.reason(
            investigation_result=investigation_result,
            knowledge_result=knowledge_result,
        )

        self._emit_completed(case, topic, reasoning_result, workflow_id, step_id)

        result = reasoning_result.to_dict()
        result["status"] = "COMPLETED"
        return result

    # ── Audit emission ─────────────────────────────────────────────────────────

    def _emit_started(
        self,
        case:                Any,
        topic:               str,
        root_cause_category: str,
        workflow_id:         str,
        step_id:             str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_reasoning_started(
                case,
                topic=topic,
                root_cause_category=root_cause_category,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _emit_completed(
        self,
        case:             Any,
        topic:            str,
        reasoning_result: Any,
        workflow_id:      str,
        step_id:          str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_reasoning_completed(
                case,
                topic=topic,
                result_id=reasoning_result.result_id,
                outcome=reasoning_result.outcome.value,
                recommended_action=reasoning_result.recommended_action,
                should_escalate=reasoning_result.should_escalate,
                confidence=reasoning_result.confidence,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _emit_blocked(
        self,
        case:        Any,
        workflow_id: str,
        step_id:     str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_reasoning_started(
                case,
                topic="UNKNOWN",
                root_cause_category="UNKNOWN",
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _error_result(self, reason: str, workflow_id: str) -> dict[str, Any]:
        return {
            "status":       "ERROR",
            "block_reason": reason,
            "bundle_id":    str(uuid.uuid4()),
            "error_at":     _now_iso(),
            "workflow_id":  workflow_id,
        }


def build_reasoning_service(audit_logger: Any = None) -> ReasoningService:
    """Factory: return a configured ReasoningService."""
    return ReasoningService(audit_logger=audit_logger)
