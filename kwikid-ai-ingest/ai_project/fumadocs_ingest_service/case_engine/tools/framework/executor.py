"""
case_engine/tools/framework/executor.py

Sprint 2.45: ProductionToolExecutor — the single execution layer for all investigation tools.

No caller may invoke tools directly. All tool execution goes through here.

Responsibilities:
  1. Validate ToolExecutionRequest
  2. Resolve tool (by tool_name or evidence_kind capability map)
  3. Execute with retry (per ToolRetryPolicy) and timing
  4. Collect per-execution ToolExecutionMetrics
  5. Build ToolExecutionAudit record (PII-redacted inputs)
  6. Update registry health on each execution
  7. Update framework-level ToolFrameworkMetrics
  8. Return ToolExecutionResult — NEVER raises

Dependency direction:
  executor.py → framework/models.py
  executor.py → framework/registry.py (ProductionToolRegistry)
  executor.py → framework/metrics.py (ToolFrameworkMetrics)
  executor.py → case_engine/tools/tool_executor.py (BaseTool) via registry
  executor.py → stdlib only for logic
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from case_engine.tools.framework.metrics import ToolFrameworkMetrics
from case_engine.tools.framework.models import (
    ToolContext,
    ToolExecutionAudit,
    ToolExecutionMetrics,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionTrace,
    ToolRetryPolicy,
    ToolTimeout,
    _new_id,
    _now_iso,
)
from case_engine.tools.framework.registry import ProductionToolRegistry

LOGGER = logging.getLogger(__name__)

# Sensible production defaults
_DEFAULT_TIMEOUT = ToolTimeout(total_seconds=30.0, per_attempt_seconds=10.0)
_DEFAULT_RETRY   = ToolRetryPolicy(max_attempts=3, backoff_seconds=0.5)


class ProductionToolExecutor:
    """
    Production-grade, single execution layer for all investigation tools.

    All evidence collection MUST pass through this executor.
    The executor owns: validation, capability resolution, retry,
    timeout tracking, metrics, audit, health reporting.
    """

    def __init__(
        self,
        registry:        ProductionToolRegistry,
        default_timeout: ToolTimeout    = _DEFAULT_TIMEOUT,
        default_retry:   ToolRetryPolicy = _DEFAULT_RETRY,
        metrics:         ToolFrameworkMetrics | None = None,
    ) -> None:
        self._registry        = registry
        self._default_timeout = default_timeout
        self._default_retry   = default_retry
        self._metrics         = metrics or ToolFrameworkMetrics()

    # ── Public API ─────────────────────────────────────────────────────────────

    def execute_request(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """
        Execute a ToolExecutionRequest. Never raises — returns a failure result on error.

        Resolution order:
          1. If request.tool_name is set → use directly.
          2. Else if request.evidence_kind → resolve via registry capability map.
          3. If neither → INVALID_REQUEST failure.
        """
        started_at   = _now_iso()
        start_mono   = time.monotonic()
        invocation_id = request.context.invocation_id
        request_id   = request.request_id

        # ── 1. Resolve tool name ───────────────────────────────────────────────
        tool_name = request.tool_name
        if not tool_name:
            if request.evidence_kind:
                tool_name = self._registry.resolve_capability(request.evidence_kind)
                if not tool_name:
                    return self._fail_result(
                        tool_name="<unresolved>",
                        invocation_id=invocation_id,
                        request_id=request_id,
                        error_code="CAPABILITY_NOT_FOUND",
                        error_message=(
                            f"No tool registered for evidence_kind="
                            f"{request.evidence_kind!r}"
                        ),
                        started_at=started_at,
                        start_mono=start_mono,
                        inputs=request.context.slots,
                    )
            else:
                return self._fail_result(
                    tool_name="<none>",
                    invocation_id=invocation_id,
                    request_id=request_id,
                    error_code="INVALID_REQUEST",
                    error_message="ToolExecutionRequest must specify tool_name or evidence_kind",
                    started_at=started_at,
                    start_mono=start_mono,
                    inputs={},
                )

        # ── 2. Look up tool ────────────────────────────────────────────────────
        tool = self._registry.get(tool_name)
        if tool is None:
            return self._fail_result(
                tool_name=tool_name,
                invocation_id=invocation_id,
                request_id=request_id,
                error_code="TOOL_NOT_FOUND",
                error_message=f"No tool registered with name {tool_name!r}",
                started_at=started_at,
                start_mono=start_mono,
                inputs=request.context.slots,
            )

        # ── 3. Check lifecycle ─────────────────────────────────────────────────
        if not self._registry.get_lifecycle_status(tool_name).value in (
            "READY", "DEGRADED", "REGISTERED"
        ):
            lc_status = self._registry.get_lifecycle_status(tool_name)
            LOGGER.warning(
                "tool_executor.lifecycle_blocked tool=%s status=%s",
                tool_name, lc_status.value,
            )
            return self._fail_result(
                tool_name=tool_name,
                invocation_id=invocation_id,
                request_id=request_id,
                error_code="TOOL_LIFECYCLE_BLOCKED",
                error_message=(
                    f"Tool {tool_name!r} is in lifecycle state "
                    f"{lc_status.value!r} and cannot be executed"
                ),
                started_at=started_at,
                start_mono=start_mono,
                inputs=request.context.slots,
            )

        # ── 4. Validate required inputs ────────────────────────────────────────
        inputs  = dict(request.context.slots)
        missing = [
            req for req in tool.definition.required_inputs
            if req not in inputs
        ]
        if missing:
            return self._fail_result(
                tool_name=tool_name,
                invocation_id=invocation_id,
                request_id=request_id,
                error_code="MISSING_REQUIRED_INPUTS",
                error_message=f"Missing required inputs: {missing}",
                started_at=started_at,
                start_mono=start_mono,
                inputs=inputs,
            )

        # ── 5. Execute with retry ──────────────────────────────────────────────
        retry   = request.retry_policy or self._default_retry
        timeout = request.timeout      or self._default_timeout

        per_exec_metrics = ToolExecutionMetrics()
        attempt_records: list[dict[str, Any]] = []
        last_error_code: str | None = None
        last_error_msg:  str | None = None
        success = False
        payload: dict[str, Any] = {}

        for attempt in range(1, retry.max_attempts + 1):
            attempt_start = time.monotonic()
            timed_out_flag = False
            attempt_error_code: str | None = None
            attempt_error_msg: str | None  = None

            try:
                payload = tool.run(inputs)
                attempt_duration = int((time.monotonic() - attempt_start) * 1000)
                success = True
                per_exec_metrics.record_attempt(attempt_duration, success=True)
                attempt_records.append({
                    "attempt":     attempt,
                    "success":     True,
                    "duration_ms": attempt_duration,
                })
                LOGGER.debug(
                    "tool_executor.attempt_success tool=%s attempt=%d duration_ms=%d",
                    tool_name, attempt, attempt_duration,
                )
                break

            except Exception as exc:  # noqa: BLE001
                attempt_duration  = int((time.monotonic() - attempt_start) * 1000)
                attempt_error_code = "TOOL_EXECUTION_ERROR"
                attempt_error_msg  = str(exc)[:500]
                last_error_code    = attempt_error_code
                last_error_msg     = attempt_error_msg

                per_exec_metrics.record_attempt(
                    attempt_duration, success=False, timed_out=timed_out_flag
                )
                attempt_records.append({
                    "attempt":     attempt,
                    "success":     False,
                    "error_code":  attempt_error_code,
                    "error":       attempt_error_msg,
                    "duration_ms": attempt_duration,
                })
                LOGGER.warning(
                    "tool_executor.attempt_failed tool=%s attempt=%d/%d error=%s",
                    tool_name, attempt, retry.max_attempts, exc,
                )

            # Retry decision
            if attempt < retry.max_attempts and retry.should_retry(
                attempt, attempt_error_code, timed_out_flag
            ):
                if retry.backoff_seconds > 0:
                    time.sleep(retry.backoff_seconds)
            else:
                break

        # ── 6. Build result ────────────────────────────────────────────────────
        total_duration_ms = int((time.monotonic() - start_mono) * 1000)
        completed_at      = _now_iso()

        # Update registry health and stats
        self._registry.update_health(tool_name, success, total_duration_ms)
        self._registry.record_execution_stats(
            tool_name, total_duration_ms,
            success=success,
            retries=per_exec_metrics.total_attempts - 1,
        )

        # Update framework metrics
        self._metrics.record_execution(
            tool_name, total_duration_ms,
            success=success,
            retries=per_exec_metrics.total_attempts - 1,
            evidence_kind=request.evidence_kind,
        )

        audit = ToolExecutionAudit(
            invocation_id=invocation_id,
            tool_name=tool_name,
            request_id=request_id,
            tenant_id=request.context.tenant_id,
            case_id=request.context.case_id,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=total_duration_ms,
            attempts=per_exec_metrics.total_attempts,
            outcome="SUCCESS" if success else "FAILURE",
            error_code=last_error_code if not success else None,
            inputs_redacted=ToolExecutionAudit._redact_inputs(inputs),
            output_summary=(
                ToolExecutionAudit._summarise_output(payload)
                if success else {"key_count": 0, "keys": []}
            ),
        )
        trace = ToolExecutionTrace(
            request_id=request_id,
            tool_name=tool_name,
            attempts=attempt_records,
            final_outcome="SUCCESS" if success else "FAILURE",
            total_duration_ms=total_duration_ms,
            metrics=per_exec_metrics,
        )

        if success:
            return ToolExecutionResult.ok(
                tool_name=tool_name,
                invocation_id=invocation_id,
                request_id=request_id,
                payload=payload,
                metrics=per_exec_metrics,
                audit=audit,
                trace=trace,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=total_duration_ms,
            )
        return ToolExecutionResult.fail(
            tool_name=tool_name,
            invocation_id=invocation_id,
            request_id=request_id,
            error_code=last_error_code or "TOOL_EXECUTION_ERROR",
            error_message=last_error_msg or "Unknown error",
            metrics=per_exec_metrics,
            audit=audit,
            trace=trace,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=total_duration_ms,
        )

    def execute(
        self,
        tool_name: str,
        inputs:    dict[str, Any],
        *,
        tenant_id: str = "",
        case_id:   str = "",
        topic:     str = "",
    ) -> ToolExecutionResult:
        """
        Convenience API matching (roughly) the Sprint 2.17 ToolExecutor.execute() signature.
        Wraps into a ToolExecutionRequest and delegates to execute_request().
        """
        context = ToolContext(
            tool_name=tool_name,
            tenant_id=tenant_id,
            case_id=case_id,
            topic=topic,
            slots=inputs,
        )
        request = ToolExecutionRequest(context=context, tool_name=tool_name)
        return self.execute_request(request)

    def execute_for_capability(
        self,
        evidence_kind: str,
        inputs:        dict[str, Any],
        *,
        tenant_id: str = "",
        case_id:   str = "",
        topic:     str = "",
    ) -> ToolExecutionResult:
        """
        Convenience API: resolve tool by capability (evidence_kind) and execute.
        """
        context = ToolContext(
            tool_name="<capability>",
            tenant_id=tenant_id,
            case_id=case_id,
            topic=topic,
            slots=inputs,
        )
        request = ToolExecutionRequest(
            context=context,
            evidence_kind=evidence_kind,
        )
        return self.execute_request(request)

    @property
    def metrics(self) -> ToolFrameworkMetrics:
        """Return the framework-level metrics accumulator."""
        return self._metrics

    # ── Private ────────────────────────────────────────────────────────────────

    def _fail_result(
        self,
        tool_name:    str,
        invocation_id: str,
        request_id:   str,
        error_code:   str,
        error_message: str,
        started_at:   str,
        start_mono:   float,
        inputs:       dict[str, Any],
    ) -> ToolExecutionResult:
        """Build a failure ToolExecutionResult with minimal metrics/audit."""
        duration_ms  = int((time.monotonic() - start_mono) * 1000)
        completed_at = _now_iso()
        metrics = ToolExecutionMetrics()
        metrics.record_attempt(duration_ms, success=False)
        audit = ToolExecutionAudit(
            invocation_id=invocation_id,
            tool_name=tool_name,
            request_id=request_id,
            tenant_id="",
            case_id="",
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
            attempts=0,
            outcome="FAILURE",
            error_code=error_code,
            inputs_redacted=ToolExecutionAudit._redact_inputs(inputs),
            output_summary={"key_count": 0, "keys": []},
        )
        trace = ToolExecutionTrace(
            request_id=request_id,
            tool_name=tool_name,
            attempts=[],
            final_outcome="FAILURE",
            total_duration_ms=duration_ms,
            metrics=metrics,
        )
        LOGGER.warning(
            "tool_executor.fail tool=%s error_code=%s message=%s",
            tool_name, error_code, error_message[:200],
        )
        return ToolExecutionResult.fail(
            tool_name=tool_name,
            invocation_id=invocation_id,
            request_id=request_id,
            error_code=error_code,
            error_message=error_message,
            metrics=metrics,
            audit=audit,
            trace=trace,
            started_at=started_at,
            completed_at=completed_at,
            duration_ms=duration_ms,
        )
