"""
worker/action_worker.py

Sprint 2.6: ActionWorker — lightweight execution coordinator.

Responsibilities:
  - Poll APPROVED actions and invoke ActionRuntime.execute_action()
  - Poll ROLLING_BACK originals and invoke ActionRuntime.execute_rollback()
  - Collect and return results per tick

What ActionWorker is NOT:
  - Not a background thread or process
  - Not a task queue, Celery task, or scheduled job
  - Not aware of Freshdesk or any provider
  - Not responsible for retry logic (ActionRuntime/ActionGateway handle retries)

Usage:
    worker = ActionWorker(runtime=runtime, repository=repo, worker_id="w-1")
    forward = worker.tick(client="unity_bank")
    rollbacks = worker.tick_rollbacks(client="unity_bank")

Design:
    tick() and tick_rollbacks() are single-pass batch operations.
    The caller is responsible for scheduling repetition. Keeping the worker
    deterministic and stateless makes it trivially testable.

    Production sequencing: call tick_rollbacks() before tick() so that
    compensation actions are claimed (APPROVED → EXECUTING) before tick()
    fetches the APPROVED queue. This prevents tick() from accidentally
    picking up compensation actions and calling execute() on rollback payloads.
"""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from case_engine.action_executor import ExecutionResult
from case_engine.action_repository import ActionRepository
from case_engine.action_runtime import ActionRuntime

LOGGER = logging.getLogger(__name__)


@dataclass
class WorkerTickResult:
    """Result of a single worker tick (forward or rollback pass)."""
    worker_id: str
    client: str
    processed: int
    results: list[ExecutionResult] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)


class ActionWorker:
    """
    Lightweight execution coordinator.

    Single responsibility: for each tick, fetch APPROVED (or ROLLING_BACK)
    actions and delegate execution to ActionRuntime. All state management
    is handled by the gateway/runtime layers.

    Thread safety:
        ActionWorker is stateless (worker_id is a stable label, not mutable
        state). Multiple workers with distinct worker_ids may share a
        runtime instance safely.
    """

    def __init__(
        self,
        runtime: ActionRuntime,
        repository: ActionRepository,
        *,
        worker_id: str | None = None,
        batch_size: int = 10,
    ) -> None:
        self._runtime = runtime
        self._repo = repository
        self.worker_id = worker_id or f"worker-{str(uuid.uuid4())[:8]}"
        self._batch_size = batch_size

    # ── Forward execution ──────────────────────────────────────────────────────

    def tick(self, client: str) -> WorkerTickResult:
        """
        Process one batch of APPROVED actions for the given client.

        For each action:
          1. Call runtime.execute_action(action_id, executor_id=worker_id)
          2. Collect results (success and failure)

        Returns:
            WorkerTickResult summarising the batch.
        """
        actions = self._repo.list_approved_actions(client)
        if self._batch_size:
            actions = actions[: self._batch_size]

        results: list[ExecutionResult] = []
        errors: list[str] = []

        for action in actions:
            try:
                result = self._runtime.execute_action(
                    action.action_id,
                    executor_id=self.worker_id,
                )
                results.append(result)
            except Exception as exc:
                LOGGER.exception(
                    "action_worker.tick: unexpected exception action_id=%s error=%s",
                    action.action_id, exc,
                )
                errors.append(f"{action.action_id}: {exc}")

        LOGGER.info(
            "action_worker.tick: worker=%s client=%s processed=%d success=%d failure=%d",
            self.worker_id, client, len(actions),
            sum(1 for r in results if r.success),
            sum(1 for r in results if not r.success),
        )
        return WorkerTickResult(
            worker_id=self.worker_id,
            client=client,
            processed=len(actions),
            results=results,
            errors=errors,
        )

    # ── Rollback execution ─────────────────────────────────────────────────────

    def tick_rollbacks(self, client: str) -> WorkerTickResult:
        """
        Process one batch of ROLLING_BACK originals for the given client.

        For each ROLLING_BACK original action:
          1. Call runtime.execute_rollback(original_id, executor_id=worker_id)
          2. This internally claims and runs the linked compensation action.

        Returns:
            WorkerTickResult summarising the rollback batch.
        """
        originals = self._repo.list_rolling_back_actions(client)
        if self._batch_size:
            originals = originals[: self._batch_size]

        results: list[ExecutionResult] = []
        errors: list[str] = []

        for original in originals:
            try:
                result = self._runtime.execute_rollback(
                    original.action_id,
                    executor_id=self.worker_id,
                )
                results.append(result)
            except Exception as exc:
                LOGGER.exception(
                    "action_worker.tick_rollbacks: unexpected exception action_id=%s error=%s",
                    original.action_id, exc,
                )
                errors.append(f"{original.action_id}: {exc}")

        LOGGER.info(
            "action_worker.tick_rollbacks: worker=%s client=%s processed=%d success=%d",
            self.worker_id, client, len(originals),
            sum(1 for r in results if r.success),
        )
        return WorkerTickResult(
            worker_id=self.worker_id,
            client=client,
            processed=len(originals),
            results=results,
            errors=errors,
        )

    # ── Combined ───────────────────────────────────────────────────────────────

    def process(self, client: str) -> tuple[WorkerTickResult, WorkerTickResult]:
        """
        Run one complete processing cycle for the given client.

        Executes rollbacks first (to claim compensation actions) then
        forward actions (safe to run now — compensation actions are claimed).

        Returns:
            (rollback_result, forward_result) tuple.
        """
        rollback_result = self.tick_rollbacks(client)
        forward_result = self.tick(client)
        return rollback_result, forward_result
