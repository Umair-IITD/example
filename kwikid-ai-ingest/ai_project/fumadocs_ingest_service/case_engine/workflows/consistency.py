"""
case_engine/workflows/consistency.py

Sprint 2.17: WorkflowConsistencyChecker

Validates that workflow state and case state are coherent.
Used as a pre-condition check before workflow operations and as a
diagnostic tool for admin visibility.

Design: no side effects, no DB calls, deterministic output.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from case_engine.case_state import CaseState
from case_engine.workflows.models import TERMINAL_WORKFLOW_STATES, WorkflowState

if TYPE_CHECKING:
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)

# Which case states are compatible with which workflow states
_CASE_TO_WORKFLOW_COMPATIBLE: dict[CaseState, frozenset[str]] = {
    CaseState.WORKFLOW_ACTIVE: frozenset({
        WorkflowState.RUNNING.value, WorkflowState.PENDING.value,
        WorkflowState.COMPLETED.value, WorkflowState.ESCALATED.value,
        WorkflowState.FAILED.value,
    }),
    CaseState.ACTION_PENDING: frozenset({
        WorkflowState.PAUSED.value,
    }),
    CaseState.RESOLVED: frozenset({
        WorkflowState.COMPLETED.value,
    }),
    CaseState.ESCALATED: frozenset({
        WorkflowState.ESCALATED.value, WorkflowState.FAILED.value,
    }),
    CaseState.FAILED: frozenset({
        WorkflowState.FAILED.value, WorkflowState.ESCALATED.value,
    }),
}


class WorkflowConsistencyError(ValueError):
    """Raised when a workflow operation would violate consistency invariants."""


class WorkflowConsistencyChecker:
    """
    Validates workflow / case state coherence before workflow operations.

    All methods are pure (no side effects, no DB calls).
    Raises WorkflowConsistencyError if a condition is violated.
    Returns list[str] warnings for soft checks.
    """

    # ── Hard guards (raise on violation) ──────────────────────────────────────

    def validate_start(self, case: "Case") -> None:
        """
        Raise WorkflowConsistencyError if starting a workflow would be invalid.

        Checks:
        1. Terminal workflow states cannot be restarted.
        2. Active workflow states should not be restarted (use resume instead).
        """
        ws = case.workflow_state
        if ws is None:
            return  # No prior workflow — starting is valid

        if ws in {WorkflowState.COMPLETED.value}:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} already has a COMPLETED workflow. "
                "Cannot restart a completed workflow."
            )

        if ws in {WorkflowState.RUNNING.value, WorkflowState.PAUSED.value}:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} has an active workflow (state={ws}). "
                "Call resume_workflow instead of start_workflow."
            )

    def validate_resume(self, case: "Case") -> None:
        """
        Raise WorkflowConsistencyError if resuming would be invalid.

        Checks:
        1. Only PAUSED workflows can be resumed.
        2. Terminal workflows cannot be resumed.
        3. Case must be in ACTION_PENDING state to resume.
        """
        ws = case.workflow_state

        if ws is None or ws == WorkflowState.PENDING.value:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} has no active paused workflow (workflow_state={ws}). "
                "Cannot resume."
            )

        terminal_values = {s.value for s in TERMINAL_WORKFLOW_STATES}
        if ws in terminal_values:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} workflow is in terminal state {ws}. "
                "Cannot resume a terminal workflow."
            )

        if ws != WorkflowState.PAUSED.value:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} workflow is in state {ws} (expected PAUSED). "
                "Only PAUSED workflows can be resumed."
            )

        if case.current_state != CaseState.ACTION_PENDING:
            raise WorkflowConsistencyError(
                f"Case {case.case_id} is in case state {case.current_state.value} "
                f"(expected ACTION_PENDING) while workflow_state=PAUSED. "
                "State misalignment detected."
            )

    # ── Soft checks (return warning list) ─────────────────────────────────────

    def check_state_alignment(self, case: "Case") -> list[str]:
        """
        Return a list of consistency warnings for a case.

        Empty list means the case is fully consistent.
        Non-empty list means there are alignment issues worth investigating.
        """
        warnings: list[str] = []
        ws = case.workflow_state

        if ws is None:
            return warnings  # No workflow — nothing to check

        allowed_ws = _CASE_TO_WORKFLOW_COMPATIBLE.get(case.current_state)
        if allowed_ws is not None and ws not in allowed_ws:
            warnings.append(
                f"case_state={case.current_state.value} is incompatible with "
                f"workflow_state={ws}. "
                f"Expected one of: {sorted(allowed_ws)}"
            )

        # PAUSED workflow should always have pending_action_id
        if ws == WorkflowState.PAUSED.value:
            ctx = case.workflow_context or {}
            if not ctx.get("pending_action_id"):
                warnings.append(
                    f"Case {case.case_id} has PAUSED workflow but no pending_action_id "
                    "in workflow_context. May be missing action gateway entry."
                )

        # Completed workflow should always have completed_at
        if ws in {WorkflowState.COMPLETED.value, WorkflowState.ESCALATED.value, WorkflowState.FAILED.value}:
            ctx = case.workflow_context or {}
            if not ctx.get("completed_at"):
                warnings.append(
                    f"Case {case.case_id} has terminal workflow_state={ws} "
                    "but no completed_at in workflow_context."
                )

        return warnings

    def validate_no_duplicate_active_workflow(
        self,
        cases: list["Case"],
    ) -> list[str]:
        """
        Scan a list of cases and return case_ids with multiple active workflows.

        In the current architecture, each case should have at most one active
        workflow. This method is for batch diagnostic use.
        """
        violations: list[str] = []
        active_states = {WorkflowState.RUNNING.value, WorkflowState.PAUSED.value}
        seen: dict[str, int] = {}
        for case in cases:
            if case.workflow_state in active_states:
                seen[case.case_id] = seen.get(case.case_id, 0) + 1
        for case_id, count in seen.items():
            if count > 1:
                violations.append(
                    f"Case {case_id} appears {count} times with an active workflow"
                )
        return violations
