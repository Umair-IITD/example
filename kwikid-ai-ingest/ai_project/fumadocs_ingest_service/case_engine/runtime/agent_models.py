"""
case_engine/runtime/agent_models.py

Sprint 2.27.5: SupportAgentRuntime output models.

AgentStatus and AgentExecutionResult are the output types for every
run_case() call on the SupportAgentRuntime.

Design:
  - Frozen dataclasses: immutable, JSON-serializable
  - AgentExecutionResult.to_dict() produces the full audit trail
    for a single run_case() call
  - All sub-results (workflow, response, engineering) are included
    as nested dicts — callers can inspect at any granularity
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class AgentStatus(str, Enum):
    """
    Outcome of a SupportAgentRuntime.run_case() call.

    SUCCESS:          Case fully processed; customer response generated.
    AWAITING_CLARIFICATION: Clarification requested; waiting for customer reply.
    AWAITING_APPROVAL: Action requires human approval before execution.
    ESCALATED:        Case escalated to L2 / engineering team.
    FAILED:           Agent hit an unrecoverable error.
    """
    SUCCESS                = "SUCCESS"
    AWAITING_CLARIFICATION = "AWAITING_CLARIFICATION"
    AWAITING_APPROVAL      = "AWAITING_APPROVAL"
    ESCALATED              = "ESCALATED"
    FAILED                 = "FAILED"


@dataclass(frozen=True)
class AgentExecutionResult:
    """
    Full output of a SupportAgentRuntime.run_case() call.

    This is the single artifact that callers (TicketOrchestrator, API endpoints,
    tests) receive. It includes the agent status, the customer response draft,
    and all intermediate step results for audit and debugging.

    Fields:
        run_id             — UUID for this specific agent run
        case_id            — the case being processed
        agent_status       — high-level outcome (AgentStatus enum)
        workflow_result    — WorkflowExecutionResult.to_dict() if workflow ran, else None
        response_draft     — ResponseDraft.to_dict() if generated, else None
        engineering_result — EngineeringEscalationResult.to_dict() if escalated, else None
        classification     — topic classification result dict
        steps_completed    — ordered list of pipeline step names that ran
        error_code         — populated iff agent_status == FAILED
        error_msg          — populated iff agent_status == FAILED
        started_at         — ISO timestamp when run_case() was called
        completed_at       — ISO timestamp when run_case() returned
        duration_ms        — wall-clock duration of the entire run
        metadata           — extensible dict for future fields (e.g. LLM tokens used)
    """
    run_id:             str
    case_id:            str
    agent_status:       AgentStatus
    workflow_result:    dict[str, Any] | None
    response_draft:     dict[str, Any] | None
    engineering_result: dict[str, Any] | None
    classification:     dict[str, Any] | None
    steps_completed:    tuple[str, ...]
    error_code:         str | None
    error_msg:          str | None
    started_at:         str
    completed_at:       str
    duration_ms:        int
    metadata:           dict[str, Any] = field(default_factory=dict)

    @property
    def success(self) -> bool:
        return self.agent_status == AgentStatus.SUCCESS

    @property
    def needs_clarification(self) -> bool:
        return self.agent_status == AgentStatus.AWAITING_CLARIFICATION

    @property
    def escalated(self) -> bool:
        return self.agent_status == AgentStatus.ESCALATED

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":             self.run_id,
            "case_id":            self.case_id,
            "agent_status":       self.agent_status.value,
            "success":            self.success,
            "needs_clarification": self.needs_clarification,
            "escalated":          self.escalated,
            "workflow_result":    self.workflow_result,
            "response_draft":     self.response_draft,
            "engineering_result": self.engineering_result,
            "classification":     self.classification,
            "steps_completed":    list(self.steps_completed),
            "error_code":         self.error_code,
            "error_msg":          self.error_msg,
            "started_at":         self.started_at,
            "completed_at":       self.completed_at,
            "duration_ms":        self.duration_ms,
            "metadata":           dict(self.metadata),
        }

    @classmethod
    def failure(
        cls,
        case_id:    str,
        error_code: str,
        error_msg:  str,
        started_at: str,
        steps_completed: tuple[str, ...] = (),
        duration_ms: int = 0,
    ) -> "AgentExecutionResult":
        """Build a failure result for fatal errors in run_case()."""
        return cls(
            run_id=_new_id(),
            case_id=case_id,
            agent_status=AgentStatus.FAILED,
            workflow_result=None,
            response_draft=None,
            engineering_result=None,
            classification=None,
            steps_completed=steps_completed,
            error_code=error_code,
            error_msg=error_msg,
            started_at=started_at,
            completed_at=_now_iso(),
            duration_ms=duration_ms,
        )
