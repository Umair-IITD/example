"""
case_engine/workflows/models.py

Sprint 2.16: Workflow domain model.

Design principles:
- All model objects are frozen dataclasses — immutable after construction.
- WorkflowDefinition is loaded once from YAML and shared across all case executions.
- WorkflowStep is the unit of execution; it declares intent, not implementation.
- WorkflowExecutionResult is mutable — one instance per workflow run, updated per step.
- No LLM. No async. No side effects in models.

Serialization:
- WorkflowDefinition/WorkflowStep/WorkflowCondition: to_dict() for logging / API responses.
- WorkflowExecutionResult: to_dict() / from_dict() for JSONB persistence in cases.workflow_context.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _new_id() -> str:
    return str(uuid.uuid4())


# ── Step type enum ─────────────────────────────────────────────────────────────

class WorkflowStepType(str, Enum):
    """
    Defines what a workflow step does.

    COLLECT_INFORMATION : Ask the user/system for a specific value (slot or lookup).
    CHECK_CONDITION     : Evaluate a boolean condition against slot_values or context.
    PROPOSE_ACTION      : Generate an ActionRequest via the Action Gateway.
    REQUEST_APPROVAL    : Pause and wait for human approval before continuing.
    RESOLVE_CASE        : Mark the case as RESOLVED and close the workflow.
    ESCALATE_CASE       : Mark the case as ESCALATED and stop the workflow.
    INVESTIGATE         : Run the Investigation Layer (Sprint 2.19).
                          Calls InvestigationService; stores result in workflow context.
                          on_success → next step (root_cause.escalate=False)
                          on_failure → escalation path (root_cause.escalate=True or service error)
    KNOWLEDGE_LOOKUP    : Run the Knowledge Layer (Sprint 2.20).
                          Calls KnowledgeService; stores result in workflow context.
                          on_success → next step (service ran successfully)
                          on_failure → escalation path (service unavailable or pipeline error)
    """
    COLLECT_INFORMATION = "COLLECT_INFORMATION"
    CHECK_CONDITION     = "CHECK_CONDITION"
    PROPOSE_ACTION      = "PROPOSE_ACTION"
    REQUEST_APPROVAL    = "REQUEST_APPROVAL"
    RESOLVE_CASE        = "RESOLVE_CASE"
    ESCALATE_CASE       = "ESCALATE_CASE"
    INVESTIGATE         = "INVESTIGATE"
    KNOWLEDGE_LOOKUP    = "KNOWLEDGE_LOOKUP"    # Sprint 2.20
    ACTION_GATEWAY      = "ACTION_GATEWAY"      # Sprint 2.22: ACTIONGW -> RISKCHECK -> APPROVAL
    EXECUTE             = "EXECUTE"             # Sprint 2.23: EXECUTE -> VERIFY -> RECOVERY -> RESOLUTION
    REASON              = "REASON"              # Sprint 2.24: ROOTCAUSE -> REASONING -> ACTIONPROPOSAL
    CLARIFY             = "CLARIFY"             # Sprint 2.25: Missing Slots -> Clarification -> Resume


# ── Workflow-level state ───────────────────────────────────────────────────────

class WorkflowState(str, Enum):
    """
    Lifecycle state of a workflow execution on a case.

    PENDING   : workflow selected but not yet started (e.g., awaiting first slot)
    RUNNING   : execution in progress; a step is active
    PAUSED    : execution paused waiting for an external event (action approval)
    COMPLETED : workflow reached a RESOLVE_CASE terminal step
    ESCALATED : workflow reached an ESCALATE_CASE terminal step
    FAILED    : unrecoverable internal error during workflow execution
    """
    PENDING   = "PENDING"
    RUNNING   = "RUNNING"
    PAUSED    = "PAUSED"
    COMPLETED = "COMPLETED"
    ESCALATED = "ESCALATED"
    FAILED    = "FAILED"


TERMINAL_WORKFLOW_STATES: frozenset[WorkflowState] = frozenset({
    WorkflowState.COMPLETED,
    WorkflowState.ESCALATED,
    WorkflowState.FAILED,
})


# ── Condition ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowCondition:
    """
    A boolean condition evaluated against slot_values or workflow_context.

    field       : key to look up (slot name, or context key like "action_result.success")
    operator    : "eq" | "neq" | "exists" | "not_exists" | "in" | "not_in"
    value       : expected value (string, bool, or list for "in"/"not_in")
    description : human-readable explanation for audit/logging
    """
    field:       str
    operator:    str
    value:       Any = None
    description: str = ""

    _OPERATORS: frozenset[str] = frozenset({
        "eq", "neq", "exists", "not_exists", "in", "not_in",
    })

    def evaluate(self, context: dict[str, Any]) -> bool:
        """
        Evaluate this condition against the provided context dict.

        context is a flat-ish dict that may include dot-notation keys resolved
        from nested structures by the WorkflowEngine before calling this method.
        """
        raw = context.get(self.field)

        if self.operator == "exists":
            return raw is not None

        if self.operator == "not_exists":
            return raw is None

        if raw is None:
            return False

        if self.operator == "eq":
            return str(raw).upper() == str(self.value).upper()

        if self.operator == "neq":
            return str(raw).upper() != str(self.value).upper()

        if self.operator == "in":
            valid = [str(v).upper() for v in (self.value or [])]
            return str(raw).upper() in valid

        if self.operator == "not_in":
            valid = [str(v).upper() for v in (self.value or [])]
            return str(raw).upper() not in valid

        return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field":       self.field,
            "operator":    self.operator,
            "value":       self.value,
            "description": self.description,
        }


# ── Step ──────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowStep:
    """
    A single unit of work in a workflow.

    step_index    : zero-based position in the steps list (set by registry at load time)
    step_id       : stable identifier used in on_success / on_failure pointers
    step_type     : what this step does
    name          : human-readable name for logging
    description   : human-readable explanation for audit trail

    For PROPOSE_ACTION steps:
      action_type       : maps to ActionProposal.action_type
      action_namespace  : maps to ActionProposal.action_namespace
      action_params_template : dict with slot_name references (e.g. {"session_id": "{session_id}"})
      risk_level        : "SAFE" | "REVERSIBLE" | "IRREVERSIBLE"
      rollback_action_type : required if risk_level is REVERSIBLE

    For CHECK_CONDITION steps:
      conditions    : list of WorkflowCondition (all must be True — AND logic)

    Navigation:
      on_success    : step_id of next step on success (or "RESOLVE" / "ESCALATE")
      on_failure    : step_id of next step on failure (or "RESOLVE" / "ESCALATE")
      on_approval   : step_id to resume at after approval (PROPOSE_ACTION / REQUEST_APPROVAL)
      on_rejection  : step_id on human rejection (or "ESCALATE")
    """
    step_index:             int
    step_id:                str
    step_type:              WorkflowStepType
    name:                   str
    description:            str = ""
    action_type:            str | None = None
    action_namespace:       str | None = None
    action_params_template: dict[str, Any] = field(default_factory=dict)
    risk_level:             str = "SAFE"
    rollback_action_type:   str | None = None
    conditions:             tuple[WorkflowCondition, ...] = field(default_factory=tuple)
    on_success:             str = "RESOLVE"
    on_failure:             str = "ESCALATE"
    on_approval:            str | None = None
    on_rejection:           str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_index":   self.step_index,
            "step_id":      self.step_id,
            "step_type":    self.step_type.value,
            "name":         self.name,
            "description":  self.description,
            "action_type":  self.action_type,
            "action_namespace": self.action_namespace,
            "risk_level":   self.risk_level,
            "conditions":   [c.to_dict() for c in self.conditions],
            "on_success":   self.on_success,
            "on_failure":   self.on_failure,
            "on_approval":  self.on_approval,
            "on_rejection": self.on_rejection,
        }


# ── Workflow definition ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowDefinition:
    """
    Immutable description of a playbook.

    Loaded once from YAML by PlaybookRegistry at startup.
    Shared (not copied) across all case executions.

    workflow_id      : unique identifier (e.g., "vkyc_session_failure_v1")
    topic            : TopicKey value this playbook handles
    version          : semver string (e.g., "1.0")
    name             : human-readable name
    description      : purpose and scope
    required_slots   : slot names that must be FILLED before execution begins
    entry_conditions : optional list of WorkflowCondition checked at workflow start
    steps            : ordered tuple of WorkflowStep objects
    """
    workflow_id:      str
    topic:            str
    version:          str
    name:             str
    description:      str = ""
    required_slots:   tuple[str, ...] = field(default_factory=tuple)
    entry_conditions: tuple[WorkflowCondition, ...] = field(default_factory=tuple)
    steps:            tuple[WorkflowStep, ...] = field(default_factory=tuple)
    # Sprint 2.17: Investigation metadata (advisory — not enforced by engine)
    investigation_steps: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    tool_candidates:     tuple[str, ...] = field(default_factory=tuple)
    resolution_paths:    dict[str, str] = field(default_factory=dict)

    def step_by_id(self, step_id: str) -> WorkflowStep | None:
        for step in self.steps:
            if step.step_id == step_id:
                return step
        return None

    def first_step(self) -> WorkflowStep | None:
        return self.steps[0] if self.steps else None

    def to_dict(self) -> dict[str, Any]:
        return {
            "workflow_id":         self.workflow_id,
            "topic":               self.topic,
            "version":             self.version,
            "name":                self.name,
            "description":         self.description,
            "required_slots":      list(self.required_slots),
            "step_count":          len(self.steps),
            "steps":               [s.to_dict() for s in self.steps],
            "investigation_steps": list(self.investigation_steps),
            "tool_candidates":     list(self.tool_candidates),
            "resolution_paths":    dict(self.resolution_paths),
        }


# ── Execution result ───────────────────────────────────────────────────────────

@dataclass
class WorkflowExecutionResult:
    """
    Mutable runtime state for one workflow execution on one case.

    Serialized to / from cases.workflow_context (JSONB).
    One instance is created at workflow start and updated after each step.

    run_id           : unique ID for this execution (new for each workflow start)
    workflow_id      : which playbook is running
    workflow_state   : current WorkflowState
    current_step_id  : step_id of the active step (None before first step)
    step_results     : ordered list of {step_id, outcome, detail, completed_at}
    pending_action_id: action_id of an ACTION_PENDING action gateway request
    started_at       : when workflow execution began
    completed_at     : when workflow reached a terminal state
    resolution_note  : human-readable outcome summary
    escalation_reason: why the workflow escalated (if applicable)
    """
    run_id:            str              = field(default_factory=_new_id)
    workflow_id:       str              = ""
    workflow_state:    WorkflowState    = WorkflowState.PENDING
    current_step_id:   str | None       = None
    step_results:      list[dict[str, Any]] = field(default_factory=list)
    pending_action_id: str | None       = None
    started_at:        datetime         = field(default_factory=_now)
    completed_at:      datetime | None  = None
    resolution_note:   str | None       = None
    escalation_reason: str | None       = None
    # Sprint 2.19: investigation context — populated by INVESTIGATE step execution
    investigation_result:   dict[str, Any] | None = None
    # Sprint 2.20: knowledge context — populated by KNOWLEDGE_LOOKUP step execution
    knowledge_result:       dict[str, Any] | None = None
    # Sprint 2.21: action proposal context — populated by PROPOSE_ACTION step execution
    action_proposal_result: dict[str, Any] | None = None
    # Sprint 2.22: gateway result — populated by ACTION_GATEWAY step execution
    gateway_result:         dict[str, Any] | None = None
    # Sprint 2.23: execution result — populated by EXECUTE step execution
    execution_result:       dict[str, Any] | None = None
    # Sprint 2.24: reasoning result — populated by REASON step execution
    reasoning_result:       dict[str, Any] | None = None
    # Sprint 2.25: clarification result — populated by CLARIFY step execution
    clarification_result:   dict[str, Any] | None = None

    def record_step(
        self,
        step_id:  str,
        outcome:  str,
        detail:   dict[str, Any] | None = None,
    ) -> None:
        self.step_results.append({
            "step_id":      step_id,
            "outcome":      outcome,
            "detail":       detail or {},
            "completed_at": _now().isoformat(),
        })

    def is_terminal(self) -> bool:
        return self.workflow_state in TERMINAL_WORKFLOW_STATES

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id":               self.run_id,
            "workflow_id":          self.workflow_id,
            "workflow_state":       self.workflow_state.value,
            "current_step_id":      self.current_step_id,
            "step_results":         self.step_results,
            "pending_action_id":    self.pending_action_id,
            "started_at":           self.started_at.isoformat(),
            "completed_at":         self.completed_at.isoformat() if self.completed_at else None,
            "resolution_note":      self.resolution_note,
            "escalation_reason":    self.escalation_reason,
            "investigation_result":   self.investigation_result,
            "knowledge_result":       self.knowledge_result,
            "action_proposal_result": self.action_proposal_result,
            "gateway_result":         self.gateway_result,
            "execution_result":       self.execution_result,
            "reasoning_result":       self.reasoning_result,
            "clarification_result":   self.clarification_result,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "WorkflowExecutionResult":
        def _dt(v: str | None) -> datetime | None:
            return datetime.fromisoformat(v) if v else None

        return cls(
            run_id=d.get("run_id", _new_id()),
            workflow_id=d.get("workflow_id", ""),
            workflow_state=WorkflowState(d.get("workflow_state", WorkflowState.PENDING.value)),
            current_step_id=d.get("current_step_id"),
            step_results=d.get("step_results", []),
            pending_action_id=d.get("pending_action_id"),
            started_at=_dt(d.get("started_at")) or _now(),
            completed_at=_dt(d.get("completed_at")),
            resolution_note=d.get("resolution_note"),
            escalation_reason=d.get("escalation_reason"),
            investigation_result=d.get("investigation_result"),
            knowledge_result=d.get("knowledge_result"),
            action_proposal_result=d.get("action_proposal_result"),
            gateway_result=d.get("gateway_result"),
            execution_result=d.get("execution_result"),
            reasoning_result=d.get("reasoning_result"),
            clarification_result=d.get("clarification_result"),
        )
