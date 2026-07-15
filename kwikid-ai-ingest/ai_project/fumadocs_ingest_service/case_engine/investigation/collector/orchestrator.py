"""
case_engine/investigation/collector/orchestrator.py

Sprint 2.42: StepOrchestrator — executes InvestigationGraph steps.

Responsibilities:
  - Traverse the InvestigationGraph in dependency-correct order
  - Execute parallel groups concurrently (ThreadPoolExecutor)
  - Evaluate preconditions before each step (with a stable snapshot of completed steps)
  - Handle FALLBACK dependency activation on step failure
  - Return PlanExecutionSummary (never raises)

Deterministic replay guarantee:
  Within a parallel group, steps are sorted by step_id (lexicographic) before
  submission, so execution ORDER is deterministic even though completion order
  depends on provider latency.

Dependency direction:
  orchestrator.py → collector/dispatcher.py (ProviderDispatcher)
  orchestrator.py → collector/execution.py (StepExecutionRecord, StepStatus, PlanExecutionSummary)
  orchestrator.py → collector/validators.py (CollectorValidator)
  orchestrator.py → collector/contracts.py (CollectionContext, CollectionResult)
  orchestrator.py → investigation/planner/models.py (InvestigationPlan, PlanningStep, StepStatus)
  orchestrator.py → stdlib (concurrent.futures, threading)
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Callable

from case_engine.investigation.collector.contracts import CollectionContext, CollectionResult
from case_engine.investigation.collector.dispatcher import ProviderDispatcher
from case_engine.investigation.collector.execution import (
    PlanExecutionSummary,
    StepExecutionRecord,
    StepStatus,
)
from case_engine.investigation.collector.validators import CollectorValidator
from case_engine.investigation.planner.models import (
    InvestigationPlan,
    PlanningStep,
)

LOGGER = logging.getLogger(__name__)

_MAX_PARALLEL_WORKERS = 8


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class StepOrchestrator:
    """
    Executes an InvestigationGraph's steps with correct execution semantics:

      Sequential:   steps are executed in topological order
      Parallel:     steps in the same parallel_group run concurrently
      Conditional:  steps with EVIDENCE_PRESENT preconditions are skipped
                    if the prerequisite step failed
      Fallback:     steps with FALLBACK dependency edges run when their
                    primary step fails (handled by InvestigationGraph.get_next_steps)

    Never raises. All failures are captured in StepExecutionRecord.
    """

    def __init__(
        self,
        dispatcher: ProviderDispatcher,
        validator: CollectorValidator | None = None,
        max_workers: int = _MAX_PARALLEL_WORKERS,
    ) -> None:
        self._dispatcher  = dispatcher
        self._validator   = validator or CollectorValidator()
        self._max_workers = max_workers

    def execute(
        self,
        plan: InvestigationPlan,
        context: CollectionContext,
    ) -> PlanExecutionSummary:
        """
        Execute all steps in the plan respecting the InvestigationGraph structure.

        Returns:
            PlanExecutionSummary with all StepExecutionRecords sorted by step_order.
        """
        graph = plan.graph
        all_step_ids  = {s.step_id for s in graph.nodes}
        step_by_id    = {s.step_id: s for s in graph.nodes}
        records: list[StepExecutionRecord] = []
        completed: set[str] = set()   # step_ids that succeeded
        failed:    set[str] = set()   # step_ids that failed/timed-out
        done_ids:  set[str] = set()   # all step_ids that have a record (incl. skipped)

        # Build parallel group lookup: step_id → frozenset of co-group step_ids
        parallel_group_of: dict[str, frozenset[str]] = {}
        for group in graph.parallel_groups:
            for sid in group:
                parallel_group_of[sid] = group

        def _run_step_with_snapshot(
            step: PlanningStep,
            completed_snapshot: frozenset[str],
        ) -> StepExecutionRecord:
            """Check preconditions (using a stable snapshot), then dispatch."""
            ok, reasons = self._validator.validate_preconditions(
                step, context, completed_snapshot
            )
            if not ok:
                LOGGER.info(
                    "orchestrator.step_skipped step=%s reasons=%s",
                    step.step_id, reasons,
                )
                return StepExecutionRecord(
                    step_id=step.step_id,
                    status=StepStatus.SKIPPED,
                    attempts=0,
                    results=[],
                    final_result=None,
                    started_at=_now_iso(),
                    completed_at=_now_iso(),
                    duration_ms=0,
                    skipped_reason="; ".join(reasons),
                    step_order=step.order,
                )
            return self._dispatcher.dispatch(step, context)

        def _post_record(record: StepExecutionRecord) -> None:
            records.append(record)
            done_ids.add(record.step_id)
            if record.succeeded:
                completed.add(record.step_id)
            elif record.status in (StepStatus.FAILED, StepStatus.TIMEOUT):
                failed.add(record.step_id)

        # Execute root steps first
        root_steps = sorted(graph.get_root_steps(), key=lambda s: s.step_id)
        self._execute_wave(root_steps, parallel_group_of, done_ids, context, _run_step_with_snapshot, _post_record, frozenset(completed))

        # Iterate until no more steps are ready
        max_iterations = len(all_step_ids) + 1
        for _ in range(max_iterations):
            next_ready = [
                s for s in sorted(
                    graph.get_next_steps(completed, failed),
                    key=lambda s: s.step_id,
                )
                if s.step_id not in done_ids
            ]
            if not next_ready:
                break
            snap = frozenset(completed)
            self._execute_wave(next_ready, parallel_group_of, done_ids, context, _run_step_with_snapshot, _post_record, snap)

        # Mark unreached steps as skipped
        for sid in sorted(all_step_ids - done_ids):
            step = step_by_id[sid]
            records.append(StepExecutionRecord(
                step_id=sid,
                status=StepStatus.SKIPPED,
                attempts=0,
                results=[],
                final_result=None,
                started_at=_now_iso(),
                completed_at=_now_iso(),
                duration_ms=0,
                skipped_reason="step not reached during graph traversal",
                step_order=step.order,
            ))

        # Sort by step_order for deterministic output
        records.sort(key=lambda r: r.step_order)
        return self._build_summary(plan, records)

    # ── Private ────────────────────────────────────────────────────────────────

    def _execute_wave(
        self,
        steps: list[PlanningStep],
        parallel_group_of: dict[str, frozenset[str]],
        done_ids: set[str],
        context: CollectionContext,
        run_step: Callable[[PlanningStep, frozenset[str]], StepExecutionRecord],
        post_record: Callable[[StepExecutionRecord], None],
        completed_snapshot: frozenset[str],
    ) -> None:
        """
        Execute a wave of steps, grouping parallel steps for concurrent execution.

        Steps in the same parallel_group run concurrently via ThreadPoolExecutor.
        All other steps run sequentially.
        """
        scheduled_groups: set[int] = set()  # id() of frozensets already scheduled
        solo_steps: list[PlanningStep] = []
        group_batches: list[list[PlanningStep]] = []

        for step in steps:
            if step.step_id in done_ids:
                continue
            group = parallel_group_of.get(step.step_id)
            if group is not None:
                gid = id(group)
                if gid not in scheduled_groups:
                    scheduled_groups.add(gid)
                    # Collect all group members that are in this wave and not done
                    group_members = sorted(
                        [s for s in steps if s.step_id in group and s.step_id not in done_ids],
                        key=lambda s: s.step_id,  # deterministic order within group
                    )
                    if group_members:
                        group_batches.append(group_members)
                # steps already belonging to a scheduled group are handled above
            else:
                solo_steps.append(step)

        # Run parallel groups concurrently
        for group_members in group_batches:
            for s in group_members:
                done_ids.add(s.step_id)  # pre-mark to prevent double-dispatch
            self._run_parallel(
                group_members, completed_snapshot, run_step, post_record
            )

        # Run solo steps sequentially
        for step in solo_steps:
            if step.step_id in done_ids:
                continue
            done_ids.add(step.step_id)
            record = run_step(step, completed_snapshot)
            post_record(record)

    def _run_parallel(
        self,
        steps: list[PlanningStep],
        completed_snapshot: frozenset[str],
        run_step: Callable[[PlanningStep, frozenset[str]], StepExecutionRecord],
        post_record: Callable[[StepExecutionRecord], None],
    ) -> None:
        """Execute a set of steps concurrently using ThreadPoolExecutor."""
        if not steps:
            return
        if len(steps) == 1:
            record = run_step(steps[0], completed_snapshot)
            post_record(record)
            return

        workers = min(len(steps), self._max_workers)
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(run_step, step, completed_snapshot): step
                for step in steps
            }
            for fut in as_completed(futures):
                step = futures[fut]
                try:
                    record = fut.result()
                except Exception as exc:  # noqa: BLE001
                    LOGGER.error(
                        "orchestrator.parallel_future_failed step=%s error=%s",
                        step.step_id, exc,
                    )
                    record = StepExecutionRecord(
                        step_id=step.step_id,
                        status=StepStatus.FAILED,
                        attempts=1,
                        results=[],
                        final_result=CollectionResult.fail(
                            provider_name="<orchestrator>",
                            step_id=step.step_id,
                            evidence_kind="",
                            error_code="PARALLEL_EXECUTION_ERROR",
                            error_message=str(exc),
                        ),
                        started_at=_now_iso(),
                        completed_at=_now_iso(),
                        duration_ms=0,
                        step_order=step.order,
                    )
                post_record(record)

    def _build_summary(
        self,
        plan: InvestigationPlan,
        records: list[StepExecutionRecord],
    ) -> PlanExecutionSummary:
        successful = sum(1 for r in records if r.status == StepStatus.SUCCESS)
        failed     = sum(1 for r in records if r.status == StepStatus.FAILED)
        skipped    = sum(1 for r in records if r.status == StepStatus.SKIPPED)
        timed_out  = sum(1 for r in records if r.status == StepStatus.TIMEOUT)
        total_ms   = sum(r.duration_ms for r in records)

        return PlanExecutionSummary(
            plan_id=plan.plan_id,
            case_id=plan.case_id,
            total_steps=len(records),
            successful_steps=successful,
            failed_steps=failed,
            skipped_steps=skipped,
            timeout_steps=timed_out,
            total_duration_ms=total_ms,
            records=records,
            completed_at=_now_iso(),
        )
