"""
case_engine/actions/service.py

Sprint 2.21: ActionProposalService — orchestration layer.

Responsible for:
  1. Extracting root cause data from investigation_result dict
  2. Calling ActionProposalEngine.propose() → ActionProposalBundle
  3. Emitting audit events (STARTED, COMPLETED, BLOCKED)
  4. Returning JSONB-compatible dict

Per blueprint Principle 1: "Investigation before action."
If investigation_result is None, the service returns a BLOCKED result.

Never raises. All exceptions produce an error bundle dict.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.actions.models import (
    ActionProposalBundle,
    ProposedActionType,
)
from case_engine.actions.proposal import ActionProposalEngine
from case_engine.actions.risk import RiskAssessmentEngine

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ActionProposalService:
    """
    Orchestration layer for the Action Proposal Engine.

    Wraps ActionProposalEngine + audit emission into a single callable.

    propose() accepts the JSONB context dicts that live in WorkflowExecutionResult
    and returns a JSONB-compatible result dict.

    Sprint 2.24: reasoning_required=True (default False for backwards compat) makes
    propose() block with BLOCKED_NO_REASONING when reasoning_result is None.
    This enforces blueprint Principle 3 — Reasoning before execution.
    """

    def __init__(
        self,
        proposal_engine:   ActionProposalEngine | None = None,
        audit_logger:      Any = None,
        reasoning_required: bool = False,
    ) -> None:
        self._engine             = proposal_engine or ActionProposalEngine(RiskAssessmentEngine())
        self._audit              = audit_logger
        self._reasoning_required = reasoning_required

    def propose(
        self,
        topic:               str,
        investigation_result: dict[str, Any] | None,
        knowledge_result:    dict[str, Any] | None = None,
        reasoning_result:    dict[str, Any] | None = None,
        case:                Any = None,
        workflow_id:         str = "",
        step_id:             str = "",
    ) -> dict[str, Any]:
        """
        Run the action proposal pipeline and return a JSONB-compatible dict.

        If investigation_result is None, returns a BLOCKED result dict
        (blueprint Principle 1: Investigation before action).

        Never raises.
        """
        try:
            return self._propose(
                topic=topic,
                investigation_result=investigation_result,
                knowledge_result=knowledge_result,
                reasoning_result=reasoning_result,
                case=case,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            LOGGER.exception(
                "action_proposal_service.propose failed topic=%s", topic
            )
            return self._error_result(topic, "internal_error")

    # ── Private ───────────────────────────────────────────────────────────────

    def _propose(
        self,
        topic:               str,
        investigation_result: dict[str, Any] | None,
        knowledge_result:    dict[str, Any] | None,
        reasoning_result:    dict[str, Any] | None,
        case:                Any,
        workflow_id:         str,
        step_id:             str,
    ) -> dict[str, Any]:
        # Guard 0: "Reasoning Before Action" (blueprint Principle 3) — Sprint 2.24
        # When reasoning_required=True, no proposal without a completed ReasoningResult.
        if self._reasoning_required and reasoning_result is None:
            LOGGER.warning(
                "action_proposal_service: propose called without reasoning_result"
                " — topic=%s workflow=%s step=%s (blueprint Principle 3 violation)",
                topic, workflow_id, step_id,
            )
            return {
                "status":          "BLOCKED",
                "block_reason":    "NO_REASONING_RESULT",
                "topic":           topic,
                "bundle_id":       str(uuid.uuid4()),
                "proposals":       [],
                "proposal_count":  0,
                "blocked_at":      _now_iso(),
            }

        # Guard: Investigation Before Action (blueprint Section 29, Principle 1)
        if investigation_result is None:
            LOGGER.warning(
                "action_proposal_service: propose called without investigation_result"
                " — topic=%s workflow=%s step=%s (blueprint violation)",
                topic, workflow_id, step_id,
            )
            self._emit_blocked(case, topic, workflow_id, step_id)
            return {
                "status":          "BLOCKED",
                "block_reason":    "NO_INVESTIGATION_RESULT",
                "topic":           topic,
                "bundle_id":       str(uuid.uuid4()),
                "proposals":       [],
                "proposal_count":  0,
                "blocked_at":      _now_iso(),
            }

        # Extract root cause from investigation_result
        root_cause = investigation_result.get("root_cause") or {}
        root_cause_category   = root_cause.get("category", "UNKNOWN")
        inv_confidence        = float(root_cause.get("confidence", 0.0))
        inv_escalate          = bool(root_cause.get("escalate", False))

        self._emit_started(case, topic, root_cause_category, workflow_id, step_id)

        bundle = self._engine.propose(
            topic=topic,
            root_cause_category=root_cause_category,
            investigation_confidence=inv_confidence,
            investigation_escalate=inv_escalate,
            knowledge_result=knowledge_result,
        )

        self._emit_completed(case, topic, bundle, workflow_id, step_id)

        result = bundle.to_dict()
        result["status"] = "COMPLETED"
        return result

    # ── Audit emission ────────────────────────────────────────────────────────

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
            self._audit.log_action_proposal_started(
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
        case:       Any,
        topic:      str,
        bundle:     ActionProposalBundle,
        workflow_id: str,
        step_id:    str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            top = bundle.top_proposal
            self._audit.log_action_proposal_completed(
                case,
                topic=topic,
                bundle_id=bundle.bundle_id,
                proposal_count=bundle.proposal_count,
                top_action=top.action_type.value if top else None,
                risk_level=top.risk_assessment.risk_level.value if top else None,
                requires_approval=bundle.requires_approval,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _emit_blocked(
        self,
        case:       Any,
        topic:      str,
        workflow_id: str,
        step_id:    str,
    ) -> None:
        if self._audit is None or case is None:
            return
        try:
            self._audit.log_action_proposal_blocked(
                case,
                topic=topic,
                block_reason="NO_INVESTIGATION_RESULT",
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception:
            pass

    def _error_result(self, topic: str, error_type: str) -> dict[str, Any]:
        return {
            "status":         "ERROR",
            "error_type":     error_type,
            "topic":          topic,
            "bundle_id":      str(uuid.uuid4()),
            "proposals":      [],
            "proposal_count": 0,
            "created_at":     _now_iso(),
        }
