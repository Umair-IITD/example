"""
case_engine/retry/scheduler.py

Sprint 2.26: RetryScheduler — schedule retry jobs with exponential backoff.

Responsibilities:
  - Compute next_retry_at using exponential backoff: base * 2^attempt, capped at max.
  - Create and persist RetryJob instances via RetryRepository.
  - Reschedule a failed job (increment attempt_count; move to DLQ if exhausted).
  - Cancel a PENDING job (move to DLQ).

No execution. No external I/O.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository

LOGGER = logging.getLogger(__name__)

_DEFAULT_BASE_DELAY_S: int = 30
_DEFAULT_MAX_DELAY_S:  int = 3_600   # 1 hour cap


def _backoff_delay(
    attempt_count: int,
    base_delay_s:  int = _DEFAULT_BASE_DELAY_S,
    max_delay_s:   int = _DEFAULT_MAX_DELAY_S,
) -> timedelta:
    """
    Exponential backoff with cap.

    attempt_count=0 → base_delay_s
    attempt_count=1 → base_delay_s * 2
    attempt_count=2 → base_delay_s * 4
    ...capped at max_delay_s.
    """
    delay = min(base_delay_s * (2 ** attempt_count), max_delay_s)
    return timedelta(seconds=delay)


class RetryScheduler:
    """
    Schedules RetryJobs with exponential backoff.

    Pure orchestration — delegates all persistence to RetryRepository.
    Never raises.
    """

    def __init__(
        self,
        repository:   RetryRepository,
        base_delay_s: int = _DEFAULT_BASE_DELAY_S,
        max_delay_s:  int = _DEFAULT_MAX_DELAY_S,
        max_attempts: int = 3,
    ) -> None:
        self._repo         = repository
        self._base_delay_s = base_delay_s
        self._max_delay_s  = max_delay_s
        self._max_attempts = max_attempts

    # ── Schedule ──────────────────────────────────────────────────────────────

    def schedule(
        self,
        action_id:     str,
        case_id:       str,
        action_type:   str,
        action_params: dict[str, Any],
        attempt_count: int = 0,
        max_attempts:  int | None = None,
        now:           datetime | None = None,
    ) -> RetryJob:
        """
        Create and store a new PENDING RetryJob.

        next_retry_at is calculated as now + backoff(attempt_count).
        Never raises.
        """
        try:
            at_now     = now or datetime.now(tz=timezone.utc)
            delay      = _backoff_delay(attempt_count, self._base_delay_s, self._max_delay_s)
            effective_max = max_attempts if max_attempts is not None else self._max_attempts

            job = RetryJob.create(
                action_id=action_id,
                case_id=case_id,
                action_type=action_type,
                action_params=action_params,
                next_retry_at=at_now + delay,
                max_attempts=effective_max,
            )
            stored = self._repo.add_job(job)
            LOGGER.info(
                "retry_scheduler.schedule job_id=%s action_id=%s next_retry=%s attempt=%d max=%d",
                stored.job_id, action_id, stored.next_retry_at.isoformat(),
                attempt_count, effective_max,
            )
            return stored
        except Exception as exc:
            LOGGER.exception("retry_scheduler.schedule failed action_id=%s error=%s", action_id, exc)
            raise

    def reschedule(
        self,
        job:        RetryJob,
        last_error: str,
        now:        datetime | None = None,
    ) -> RetryJob | None:
        """
        Increment attempt_count and schedule the next retry with backoff.

        If attempt_count + 1 >= max_attempts: move to DLQ and return None.
        Otherwise: update the job with new attempt_count and next_retry_at, return it.
        Never raises.
        """
        try:
            new_count = job.attempt_count + 1
            if new_count >= job.max_attempts:
                LOGGER.warning(
                    "retry_scheduler.reschedule max_attempts_exceeded job_id=%s "
                    "action_id=%s attempts=%d/%d last_error=%s",
                    job.job_id, job.action_id, new_count, job.max_attempts, last_error[:200],
                )
                self._repo.move_to_dlq(
                    job.job_id,
                    reason=f"max_attempts_exceeded({new_count}/{job.max_attempts}): {last_error[:300]}",
                )
                return None

            at_now  = now or datetime.now(tz=timezone.utc)
            delay   = _backoff_delay(new_count, self._base_delay_s, self._max_delay_s)
            updated = job.with_update(
                attempt_count=new_count,
                next_retry_at=at_now + delay,
                status=RetryStatus.PENDING,
                last_error=last_error[:500],
            )
            self._repo.update_job(updated)
            LOGGER.info(
                "retry_scheduler.reschedule job_id=%s next_retry=%s attempt=%d/%d",
                updated.job_id, updated.next_retry_at.isoformat(), new_count, job.max_attempts,
            )
            return updated
        except Exception as exc:
            LOGGER.exception(
                "retry_scheduler.reschedule failed job_id=%s error=%s", job.job_id, exc
            )
            return None

    def cancel(self, job_id: str) -> bool:
        """
        Cancel a PENDING job by moving it to DEAD_LETTERED.

        Returns True if found and cancelled, False if not found or already terminal.
        Never raises.
        """
        try:
            job = self._repo.get_job(job_id)
            if job is None:
                return False
            if job.status != RetryStatus.PENDING:
                return False
            self._repo.move_to_dlq(job_id, reason="cancelled")
            LOGGER.info("retry_scheduler.cancel job_id=%s", job_id)
            return True
        except Exception as exc:
            LOGGER.exception("retry_scheduler.cancel failed job_id=%s error=%s", job_id, exc)
            return False

    # ── Query ─────────────────────────────────────────────────────────────────

    def list_pending(self, now: datetime | None = None) -> list[RetryJob]:
        """Return all PENDING jobs that are due by now."""
        return self._repo.list_pending(now)

    def list_all_pending(self) -> list[RetryJob]:
        """Return all PENDING jobs regardless of next_retry_at."""
        return self._repo.list_by_status(RetryStatus.PENDING)
