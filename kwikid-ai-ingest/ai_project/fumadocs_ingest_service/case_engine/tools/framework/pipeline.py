"""
case_engine/tools/framework/pipeline.py

Sprint 2.45: ExecutionPipeline — orchestrates the full tool execution flow.

Pipeline stages:
  1. Validation     (ToolFrameworkValidators.validate_request)
  2. Capability resolution  (evidence_kind → tool_name via registry)
  3. Registry resolution    (tool_name → BaseTool)
  4. Authentication validation  (placeholder — checked via ToolMetadata)
  5. Execution      (ProductionToolExecutor.execute_request)
  6. Metrics        (update ToolFrameworkMetrics)
  7. Audit          (ToolExecutionAudit built by executor)
  8. Evidence mapping  (caller converts ToolExecutionResult → Evidence)
  9. Return result

The ExecutionPipeline is a thin orchestrator. It delegates to:
  - ToolFrameworkValidators for pre-execution checks
  - ProductionToolExecutor for actual execution
  - ToolFrameworkMetrics for post-execution tracking

Never raises — all errors produce ToolExecutionResult.fail().

Dependency direction:
  pipeline.py → framework/executor.py
  pipeline.py → framework/validators.py
  pipeline.py → framework/metrics.py
  pipeline.py → framework/models.py
"""
from __future__ import annotations

import logging
from typing import Any

from case_engine.tools.framework.executor import ProductionToolExecutor
from case_engine.tools.framework.metrics import ToolFrameworkMetrics
from case_engine.tools.framework.models import (
    ToolContext,
    ToolExecutionAudit,
    ToolExecutionMetrics,
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolExecutionTrace,
    _now_iso,
)
from case_engine.tools.framework.validators import ToolFrameworkValidators

LOGGER = logging.getLogger(__name__)


class ExecutionPipeline:
    """
    Orchestrates the complete tool execution flow from request to result.

    Architecture:
      ToolExecutionRequest
        → Validation
        → Capability Resolution
        → Registry Resolution
        → Authentication Validation
        → Execution (via ProductionToolExecutor)
        → Metrics
        → Audit
        → Evidence Mapping (caller's responsibility)
        → ToolExecutionResult

    The pipeline is the outermost entry point for all tool execution.
    Callers that use execute_through_pipeline() are guaranteed:
      - Pre-execution validation
      - Structured failure on validation error (not exception)
      - All metrics and audit records are captured
      - Never raises
    """

    def __init__(
        self,
        executor:   ProductionToolExecutor,
        validators: ToolFrameworkValidators | None = None,
        metrics:    ToolFrameworkMetrics | None    = None,
    ) -> None:
        self._executor   = executor
        self._validators = validators or ToolFrameworkValidators()
        self._metrics    = metrics or executor.metrics

    def run(self, request: ToolExecutionRequest) -> ToolExecutionResult:
        """
        Run a ToolExecutionRequest through the complete execution pipeline.

        Stage 1: Validate request structure.
        Stage 2: Delegate to ProductionToolExecutor (handles resolution, retry, audit).
        Stage 3: Post-execution logging.

        Never raises.
        """
        # ── Stage 1: Request validation ────────────────────────────────────────
        valid, issues = self._validators.validate_request(request)
        if not valid:
            LOGGER.warning(
                "execution_pipeline.validation_failed request_id=%s issues=%s",
                request.request_id, issues,
            )
            return self._validation_failure(request, issues)

        # ── Stage 2: Delegate to executor ──────────────────────────────────────
        result = self._executor.execute_request(request)

        # ── Stage 3: Post-execution logging ────────────────────────────────────
        if result.success:
            LOGGER.info(
                "execution_pipeline.success tool=%s request=%s duration_ms=%d",
                result.tool_name, request.request_id, result.duration_ms,
            )
        else:
            LOGGER.warning(
                "execution_pipeline.failure tool=%s request=%s error=%s",
                result.tool_name, request.request_id, result.error_code,
            )

        return result

    def run_for_capability(
        self,
        evidence_kind: str,
        inputs:        dict[str, Any],
        *,
        tenant_id:     str = "",
        case_id:       str = "",
        topic:         str = "",
    ) -> ToolExecutionResult:
        """
        Convenience wrapper: build a request from evidence_kind + inputs and run.
        """
        ctx     = ToolContext(
            tool_name=f"<{evidence_kind}>",
            tenant_id=tenant_id,
            case_id=case_id,
            topic=topic,
            slots=inputs,
        )
        request = ToolExecutionRequest(context=ctx, evidence_kind=evidence_kind)
        return self.run(request)

    # ── Private ────────────────────────────────────────────────────────────────

    def _validation_failure(
        self,
        request: ToolExecutionRequest,
        issues:  list[str],
    ) -> ToolExecutionResult:
        """Build a failure result for pre-execution validation errors."""
        now = _now_iso()
        metrics = ToolExecutionMetrics()
        metrics.record_attempt(0, success=False)
        audit = ToolExecutionAudit(
            invocation_id=request.context.invocation_id,
            tool_name=request.tool_name or request.evidence_kind or "<none>",
            request_id=request.request_id,
            tenant_id=request.context.tenant_id,
            case_id=request.context.case_id,
            started_at=now,
            completed_at=now,
            duration_ms=0,
            attempts=0,
            outcome="FAILURE",
            error_code="VALIDATION_ERROR",
            inputs_redacted=ToolExecutionAudit._redact_inputs(request.context.slots),
            output_summary={"key_count": 0, "keys": []},
        )
        trace = ToolExecutionTrace(
            request_id=request.request_id,
            tool_name=request.tool_name or "<none>",
            attempts=[],
            final_outcome="FAILURE",
            total_duration_ms=0,
            metrics=metrics,
        )
        return ToolExecutionResult.fail(
            tool_name=request.tool_name or "<none>",
            invocation_id=request.context.invocation_id,
            request_id=request.request_id,
            error_code="VALIDATION_ERROR",
            error_message="; ".join(issues),
            metrics=metrics,
            audit=audit,
            trace=trace,
            started_at=now,
            completed_at=now,
            duration_ms=0,
        )
