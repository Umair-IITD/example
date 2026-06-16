"""
case_engine/execution/verification.py

Sprint 2.23: Verification Engine -- VERIFY node.

Blueprint Section 19:
  Confirm action succeeded.
  Examples: OTP delivered, Session reset successful, Callback acknowledged.
  Output: Success / Failure

flow_diagram.mermaid:
  EXECUTE --> VERIFY
  VERIFY --> DECISION3{Outcome Successful?}

Design:
- VerificationEngine.verify() always returns a VerificationResult.
- Never assumes success -- if evidence is absent or ambiguous: UNCERTAIN.
- UNCERTAIN is treated identically to VERIFIED_FAILURE in the recovery path.
- Deterministic: no LLM, no async, no external API calls.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from case_engine.execution.models import (
    ExecutionResult,
    ExecutionStatus,
    VerificationResult,
    VerificationStatus,
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# Actions verified by execution success alone (no external evidence needed)
_SUCCESS_BY_EXECUTION: frozenset[str] = frozenset({
    "resend_otp",
    "retry_ocr",
    "refresh_session",
    "acknowledge_session",
    "ping_callback",
    "reset_session",
    "resend_callback",
    "unlock_account",
    "force_logout",
    "clear_cache",
    "resend_callback",
    "refresh_token",
    "reinitialize_session",
    "retry_callback",
})


class VerificationEngine:
    """
    VERIFY node: confirms whether an action succeeded.

    Fail-closed: returns UNCERTAIN when evidence is absent or ambiguous.
    Never raises.
    """

    def verify(
        self,
        execution_result: ExecutionResult,
        additional_evidence: dict[str, Any] | None = None,
    ) -> VerificationResult:
        """
        Verify the outcome of an execution.

        Returns VerificationResult. Never raises.
        """
        try:
            return self._verify(execution_result, additional_evidence or {})
        except Exception as exc:
            LOGGER.exception(
                "verification_engine.verify error action_type=%s error=%s",
                getattr(execution_result, "action_type", "unknown"), exc,
            )
            return VerificationResult(
                verification_id=_new_id(),
                action_type=getattr(execution_result, "action_type", "unknown"),
                status=VerificationStatus.UNCERTAIN,
                confirmed=False,
                evidence={"verification_error": str(exc)},
                failure_reason=f"verification_internal_error: {type(exc).__name__}",
                verified_at=_now_iso(),
            )

    def _verify(
        self,
        execution_result: ExecutionResult,
        additional_evidence: dict[str, Any],
    ) -> VerificationResult:
        action_type = execution_result.action_type
        vid = _new_id()

        # Execution itself failed: confirm VERIFIED_FAILURE immediately
        if execution_result.status == ExecutionStatus.FAILED:
            return VerificationResult(
                verification_id=vid,
                action_type=action_type,
                status=VerificationStatus.VERIFIED_FAILURE,
                confirmed=True,
                evidence={
                    "execution_status": execution_result.status.value,
                    "error_code": execution_result.error_code,
                },
                failure_reason=execution_result.error_message or "execution_failed",
                verified_at=_now_iso(),
            )

        # Execution succeeded and action_type is in the verifiable set
        if execution_result.success and action_type in _SUCCESS_BY_EXECUTION:
            evidence: dict[str, Any] = {
                "execution_status": execution_result.status.value,
                "adapter": execution_result.adapter_name,
                "verification_method": "execution_success",
            }
            if additional_evidence:
                evidence["additional"] = additional_evidence
            return VerificationResult(
                verification_id=vid,
                action_type=action_type,
                status=VerificationStatus.VERIFIED_SUCCESS,
                confirmed=True,
                evidence=evidence,
                failure_reason=None,
                verified_at=_now_iso(),
            )

        # Execution succeeded but action_type is unknown -- UNCERTAIN (fail-closed)
        if execution_result.success:
            return VerificationResult(
                verification_id=vid,
                action_type=action_type,
                status=VerificationStatus.UNCERTAIN,
                confirmed=False,
                evidence={
                    "execution_status": execution_result.status.value,
                    "note": "action_type not in verifiable set; cannot confirm",
                },
                failure_reason="unverifiable_action_type",
                verified_at=_now_iso(),
            )

        # Catch-all: UNCERTAIN
        return VerificationResult(
            verification_id=vid,
            action_type=action_type,
            status=VerificationStatus.UNCERTAIN,
            confirmed=False,
            evidence={"execution_status": execution_result.status.value},
            failure_reason="uncertain_outcome",
            verified_at=_now_iso(),
        )
