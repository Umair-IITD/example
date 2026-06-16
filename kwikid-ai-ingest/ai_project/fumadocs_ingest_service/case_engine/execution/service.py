"""
case_engine/execution/service.py

Sprint 2.23: ExecutionService -- full EXECUTE --> VERIFY --> RECOVERY --> RESOLUTION pipeline.

Orchestration:
  1. ActionExecutor.execute()          --> ExecutionResult
  2. VerificationEngine.verify()       --> VerificationResult
  3. If verify fails: RecoveryEngine.recover() --> RecoveryResult
  4. ResolutionEngine.resolve()        --> ResolutionResult
  5. Bundle all into ExecutionBundle
  6. Emit audit events at each stage

Design:
  - Never raises -- all exceptions return an ERROR-status ExecutionBundle.
  - Deterministic: no LLM, no async, no external API calls.
  - Audit events: EXECUTION_STARTED/COMPLETED, VERIFICATION_STARTED/COMPLETED,
    RECOVERY_STARTED/COMPLETED, RESOLUTION_STARTED/COMPLETED.
"""
from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, TYPE_CHECKING

from case_engine.execution.executor import ActionExecutor, ExecutionAdapter, build_action_executor
from case_engine.execution.models import (
    ExecutionAttempt,
    ExecutionBundle,
    ExecutionResult,
    ExecutionStatus,
    RecoveryResult,
    RecoveryStrategy,
    ResolutionResult,
    ResolutionStatus,
    VerificationResult,
    VerificationStatus,
)
from case_engine.execution.recovery import RecoveryEngine
from case_engine.execution.resolution import ResolutionEngine
from case_engine.execution.verification import VerificationEngine

if TYPE_CHECKING:
    from case_engine.audit import AuditLogger
    from case_engine.models import Case

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


