"""
case_engine/workflows/contracts.py

Sprint 2.38: Typed workflow contracts.

Defines WorkflowRequirement, WorkflowOutput, and WorkflowTransition —
the explicit typed contracts for what each workflow step needs, produces,
and how it transitions to the next step.

These types complement the existing WorkflowStep (models.py) without
modifying its frozen dataclass. They are consumed by:
  - InvestigationPlanner (Sprint 2.39): builds investigation plan from requirements
  - WorkflowResolver (repository.py): validates playbooks at load time
  - InvestigationContext (investigation/context.py): tracks what has been fulfilled

Design:
  - All types are frozen dataclasses (immutable).
  - WorkflowTransition replaces raw on_success/on_failure strings with typed records.
  - WorkflowRequirement + WorkflowOutput provide machine-readable step contracts.

Dependency direction:
  contracts.py imports ONLY from standard library + enums.
  Does NOT import from case_engine/investigation, case_engine/action_gateway,
  or case_engine/knowledge.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Enumerations ───────────────────────────────────────────────────────────────

class RequirementKind(str, Enum):
    """Category of a workflow step prerequisite."""
    SLOT          = "SLOT"           # A filled slot value (e.g., session_id, urn)
    EVIDENCE      = "EVIDENCE"       # Collected evidence of a given type
    INVESTIGATION = "INVESTIGATION"  # A prior investigation must have completed
    KNOWLEDGE     = "KNOWLEDGE"      # A knowledge lookup must have completed
    APPROVAL      = "APPROVAL"       # Human approval record must be present
    TOOL_RESULT   = "TOOL_RESULT"    # A specific tool result must be present


class OutputKind(str, Enum):
    """Category of value a workflow step produces."""
    EVIDENCE        = "EVIDENCE"
    ROOT_CAUSE      = "ROOT_CAUSE"
    RECOMMENDATION  = "RECOMMENDATION"
    ACTION_PROPOSAL = "ACTION_PROPOSAL"
    KNOWLEDGE_ENTRY = "KNOWLEDGE_ENTRY"
    OBSERVATION     = "OBSERVATION"
    CLARIFICATION   = "CLARIFICATION"


class TransitionTrigger(str, Enum):
    """Event that activates a workflow transition."""
    SUCCESS        = "SUCCESS"
    FAILURE        = "FAILURE"
    ESCALATE       = "ESCALATE"
    APPROVED       = "APPROVED"
    REJECTED       = "REJECTED"
    SLOT_MISSING   = "SLOT_MISSING"
    LOW_CONFIDENCE = "LOW_CONFIDENCE"
    HIGH_RISK      = "HIGH_RISK"


# ── Requirement ────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowRequirement:
    """
    Declares a prerequisite that must be satisfied before a step executes.

    Used by InvestigationPlanner (Sprint 2.39) to verify the investigation
    context is sufficiently populated before executing each step.

    kind:        Category of the requirement
    key:         Name of the required item (slot name, evidence type, etc.)
    required:    True = step is blocked without this; False = optional enrichment
    description: Human-readable explanation for audit and logging
    """
    kind:        RequirementKind
    key:         str
    required:    bool = True
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":        self.kind.value,
            "key":         self.key,
            "required":    self.required,
            "description": self.description,
        }


# ── Output ─────────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowOutput:
    """
    Declares what value a workflow step produces on success.

    Used by InvestigationPlanner to understand what each step contributes
    to the InvestigationContext, and to validate that downstream steps
    can satisfy their requirements.

    kind:        Category of the output value
    key:         Context key under which the output is stored
    description: Human-readable explanation
    schema_hint: Optional type description for documentation and validation
    """
    kind:        OutputKind
    key:         str
    description: str = ""
    schema_hint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind":        self.kind.value,
            "key":         self.key,
            "description": self.description,
            "schema_hint": self.schema_hint,
        }


# ── Transition ─────────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class WorkflowTransition:
    """
    A typed transition between workflow steps.

    Provides a structured, auditable alternative to the raw on_success /
    on_failure string fields in WorkflowStep. Used by WorkflowResolver to
    build a typed transition graph for validation and planning.

    trigger:     Event that fires this transition
    target_step: step_id of the destination step ("RESOLVE" or "ESCALATE" for terminals)
    condition:   Optional guard condition expression (evaluated by WorkflowEngine)
    description: Human-readable explanation for audit trail
    """
    trigger:     TransitionTrigger
    target_step: str
    condition:   str = ""
    description: str = ""

    def is_terminal(self) -> bool:
        """Return True if this transition leads to a terminal workflow state."""
        return self.target_step in {"RESOLVE", "ESCALATE", "FAIL"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "trigger":     self.trigger.value,
            "target_step": self.target_step,
            "condition":   self.condition,
            "description": self.description,
            "is_terminal": self.is_terminal(),
        }
