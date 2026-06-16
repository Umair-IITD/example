"""
case_engine/execution/recovery.py

Sprint 2.23: Recovery Engine -- RECOVERY node.

Blueprint Section 20:
  Handle failures safely.
  Capabilities: Retry, Rollback, Dead-letter recovery, Manual recovery.
  All recovery operations audited.

flow_diagram.mermaid:
  DECISION3 -->|No| RECOVERY
  RECOVERY --> RETRY[Retry]
  RECOVERY --> ROLLBACK[Rollback]
  RECOVERY --> DEADLETTER[Dead Letter Queue]

Design:
- Deterministic routing based on action_type, attempt_count, and risk_level.
- No LLM. No async.
- Fail-closed: unknown inputs --> ESCALATE (safest option).
- MAX_RETRIES = 3 (no infinite loops).
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.execution.models import (
    RecoveryResult,
    RecoveryStatus,
    RecoveryStrategy,
    VerificationResult,
)

LOGGER = logging.getLogger(__name__)

MAX_RETRIES = 3


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# Actions that support retry (idempotent, low-risk)
_RETRYABLE_ACTIONS: frozenset[str] = frozenset({
    "resend_otp",
    "retry_ocr",
    "refresh_session",
    "ping_callback",
    "resend_callback",
    "acknowledge_session",
    "refresh_token",
    "retry_callback",
})

# Actions that support rollback (reversible, have undo operations)
_ROLLBACK_ACTIONS: frozenset[str] = frozenset({
    "reset_session",
    "force_logout",
    "clear_cache",
    "reinitialize_session",
})

# Actions that go straight to dead letter (irreversible / high-risk)
_DEAD_LETTER_ACTIONS: frozenset[str] = frozenset({
    "financial_operation",
    "account_delete",
    "irreversible_action",
    "simulate_dead_letter",
})


class RecoveryEngine:
    """
    RECOVERY node: selects and applies a recovery strategy after verification failure.

    Strategy selection (deterministic):
      1. HIGH_RISK risk_level or action in _DEAD_LETTER_ACTIONS --> DEAD_LETTER
      2. Action in _ROLLBACK_ACTIONS --> ROLLBACK
      3. Action in _RETRYABLE_ACTIONS AND attempt_count < MAX_RETRIES --> RETRY
      4. Action in _RETRYABLE_ACTIONS AND attempt_count >= MAX_RETRIES --> ESCALATE
      5. Default --> ESCALATE (fail-closed)
    """

    def recover(
        self,
        verification_result: VerificationResult,
        attempt_count: int = 0,
        risk_level: str = "SAFE",
        action_params: dict[str, Any] | None = None,
    ) -> RecoveryResult:
        """
        Select and apply a recovery strategy.

        Never raises. Returns RecoveryResult.
        """
        try:
            return self._recover(
                verification_result, attempt_count, risk_level, action_params or {}
            )
        except Exception as exc:
            LOGGER.exception(
                "recovery_engine.recover error action_type=%s error=%s",
                getattr(verification_result, "action_type", "unknown"), exc,
            )
            return RecoveryResult(
                recovery_id=_new_id(),
                strategy_applied=RecoveryStrategy.ESCALATE,
                status=RecoveryStatus.ESCALATED,
                max_retries=MAX_RETRIES,
                attempts_used=attempt_count,
                can_retry=False,
                notes=f"recovery_internal_error: {type(exc).__name__}",
                recovered_at=_now_iso(),
            )

    def _recover(
        self,
        verification_result: VerificationResult,
        attempt_count: int,
        risk_level: str,
        action_params: dict[str, Any],
    ) -> RecoveryResult:
        action_type = verification_result.action_type
        rid = _new_id()

        # 1. Dead-letter for irreversible/high-risk actions
        if action_type in _DEAD_LETTER_ACTIONS or risk_level.upper() == "HIGH_RISK":
            return RecoveryResult(
                recovery_id=rid,
                strategy_applied=RecoveryStrategy.DEAD_LETTER,
                status=RecoveryStatus.ESCALATED,
                max_retries=MAX_RETRIES,
                attempts_used=attempt_count,
                can_retry=False,
                notes=(
                    f"action_type={action_type} routed to dead letter "
                    f"(irreversible/high-risk risk_level={risk_level})"
                ),
                recovered_at=_now_iso(),
            )

        # 2. Rollback for reversible actions
        if action_type in _ROLLBACK_ACTIONS:
            return RecoveryResult(
                recovery_id=rid,
                strategy_applied=RecoveryStrategy.ROLLBACK,
                status=RecoveryStatus.RECOVERY_COMPLETED,
                max_retries=MAX_RETRIES,
                attempts_used=attempt_count,
                can_retry=False,
                notes=f"action_type={action_type} rolled back",
                recovered_at=_now_iso(),
            )

        # 3. Retry for retryable actions within limit
        if action_type in _RETRYABLE_ACTIONS and attempt_count < MAX_RETRIES:
            return RecoveryResult(
                recovery_id=rid,
                strategy_applied=RecoveryStrategy.RETRY,
                status=RecoveryStatus.RECOVERY_PENDING,
                max_retries=MAX_RETRIES,
                attempts_used=attempt_count,
                can_retry=True,
                notes=(
                    f"action_type={action_type} scheduled for retry "
                    f"(attempt {attempt_count + 1}/{MAX_RETRIES})"
                ),
                recovered_at=_now_iso(),
            )

        # 4. Retry limit exceeded
        if action_type in _RETRYABLE_ACTIONS and attempt_count >= MAX_RETRIES:
            return RecoveryResult(
                recovery_id=rid,
                strategy_applied=RecoveryStrategy.ESCALATE,
                status=RecoveryStatus.ESCALATED,
                max_retries=MAX_RETRIES,
                attempts_used=attempt_count,
                can_retry=False,
                notes=(
                    f"action_type={action_type} exceeded max retries "
                    f"({MAX_RETRIES}); escalating"
                ),
                recovered_at=_now_iso(),
            )

        # 5. Default: escalate (fail-closed)
        return RecoveryResult(
            recovery_id=rid,
            strategy_applied=RecoveryStrategy.ESCALATE,
            status=RecoveryStatus.ESCALATED,
            max_retries=MAX_RETRIES,
            attempts_used=attempt_count,
            can_retry=False,
            notes=f"action_type={action_type} has no recovery rule; escalating (default)",
            recovered_at=_now_iso(),
        )
