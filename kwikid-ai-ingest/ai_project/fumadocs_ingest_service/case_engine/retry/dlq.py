"""
case_engine/retry/dlq.py

Sprint 2.26: DeadLetterQueue — view and management of DEAD_LETTERED jobs.

Responsibilities:
  - list(): enumerate all DEAD_LETTERED jobs.
  - requeue(): move a DEAD_LETTERED job back to PENDING (reset attempt_count=0).
  - purge(): permanently delete all DEAD_LETTERED jobs.
  - count(): how many dead-lettered jobs exist.

Does not execute jobs. Pure state management over RetryRepository.
Never raises.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository

LOGGER = logging.getLogger(__name__)


class DeadLetterQueue:
    """
    View and management of DEAD_LETTERED jobs in the RetryRepository.

    All methods are safe to call on an empty repository.
    Never raises.
    """

    def __init__(self, repository: RetryRepository) -> None:
        self._repo = repository

    # ── Enumeration ───────────────────────────────────────────────────────────

    def list(self) -> list[RetryJob]:
        """Return all DEAD_LETTERED jobs, sorted by created_at (oldest first)."""
        try:
            dead = self._repo.list_by_status(RetryStatus.DEAD_LETTERED)
            return sorted(dead, key=lambda j: j.created_at)
        except Exception as exc:
            LOGGER.exception("dlq.list failed error=%s", exc)
            return []

    def count(self) -> int:
        """Return the count of DEAD_LETTERED jobs."""
        try:
            return self._repo.count_by_status(RetryStatus.DEAD_LETTERED)
        except Exception as exc:
            LOGGER.exception("dlq.count failed error=%s", exc)
            return 0

    # ── Requeue ───────────────────────────────────────────────────────────────

    def requeue(self, job_id: str, now: datetime | None = None) -> RetryJob | None:
        """
        Move a DEAD_LETTERED job back to PENDING with reset attempt_count.

        next_retry_at is set to now (immediately eligible for retry).
        Returns the re-queued job, or None if not found or not in DLQ.
        Never raises.
        """
        try:
            job = self._repo.get_job(job_id)
            if job is None:
                LOGGER.warning("dlq.requeue job_id=%s not found", job_id)
                return None
            if job.status != RetryStatus.DEAD_LETTERED:
                LOGGER.warning(
                    "dlq.requeue job_id=%s not in DLQ (status=%s)", job_id, job.status.value
                )
                return None

            at_now   = now or datetime.now(tz=timezone.utc)
            requeued = job.with_update(
                attempt_count=0,
                status=RetryStatus.PENDING,
                next_retry_at=at_now,
                last_error=None,
            )
            self._repo.update_job(requeued)
            LOGGER.info(
                "dlq.requeue job_id=%s action_id=%s case_id=%s",
                job_id, job.action_id, job.case_id,
            )
            return requeued
        except Exception as exc:
            LOGGER.exception("dlq.requeue failed job_id=%s error=%s", job_id, exc)
            return None

    def requeue_all(self, now: datetime | None = None) -> int:
        """
        Requeue all DEAD_LETTERED jobs. Returns count successfully requeued.
        Never raises.
        """
        try:
            dead = self.list()
            count = 0
            for job in dead:
                if self.requeue(job.job_id, now=now) is not None:
                    count += 1
            if count > 0:
                LOGGER.info("dlq.requeue_all requeued=%d", count)
            return count
        except Exception as exc:
            LOGGER.exception("dlq.requeue_all failed error=%s", exc)
            return 0

    # ── Purge ─────────────────────────────────────────────────────────────────

    def purge(self) -> int:
        """
        Permanently delete all DEAD_LETTERED jobs from the repository.

        Returns count of jobs purged. Never raises.
        """
        try:
            dead    = self.list()
            purged  = 0
            for job in dead:
                if self._repo.delete_job(job.job_id):
                    purged += 1
            if purged > 0:
                LOGGER.info("dlq.purge purged=%d", purged)
            return purged
        except Exception as exc:
            LOGGER.exception("dlq.purge failed error=%s", exc)
            return 0

    # ── Summary ───────────────────────────────────────────────────────────────

    def summary(self) -> dict[str, Any]:
        """Return a summary dict for the DLQ (for admin visibility)."""
        try:
            dead = self.list()
            return {
                "count":       len(dead),
                "action_types": list({j.action_type for j in dead}),
                "case_ids":     list({j.case_id for j in dead}),
                "oldest_created_at": dead[0].created_at.isoformat() if dead else None,
            }
        except Exception as exc:
            LOGGER.exception("dlq.summary failed error=%s", exc)
            return {"count": 0, "error": str(exc)}
