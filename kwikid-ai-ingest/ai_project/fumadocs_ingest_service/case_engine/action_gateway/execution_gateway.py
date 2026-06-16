"""
case_engine/action_gateway/execution_gateway.py

Sprint 2.1 ActionGateway — Action Execution Foundation.

This module contains the original case_engine/action_gateway.py content,
moved here to make room for the Sprint 2.22 action_gateway/ package.

The package __init__.py re-exports ActionGateway, ActionGatewayError,
DuplicateActionError, and build_action_gateway so all existing imports
continue to work transparently:

    from case_engine.action_gateway import ActionGateway          # unchanged
    from case_engine.action_gateway import build_action_gateway   # unchanged

The gateway implements the Proposal/Validation/Execution split:

    AI proposes → human validates (REVERSIBLE/IRREVERSIBLE) → executor runs

The AI NEVER calls this gateway's execute path directly — it calls propose().
The executor (Sprint 2.2: Temporal worker / Celery task) calls begin_execution(),
record_success(), record_failure(). Human agents call approve() or reject()
via the approval API (Sprint 2.2).

Design principles:
  - Fail closed: any unexpected state raises ActionGatewayError, not silently proceeds.
  - Idempotency-first: duplicate proposals are detected at propose() via the
    idempotency_key and returned as-is (not rejected as errors).
  - Risk-tiered: SAFE auto-approves immediately; REVERSIBLE/IRREVERSIBLE require
    human decision within an SLA window.
  - Audit trail: every transition is recorded in action_gateway_transitions via the
    ActionStateMachine's on_transition callback.

Dependency surface for Sprint 2.2 (NOT implemented here):
  - Executor: calls begin_execution() + record_success() / record_failure()
  - SLA watchdog: calls expire() on AWAITING_APPROVAL past deadline
  - Approval API: HTTP endpoint calls approve() / reject()
  - Temporal workflow: wraps begin_execution → result recording in a durable activity
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from case_engine.action_models import (
    APPROVAL_DEADLINE_IRREVERSIBLE,
    APPROVAL_DEADLINE_REVERSIBLE,
    ActionProposal,
    ActionRequest,
    ActionTransitionRecord,
    compute_idempotency_key,
)
from case_engine.action_repository import ActionRepository
from case_engine.action_state import (
    DEFAULT_MAX_ATTEMPTS,
    ActionRiskLevel,
    ActionState,
    ActionStateMachine,
    ActionTransitionError,
)
from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


class ActionGatewayError(RuntimeError):
    """Raised for invalid gateway operations that are not state-machine violations."""


class DuplicateActionError(ActionGatewayError):
    """
    Raised when propose() detects an existing action with the same idempotency key.

    Callers should treat this as idempotent success and use the existing action.
    """
    def __init__(self, existing: ActionRequest) -> None:
        self.existing = existing
        super().__init__(
            f"Action already exists for idempotency_key={existing.idempotency_key[:16]}... "
            f"(action_id={existing.action_id} state={existing.current_state.value})"
        )


class ActionGateway:
    """
    Orchestrates the full Action Request lifecycle.

    Stateless: all state lives in ActionRequest objects and the DB.
    Thread-safe: no shared mutable state.
    """

    def __init__(
        self,
        repository: ActionRepository,
        supabase_client: Any = None,
        metrics_service: Any = None,
    ) -> None:
        self._repo = repository
        self._sm   = ActionStateMachine(on_transition=self._persist_transition)
        self._metrics = metrics_service

    def _record_metric(self, method_name: str) -> None:
        """Fire-and-forget metrics recording. Never raises."""
        if self._metrics is not None:
            try:
                getattr(self._metrics, method_name)()
            except Exception:
                pass

    # ── Proposal ───────────────────────────────────────────────────────────────

    def propose(
        self,
        case: Case,
        proposal: ActionProposal,
    ) -> ActionRequest:
        """
        Create a new action request for the given case.

        Steps:
          1. Validate the proposal (risk consistency, required fields).
          2. Compute idempotency key and check for duplicate.
          3. Build ActionRequest with appropriate defaults per risk_level.
          4. INSERT the action row in PROPOSED state — establishes the FK anchor
             required by action_gateway_transitions.action_id before any transition
             callback fires.
          5. For SAFE: auto-approve immediately (PROPOSED → APPROVED).
          6. For REVERSIBLE/IRREVERSIBLE: move to AWAITING_APPROVAL with SLA deadline.
          7. UPDATE the row with the post-transition state and return.

        Raises:
          ValueError: proposal validation failed.
          DuplicateActionError: same logical action already proposed.
          ActionGatewayError: unexpected error during persistence.
        """
        proposal.validate()

        idem_key = compute_idempotency_key(
            case_id=case.case_id,
            action_type=proposal.action_type,
            action_namespace=proposal.action_namespace,
            action_params=proposal.action_params,
        )

        # Idempotency pre-check: return existing action if already proposed
        existing = self._repo.get_action_by_idempotency_key(idem_key)
        if existing is not None:
            LOGGER.info(
                "action_gateway.propose: duplicate detected action_id=%s key=%s...",
                existing.action_id, idem_key[:16],
            )
            raise DuplicateActionError(existing)

        now = datetime.now(tz=timezone.utc)

        action = ActionRequest(
            case_id=case.case_id,
            ticket_id=case.ticket_id,
            client=case.client,
            action_type=proposal.action_type,
            action_namespace=proposal.action_namespace,
            risk_level=proposal.risk_level,
            current_state=ActionState.PROPOSED,
            proposed_by=proposal.proposed_by,
            proposed_at=now,
            action_payload=proposal.action_params,
            rollback_action_type=proposal.rollback_action_type,
            rollback_params=proposal.rollback_params,
            approval_required=(proposal.risk_level != ActionRiskLevel.SAFE),
            max_attempts=DEFAULT_MAX_ATTEMPTS[proposal.risk_level],
            idempotency_key=idem_key,
        )

        # Pre-set SLA deadline before insert so the initial row carries expires_at.
        if proposal.risk_level == ActionRiskLevel.REVERSIBLE:
            action.expires_at = now + APPROVAL_DEADLINE_REVERSIBLE
        elif proposal.risk_level == ActionRiskLevel.IRREVERSIBLE:
            action.expires_at = now + APPROVAL_DEADLINE_IRREVERSIBLE

        # ── INSERT the action row FIRST ────────────────────────────────────────
        action = self._repo.insert_action(action)

        # ── Apply initial state transition ─────────────────────────────────────
        if proposal.risk_level == ActionRiskLevel.SAFE:
            self._sm.transition(
                action, ActionState.APPROVED,
                reason="auto_approved_safe",
                actor="auto_approval",
                detail={"risk_level": ActionRiskLevel.SAFE.value},
            )
            action.approver = "auto_approval"
            action.approved_at = datetime.now(tz=timezone.utc)

        elif proposal.risk_level == ActionRiskLevel.REVERSIBLE:
            self._sm.transition(
                action, ActionState.AWAITING_APPROVAL,
                reason="requires_human_approval",
                actor="system",
                detail={
                    "risk_level": ActionRiskLevel.REVERSIBLE.value,
                    "expires_at": action.expires_at.isoformat(),
                },
            )

        else:  # IRREVERSIBLE
            self._sm.transition(
                action, ActionState.AWAITING_APPROVAL,
                reason="requires_strict_human_approval",
                actor="system",
                detail={
                    "risk_level": ActionRiskLevel.IRREVERSIBLE.value,
                    "expires_at": action.expires_at.isoformat(),
                    "max_attempts": 1,
                },
            )

        self._repo.update_action(action)
        self._record_metric("record_action_created")

        LOGGER.info(
            "action_gateway.propose: action_id=%s type=%s risk=%s state=%s",
            action.action_id, action.action_type,
            action.risk_level.value, action.current_state.value,
        )
        return action

    # ── Human approval ─────────────────────────────────────────────────────────

    def approve(
        self,
        action: ActionRequest,
        *,
        approved_by: str,
        notes: str = "",
    ) -> ActionRequest:
        """Record a human approval decision. Transitions to APPROVED."""
        now = datetime.now(tz=timezone.utc)
        self._sm.transition(
            action, ActionState.APPROVED,
            reason="human_approved",
            actor=f"human:{approved_by}",
            detail={"approved_by": approved_by, "notes": notes},
        )
        action.approver = approved_by
        action.approved_at = now
        action.approval_notes = notes or None
        self._repo.update_action(action)
        self._record_metric("record_action_approved")
        return action

    def reject(
        self,
        action: ActionRequest,
        *,
        rejected_by: str,
        notes: str = "",
    ) -> ActionRequest:
        """Record a human rejection decision. Transitions to REJECTED (terminal)."""
        now = datetime.now(tz=timezone.utc)
        self._sm.transition(
            action, ActionState.REJECTED,
            reason="human_rejected",
            actor=f"human:{rejected_by}",
            detail={"rejected_by": rejected_by, "notes": notes},
        )
        action.rejected_at = now
        action.approval_notes = notes or None
        self._repo.update_action(action)
        self._record_metric("record_action_rejected")

        LOGGER.info(
            "action_gateway.reject: action_id=%s rejected_by=%s",
            action.action_id, rejected_by,
        )
        return action

    def expire(self, action: ActionRequest, *, reason: str = "sla_deadline_elapsed") -> ActionRequest:
        """Expire an action whose approval SLA has elapsed."""
        self._sm.transition(
            action, ActionState.EXPIRED,
            reason=reason,
            actor="watchdog:sla",
            detail={"expires_at": action.expires_at.isoformat() if action.expires_at else None},
        )
        self._repo.update_action(action)
        return action

    # ── Execution ──────────────────────────────────────────────────────────────

    def begin_execution(
        self,
        action: ActionRequest,
        *,
        executor_id: str,
    ) -> ActionRequest:
        """
        Claim an APPROVED action for execution.

        Uses optimistic locking: UPDATE WHERE current_state='APPROVED'.
        Raises ActionGatewayError if action is not APPROVED or another worker
        already claimed it.
        """
        if action.current_state != ActionState.APPROVED:
            raise ActionGatewayError(
                f"begin_execution requires APPROVED state, got {action.current_state.value} "
                f"for action {action.action_id}"
            )

        from_state = action.current_state

        action.execution_attempt += 1
        action.executor_id = executor_id
        action.execution_started_at = datetime.now(tz=timezone.utc)

        self._sm.transition(
            action, ActionState.EXECUTING,
            reason="executor_acquired",
            actor=f"executor:{executor_id}",
            detail={
                "executor_id":       executor_id,
                "execution_attempt": action.execution_attempt,
                "max_attempts":      action.max_attempts,
            },
        )
        claimed = self._repo.update_action(action, expected_state=from_state)
        if not claimed:
            raise ActionGatewayError(
                f"begin_execution: optimistic lock failure for action {action.action_id} — "
                "another worker already claimed this action. "
                "The local state has been mutated; discard this action object."
            )
        return action

    def record_success(
        self,
        action: ActionRequest,
        *,
        result: dict[str, Any],
    ) -> ActionRequest:
        """Record a successful execution outcome. Transitions EXECUTING → EXECUTED."""
        action.execution_completed_at = datetime.now(tz=timezone.utc)
        action.execution_result = result
        action.failure_code = None
        action.failure_reason = None

        self._sm.transition(
            action, ActionState.EXECUTED,
            reason="execution_succeeded",
            actor=f"executor:{action.executor_id or 'unknown'}",
            detail={"execution_attempt": action.execution_attempt},
        )
        self._repo.update_action(action)

        LOGGER.info(
            "action_gateway.record_success: action_id=%s attempt=%d",
            action.action_id, action.execution_attempt,
        )
        return action

    def record_failure(
        self,
        action: ActionRequest,
        *,
        failure_code: str,
        reason: str,
        permanent: bool = False,
    ) -> ActionRequest:
        """
        Record an execution failure.

        Retries if execution_attempt < max_attempts AND permanent=False.
        Otherwise dead-letters.
        """
        now = datetime.now(tz=timezone.utc)
        action.execution_completed_at = now
        action.execution_failed_at = now
        action.failure_code = failure_code
        action.failure_reason = reason

        self._sm.transition(
            action, ActionState.FAILED,
            reason=f"execution_failed:{failure_code}",
            actor=f"executor:{action.executor_id or 'unknown'}",
            detail={
                "failure_code":      failure_code,
                "reason":            reason,
                "execution_attempt": action.execution_attempt,
                "max_attempts":      action.max_attempts,
                "permanent":         permanent,
            },
        )

        if action.can_retry and not permanent:
            action.executor_id = None
            action.execution_started_at = None
            action.execution_completed_at = None
            self._sm.transition(
                action, ActionState.APPROVED,
                reason="retry_scheduled",
                actor="system",
                detail={
                    "execution_attempt":  action.execution_attempt,
                    "max_attempts":       action.max_attempts,
                    "remaining_attempts": action.max_attempts - action.execution_attempt,
                },
            )
            LOGGER.warning(
                "action_gateway.record_failure: retry scheduled action_id=%s "
                "attempt=%d/%d code=%s",
                action.action_id, action.execution_attempt, action.max_attempts, failure_code,
            )
            self._record_metric("record_action_retried")
        else:
            action.dead_lettered_at = datetime.now(tz=timezone.utc)
            self._sm.transition(
                action, ActionState.DEAD_LETTER,
                reason=f"dead_lettered:{failure_code}",
                actor="system",
                detail={
                    "failure_code":      failure_code,
                    "reason":            reason,
                    "execution_attempt": action.execution_attempt,
                    "max_attempts":      action.max_attempts,
                    "permanent":         permanent,
                },
            )
            LOGGER.error(
                "action_gateway.record_failure: dead-lettered action_id=%s "
                "attempt=%d/%d code=%s permanent=%s — REQUIRES HUMAN INTERVENTION",
                action.action_id, action.execution_attempt, action.max_attempts,
                failure_code, permanent,
            )
            self._record_metric("record_action_dead_lettered")

        self._repo.update_action(action)
        return action

    def record_timeout(
        self,
        action: ActionRequest,
        *,
        executor_id: str,
    ) -> ActionRequest:
        """Record an execution timeout. Retries if budget allows, otherwise dead-letters."""
        now = datetime.now(tz=timezone.utc)
        action.execution_completed_at = now
        action.execution_failed_at = now
        action.failure_code = "EXECUTOR_TIMEOUT"
        action.failure_reason = f"Executor {executor_id} lost heartbeat"

        self._sm.transition(
            action, ActionState.TIMED_OUT,
            reason="executor_timeout",
            actor="watchdog:stuck_execution",
            detail={
                "executor_id":       executor_id,
                "execution_attempt": action.execution_attempt,
            },
        )

        if action.can_retry:
            action.executor_id = None
            action.execution_started_at = None
            action.execution_completed_at = None
            self._sm.transition(
                action, ActionState.APPROVED,
                reason="retry_after_timeout",
                actor="system",
                detail={"remaining_attempts": action.max_attempts - action.execution_attempt},
            )
        else:
            self._sm.transition(
                action, ActionState.FAILED,
                reason="max_attempts_after_timeout",
                actor="system",
                detail={"execution_attempt": action.execution_attempt},
            )
            action.dead_lettered_at = datetime.now(tz=timezone.utc)
            self._sm.transition(
                action, ActionState.DEAD_LETTER,
                reason="dead_lettered:EXECUTOR_TIMEOUT",
                actor="system",
                detail={
                    "executor_id":       executor_id,
                    "execution_attempt": action.execution_attempt,
                    "max_attempts":      action.max_attempts,
                },
            )

        self._repo.update_action(action)
        return action

    # ── Rollback ───────────────────────────────────────────────────────────────

    def propose_rollback(
        self,
        action: ActionRequest,
        case: Case,
    ) -> ActionRequest:
        """Create a compensating action for a successfully executed REVERSIBLE action."""
        if action.current_state != ActionState.EXECUTED:
            raise ActionGatewayError(
                f"propose_rollback requires EXECUTED state, got {action.current_state.value}"
            )
        if action.risk_level != ActionRiskLevel.REVERSIBLE:
            raise ActionGatewayError(
                f"Only REVERSIBLE actions can be rolled back. "
                f"action {action.action_id} has risk_level={action.risk_level.value}"
            )
        if not action.rollback_action_type:
            raise ActionGatewayError(
                f"action {action.action_id} has no rollback_action_type — "
                "cannot propose rollback."
            )
        if action.is_rolled_back:
            raise ActionGatewayError(
                f"action {action.action_id} is already marked as rolled back "
                f"(rollback_action_id={action.rollback_action_id})"
            )

        compensation_proposal = ActionProposal(
            action_type=action.rollback_action_type,
            action_namespace=action.action_namespace,
            risk_level=ActionRiskLevel.SAFE,
            action_params=action.rollback_params or {},
            proposed_by="system",
        )

        try:
            compensation = self.propose(case, compensation_proposal)
        except DuplicateActionError as exc:
            compensation = exc.existing
            LOGGER.info(
                "action_gateway.propose_rollback: rollback already exists %s",
                compensation.action_id,
            )

        action.rollback_action_id = compensation.action_id

        self._sm.transition(
            action, ActionState.ROLLING_BACK,
            reason="rollback_proposed",
            actor="system",
            detail={"rollback_action_id": compensation.action_id},
        )
        self._repo.update_action(action)

        LOGGER.info(
            "action_gateway.propose_rollback: original=%s compensation=%s",
            action.action_id, compensation.action_id,
        )
        return compensation

    def record_rollback_success(self, action: ActionRequest) -> ActionRequest:
        """Mark a ROLLING_BACK action as successfully rolled back."""
        now = datetime.now(tz=timezone.utc)
        self._sm.transition(
            action, ActionState.ROLLED_BACK,
            reason="rollback_succeeded",
            actor="system",
        )
        action.is_rolled_back = True
        action.rollback_completed_at = now
        self._repo.update_action(action)
        return action

    def record_rollback_failure(
        self,
        action: ActionRequest,
        *,
        reason: str,
    ) -> ActionRequest:
        """Mark a ROLLING_BACK action as failed. Requires human escalation."""
        self._sm.transition(
            action, ActionState.ROLLBACK_FAILED,
            reason=f"rollback_failed:{reason}",
            actor="system",
            detail={"failure_reason": reason},
        )
        self._repo.update_action(action)
        LOGGER.error(
            "action_gateway.record_rollback_failure: action_id=%s reason=%s — "
            "REQUIRES HUMAN ESCALATION",
            action.action_id, reason,
        )
        return action

    # ── Internal ───────────────────────────────────────────────────────────────

    def _persist_transition(
        self,
        action: ActionRequest,
        record: ActionTransitionRecord,
    ) -> None:
        """on_transition callback: persists the ActionTransitionRecord."""
        self._repo.record_transition(record)


# ── Factory ────────────────────────────────────────────────────────────────────

def build_action_gateway(supabase_client: Any = None) -> ActionGateway:
    """
    Factory function for constructing a fully-wired ActionGateway.

    supabase_client=None → offline/test mode (in-memory only).
    """
    repo = ActionRepository(supabase_client=supabase_client)
    return ActionGateway(repository=repo, supabase_client=supabase_client)
