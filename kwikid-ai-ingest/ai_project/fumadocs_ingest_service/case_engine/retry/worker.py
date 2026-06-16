"""
case_engine/retry/worker.py

Sprint 2.26: RetryWorker — processes pending RetryJobs.

Responsibilities:
  - Call list_pending() on the repository to find due jobs.
  - Mark each job RUNNING before execution.
  - Delegate execution to RetryExecutor (protocol).
  - On success: mark SUCCEEDED.
  - On failure: call RetryScheduler.reschedule() → PENDING (or DLQ if exhausted).
  - Return a RetryRunResult summary.

No async. No external I/O. Thread-safe (repository is thread-safe).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository
from case_engine.retry.scheduler import RetryScheduler

LOGGER = logging.getLogger(__name__)


@runtime_checkable
class RetryExecutor(Protocol):
    """
    Protocol for executing a single RetryJob.

    Implementations must raise on failure so RetryWorker can reschedule.
    Return value is ignored (success = no exception raised).
    """

    def execute(self, job: RetryJob) -> Any:
        """Execute the action described by job. Raise on failure."""
        ...


@dataclass
class RetryRunResult:
    """Summary returned by RetryWorker.run_once()."""
    processed:     int = 0
    succeeded:     int = 0
    failed:        int = 0
    dead_lettered: int = 0
    errors:        list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "processed":     self.processed,
            "succeeded":     self.succeeded,
            "failed":        self.failed,
            "dead_lettered": self.dead_lettered,
            "errors":        list(self.errors),
        }


class RetryWorker:
    """
    Processes pending RetryJobs by delegating to a RetryExecutor.

    One run_once() call processes all currently-due PENDING jobs in order
    of next_retry_at (oldest first). Each job is processed independently;
    failure of one job does not prevent processing of subsequent jobs.

    Never raises.
    """

    def __init__(
        self,
        repository: RetryRepository,
        scheduler:  RetryScheduler,
        executor:   RetryExecutor | None = None,
    ) -> None:
        self._repo      = repository
        self._scheduler = scheduler
        self._executor  = executor

    def run_once(self, now: datetime | None = None) -> RetryRunResult:
        """
        Process all PENDING jobs due by `now`.

        Returns a RetryRunResult summary. Never raises.
        """
        result = RetryRunResult()
        try:
            pending = self._repo.list_pending(now)
        except Exception as exc:
            LOGGER.exception("retry_worker.run_once: list_pending failed error=%s", exc)
            result.errors.append(f"list_pending: {exc}")
            return result

        for job in pending:
            try:
                self._process_job(job, result, now)
            except Exception as exc:
                LOGGER.exception(
                    "retry_worker.run_once: unexpected error job_id=%s error=%s",
                    job.job_id, exc,
                )
                result.errors.append(f"job_id={job.job_id}: {exc}")

        if result.processed > 0:
            LOGGER.info(
                "retry_worker.run_once processed=%d succeeded=%d failed=%d dead_lettered=%d",
                result.processed, result.succeeded, result.failed, result.dead_lettered,
            )
        return result

    # ── Internal ──────────────────────────────────────────────────────────────

    def _process_job(
        self,
        job:    RetryJob,
        result: RetryRunResult,
        now:    datetime | None,
    ) -> None:
        result.processed += 1

        # Mark RUNNING
        running_job = job.with_update(status=RetryStatus.RUNNING)
        self._repo.update_job(running_job)

        if self._executor is None:
            LOGGER.warning(
                "retry_worker: no executor wired — rescheduling job_id=%s", job.job_id
            )
            rescheduled = self._scheduler.reschedule(
                running_job, last_error="no_executor_wired", now=now
            )
            if rescheduled is None:
                result.dead_lettered += 1
            else:
                result.failed += 1
            return

        try:
            self._executor.execute(running_job)
            succeeded = running_job.with_update(status=RetryStatus.SUCCEEDED)
            self._repo.update_job(succeeded)
            result.succeeded += 1
            LOGGER.info("retry_worker: SUCCEEDED job_id=%s action_id=%s", job.job_id, job.action_id)

        except Exception as exc:
            error_str = f"{type(exc).__name__}: {exc}"
            LOGGER.warning(
                "retry_worker: FAILED job_id=%s action_id=%s error=%s",
                job.job_id, job.action_id, error_str,
            )
            rescheduled = self._scheduler.reschedule(
                running_job, last_error=error_str[:500], now=now
            )
            if rescheduled is None:
                result.dead_lettered += 1
            else:
                result.failed += 1
