"""
case_engine/clarification/service.py

Sprint 2.25: ClarificationService -- orchestration layer for WorkflowClarificationEngine.

Responsibilities:
  - Validate inputs
  - Invoke WorkflowClarificationEngine.clarify()
  - Emit CLARIFICATION_STARTED / CLARIFICATION_COMPLETED audit events
  - Return JSONB-compatible dict (stored in WorkflowExecutionResult.clarification_result)
  - Never raise

Blueprint alignment:
  The clarification check is the gate before INVESTIGATE.
  No investigation may begin when required slots are missing.
  This service is the workflow-level enforcement of that constraint.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.clarification.engine import WorkflowClarificationEngine, build_clarification_engine

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ClarificationService:
    """
    Orchestration wrapper for WorkflowClarificationEngine.

    clarify() accepts slot context dicts and returns a JSONB-compatible result dict
    that is stored in WorkflowExecutionResult.clarification_result.

    Never raises -- all exceptions produce an ERROR status dict.
    """

    def __init__(
        self,
        engine:       WorkflowClarificationEngine | None = None,
        audit_logger: Any = None,
    ) -> None:
        self._engine = engine or build_clarification_engine()
        self._audit  = audit_logger

    def clarify(
        self,
        topic:          str,
        slot_context:   dict[str, str],
        required_slots: tuple[str, ...] | list[str],
        slot_state:     dict[str, Any] | None = None,
        case:           Any = None,
        workflow_id:    str = "",
        step_id:        str = "",
    ) -> dict[str, Any]:
        """
        Run the clarification check and return a JSONB-compatible dict.

        Return dict keys:
          status                -- READY | NEEDS_CLARIFICATION | ESCALATE | ERROR
          result_id             -- unique ID
          missing_slots         -- list[str] of missing slot names
          clarification_message -- human-readable message
          ready_to_continue     -- bool
          next_question         -- dict | None
          topic                 -- echo
          workflow_id           -- echo
          step_id               -- echo

        Never raises.
        """
        try:
            return self._clarify(
                topic=topic,
                slot_context=slot_context,
                required_slots=required_slots,
                slot_state=slot_state,
                case=case,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            LOGGER.exception(
                "clarification_service.clarify failed workflow=%s step=%s",
                workflow_id, step_id,
            )
            return self._error_result("internal_error", workflow_id, step_id)

    # ── Private ────────────────────────────────────────────────────────────────

    def _clarify(
        self,
        topic:          str,
        slot_context:   dict[str, str],
        required_slots: tuple[str, ...] | list[str],
        slot_state:     dict[str, Any] | None,
        case:           Any,
        workflow_id:    str,
        step_id:        str,
    ) -> dict[str, Any]:
        missing_preview = [
            s for s in required_slots if not slot_context.get(s)
        ]
        self._emit_started(case, topic, missing_preview, workflow_id, step_id)

        clarification_result = self._engine.clarify(
            topic=topic,
            slot_context=slot_context,
            required_slots=required_slots,
            slot_state=slot_state,
        )

        self._emit_completed(case, topic, clarification_result, workflow_id, step_id)

        result = clarification_result.to_dict()
        result["topic"]       = topic
        result["workflow_id"] = workflow_id
        result["step_id"]     = step_id
        return result

    # ── Audit emission ─────────────────────────────────────────────────────────

    def _emit_started(
        self,
        case:          Any,
        topic:         str,
        missing_slots: list[str],
        workflow_id:   str,
        step_id:       str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_clarification_started(
                case,
                topic=topic,
                missing_slots=missing_slots,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _emit_completed(
        self,
        case:                 Any,
        topic:                str,
        clarification_result: Any,
        workflow_id:          str,
        step_id:              str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_clarification_completed(
                case,
                topic=topic,
                status=clarification_result.status.value,
                ready_to_continue=clarification_result.ready_to_continue,
                slot_count=len(clarification_result.missing_slots),
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _error_result(
        self,
        reason:     str,
        workflow_id: str,
        step_id:    str,
    ) -> dict[str, Any]:
        return {
            "status":               "ERROR",
            "result_id":            str(uuid.uuid4()),
            "missing_slots":        [],
            "clarification_message": f"ClarificationService error: {reason}",
            "ready_to_continue":    False,
            "next_question":        None,
            "error_at":             _now_iso(),
            "workflow_id":          workflow_id,
            "step_id":              step_id,
        }


def build_clarification_service(audit_logger: Any = None) -> ClarificationService:
    """Factory: return a configured ClarificationService."""
    return ClarificationService(audit_logger=audit_logger)
