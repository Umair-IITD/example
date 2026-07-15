"""
case_engine/investigation/collector/dispatcher.py

Sprint 2.42: ProviderDispatcher — selects and invokes the right provider for a step.

Responsibilities:
  - Resolve provider from ProviderRegistry by EvidenceKind
  - Execute provider with retry per PlanningStep.retry_policy
  - Capture timing for each attempt
  - Return a StepExecutionRecord with all attempt results

Guarantee:
  dispatch() NEVER raises. All errors are recorded in StepExecutionRecord.

Dependency direction:
  dispatcher.py → collector/registry.py (ProviderRegistry)
  dispatcher.py → collector/contracts.py (CollectionContext, CollectionResult)
  dispatcher.py → collector/execution.py (StepExecutionRecord, StepStatus)
  dispatcher.py → collector/validators.py (CollectorValidator)
  dispatcher.py → collector/exceptions.py (ProviderNotFoundError)
  dispatcher.py → investigation/planner/models.py (PlanningStep, EvidenceKind)
  dispatcher.py → stdlib only
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from typing import Any

from case_engine.investigation.collector.contracts import CollectionContext, CollectionResult
from case_engine.investigation.collector.exceptions import ProviderNotFoundError
from case_engine.investigation.collector.execution import StepExecutionRecord, StepStatus
from case_engine.investigation.collector.registry import ProviderRegistry
from case_engine.investigation.collector.validators import CollectorValidator
from case_engine.investigation.planner.models import (
    EvidenceKind,
    PlanningStep,
)

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ProviderDispatcher:
    """
    Selects and invokes an EvidenceProvider for a PlanningStep.

    Handles:
      - Provider resolution by EvidenceKind (primary requirement drives selection)
      - Retry logic per PlanningStep.retry_policy
      - Timing capture (per-attempt and total)
      - Structured failure recording (never raises)

    One ProviderDispatcher instance is safe to share across threads if
    the underlying ProviderRegistry is thread-safe (it is).
    """

    def __init__(
        self,
        registry: ProviderRegistry,
        validator: CollectorValidator | None = None,
    ) -> None:
        self._registry  = registry
        self._validator = validator or CollectorValidator()

    def dispatch(
        self,
        step: PlanningStep,
        context: CollectionContext,
    ) -> StepExecutionRecord:
        """
        Execute a PlanningStep against the appropriate provider.

        Selects provider by the EvidenceKind of the step's primary requirement.
        Applies retry logic per step.retry_policy. Never raises.

        Returns:
            StepExecutionRecord with all attempt results and final status.
        """
        started_at = _now_iso()
        start_ts   = time.monotonic()
        results: list[CollectionResult] = []

        kind     = self._select_kind(step)
        req_meta = self._build_requirement_metadata(step)
        policy   = step.retry_policy
        max_attempts = max(1, policy.max_attempts)

        # Resolve provider once — retry with same provider
        try:
            provider = self._registry.resolve_with_fallback(kind)
        except ProviderNotFoundError:
            elapsed_ms = int((time.monotonic() - start_ts) * 1000)
            result = CollectionResult.fail(
                provider_name="<none>",
                step_id=step.step_id,
                evidence_kind=kind.value,
                error_code="PROVIDER_NOT_FOUND",
                error_message=f"No provider registered for EvidenceKind={kind.value!r}",
                duration_ms=elapsed_ms,
            )
            return StepExecutionRecord(
                step_id=step.step_id,
                status=StepStatus.FAILED,
                attempts=0,
                results=[result],
                final_result=result,
                started_at=started_at,
                completed_at=_now_iso(),
                duration_ms=elapsed_ms,
                step_order=step.order,
            )

        last_result: CollectionResult | None = None

        for attempt in range(1, max_attempts + 1):
            attempt_start = time.monotonic()
            try:
                result = provider.execute(
                    step_id=step.step_id,
                    evidence_kind=kind.value,
                    context=context,
                    requirement_metadata=req_meta,
                )
            except Exception as exc:  # noqa: BLE001
                elapsed = int((time.monotonic() - attempt_start) * 1000)
                LOGGER.warning(
                    "dispatcher.provider_raised step=%s attempt=%d/%d error=%s",
                    step.step_id, attempt, max_attempts, exc,
                )
                result = CollectionResult.fail(
                    provider_name=provider.provider_name,
                    step_id=step.step_id,
                    evidence_kind=kind.value,
                    error_code="PROVIDER_EXCEPTION",
                    error_message=str(exc),
                    duration_ms=elapsed,
                )

            results.append(result)
            last_result = result

            if result.success:
                LOGGER.debug(
                    "dispatcher.step_success step=%s attempt=%d provider=%s",
                    step.step_id, attempt, provider.provider_name,
                )
                break

            is_last = attempt >= max_attempts
            if is_last:
                break

            # Decide whether to retry
            if result.error_code == "PROVIDER_NOT_FOUND":
                break  # no point retrying a missing provider
            if not policy.retry_on_partial and result.partial:
                break

            LOGGER.debug(
                "dispatcher.retrying step=%s attempt=%d/%d backoff=%.2fs",
                step.step_id, attempt, max_attempts, policy.backoff_seconds,
            )
            if policy.backoff_seconds > 0:
                time.sleep(policy.backoff_seconds)

        total_ms = int((time.monotonic() - start_ts) * 1000)
        succeeded = last_result is not None and last_result.success
        status = StepStatus.SUCCESS if succeeded else StepStatus.FAILED

        LOGGER.info(
            "dispatcher.dispatch_complete step=%s status=%s attempts=%d duration_ms=%d",
            step.step_id, status.value, len(results), total_ms,
        )

        return StepExecutionRecord(
            step_id=step.step_id,
            status=status,
            attempts=len(results),
            results=results,
            final_result=last_result,
            started_at=started_at,
            completed_at=_now_iso(),
            duration_ms=total_ms,
            step_order=step.order,
        )

    # ── Private ────────────────────────────────────────────────────────────────

    def _select_kind(self, step: PlanningStep) -> EvidenceKind:
        """
        Select the EvidenceKind to use for provider resolution.

        Uses the first requirement's kind. Falls back to API if no requirements.
        """
        if step.evidence_required:
            return step.evidence_required[0].kind
        return EvidenceKind.API

    def _build_requirement_metadata(self, step: PlanningStep) -> dict[str, Any]:
        """Build metadata dict from step requirements for provider hint passing."""
        meta: dict[str, Any] = {
            "step_id":    step.step_id,
            "step_title": step.title,
        }
        if step.evidence_required:
            req = step.evidence_required[0]
            meta["requirement_id"]   = req.requirement_id
            meta["expected_fields"]  = list(req.expected_fields)
            meta["validation_hints"] = list(req.validation_hints)
            meta["priority"]         = req.priority.value
            if req.capability_hint is not None:
                meta["capability_hint"] = req.capability_hint.value
        return meta
