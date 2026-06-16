"""
tests/test_sprint226_retry_scheduler.py

Sprint 2.26: RetryScheduler tests.

Coverage:
  - schedule(): creates PENDING job with correct backoff
  - schedule(): respects explicit max_attempts
  - reschedule(): increments attempt_count
  - reschedule(): moves to DLQ when max_attempts reached
  - reschedule(): returns None when DLQ
  - cancel(): cancels PENDING job
  - cancel(): returns False for non-PENDING or non-existent
  - list_pending() / list_all_pending()
  - _backoff_delay() exponential formula
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository
from case_engine.retry.scheduler import RetryScheduler, _backoff_delay


def _repo() -> RetryRepository:
    return RetryRepository()


def _scheduler(base_delay_s: int = 10, max_delay_s: int = 3600, max_attempts: int = 3) -> tuple[RetryRepository, RetryScheduler]:
    repo = _repo()
    sched = RetryScheduler(repo, base_delay_s=base_delay_s, max_delay_s=max_delay_s, max_attempts=max_attempts)
    return repo, sched


# ── _backoff_delay() ──────────────────────────────────────────────────────────

class TestBackoffDelay:
    def test_attempt_0_is_base(self):
        d = _backoff_delay(0, base_delay_s=30, max_delay_s=3600)
        assert d.total_seconds() == 30.0

    def test_attempt_1_doubles(self):
        d = _backoff_delay(1, base_delay_s=30, max_delay_s=3600)
        assert d.total_seconds() == 60.0

    def test_attempt_2_quadruples(self):
        d = _backoff_delay(2, base_delay_s=30, max_delay_s=3600)
        assert d.total_seconds() == 120.0

    def test_capped_at_max(self):
        d = _backoff_delay(100, base_delay_s=30, max_delay_s=300)
        assert d.total_seconds() == 300.0

    def test_no_exceed_max(self):
        for attempt in range(10):
            d = _backoff_delay(attempt, base_delay_s=10, max_delay_s=100)
            assert d.total_seconds() <= 100


# ── schedule() ───────────────────────────────────────────────────────────────

class TestSchedulerSchedule:
    def test_creates_pending_job(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        assert job.status == RetryStatus.PENDING

    def test_job_stored_in_repo(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        assert repo.get_job(job.job_id) is not None

    def test_next_retry_at_in_future(self):
        repo, sched = _scheduler(base_delay_s=30)
        now = datetime.now(tz=timezone.utc)
        job = sched.schedule("act-1", "case-1", "otp_resend", {}, now=now)
        assert job.next_retry_at > now

    def test_next_retry_at_matches_backoff_attempt_0(self):
        repo, sched = _scheduler(base_delay_s=30)
        now = datetime.now(tz=timezone.utc)
        job = sched.schedule("act-1", "case-1", "otp_resend", {}, attempt_count=0, now=now)
        expected = now + timedelta(seconds=30)
        diff = abs((job.next_retry_at - expected).total_seconds())
        assert diff < 1

    def test_next_retry_at_matches_backoff_attempt_1(self):
        repo, sched = _scheduler(base_delay_s=30)
        now = datetime.now(tz=timezone.utc)
        job = sched.schedule("act-1", "case-1", "otp_resend", {}, attempt_count=1, now=now)
        expected = now + timedelta(seconds=60)
        diff = abs((job.next_retry_at - expected).total_seconds())
        assert diff < 1

    def test_explicit_max_attempts(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {}, max_attempts=5)
        assert job.max_attempts == 5

    def test_default_max_attempts(self):
        repo, sched = _scheduler(max_attempts=3)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        assert job.max_attempts == 3

    def test_action_params_stored(self):
        repo, sched = _scheduler()
        params = {"phone": "999"}
        job = sched.schedule("act-1", "case-1", "otp_resend", params)
        assert job.action_params == params


# ── reschedule() ─────────────────────────────────────────────────────────────

class TestSchedulerReschedule:
    def test_increments_attempt_count(self):
        repo, sched = _scheduler(max_attempts=3)
        original = sched.schedule("act-1", "case-1", "otp_resend", {})
        rescheduled = sched.reschedule(original, last_error="timeout")
        assert rescheduled is not None
        assert rescheduled.attempt_count == 1

    def test_status_is_pending_after_reschedule(self):
        repo, sched = _scheduler(max_attempts=3)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        running = job.with_update(status=RetryStatus.RUNNING)
        rescheduled = sched.reschedule(running, last_error="timeout")
        assert rescheduled.status == RetryStatus.PENDING

    def test_stores_last_error(self):
        repo, sched = _scheduler(max_attempts=3)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        rescheduled = sched.reschedule(job, last_error="ConnectionError")
        assert rescheduled.last_error == "ConnectionError"

    def test_next_retry_at_in_future(self):
        repo, sched = _scheduler(base_delay_s=10, max_attempts=3)
        now = datetime.now(tz=timezone.utc)
        job = sched.schedule("act-1", "case-1", "otp_resend", {}, now=now)
        rescheduled = sched.reschedule(job, last_error="x", now=now)
        assert rescheduled.next_retry_at > now

    def test_moves_to_dlq_when_max_reached(self):
        repo, sched = _scheduler(max_attempts=2)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        job_at_1 = job.with_update(attempt_count=1)
        result = sched.reschedule(job_at_1, last_error="final failure")
        assert result is None

    def test_dlq_job_stored(self):
        repo, sched = _scheduler(max_attempts=2)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        job_at_1 = job.with_update(attempt_count=1)
        sched.reschedule(job_at_1, last_error="final failure")
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.DEAD_LETTERED

    def test_updated_job_persisted(self):
        repo, sched = _scheduler(max_attempts=3)
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        rescheduled = sched.reschedule(job, last_error="x")
        found = repo.get_job(job.job_id)
        assert found.attempt_count == 1


# ── cancel() ─────────────────────────────────────────────────────────────────

class TestSchedulerCancel:
    def test_cancel_pending_returns_true(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        assert sched.cancel(job.job_id) is True

    def test_cancel_moves_to_dlq(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        sched.cancel(job.job_id)
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.DEAD_LETTERED

    def test_cancel_nonexistent_returns_false(self):
        _, sched = _scheduler()
        assert sched.cancel("not-there") is False

    def test_cancel_non_pending_returns_false(self):
        repo, sched = _scheduler()
        job = sched.schedule("act-1", "case-1", "otp_resend", {})
        running = job.with_update(status=RetryStatus.RUNNING)
        repo.update_job(running)
        assert sched.cancel(job.job_id) is False


# ── list_pending / list_all_pending ──────────────────────────────────────────

class TestSchedulerListPending:
    def test_list_pending_returns_due_jobs(self):
        repo, sched = _scheduler(base_delay_s=0)
        now  = datetime.now(tz=timezone.utc)
        past = now - timedelta(seconds=10)
        sched.schedule("act-1", "case-1", "otp_resend", {}, now=past)
        assert len(sched.list_pending(now)) == 1

    def test_list_all_pending_includes_future(self):
        repo, sched = _scheduler(base_delay_s=3600)
        now = datetime.now(tz=timezone.utc)
        sched.schedule("act-1", "case-1", "otp_resend", {}, now=now)
        assert len(sched.list_all_pending()) == 1
