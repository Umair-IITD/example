"""
case_engine/retry/repository.py

Sprint 2.26: RetryRepository — in-memory store for RetryJob instances.

Thread-safe. Future: swap _jobs dict for Redis or Supabase without changing callers.
All methods are O(n) linear scans acceptable at the concurrency level of this system.
"""
from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

from case_engine.retry.models import RetryJob, RetryStatus


class RetryRepository:
    """
    In-memory store for RetryJob instances.

    All public methods are thread-safe via a single reentrant lock.
    """

    def __init__(self) -> None:
        self._lock: threading.RLock = threading.RLock()
        self._jobs: dict[str, RetryJob] = {}

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_job(self, job: RetryJob) -> RetryJob:
        """Store a new RetryJob. Overwrites if job_id already exists."""
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def update_job(self, job: RetryJob) -> RetryJob:
        """Persist a modified RetryJob (must already exist or will be created)."""
        with self._lock:
            self._jobs[job.job_id] = job
        return job

    def move_to_dlq(self, job_id: str, reason: str) -> RetryJob | None:
        """Transition a job to DEAD_LETTERED. Returns updated job or None if not found."""
        with self._lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            updated = job.with_update(
                status=RetryStatus.DEAD_LETTERED,
                last_error=reason,
            )
            self._jobs[job_id] = updated
            return updated

    def delete_job(self, job_id: str) -> bool:
        """Remove a job entirely. Returns True if found and deleted."""
        with self._lock:
            if job_id in self._jobs:
                del self._jobs[job_id]
                return True
            return False

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_job(self, job_id: str) -> RetryJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def get_job_by_action(self, action_id: str) -> RetryJob | None:
        """Find first job matching action_id. Returns None if not found."""
        with self._lock:
            for job in self._jobs.values():
                if job.action_id == action_id:
                    return job
        return None

    def list_pending(self, now: datetime | None = None) -> list[RetryJob]:
        """Return all PENDING jobs whose next_retry_at <= now, sorted by next_retry_at."""
        cutoff = now or datetime.now(tz=timezone.utc)
        with self._lock:
            due = [
                j for j in self._jobs.values()
                if j.status == RetryStatus.PENDING and j.next_retry_at <= cutoff
            ]
        return sorted(due, key=lambda j: j.next_retry_at)

    def list_by_status(self, status: RetryStatus) -> list[RetryJob]:
        """Return all jobs with the given status."""
        with self._lock:
            return [j for j in self._jobs.values() if j.status == status]

    def list_all(self) -> list[RetryJob]:
        """Return a snapshot of all jobs."""
        with self._lock:
            return list(self._jobs.values())

    def list_by_case(self, case_id: str) -> list[RetryJob]:
        """Return all jobs for a given case_id."""
        with self._lock:
            return [j for j in self._jobs.values() if j.case_id == case_id]

    # ── Counts ────────────────────────────────────────────────────────────────

    def count(self) -> int:
        with self._lock:
            return len(self._jobs)

    def count_by_status(self, status: RetryStatus) -> int:
        with self._lock:
            return sum(1 for j in self._jobs.values() if j.status == status)

    def stats(self) -> dict[str, int]:
        """Return count by each RetryStatus value."""
        with self._lock:
            result: dict[str, int] = {s.value: 0 for s in RetryStatus}
            for job in self._jobs.values():
                result[job.status.value] += 1
        return result
