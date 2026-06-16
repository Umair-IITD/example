"""
tests/test_sprint226_retry_worker.py

Sprint 2.26: RetryWorker tests.

Coverage:
  - run_once() with no pending jobs → empty result
  - run_once() with executor success → SUCCEEDED status
  - run_once() with executor failure → reschedule
  - run_once() when max_attempts reached → DEAD_LETTERED
  - run_once() with no executor wired → reschedule
  - RetryRunResult fields
  - RetryExecutor protocol check
  - Never raises even when executor throws unexpected exception
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository
from case_engine.retry.scheduler import RetryScheduler
from case_engine.retry.worker import RetryExecutor, RetryRunResult, RetryWorker


# ── Helpers ───────────────────────────────────────────────────────────────────

def _build_stack(
    base_delay_s: int = 5,
    max_attempts: int = 3,
):
    repo      = RetryRepository()
    scheduler = RetryScheduler(repo, base_delay_s=base_delay_s, max_attempts=max_attempts)
    return repo, scheduler


class _SuccessExecutor:
    """Always succeeds."""
    def execute(self, job: RetryJob):
        pass  # no exception = success


class _FailExecutor:
    """Always raises RuntimeError."""
    def execute(self, job: RetryJob):
        raise RuntimeError("simulated failure")


class _BombExecutor:
    """Raises unexpected exception type."""
    def execute(self, job: RetryJob):
        raise MemoryError("unexpected")


def _past_job(
    repo: RetryRepository,
    scheduler: RetryScheduler,
    action_id: str = "act-1",
) -> RetryJob:
    now  = datetime.now(tz=timezone.utc)
    past = now - timedelta(seconds=10)
    return scheduler.schedule(action_id, "case-1", "otp_resend", {}, now=past)


# ── RetryRunResult ────────────────────────────────────────────────────────────

class TestRetryRunResult:
    def test_default_values(self):
        r = RetryRunResult()
        assert r.processed     == 0
        assert r.succeeded     == 0
        assert r.failed        == 0
        assert r.dead_lettered == 0
        assert r.errors        == []

    def test_to_dict(self):
        r = RetryRunResult(processed=3, succeeded=2, failed=1)
        d = r.to_dict()
        assert d["processed"]     == 3
        assert d["succeeded"]     == 2
        assert d["failed"]        == 1
        assert d["dead_lettered"] == 0


# ── RetryExecutor protocol ────────────────────────────────────────────────────

class TestRetryExecutorProtocol:
    def test_success_executor_satisfies_protocol(self):
        assert isinstance(_SuccessExecutor(), RetryExecutor)

    def test_fail_executor_satisfies_protocol(self):
        assert isinstance(_FailExecutor(), RetryExecutor)


# ── run_once() ────────────────────────────────────────────────────────────────

class TestRetryWorkerRunOnce:
    def test_empty_repo_returns_zero(self):
        repo, sched = _build_stack()
        worker = RetryWorker(repo, sched, _SuccessExecutor())
        result = worker.run_once()
        assert result.processed  == 0
        assert result.succeeded  == 0

    def test_no_due_jobs_returns_zero(self):
        repo, sched = _build_stack(base_delay_s=3600)
        sched.schedule("act-1", "case-1", "otp_resend", {})
        worker = RetryWorker(repo, sched, _SuccessExecutor())
        result = worker.run_once(now=datetime.now(tz=timezone.utc))
        assert result.processed == 0

    def test_success_path(self):
        repo, sched = _build_stack()
        _past_job(repo, sched)
        worker = RetryWorker(repo, sched, _SuccessExecutor())
        result = worker.run_once(now=datetime.now(tz=timezone.utc))
        assert result.processed == 1
        assert result.succeeded == 1
        assert result.failed    == 0

    def test_success_marks_job_succeeded(self):
        repo, sched = _build_stack()
        job = _past_job(repo, sched)
        worker = RetryWorker(repo, sched, _SuccessExecutor())
        worker.run_once(now=datetime.now(tz=timezone.utc))
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.SUCCEEDED

    def test_failure_reschedules(self):
        repo, sched = _build_stack(max_attempts=3)
        job = _past_job(repo, sched)
        worker = RetryWorker(repo, sched, _FailExecutor())
        result = worker.run_once(now=datetime.now(tz=timezone.utc))
        assert result.processed     == 1
        assert result.failed        == 1
        assert result.dead_lettered == 0
        found = repo.get_job(job.job_id)
        assert found.status        == RetryStatus.PENDING
        assert found.attempt_count == 1

    def test_failure_with_last_attempt_dead_letters(self):
        repo, sched = _build_stack(max_attempts=2)
        job = _past_job(repo, sched)
        # Pre-advance attempt_count to 1 (already failed once)
        repo.update_job(job.with_update(attempt_count=1))
        # Fetch current state
        now = datetime.now(tz=timezone.utc)
        repo.update_job(repo.get_job(job.job_id).with_update(
            next_retry_at=now - timedelta(seconds=1)
        ))
        worker = RetryWorker(repo, sched, _FailExecutor())
        result = worker.run_once(now=now)
        assert result.dead_lettered == 1
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.DEAD_LETTERED

    def test_no_executor_reschedules(self):
        repo, sched = _build_stack(max_attempts=3)
        job = _past_job(repo, sched)
        worker = RetryWorker(repo, sched, executor=None)
        result = worker.run_once(now=datetime.now(tz=timezone.utc))
        assert result.processed     == 1
        assert result.failed        == 1
        assert result.dead_lettered == 0

    def test_multiple_jobs_processed(self):
        repo, sched = _build_stack()
        now  = datetime.now(tz=timezone.utc)
        past = now - timedelta(seconds=5)
        sched.schedule("act-1", "case-1", "otp", {}, now=past)
        sched.schedule("act-2", "case-2", "otp", {}, now=past)
        sched.schedule("act-3", "case-3", "otp", {}, now=past)
        worker = RetryWorker(repo, sched, _SuccessExecutor())
        result = worker.run_once(now=now)
        assert result.processed == 3
        assert result.succeeded == 3

    def test_never_raises_on_unexpected_exception(self):
        repo, sched = _build_stack()
        _past_job(repo, sched)
        worker = RetryWorker(repo, sched, _BombExecutor())
        # Should not raise — the worker catches all exceptions
        result = worker.run_once(now=datetime.now(tz=timezone.utc))
        # Bomb causes failure path, dead lettered or rescheduled depending on attempts
        assert result.processed == 1

    def test_marks_running_before_execution(self):
        """Executor sees RUNNING status during execution."""
        seen_status = []

        class _StatusCapture:
            def execute(self, job: RetryJob):
                seen_status.append(job.status)

        repo, sched = _build_stack()
        _past_job(repo, sched)
        worker = RetryWorker(repo, sched, _StatusCapture())
        worker.run_once(now=datetime.now(tz=timezone.utc))
        assert seen_status == [RetryStatus.RUNNING]
