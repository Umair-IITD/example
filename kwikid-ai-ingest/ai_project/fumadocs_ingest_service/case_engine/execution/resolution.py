"""
case_engine/execution/resolution.py

Sprint 2.23: Resolution Engine -- RESOLUTION node.

flow_diagram.mermaid:
  DECISION3 -->|Yes| RESOLUTION
  RESOLUTION --> NOTEGEN

Blueprint Section 25 (Ticket Closure conditions):
  Resolution completed + Customer informed + Escalations completed
  + Verification successful. Only then: Case Closed.

Design:
- Deterministic mapping: VerificationResult + RecoveryResult --> ResolutionStatus.
- No LLM. No async.
- Fail-closed: missing/ambiguous inputs --> UNRESOLVED or ESCALATED.

Status mapping:
  VERIFIED_SUCCESS + no recovery         --> RESOLVED
  VERIFIED_SUCCESS + recovery applied    --> PARTIALLY_RESOLVED
  ROLLBACK completed                     --> PARTIALLY_RESOLVED
  RETRY scheduled                        --> UNRESOLVED (pending)
  ESCALATE or DEAD_LETTER strategy       --> ESCALATED
  VERIFIED_FAILURE with no recovery      --> UNRESOLVED
  UNCERTAIN                              --> UNRESOLVED (fail-closed)
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.execution.models import (
    RecoveryResult,
    RecoveryStrategy,
    ResolutionResult,
    ResolutionStatus,
    VerificationResult,
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ResolutionEngine:
    """
    RESOLUTION node: determines final case disposition.

    resolve() maps VerificationResult (and optional RecoveryResult) to a
    ResolutionResult. Never raises.
    """

    def resolve(
        self,
        verification_result: VerificationResult,
        recovery_result: RecoveryResult | None = None,
        action_type: str | None = None,
    ) -> ResolutionResult:
        """
        Determine resolution status from verification and (optionally) recovery.

        Never raises. Returns ResolutionResult.
        """
        try:
            return self._resolve(verification_result, recovery_result, action_type)
        except Exception as exc:
            LOGGER.exception(
                "resolution_engine.resolve error action_type=%s error=%s",
                action_type or getattr(verification_result, "action_type", "unknown"), exc,
            )
            return ResolutionResult(
                resolution_id=_new_id(),
                action_type=action_type or getattr(verification_result, "action_type", "unknown"),
                status=ResolutionStatus.UNRESOLVED,
                resolved=False,
                resolution_note=f"resolution_internal_error: {type(exc).__name__}",
                evidence_keys=(),
                resolved_at=_now_iso(),
            )

    def _resolve(
        self,
        verification_result: VerificationResult,
        recovery_result: RecoveryResult | None,
        action_type: str | None,
    ) -> ResolutionResult:
        at = action_type or verification_result.action_type
        rid = _new_id()
        evidence = list(verification_result.evidence.keys()) if verification_result.evidence else []

        # Success path: verified success with no recovery needed
        if verification_result.is_success() and recovery_result is None:
            return ResolutionResult(
                resolution_id=rid,
                action_type=at,
                status=ResolutionStatus.RESOLVED,
                resolved=True,
                resolution_note=f"Action '{at}' verified successful. No recovery required.",
                evidence_keys=tuple(evidence),
                resolved_at=_now_iso(),
            )

        # Success path: verified success after recovery (e.g., retry succeeded)
        if verification_result.is_success() and recovery_result is not None:
            return ResolutionResult(
                resolution_id=rid,
                action_type=at,
                status=ResolutionStatus.PARTIALLY_RESOLVED,
                resolved=False,
                resolution_note=(
                    f"Action '{at}' verified successful after "
                    f"{recovery_result.strategy_applied.value} recovery."
                ),
                evidence_keys=tuple(evidence),
                resolved_at=_now_iso(),
            )

        # Recovery: ROLLBACK --> PARTIALLY_RESOLVED
        if recovery_result is not None and recovery_result.strategy_applied == RecoveryStrategy.ROLLBACK:
            return ResolutionResult(
                resolution_id=rid,
                action_type=at,
                status=ResolutionStatus.PARTIALLY_RESOLVED,
                resolved=False,
                resolution_note=f"Action '{at}' rolled back. Original action did not complete.",
                evidence_keys=tuple(evidence),
                resolved_at=_now_iso(),
            )

        # Recovery: RETRY scheduled --> UNRESOLVED (pending)
        if recovery_result is not None and recovery_result.strategy_applied == RecoveryStrategy.RETRY:
            return ResolutionResult(
                resolution_id=rid,
                action_type=at,
                status=ResolutionStatus.UNRESOLVED,
                resolved=False,
                resolution_note=f"Action '{at}' scheduled for retry. Resolution pending.",
                evidence_keys=tuple(evidence),
                resolved_at=_now_iso(),
            )

        # Recovery: ESCALATE or DEAD_LETTER --> ESCALATED
        if recovery_result is not None and recovery_result.strategy_applied in (
            RecoveryStrategy.ESCALATE, RecoveryStrategy.DEAD_LETTER
        ):
            return ResolutionResult(
                resolution_id=rid,
                action_type=at,
                status=ResolutionStatus.ESCALATED,
                resolved=False,
                resolution_note=(
                    f"Action '{at}' escalated via "
                    f"{recovery_result.strategy_applied.value}. "
                    f"{recovery_result.notes}"
                ),
                evidence_keys=tuple(evidence),
                resolved_at=_now_iso(),
            )

        # Failure with no recovery provided --> UNRESOLVED
        return ResolutionResult(
            resolution_id=rid,
            action_type=at,
            status=ResolutionStatus.UNRESOLVED,
            resolved=False,
            resolution_note=(
                f"Action '{at}' failed verification "
                f"(status={verification_result.status.value}). No recovery applied."
            ),
            evidence_keys=tuple(evidence),
            resolved_at=_now_iso(),
        )