class ExecutionService:
    """
    Full execution pipeline: EXECUTE --> VERIFY --> RECOVERY --> RESOLUTION.

    process() runs the complete pipeline for one action and returns an ExecutionBundle.
    Never raises.
    """

    def __init__(
        self,
        executor: ActionExecutor | None = None,
        verification_engine: VerificationEngine | None = None,
        recovery_engine: RecoveryEngine | None = None,
        resolution_engine: ResolutionEngine | None = None,
    ) -> None:
        self._executor     = executor or build_action_executor()
        self._verifier     = verification_engine or VerificationEngine()
        self._recovery     = recovery_engine or RecoveryEngine()
        self._resolution   = resolution_engine or ResolutionEngine()

    def process(
        self,
        action_type:      str,
        action_params:    dict[str, Any],
        case_id:          str = "",
        action_namespace: str = "",
        risk_level:       str = "SAFE",
        audit:            "AuditLogger | None" = None,
        case:             "Case | None" = None,
        workflow_id:      str | None = None,
        step_id:          str | None = None,
    ) -> ExecutionBundle:
        """
        Run the full EXECUTE --> VERIFY --> (optional RECOVERY) --> RESOLUTION pipeline.

        Returns ExecutionBundle. Never raises.
        """
        bundle_id = _new_id()
        try:
            return self._process(
                bundle_id=bundle_id,
                action_type=action_type,
                action_params=action_params,
                case_id=case_id,
                action_namespace=action_namespace,
                risk_level=risk_level,
                audit=audit,
                case=case,
                workflow_id=workflow_id,
                step_id=step_id,
            )
        except Exception as exc:
            LOGGER.exception(
                "execution_service.process failed action_type=%s case_id=%s error=%s",
                action_type, case_id, exc,
            )
            error_result = ExecutionResult(
                result_id=_new_id(),
                adapter_name="unknown",
                action_type=action_type,
                action_params=action_params,
                status=ExecutionStatus.FAILED,
                success=False,
                response_data={},
                error_code="SERVICE_INTERNAL_ERROR",
                error_message=f"{type(exc).__name__}: {exc}",
                executed_at=_now_iso(),
                duration_ms=0,
            )
            attempt = ExecutionAttempt(
                attempt_id=_new_id(),
                attempt_number=1,
                execution_result=error_result,
                recovery_strategy=None,
                attempted_at=_now_iso(),
            )
            return ExecutionBundle(
                bundle_id=bundle_id,
                case_id=case_id,
                action_type=action_type,
                action_namespace=action_namespace,
                attempts=(attempt,),
                final_status=ExecutionStatus.FAILED,
                total_attempts=1,
                created_at=_now_iso(),
                completed_at=_now_iso(),
            )

    def _process(
        self,
        bundle_id:        str,
        action_type:      str,
        action_params:    dict[str, Any],
        case_id:          str,
        action_namespace: str,
        risk_level:       str,
        audit:            "AuditLogger | None",
        case:             "Case | None",
        workflow_id:      str | None,
        step_id:          str | None,
    ) -> ExecutionBundle:
        created_at = _now_iso()
        attempt_number = 1

        # 1. Emit EXECUTION_STARTED
        self._emit(audit, case, "log_execution_started",
                   action_type, bundle_id, workflow_id, step_id)

        # 2. Execute
        execution_result = self._executor.execute(action_type, action_params, case_id)
        attempt = ExecutionAttempt(
            attempt_id=_new_id(),
            attempt_number=attempt_number,
            execution_result=execution_result,
            recovery_strategy=None,
            attempted_at=_now_iso(),
        )

        # 3. Emit EXECUTION_COMPLETED
        self._emit(audit, case, "log_execution_completed",
                   action_type, bundle_id, execution_result.status.value,
                   execution_result.success, workflow_id, step_id)

        # 4. Verify
        self._emit(audit, case, "log_verification_started",
                   action_type, bundle_id, workflow_id, step_id)
        verification_result = self._verifier.verify(execution_result)
        self._emit(audit, case, "log_verification_completed",
                   action_type, bundle_id, verification_result.status.value,
                   verification_result.is_success(), workflow_id, step_id)

        # 5. Recovery (if verification failed)
        recovery_result: RecoveryResult | None = None
        if verification_result.requires_recovery():
            self._emit(audit, case, "log_recovery_started",
                       action_type, bundle_id, workflow_id, step_id)
            recovery_result = self._recovery.recover(
                verification_result,
                attempt_count=attempt_number,
                risk_level=risk_level,
            )
            self._emit(audit, case, "log_recovery_completed",
                       action_type, bundle_id,
                       recovery_result.strategy_applied.value,
                       recovery_result.status.value, workflow_id, step_id)

        # 6. Resolution
        self._emit(audit, case, "log_resolution_started",
                   action_type, bundle_id, workflow_id, step_id)
        resolution_result = self._resolution.resolve(
            verification_result, recovery_result, action_type
        )
        self._emit(audit, case, "log_resolution_completed",
                   action_type, bundle_id,
                   resolution_result.status.value, resolution_result.resolved,
                   workflow_id, step_id)

        # 7. Determine final status
        final_status = self._determine_final_status(
            verification_result, recovery_result
        )

        return ExecutionBundle(
            bundle_id=bundle_id,
            case_id=case_id,
            action_type=action_type,
            action_namespace=action_namespace,
            attempts=(attempt,),
            final_status=final_status,
            total_attempts=1,
            created_at=created_at,
            completed_at=_now_iso(),
        )

    def _determine_final_status(
        self,
        verification_result: VerificationResult,
        recovery_result: RecoveryResult | None,
    ) -> ExecutionStatus:
        if verification_result.is_success():
            return ExecutionStatus.SUCCESS
        if recovery_result is None:
            return ExecutionStatus.FAILED
        if recovery_result.strategy_applied == RecoveryStrategy.ROLLBACK:
            return ExecutionStatus.ROLLBACK_COMPLETED
        if recovery_result.strategy_applied == RecoveryStrategy.RETRY and recovery_result.can_retry:
            return ExecutionStatus.RETRY_SCHEDULED
        return ExecutionStatus.FAILED

    # ── Audit emission ─────────────────────────────────────────────────────────

    def _emit(self, audit: Any, case: Any, method_name: str, *args: Any) -> None:
        if audit is None or case is None:
            return
        try:
            getattr(audit, method_name)(case, *args)
        except Exception as exc:
            LOGGER.warning("audit.%s failed: %s", method_name, exc)


def build_execution_service(
    adapter: ExecutionAdapter | None = None,
    adapter_router: Any = None,
) -> ExecutionService:
    """Factory: build an ExecutionService with optional adapter or router override.

    Priority: explicit adapter > adapter_router > MockExecutionAdapter default.
    """
    if adapter is None and adapter_router is not None:
        from case_engine.adapters.execution_adapter import AdapterBackedExecutionAdapter  # noqa: PLC0415
        adapter = AdapterBackedExecutionAdapter(adapter_router)
    executor = build_action_executor(adapter)
    return ExecutionService(
        executor=executor,
        verification_engine=VerificationEngine(),
        recovery_engine=RecoveryEngine(),
        resolution_engine=ResolutionEngine(),
    )
