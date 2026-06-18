"""
runtime/invariants.py

Sprint 2.27.8: Runtime Invariant System.

Mechanical checks that must hold at runtime to prevent dangerous or
incorrect system behaviour. Violations are non-recoverable: they indicate
a programmer error or misconfiguration, not a runtime failure.

Invariants:
  1. Gateway approval — REVERSIBLE and IRREVERSIBLE actions always require
     approval. SAFE actions never do. Any deviation is a configuration bug.

  2. Slot fill before workflow — workflow must not start if required slots
     are unfilled. Clarification must run first.

  3. Action routing — every action dispatched through RouterService must
     resolve to a known adapter. Unknown action types must be rejected before
     reaching execution.

  4. Playbook terminal states — every playbook must have at least one
     ESCALATE_CASE or RESOLVE_CASE step. A playbook with no terminal
     state can run forever.

Usage:
    from runtime.invariants import (
        check_gateway_approval_invariant,
        check_slot_fill_before_workflow,
        check_action_routing_invariant,
        assert_playbook_has_terminal_states,
        InvariantViolation,
    )
"""
from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class InvariantViolation(Exception):
    """
    Raised when a runtime invariant is violated.

    This is a programming/configuration error — it must never happen in
    a correctly configured system. Callers that catch this should log
    it as CRITICAL and stop processing the current request.
    """
    def __init__(self, invariant_name: str, detail: str) -> None:
        self.invariant_name = invariant_name
        self.detail = detail
        super().__init__(f"InvariantViolation [{invariant_name}]: {detail}")


# ── Invariant 1: Gateway Approval ─────────────────────────────────────────────

# Risk levels that ALWAYS require approval before execution.
# SAFE actions are auto-approvable; REVERSIBLE and IRREVERSIBLE are not.
_APPROVAL_REQUIRED_RISK_LEVELS: frozenset[str] = frozenset({
    "REVERSIBLE",
    "IRREVERSIBLE",
})

_NEVER_APPROVE_RISK_LEVELS: frozenset[str] = frozenset({
    "SAFE",
})


def check_gateway_approval_invariant(
    risk_level: str,
    approval_required: bool,
) -> None:
    """
    Verify that approval_required matches the risk_level contract.

    Invariant:
      REVERSIBLE  → approval_required must be True
      IRREVERSIBLE → approval_required must be True
      SAFE        → approval_required must be False

    Raises InvariantViolation if the contract is broken.
    """
    risk_upper = (risk_level or "").upper()

    if risk_upper in _APPROVAL_REQUIRED_RISK_LEVELS and not approval_required:
        raise InvariantViolation(
            invariant_name="GATEWAY_APPROVAL",
            detail=(
                f"risk_level={risk_upper} must have approval_required=True "
                f"but approval_required={approval_required}. "
                "This is a gateway configuration error."
            ),
        )

    if risk_upper in _NEVER_APPROVE_RISK_LEVELS and approval_required:
        raise InvariantViolation(
            invariant_name="GATEWAY_APPROVAL",
            detail=(
                f"risk_level=SAFE must have approval_required=False "
                f"but approval_required={approval_required}. "
                "SAFE actions should never require approval."
            ),
        )


# ── Invariant 2: Slot Fill Before Workflow ────────────────────────────────────

def check_slot_fill_before_workflow(msg_result: Any) -> None:
    """
    Verify that workflow was not auto-started before slots are filled.

    Invariant: msg_result.workflow_started must be False (or falsy)
    if msg_result.all_slots_filled is False.

    Args:
        msg_result: MessageResult from CaseService.receive_message()
                    (must have all_slots_filled and workflow_started attributes)

    Raises InvariantViolation if workflow started before slots are filled.
    """
    all_slots_filled = getattr(msg_result, "all_slots_filled", True)
    workflow_started = getattr(msg_result, "workflow_started", False)

    if workflow_started and not all_slots_filled:
        raise InvariantViolation(
            invariant_name="SLOT_FILL_BEFORE_WORKFLOW",
            detail=(
                "workflow_started=True but all_slots_filled=False. "
                "Clarification must complete before workflow execution. "
                "This is a CaseService state machine error."
            ),
        )


# ── Invariant 3: Action Routing ───────────────────────────────────────────────

def check_action_routing_invariant(
    action_type: str,
    adapter_result: Any,
) -> None:
    """
    Verify that every routed action has a known adapter that succeeded.

    Invariant: if adapter_result is not None, it must indicate routing
    succeeded (not a ROUTING_FAILED or NOT_ROUTABLE outcome).

    Args:
        action_type:    The action type string (e.g. "otp_resend")
        adapter_result: AdapterExecutionResult or RouterResult. May be None
                        if routing was not attempted.

    Raises InvariantViolation if the result indicates routing failure
    for a known action.
    """
    if adapter_result is None:
        return  # routing was not attempted; no invariant to check

    # Check RouterResult.success or AdapterExecutionResult.success
    success = getattr(adapter_result, "success", None)
    error_code = getattr(adapter_result, "error_code", None) or ""

    if success is False and "NOT_ROUTABLE" in str(error_code).upper():
        raise InvariantViolation(
            invariant_name="ACTION_ROUTING",
            detail=(
                f"action_type={action_type!r} has no registered adapter route "
                f"(error_code={error_code!r}). "
                "All dispatched action types must be in the routing table."
            ),
        )


# ── Invariant 4: Playbook Terminal States ─────────────────────────────────────

_TERMINAL_STEP_TYPES: frozenset[str] = frozenset({
    "ESCALATE_CASE",
    "RESOLVE_CASE",
})


def assert_playbook_has_terminal_states(playbook: Any) -> None:
    """
    Verify that a playbook definition has at least one terminal step.

    Invariant: every playbook must have at least one step of type
    ESCALATE_CASE or RESOLVE_CASE. Without a terminal state a workflow
    can loop indefinitely.

    Args:
        playbook: WorkflowDefinition (must have .steps attribute as iterable
                  of objects with .step_type attribute)

    Raises InvariantViolation if no terminal step is found.
    """
    steps = getattr(playbook, "steps", [])
    step_types = set()

    for step in steps:
        st = getattr(step, "step_type", None)
        if st is not None:
            # Handle both enum values and strings
            step_types.add(str(st.value) if hasattr(st, "value") else str(st))

    has_terminal = bool(step_types & _TERMINAL_STEP_TYPES)
    if not has_terminal:
        playbook_id = getattr(playbook, "playbook_id", repr(playbook))
        raise InvariantViolation(
            invariant_name="PLAYBOOK_TERMINAL_STATES",
            detail=(
                f"Playbook {playbook_id!r} has no ESCALATE_CASE or RESOLVE_CASE step. "
                f"Found step types: {sorted(step_types)}. "
                "A playbook without a terminal state can run indefinitely."
            ),
        )
