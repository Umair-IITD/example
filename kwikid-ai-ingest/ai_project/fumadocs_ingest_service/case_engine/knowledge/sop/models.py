"""
case_engine/knowledge/sop/models.py

Sprint 2.38 (original) + Sprint 2.41 (expanded): SOP domain models.

Blueprint Section 6:
  SOP Repository contains: Support SOPs, Resolution procedures, Escalation rules.

Blueprint Section 24:
  Customer Response Generation uses: SOPs, Root cause, Resolution outcome.

Blueprint Section 31 — Knowledge Retrieval Flow:
  Investigation → Root Cause → Knowledge Search → Relevant SOP → Recommended Action.

A SOPDocument is the authoritative resolution procedure for a specific
issue class. It is deterministic — the core resolution steps do not require
LLM interpretation. LLM is only used for naturalising the final customer reply.

Sprint 2.41 additions:
  - SOPStepKind: 14 operational step types (READ, VERIFY, NAVIGATE, etc.)
  - SOPParameter: typed parameter for SOP steps
  - SOPExecutionHints: automation and timing hints for a step
  - SOPOutcome: possible outcome of a step with routing
  - SOPReference: external reference (docs, runbooks, dashboards)
  - SOPDecision: decision branch within a procedure
  - SOPCondition: condition expression for decisions
  - SOPInstruction: structured instruction record
  - SOPProcedure: ordered sub-procedure of SOPSteps
  - Enhanced SOPStep: kind, required_inputs, expected_outputs, dependencies,
                      execution_hints, parameters, outcomes, references,
                      critical, optional, timeout_seconds, parallel_group
  - Enhanced SOPDocument: procedures, references, parameters,
                          estimated_resolution_minutes, requires_approval,
                          enabled, client_scope

All new fields on SOPStep and SOPDocument have defaults to preserve
backward compatibility with Sprint 2.38/2.39/2.40 code.

Dependency direction:
  This module imports ONLY from the standard library and enum.
  No case_engine imports — this is a leaf dependency node.
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


# ── Enumerations ───────────────────────────────────────────────────────────────

class SOPStatus(str, Enum):
    """Lifecycle status of a SOP document."""
    DRAFT      = "DRAFT"
    ACTIVE     = "ACTIVE"
    DEPRECATED = "DEPRECATED"
    ARCHIVED   = "ARCHIVED"


class SOPActionType(str, Enum):
    """High-level category of action a SOP step prescribes."""
    INVESTIGATE = "INVESTIGATE"
    EXECUTE     = "EXECUTE"
    ESCALATE    = "ESCALATE"
    VERIFY      = "VERIFY"
    COMMUNICATE = "COMMUNICATE"
    DOCUMENT    = "DOCUMENT"


class SOPStepKind(str, Enum):
    """
    Operational type of a SOP step — what the step actually does.

    Complements SOPActionType (high-level category) with granular
    operation semantics used by automation engines.

    READ:       Read a value from a data source.
    VERIFY:     Check that a condition holds.
    NAVIGATE:   Move to a location in a system or UI.
    LOOKUP:     Find a specific record by key.
    SEARCH:     Search for records matching criteria.
    COLLECT:    Gather multiple related data items.
    COMPARE:    Compare two values or states.
    EXECUTE:    Perform an action that changes system state.
    VALIDATE:   Validate the result of a previous step.
    WAIT:       Wait for an event or condition.
    ESCALATE:   Escalate the issue to a human agent or tier.
    APPROVAL:   Request explicit approval before proceeding.
    NOTE:       Record an observation in the audit trail.
    COMPLETE:   Mark the procedure successfully complete.
    """
    READ      = "READ"
    VERIFY    = "VERIFY"
    NAVIGATE  = "NAVIGATE"
    LOOKUP    = "LOOKUP"
    SEARCH    = "SEARCH"
    COLLECT   = "COLLECT"
    COMPARE   = "COMPARE"
    EXECUTE   = "EXECUTE"
    VALIDATE  = "VALIDATE"
    WAIT      = "WAIT"
    ESCALATE  = "ESCALATE"
    APPROVAL  = "APPROVAL"
    NOTE      = "NOTE"
    COMPLETE  = "COMPLETE"


class SOPTriggerOperator(str, Enum):
    """Operator for matching a trigger condition field."""
    EQUALS   = "eq"
    CONTAINS = "contains"
    IN       = "in"
    EXISTS   = "exists"


# ── SOP Sub-Components ─────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SOPParameter:
    """
    A typed parameter for an SOP or SOP step.

    Used to declare what data is needed and its expected type.
    """
    name:          str
    param_type:    str = "string"
    required:      bool = True
    description:   str = ""
    default_value: Any = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name":          self.name,
            "param_type":    self.param_type,
            "required":      self.required,
            "description":   self.description,
            "default_value": self.default_value,
        }


@dataclass(frozen=True)
class SOPExecutionHints:
    """
    Automation and timing hints for a SOP step.

    Guides both human agents (expected time) and automation engines
    (can_be_automated, requires_system_access, requires_approval).
    """
    estimated_seconds:      int  = 60
    can_be_automated:       bool = False
    requires_system_access: bool = True
    requires_approval:      bool = False
    tool_hint:              str  = ""
    system_reference:       str  = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "estimated_seconds":      self.estimated_seconds,
            "can_be_automated":       self.can_be_automated,
            "requires_system_access": self.requires_system_access,
            "requires_approval":      self.requires_approval,
            "tool_hint":              self.tool_hint,
            "system_reference":       self.system_reference,
        }


@dataclass(frozen=True)
class SOPOutcome:
    """
    A possible outcome of a SOP step with routing to the next step.

    outcome_id:   Stable identifier for this outcome.
    description:  Human-readable description.
    next_step_id: step_id to execute next (None = continue linear sequence).
    is_success:   Whether this outcome represents successful completion.
    is_terminal:  Whether this outcome ends the procedure.
    """
    outcome_id:   str
    description:  str
    next_step_id: str | None = None
    is_success:   bool = True
    is_terminal:  bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome_id":   self.outcome_id,
            "description":  self.description,
            "next_step_id": self.next_step_id,
            "is_success":   self.is_success,
            "is_terminal":  self.is_terminal,
        }


@dataclass(frozen=True)
class SOPReference:
    """
    An external reference attached to a SOP or a step.

    ref_type: "runbook" | "dashboard" | "ticket" | "doc" | "api" | "other"
    """
    ref_id:   str
    title:    str
    ref_type: str = "doc"
    url:      str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id":   self.ref_id,
            "title":    self.title,
            "ref_type": self.ref_type,
            "url":      self.url,
        }


@dataclass(frozen=True)
class SOPCondition:
    """
    A condition expression for SOPDecision branching.

    field:    Key in the investigation context or previous step output.
    operator: Comparison operator (eq, contains, in, exists).
    value:    Expected value (None for EXISTS checks).
    """
    field:       str
    operator:    SOPTriggerOperator
    value:       Any = None
    description: str = ""

    def evaluate(self, context: dict[str, Any]) -> bool:
        """Evaluate against context dict. Never raises."""
        try:
            raw = context.get(self.field)
            if self.operator == SOPTriggerOperator.EXISTS:
                return raw is not None
            if raw is None:
                return False
            if self.operator == SOPTriggerOperator.EQUALS:
                return str(raw).upper() == str(self.value).upper()
            if self.operator == SOPTriggerOperator.CONTAINS:
                return str(self.value).lower() in str(raw).lower()
            if self.operator == SOPTriggerOperator.IN:
                valid = [str(v).upper() for v in (self.value or [])]
                return str(raw).upper() in valid
            return False
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field":       self.field,
            "operator":    self.operator.value,
            "value":       self.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class SOPDecision:
    """
    A conditional branch within a SOP procedure.

    Evaluated after a step completes to determine the next step.
    """
    decision_id:   str
    condition:     SOPCondition
    true_step_id:  str
    false_step_id: str
    description:   str = ""

    def evaluate(self, context: dict[str, Any]) -> str:
        """Return true_step_id or false_step_id based on condition."""
        return self.true_step_id if self.condition.evaluate(context) else self.false_step_id

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id":   self.decision_id,
            "condition":     self.condition.to_dict(),
            "true_step_id":  self.true_step_id,
            "false_step_id": self.false_step_id,
            "description":   self.description,
        }


@dataclass(frozen=True)
class SOPInstruction:
    """
    A structured instruction record for a SOP step.

    Separates the machine-readable instruction (for automation) from the
    human-readable instruction (for agent display).
    """
    instruction_id:    str
    human_text:        str
    machine_text:      str = ""
    is_automated:      bool = False
    tool_hint:         str = ""
    system_reference:  str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "instruction_id":   self.instruction_id,
            "human_text":       self.human_text,
            "machine_text":     self.machine_text,
            "is_automated":     self.is_automated,
            "tool_hint":        self.tool_hint,
            "system_reference": self.system_reference,
        }


# ── SOP Components ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SOPTriggerCondition:
    """
    Condition that must be satisfied for this SOP to apply.

    Evaluated against the investigation context dict produced by the
    RootCauseEngine before SOP retrieval begins.

    field:       Key in the investigation context or root cause analysis
    operator:    SOPTriggerOperator
    value:       Expected value (string, list, or None for EXISTS)
    description: Human-readable description for audit trail
    """
    field:       str
    operator:    SOPTriggerOperator
    value:       Any
    description: str = ""

    def matches(self, context: dict[str, Any]) -> bool:
        """Evaluate this condition against the provided context. Never raises."""
        try:
            raw = context.get(self.field)
            if self.operator == SOPTriggerOperator.EXISTS:
                return raw is not None
            if raw is None:
                return False
            if self.operator == SOPTriggerOperator.EQUALS:
                return str(raw).upper() == str(self.value).upper()
            if self.operator == SOPTriggerOperator.CONTAINS:
                return str(self.value).lower() in str(raw).lower()
            if self.operator == SOPTriggerOperator.IN:
                valid = [str(v).upper() for v in (self.value or [])]
                return str(raw).upper() in valid
            return False
        except Exception:
            return False

    def to_dict(self) -> dict[str, Any]:
        return {
            "field":       self.field,
            "operator":    self.operator.value,
            "value":       self.value,
            "description": self.description,
        }


@dataclass(frozen=True)
class SOPStep:
    """
    One step in a SOP resolution procedure.

    Sprint 2.38 fields (required — no defaults):
      step_number:      1-based ordering
      step_id:          Stable identifier within this SOP
      action_type:      High-level action category (INVESTIGATE, EXECUTE, etc.)
      title:            Short label for display and audit
      instruction:      Detailed instruction for the L1 agent or automation

    Sprint 2.38 fields (optional with defaults):
      expected_outcome: What success looks like after this step
      on_failure:       step_id to route to on failure (None = terminate SOP)

    Sprint 2.41 fields (all have defaults for backward compatibility):
      kind:             Operational type of this step (READ, VERIFY, EXECUTE, etc.)
      required_inputs:  Slot/evidence keys that must be present before this step
      expected_outputs: Slot/evidence keys this step is expected to produce
      dependencies:     step_ids that must complete before this step
      execution_hints:  Automation and timing hints
      parameters:       Typed parameters this step accepts
      outcomes:         Possible outcomes with routing
      references:       External references (runbooks, dashboards, docs)
      critical:         True → failure triggers ESCALATE
      optional:         True → step may be skipped if not applicable
      timeout_seconds:  Maximum seconds to wait for step completion
      parallel_group:   Name of parallel execution group (None = sequential)
    """
    # ── Sprint 2.38 required fields ──────────────────────────────────────────
    step_number:      int
    step_id:          str
    action_type:      SOPActionType
    title:            str
    instruction:      str

    # ── Sprint 2.38 optional fields ──────────────────────────────────────────
    expected_outcome: str      = ""
    on_failure:       str | None = None

    # ── Sprint 2.41 extended fields (all have defaults) ──────────────────────
    kind:             SOPStepKind                = SOPStepKind.EXECUTE
    required_inputs:  tuple[str, ...]            = field(default_factory=tuple)
    expected_outputs: tuple[str, ...]            = field(default_factory=tuple)
    dependencies:     tuple[str, ...]            = field(default_factory=tuple)
    execution_hints:  SOPExecutionHints | None   = None
    parameters:       tuple[SOPParameter, ...]   = field(default_factory=tuple)
    outcomes:         tuple[SOPOutcome, ...]      = field(default_factory=tuple)
    references:       tuple[SOPReference, ...]   = field(default_factory=tuple)
    critical:         bool                       = False
    optional:         bool                       = False
    timeout_seconds:  int                        = 300
    parallel_group:   str | None                 = None

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def is_root(self) -> bool:
        """Return True if this step has no dependencies."""
        return len(self.dependencies) == 0

    @property
    def is_parallelizable(self) -> bool:
        """Return True if this step can run in parallel with siblings."""
        return self.parallel_group is not None

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_number":      self.step_number,
            "step_id":          self.step_id,
            "action_type":      self.action_type.value,
            "kind":             self.kind.value,
            "title":            self.title,
            "instruction":      self.instruction,
            "expected_outcome": self.expected_outcome,
            "on_failure":       self.on_failure,
            "required_inputs":  list(self.required_inputs),
            "expected_outputs": list(self.expected_outputs),
            "dependencies":     list(self.dependencies),
            "critical":         self.critical,
            "optional":         self.optional,
            "timeout_seconds":  self.timeout_seconds,
            "parallel_group":   self.parallel_group,
            "execution_hints":  self.execution_hints.to_dict() if self.execution_hints else None,
            "parameters":       [p.to_dict() for p in self.parameters],
            "outcomes":         [o.to_dict() for o in self.outcomes],
            "references":       [r.to_dict() for r in self.references],
        }


@dataclass(frozen=True)
class SOPVersion:
    """
    Version record for a SOP document.

    Enables audit-grade traceability of SOP changes over time.

    version:     Semver string (e.g., "1.0", "2.3")
    created_at:  ISO timestamp of this version
    created_by:  Author identifier
    change_note: Human-readable description of what changed in this version
    """
    version:     str
    created_at:  str = field(default_factory=_now_iso)
    created_by:  str = "system"
    change_note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "version":     self.version,
            "created_at":  self.created_at,
            "created_by":  self.created_by,
            "change_note": self.change_note,
        }


@dataclass(frozen=True)
class SOPProcedure:
    """
    An ordered sub-procedure within a SOPDocument.

    A SOPDocument may contain one or more procedures:
    - Primary procedure: the standard resolution path
    - Alternative procedures: fallback or variant paths

    procedure_id:    Stable identifier
    name:            Human-readable name
    steps:           Ordered sequence of SOPSteps
    entry_condition: Optional condition that must be met to enter this procedure
    description:     Purpose and scope of this procedure
    """
    procedure_id:     str
    name:             str
    steps:            tuple[SOPStep, ...]
    entry_condition:  SOPCondition | None = None
    description:      str = ""

    @property
    def step_count(self) -> int:
        return len(self.steps)

    def to_dict(self) -> dict[str, Any]:
        return {
            "procedure_id":    self.procedure_id,
            "name":            self.name,
            "step_count":      self.step_count,
            "description":     self.description,
            "entry_condition": self.entry_condition.to_dict() if self.entry_condition else None,
            "steps":           [s.to_dict() for s in self.steps],
        }


@dataclass(frozen=True)
class SOPDocument:
    """
    A Standard Operating Procedure document.

    Blueprint Section 6: SOP Repository contains support SOPs, resolution
    procedures, and escalation rules. Each SOPDocument provides deterministic
    resolution guidance for a specific issue class.

    Sprint 2.38 fields:
        sop_id, title, topic, status, version, trigger_conditions, steps,
        escalation_threshold, applicable_to, tags, current_version, description

    Sprint 2.41 additions (all have defaults for backward compat):
        procedures, references, parameters, estimated_resolution_minutes,
        requires_approval, enabled, client_scope
    """
    # ── Sprint 2.38 required fields ──────────────────────────────────────────
    sop_id:               str
    title:                str
    topic:                str
    status:               SOPStatus
    version:              str
    trigger_conditions:   tuple[SOPTriggerCondition, ...]
    steps:                tuple[SOPStep, ...]

    # ── Sprint 2.38 optional fields ──────────────────────────────────────────
    escalation_threshold: float                = 0.4
    applicable_to:        tuple[str, ...]      = field(default_factory=tuple)
    tags:                 tuple[str, ...]      = field(default_factory=tuple)
    current_version:      SOPVersion | None    = None
    description:          str                  = ""

    # ── Sprint 2.41 extended fields (all have defaults) ──────────────────────
    procedures:                  tuple[SOPProcedure, ...]  = field(default_factory=tuple)
    references:                  tuple[SOPReference, ...]  = field(default_factory=tuple)
    parameters:                  tuple[SOPParameter, ...]  = field(default_factory=tuple)
    estimated_resolution_minutes: int                      = 30
    requires_approval:            bool                     = False
    enabled:                      bool                     = True
    client_scope:                 tuple[str, ...]          = field(default_factory=tuple)

    # ── Properties ────────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        """Return True if this SOP is ACTIVE and enabled."""
        return self.status == SOPStatus.ACTIVE and self.enabled

    def is_global(self) -> bool:
        """Return True if this SOP applies to all clients (no client scope)."""
        return len(self.applicable_to) == 0 and len(self.client_scope) == 0

    def applies_to_client(self, client_id: str) -> bool:
        """
        Return True if this SOP applies to the given client.

        A SOP is global if applicable_to and client_scope are both empty.
        Otherwise the client_id must appear in at least one scope set.
        """
        scope = set(self.applicable_to) | set(self.client_scope)
        return not scope or client_id in scope

    def matches_context(self, context: dict[str, Any]) -> bool:
        """Return True if ALL trigger conditions match the given context dict."""
        return all(tc.matches(context) for tc in self.trigger_conditions)

    def has_vision_step(self) -> bool:
        """Return True if any step involves visual evidence collection."""
        visual_keywords = {"screenshot", "image", "visual", "snapshot", "capture"}
        for step in self.steps:
            if any(kw in step.instruction.lower() for kw in visual_keywords):
                return True
        return False

    def required_slots(self) -> list[str]:
        """Return all unique required_inputs across all steps."""
        seen: set[str] = set()
        result: list[str] = []
        for step in self.steps:
            for slot in step.required_inputs:
                if slot not in seen:
                    seen.add(slot)
                    result.append(slot)
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "sop_id":                    self.sop_id,
            "title":                     self.title,
            "topic":                     self.topic,
            "status":                    self.status.value,
            "version":                   self.version,
            "step_count":                len(self.steps),
            "trigger_count":             len(self.trigger_conditions),
            "escalation_threshold":      self.escalation_threshold,
            "applicable_to":             list(self.applicable_to),
            "client_scope":              list(self.client_scope),
            "tags":                      list(self.tags),
            "description":               self.description,
            "estimated_resolution_minutes": self.estimated_resolution_minutes,
            "requires_approval":         self.requires_approval,
            "enabled":                   self.enabled,
            "steps":                     [s.to_dict() for s in self.steps],
            "trigger_conditions":        [tc.to_dict() for tc in self.trigger_conditions],
            "current_version":           self.current_version.to_dict() if self.current_version else None,
            "procedures":                [p.to_dict() for p in self.procedures],
            "references":                [r.to_dict() for r in self.references],
            "parameters":                [p.to_dict() for p in self.parameters],
        }
