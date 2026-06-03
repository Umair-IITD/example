"""
case_engine/case_state.py

CaseState enum and allowed transition table.

Rules:
- All transitions must be explicitly listed here.
- Any transition not in ALLOWED_TRANSITIONS is illegal.
- Terminal states accept no further transitions.
- State machine is the only caller allowed to change state.
"""
from __future__ import annotations

from enum import Enum


class CaseState(str, Enum):
    """
    Case lifecycle states.

    Maps to 03_CASE_STATE_AND_DECISIONING.md state machine.
    CLOSED is the final resting state after RESOLVED, ESCALATED, or FAILED.
    """
    NEW             = "NEW"
    CLASSIFYING     = "CLASSIFYING"
    TRIAGE_COMPLETE = "TRIAGE_COMPLETE"   # Level 1 terminal: classified + note posted
    AWAITING_INPUT  = "AWAITING_INPUT"    # Slot filling in progress
    WORKFLOW_ACTIVE = "WORKFLOW_ACTIVE"   # Level 2: playbook executing
    ACTION_PENDING  = "ACTION_PENDING"    # Action proposal awaiting gateway
    ESCALATED       = "ESCALATED"         # Human handoff triggered
    RESOLVED        = "RESOLVED"          # Workflow resolved successfully
    FAILED          = "FAILED"            # DLQ / max retries exhausted
    CLOSED          = "CLOSED"            # Final terminal: human-closed


TERMINAL_STATES: frozenset[CaseState] = frozenset({
    CaseState.CLOSED,
})

# States where no further automated processing occurs
FROZEN_STATES: frozenset[CaseState] = frozenset({
    CaseState.ESCALATED,
    CaseState.RESOLVED,
    CaseState.FAILED,
    CaseState.CLOSED,
})

# Allowed transitions: from_state → set of allowed to_states
ALLOWED_TRANSITIONS: dict[CaseState, frozenset[CaseState]] = {
    CaseState.NEW: frozenset({
        CaseState.CLASSIFYING,
        CaseState.ESCALATED,   # direct escalation on intake (security freeze, malformed payload)
    }),
    CaseState.CLASSIFYING: frozenset({
        CaseState.TRIAGE_COMPLETE,  # confidence >= 0.85 + note posted
        CaseState.ESCALATED,        # confidence < 0.85 or unknown topic
        CaseState.FAILED,           # classifier crash
    }),
    CaseState.TRIAGE_COMPLETE: frozenset({
        CaseState.WORKFLOW_ACTIVE,  # Level 2: workflow engine activated
        CaseState.ESCALATED,        # human requests escalation post-triage
        CaseState.CLOSED,           # Level 1: human agent closes ticket
    }),
    CaseState.AWAITING_INPUT: frozenset({
        CaseState.WORKFLOW_ACTIVE,  # slot filled and validated
        CaseState.ESCALATED,        # slot_fill_timeout or repeated failure
        CaseState.FAILED,           # slot fill system error
    }),
    CaseState.WORKFLOW_ACTIVE: frozenset({
        CaseState.AWAITING_INPUT,   # required slot missing
        CaseState.ACTION_PENDING,   # action proposal generated
        CaseState.ESCALATED,        # escalation trigger fired
        CaseState.RESOLVED,         # workflow completed successfully
        CaseState.FAILED,           # max retries exhausted
    }),
    CaseState.ACTION_PENDING: frozenset({
        CaseState.WORKFLOW_ACTIVE,  # action validated and executed → back to workflow
        CaseState.ESCALATED,        # IRREVERSIBLE action or validation failure
        CaseState.FAILED,           # action gateway system error
    }),
    CaseState.ESCALATED: frozenset({
        CaseState.CLOSED,           # human agent closes the case
    }),
    CaseState.RESOLVED: frozenset({
        CaseState.CLOSED,           # finalize
        CaseState.ESCALATED,        # human re-opens and escalates (rare)
    }),
    CaseState.FAILED: frozenset({
        CaseState.CLOSED,           # manual intervention complete
        CaseState.ESCALATED,        # operator escalates failed case to human queue
    }),
    CaseState.CLOSED: frozenset(),  # terminal — no transitions out
}
