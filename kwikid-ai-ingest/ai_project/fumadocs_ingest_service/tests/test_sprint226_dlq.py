"""
tests/test_sprint226_dlq.py

Sprint 2.26: DeadLetterQueue tests.

Coverage:
  - list() — empty / populated
  - count() — empty / after DLQ
  - requeue() — moves back to PENDING, resets attempt_count
  - requeue() — returns None if not found or not in DLQ
  - requeue_all() — requeues all dead-lettered jobs
  - purge() — removes all dead-lettered jobs
  - summary() — counts and metadata
  - Never raises
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository
from case_engine.retry.dlq import DeadLetterQueue


def _repo_with_dlq_jobs(count: int = 3) -> tuple[RetryRepository, list[RetryJob]]:
    repo = RetryRepository()
    jobs = []
    for i in range(count):
        at  = datetime.now(tz=timezone.utc) + timedelta(minutes=i)
        job = RetryJob.create(f"act-{i}", f"case-{i}", "otp_resend", {}, at)
        # Move to DLQ
        repo.add_job(job)
        dead = repo.move_to_dlq(job.job_id, reason=f"reason_{i}")
        jobs.append(dead)
    return repo, jobs


# ── list() / count() ─────────────────────────────────────────────────────────

class TestDLQList:
    def test_list_empty(self):
        repo = RetryRepository()
        dlq  = DeadLetterQueue(repo)
        assert dlq.list() == []

    def test_list_returns_dead_lettered(self):
        repo, jobs = _repo_with_dlq_jobs(3)
        dlq  = DeadLetterQueue(repo)
        dead = dlq.list()
        assert len(dead) == 3

    def test_list_sorted_by_created_at(self):
        repo, jobs = _repo_with_dlq_jobs(3)
        dlq  = DeadLetterQueue(repo)
        dead = dlq.list()
        for i in range(len(dead) - 1):
            assert dead[i].created_at <= dead[i + 1].created_at

    def test_list_excludes_pending(self):
        repo, _ = _repo_with_dlq_jobs(2)
        at  = datetime.now(tz=timezone.utc) + timedelta(minutes=10)
        job = RetryJob.create("act-x", "case-x", "otp", {}, at)
        repo.add_job(job)  # PENDING
        dlq  = DeadLetterQueue(repo)
        assert len(dlq.list()) == 2  # only the 2 dead-lettered ones

    def test_count_matches_list_len(self):
        repo, _ = _repo_with_dlq_jobs(4)
        dlq = DeadLetterQueue(repo)
        assert dlq.count() == 4

    def test_count_empty(self):
        assert DeadLetterQueue(RetryRepository()).count() == 0


# ── requeue() ─────────────────────────────────────────────────────────────────

class TestDLQRequeue:
    def test_requeue_returns_pending_job(self):
        repo, jobs = _repo_with_dlq_jobs(1)
        dlq       = DeadLetterQueue(repo)
        requeued  = dlq.requeue(jobs[0].job_id)
        assert requeued is not None
        assert requeued.status == RetryStatus.PENDING

    def test_requeue_resets_attempt_count(self):
        repo, jobs = _repo_with_dlq_jobs(1)
        dlq = DeadLetterQueue(repo)
        requeued = dlq.requeue(jobs[0].job_id)
        assert requeued.attempt_count == 0

    def test_requeue_clears_last_error(self):
        repo, jobs = _repo_with_dlq_jobs(1)
        dlq = DeadLetterQueue(repo)
        requeued = dlq.requeue(jobs[0].job_id)
        assert requeued.last_error is None

    def test_requeue_sets_next_retry_at_to_now(self):
        repo, jobs = _repo_with_dlq_jobs(1)
        dlq = DeadLetterQueue(repo)
        now = datetime.now(tz=timezone.utc)
        requeued = dlq.requeue(jobs[0].job_id, now=now)
        assert requeued.next_retry_at == now

    def test_requeue_persists_to_repo(self):
        repo, jobs = _repo_with_dlq_jobs(1)
        dlq = DeadLetterQueue(repo)
        dlq.requeue(jobs[0].job_id)
        found = repo.get_job(jobs[0].job_id)
        assert found.status == RetryStatus.PENDING

    def test_requeue_nonexistent_returns_none(self):
        dlq = DeadLetterQueue(RetryRepository())
        assert dlq.requeue("no-such-job") is None

    def test_requeue_pending_job_returns_none(self):
        repo = RetryRepository()
        at   = datetime.now(tz=timezone.utc) + timedelta(seconds=30)
        job  = RetryJob.create("act-1", "case-1", "otp", {}, at)
        repo.add_job(job)
        dlq = DeadLetterQueue(repo)
        assert dlq.requeue(job.job_id) is None  # not in DLQ

    def test_dlq_count_decreases_after_requeue(self):
        repo, jobs = _repo_with_dlq_jobs(2)
        dlq = DeadLetterQueue(repo)
        assert dlq.count() == 2
        dlq.requeue(jobs[0].job_id)
        assert dlq.count() == 1


# ── requeue_all() ─────────────────────────────────────────────────────────────

class TestDLQRequeueAll:
    def test_requeue_all_returns_count(self):
        repo, _ = _repo_with_dlq_jobs(3)
        dlq   = DeadLetterQueue(repo)
        count = dlq.requeue_all()
        assert count == 3

    def test_requeue_all_clears_dlq(self):
        repo, _ = _repo_with_dlq_jobs(3)
        dlq = DeadLetterQueue(repo)
        dlq.requeue_all()
        assert dlq.count() == 0

    def test_requeue_all_empty_repo(self):
        dlq = DeadLetterQueue(RetryRepository())
        assert dlq.requeue_all() == 0


# ── purge() ───────────────────────────────────────────────────────────────────

class TestDLQPurge:
    def test_purge_returns_count(self):
        repo, _ = _repo_with_dlq_jobs(3)
        dlq     = DeadLetterQueue(repo)
        count   = dlq.purge()
        assert count == 3

    def test_purge_deletes_from_repo(self):
        repo, jobs = _repo_with_dlq_jobs(2)
        dlq = DeadLetterQueue(repo)
        dlq.purge()
        for job in jobs:
            assert repo.get_job(job.job_id) is None

    def test_purge_empty_repo(self):
        dlq = DeadLetterQueue(RetryRepository())
        assert dlq.purge() == 0

    def test_purge_does_not_delete_pending(self):
        repo, _ = _repo_with_dlq_jobs(2)
        at  = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        job = RetryJob.create("pending-1", "case-p", "t", {}, at)
        repo.add_job(job)
        dlq = DeadLetterQueue(repo)
        dlq.purge()
        assert repo.get_job(job.job_id) is not None


# ── summary() ─────────────────────────────────────────────────────────────────

class TestDLQSummary:
    def test_summary_count(self):
        repo, _ = _repo_with_dlq_jobs(3)
        dlq  = DeadLetterQueue(repo)
        s    = dlq.summary()
        assert s["count"] == 3

    def test_summary_action_types(self):
        repo, _ = _repo_with_dlq_jobs(2)
        dlq  = DeadLetterQueue(repo)
        s    = dlq.summary()
        assert "otp_resend" in s["action_types"]

    def test_summary_oldest_created_at_not_none(self):
        repo, _ = _repo_with_dlq_jobs(2)
        dlq = DeadLetterQueue(repo)
        s   = dlq.summary()
        assert s["oldest_created_at"] is not None

    def test_summary_empty_oldest_none(self):
        dlq = DeadLetterQueue(RetryRepository())
        s   = dlq.summary()
        assert s["oldest_created_at"] is None
        assert s["count"] == 0

    def test_never_raises(self):
        dlq = DeadLetterQueue(RetryRepository())
        _ = dlq.summary()  # should not raise
