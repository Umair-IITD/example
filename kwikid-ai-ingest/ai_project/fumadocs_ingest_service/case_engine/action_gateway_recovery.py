"""
case_engine/action_gateway_recovery.py

Sprint 2.14: Human Recovery & Operational Control.

ActionGatewayRecoveryService provides operator-initiated interventions that are
not part of the normal action lifecycle.  All methods:

  - Are read-idempotent on error: they leave the DB unchanged when they return
    a non-success RecoveryResult.
  - Never raise — all exceptions are caught and returned as INTERNAL_ERROR.
  - Use optimistic locking for every state mutation.
  - Emit a full audit trail entry for every successful intervention.

Methods:
  retry_dead_letter_action(action_id, actor)  — DEAD_LETTER → APPROVED (bypass)
  retry_failed_action(action_id, actor)        — FAILED → APPROVED (if can_retry)
  cancel_action(action_id, actor, reason)      — PROPOSED/AWAITING_APPROVAL/APPROVED → CANCELLED
  expire_action_manually(action_id, actor)     — PROPOSED/AWAITING_APPROVAL/APPROVED → EXPIRED
  force_rollback_action(action_id, actor)      — EXECUTED (REVERSIBLE) → ROLLING_BACK
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from audit.models import AuditEvent, AuditEventType
from audit.service import AuditService
from case_engine.action_models import ActionRequest, ActionTransitionRecord
from case_engine.action_repository import ActionRepository
from case_engine.action_state import (
    ActionRiskLevel,
    ActionState,
    ActionStateMachine,
    ActionTransitionError,
)

LOGGER = logging.getLogger(__name__)


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


# ── RecoveryResult ─────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RecoveryResult:
    """
    Outcome of a single recovery operation.

    On success=True: old_state and new_state are both set.
    On success=False: error_code and error_message explain why.
    """
    success:       bool
    action_id:     str
    old_state:     str | None = None
    new_state:     str | None = None
    error_code:    str | None = None
    error_message: str | None = None


# ── Error codes (returned in RecoveryResult.error_code) ───────────────────────

NOT_FOUND         = "NOT_FOUND"
INVALID_STATE     = "INVALID_STATE"
RETRY_EXHAUSTED   = "RETRY_EXHAUSTED"
NOT_REVERSIBLE    = "NOT_REVERSIBLE"
NO_ROLLBACK_SPEC  = "NO_ROLLBACK_SPEC"
ALREADY_ROLLED_BACK = "ALREADY_ROLLED_BACK"
LOCK_CONTENTION   = "LOCK_CONTENTION"
INTERNAL_ERROR    = "INTERNAL_ERROR"


# ── ActionGatewayRecoveryService ───────────────────────────────────────────────

class ActionGatewayRecoveryService:
    """
    Operator-initiated recovery operations for the Action Gateway.

    Requires:
        repository   — shared ActionRepository (same instance as gateway/runtime)
        gateway      — ActionGateway (for force_rollback_action delegation)
        audit_service — AuditService for operator audit trail
    """

    def __init__(
        self,
        repository: ActionRepository,
        gateway: Any,
        audit_service: AuditService | None = None,
    ) -> None:
        self._repo = repository
        self._gateway = gateway
        self._audit = audit_service

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _emit_audit(
        self,
        action: ActionRequest,
        event_type: AuditEventType,
        actor: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Fire-and-forget audit event. Never raises."""
        if self._audit is None:
            return
        try:
            event = AuditEvent(
                action_id=action.action_id,
                event_type=event_type,
                actor=actor,
                case_id=action.case_id,
                client=action.client,
                metadata=metadata or {},
            )
            self._audit.emit(event)
        except Exception:
            LOGGER.exception(
                "recovery_service._emit_audit: failed action_id=%s type=%s",
                action.action_id, event_type.value,
            )

    def _fetch(self, action_id: str) -> ActionRequest | None:
        """Return the action or None if not found. Raises on repo error."""
        return self._repo.get_action(action_id)

    # ── D2: DEAD_LETTER → APPROVED recovery ───────────────────────────────────

    def retry_dead_letter_action(
        self,
        action_id: str,
        actor: str,
        reason: str = "operator_recovery",
    ) -> RecoveryResult:
        """
        Recover a DEAD_LETTER action back to APPROVED for re-execution.

        This bypasses the normal state machine because DEAD_LETTER is a terminal
        state — can_transition() would return False.  Instead the recovery
        service directly mutates the action fields, creates a transition record
        manually, and uses update_action(expected_state=DEAD_LETTER) for
        optimistic locking.

        Also resets: execution_attempt=0, dead_lettered_at=None,
        failure_code=None, failure_reason=None, executor_id=None.
        """
        try:
            action = self._fetch(action_id)
            if action is None:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    error_code=NOT_FOUND,
                    error_message=f"Action {action_id} not found",
                )

            if action.current_state != ActionState.DEAD_LETTER:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=INVALID_STATE,
                    error_message=(
                        f"retry_dead_letter requires DEAD_LETTER state, "
                        f"got {action.current_state.value}"
                    ),
                )

            old_state = action.current_state

            # Reset retry-related fields so the action gets a clean execution slot
            action.execution_attempt = 0
            action.dead_lettered_at = None
            action.failure_code = None
            action.failure_reason = None
            action.executor_id = None
            action.execution_started_at = None
            action.execution_completed_at = None
            action.execution_failed_at = None
            action.current_state = ActionState.APPROVED
            action.updated_at = _now()

            # Manually build the transition record (bypasses can_transition check)
            record = ActionTransitionRecord(
                transition_id=str(uuid.uuid4()),
                action_id=action.action_id,
                case_id=action.case_id,
                ticket_id=action.ticket_id,
                client=action.client,
                from_state=old_state,
                to_state=ActionState.APPROVED,
                actor=actor,
                reason=reason,
                detail={
                    "recovery_type": "dead_letter_retry",
                    "operator": actor,
                },
            )
            self._repo.record_transition(record)

            # Persist with optimistic lock on DEAD_LETTER
            saved = self._repo.update_action(action, expected_state=old_state)
            if not saved:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=old_state.value,
                    error_code=LOCK_CONTENTION,
                    error_message=(
                        "Optimistic lock failure — action state changed concurrently. "
                        "Retry the operation."
                    ),
                )

            self._emit_audit(
                action,
                AuditEventType.ACTION_RECOVERED_FROM_DEAD_LETTER,
                actor=actor,
                metadata={"reason": reason, "reset_attempt": True},
            )
            LOGGER.info(
                "recovery: dead_letter→approved action_id=%s actor=%s",
                action_id, actor,
            )
            return RecoveryResult(
                success=True,
                action_id=action_id,
                old_state=old_state.value,
                new_state=ActionState.APPROVED.value,
            )

        except Exception as exc:
            LOGGER.exception(
                "recovery_service.retry_dead_letter_action: unexpected error "
                "action_id=%s error=%s", action_id, exc,
            )
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INTERNAL_ERROR,
                error_message="Unexpected internal error during dead-letter recovery",
            )

    # ── D3: FAILED → APPROVED recovery (only when retryable) ──────────────────

    def retry_failed_action(
        self,
        action_id: str,
        actor: str,
        reason: str = "operator_retry",
    ) -> RecoveryResult:
        """
        Retry a FAILED action if it has remaining retry budget.

        Uses the normal state machine — FAILED → APPROVED is a legal transition.
        Validates can_retry before proceeding; returns RETRY_EXHAUSTED if budget is gone.
        """
        try:
            action = self._fetch(action_id)
            if action is None:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    error_code=NOT_FOUND,
                    error_message=f"Action {action_id} not found",
                )

            if action.current_state != ActionState.FAILED:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=INVALID_STATE,
                    error_message=(
                        f"retry_failed requires FAILED state, "
                        f"got {action.current_state.value}"
                    ),
                )

            if not action.can_retry:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=RETRY_EXHAUSTED,
                    error_message=(
                        f"Action has exhausted retry budget "
                        f"({action.execution_attempt}/{action.max_attempts}). "
                        "Use retry_dead_letter_action to override."
                    ),
                )

            old_state = action.current_state

            sm = ActionStateMachine(on_transition=lambda a, r: self._repo.record_transition(r))
            sm.transition(
                action, ActionState.APPROVED,
                reason=reason,
                actor=actor,
                detail={
                    "recovery_type": "failed_retry",
                    "execution_attempt": action.execution_attempt,
                    "max_attempts": action.max_attempts,
                },
            )

            saved = self._repo.update_action(action, expected_state=old_state)
            if not saved:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=old_state.value,
                    error_code=LOCK_CONTENTION,
                    error_message="Optimistic lock failure — retry the operation.",
                )

            LOGGER.info(
                "recovery: failed→approved action_id=%s actor=%s attempt=%d/%d",
                action_id, actor, action.execution_attempt, action.max_attempts,
            )
            return RecoveryResult(
                success=True,
                action_id=action_id,
                old_state=old_state.value,
                new_state=ActionState.APPROVED.value,
            )

        except Exception as exc:
            LOGGER.exception(
                "recovery_service.retry_failed_action: unexpected error "
                "action_id=%s error=%s", action_id, exc,
            )
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INTERNAL_ERROR,
                error_message="Unexpected internal error during failed-action retry",
            )

    # ── D4: PROPOSED/AWAITING_APPROVAL/APPROVED → CANCELLED ───────────────────

    def cancel_action(
        self,
        action_id: str,
        actor: str,
        reason: str = "operator_cancelled",
    ) -> RecoveryResult:
        """
        Cancel an action that has not yet begun execution.

        Valid from: PROPOSED, AWAITING_APPROVAL, APPROVED.
        Not valid from: EXECUTING or any terminal state.
        """
        _CANCELLABLE = frozenset({
            ActionState.PROPOSED,
            ActionState.AWAITING_APPROVAL,
            ActionState.APPROVED,
        })

        try:
            action = self._fetch(action_id)
            if action is None:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    error_code=NOT_FOUND,
                    error_message=f"Action {action_id} not found",
                )

            if action.current_state not in _CANCELLABLE:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=INVALID_STATE,
                    error_message=(
                        f"cancel requires PROPOSED, AWAITING_APPROVAL, or APPROVED state, "
                        f"got {action.current_state.value}"
                    ),
                )

            old_state = action.current_state

            sm = ActionStateMachine(on_transition=lambda a, r: self._repo.record_transition(r))
            sm.transition(
                action, ActionState.CANCELLED,
                reason=reason,
                actor=actor,
                detail={"recovery_type": "operator_cancel", "operator": actor},
            )
            action.cancelled_at = _now()

            saved = self._repo.update_action(action, expected_state=old_state)
            if not saved:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=old_state.value,
                    error_code=LOCK_CONTENTION,
                    error_message="Optimistic lock failure — retry the operation.",
                )

            self._emit_audit(
                action,
                AuditEventType.ACTION_CANCELLED,
                actor=actor,
                metadata={"reason": reason, "cancelled_from": old_state.value},
            )
            LOGGER.info(
                "recovery: cancelled action_id=%s from=%s actor=%s",
                action_id, old_state.value, actor,
            )
            return RecoveryResult(
                success=True,
                action_id=action_id,
                old_state=old_state.value,
                new_state=ActionState.CANCELLED.value,
            )

        except ActionTransitionError as exc:
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INVALID_STATE,
                error_message=str(exc),
            )
        except Exception as exc:
            LOGGER.exception(
                "recovery_service.cancel_action: unexpected error "
                "action_id=%s error=%s", action_id, exc,
            )
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INTERNAL_ERROR,
                error_message="Unexpected internal error during cancellation",
            )

    # ── D5: PROPOSED/AWAITING_APPROVAL/APPROVED → EXPIRED (manual) ────────────

    def expire_action_manually(
        self,
        action_id: str,
        actor: str,
        reason: str = "operator_expired",
    ) -> RecoveryResult:
        """
        Manually expire an action before its SLA deadline.

        Valid from: AWAITING_APPROVAL, APPROVED.
        Semantically distinct from the SLA watchdog's automatic expiry —
        the actor field preserves the operator identity for audit purposes.

        Note: PROPOSED is not a valid source state because PROPOSED actions
        transition synchronously to AWAITING_APPROVAL or APPROVED before any
        operator can intervene.
        """
        _EXPIRABLE = frozenset({
            ActionState.AWAITING_APPROVAL,
            ActionState.APPROVED,
        })

        try:
            action = self._fetch(action_id)
            if action is None:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    error_code=NOT_FOUND,
                    error_message=f"Action {action_id} not found",
                )

            if action.current_state not in _EXPIRABLE:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=INVALID_STATE,
                    error_message=(
                        f"expire requires PROPOSED, AWAITING_APPROVAL, or APPROVED state, "
                        f"got {action.current_state.value}"
                    ),
                )

            old_state = action.current_state

            sm = ActionStateMachine(on_transition=lambda a, r: self._repo.record_transition(r))
            sm.transition(
                action, ActionState.EXPIRED,
                reason=reason,
                actor=actor,
                detail={"recovery_type": "operator_expire", "operator": actor},
            )

            saved = self._repo.update_action(action, expected_state=old_state)
            if not saved:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=old_state.value,
                    error_code=LOCK_CONTENTION,
                    error_message="Optimistic lock failure — retry the operation.",
                )

            self._emit_audit(
                action,
                AuditEventType.ACTION_MANUALLY_EXPIRED,
                actor=actor,
                metadata={"reason": reason, "expired_from": old_state.value},
            )
            LOGGER.info(
                "recovery: expired action_id=%s from=%s actor=%s",
                action_id, old_state.value, actor,
            )
            return RecoveryResult(
                success=True,
                action_id=action_id,
                old_state=old_state.value,
                new_state=ActionState.EXPIRED.value,
            )

        except ActionTransitionError as exc:
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INVALID_STATE,
                error_message=str(exc),
            )
        except Exception as exc:
            LOGGER.exception(
                "recovery_service.expire_action_manually: unexpected error "
                "action_id=%s error=%s", action_id, exc,
            )
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INTERNAL_ERROR,
                error_message="Unexpected internal error during manual expiry",
            )

    # ── D6: EXECUTED → ROLLING_BACK (force rollback) ──────────────────────────

    def force_rollback_action(
        self,
        action_id: str,
        actor: str,
        reason: str = "operator_rollback",
    ) -> RecoveryResult:
        """
        Trigger a rollback for an EXECUTED REVERSIBLE action.

        Delegates to ActionGateway.propose_rollback() which:
          - Validates EXECUTED + REVERSIBLE + rollback_action_type + not already rolled back
          - Creates the compensating action (SAFE, auto-approved)
          - Transitions the original to ROLLING_BACK

        Returns the RecoveryResult with new_state=ROLLING_BACK on success.
        """
        try:
            action = self._fetch(action_id)
            if action is None:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    error_code=NOT_FOUND,
                    error_message=f"Action {action_id} not found",
                )

            if action.current_state != ActionState.EXECUTED:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=INVALID_STATE,
                    error_message=(
                        f"force_rollback requires EXECUTED state, "
                        f"got {action.current_state.value}"
                    ),
                )

            if action.risk_level != ActionRiskLevel.REVERSIBLE:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=NOT_REVERSIBLE,
                    error_message=(
                        f"Only REVERSIBLE actions can be rolled back. "
                        f"risk_level={action.risk_level.value}"
                    ),
                )

            if not action.rollback_action_type:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=NO_ROLLBACK_SPEC,
                    error_message="Action has no rollback_action_type — cannot propose rollback",
                )

            if action.is_rolled_back:
                return RecoveryResult(
                    success=False,
                    action_id=action_id,
                    old_state=action.current_state.value,
                    error_code=ALREADY_ROLLED_BACK,
                    error_message=(
                        f"Action is already rolled back "
                        f"(rollback_action_id={action.rollback_action_id})"
                    ),
                )

            old_state = action.current_state

            # Construct a minimal Case for gateway.propose_rollback()
            from case_engine.models import Case  # noqa: PLC0415
            case = Case(
                case_id=action.case_id,
                ticket_id=action.ticket_id,
                client=action.client,
            )

            # Delegate to gateway — handles compensation creation + ROLLING_BACK transition
            compensation = self._gateway.propose_rollback(action, case)

            self._emit_audit(
                action,
                AuditEventType.ACTION_ROLLBACK_TRIGGERED,
                actor=actor,
                metadata={
                    "reason": reason,
                    "compensation_action_id": compensation.action_id,
                },
            )
            LOGGER.info(
                "recovery: rollback triggered action_id=%s compensation=%s actor=%s",
                action_id, compensation.action_id, actor,
            )
            return RecoveryResult(
                success=True,
                action_id=action_id,
                old_state=old_state.value,
                new_state=ActionState.ROLLING_BACK.value,
            )

        except Exception as exc:
            LOGGER.exception(
                "recovery_service.force_rollback_action: unexpected error "
                "action_id=%s error=%s", action_id, exc,
            )
            return RecoveryResult(
                success=False,
                action_id=action_id,
                error_code=INTERNAL_ERROR,
                error_message="Unexpected internal error during rollback",
            )
