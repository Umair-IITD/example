"""
tests/test_sprint226_retry_repository.py

Sprint 2.26: RetryRepository tests.

Coverage:
  - add_job / get_job / update_job / delete_job
  - get_job_by_action
  - list_pending (respects next_retry_at and status filter)
  - list_by_status / list_by_case / list_all
  - move_to_dlq
  - count / count_by_status / stats
  - thread-safety (basic — verify no AttributeError under concurrent usage)
"""
from __future__ import annotations

import threading
import pytest
from datetime import datetime, timedelta, timezone

from case_engine.retry.models import RetryJob, RetryStatus
from case_engine.retry.repository import RetryRepository


def _make_job(
    action_id: str = "act-1",
    case_id:   str = "case-1",
    action_type: str = "otp_resend",
    delay_s: int = 30,
    status: RetryStatus = RetryStatus.PENDING,
) -> RetryJob:
    at = datetime.now(tz=timezone.utc) + timedelta(seconds=delay_s)
    job = RetryJob.create(action_id, case_id, action_type, {}, at)
    if status != RetryStatus.PENDING:
        job = job.with_update(status=status)
    return job


# ── Basic CRUD ────────────────────────────────────────────────────────────────

class TestRetryRepositoryCRUD:
    def test_add_and_get(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        found = repo.get_job(job.job_id)
        assert found is not None
        assert found.job_id == job.job_id

    def test_get_nonexistent_returns_none(self):
        repo = RetryRepository()
        assert repo.get_job("nonexistent") is None

    def test_update_job_replaces(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        updated = job.with_update(status=RetryStatus.RUNNING)
        repo.update_job(updated)
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.RUNNING

    def test_delete_job_existing(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        assert repo.delete_job(job.job_id) is True
        assert repo.get_job(job.job_id) is None

    def test_delete_job_nonexistent(self):
        repo = RetryRepository()
        assert repo.delete_job("not-there") is False

    def test_add_overwrites_same_id(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        newer = job.with_update(attempt_count=2)
        repo.add_job(newer)
        found = repo.get_job(job.job_id)
        assert found.attempt_count == 2


# ── get_job_by_action ─────────────────────────────────────────────────────────

class TestGetJobByAction:
    def test_found(self):
        repo = RetryRepository()
        job  = _make_job(action_id="act-XYZ")
        repo.add_job(job)
        found = repo.get_job_by_action("act-XYZ")
        assert found is not None
        assert found.action_id == "act-XYZ"

    def test_not_found(self):
        repo = RetryRepository()
        assert repo.get_job_by_action("no-such-action") is None

    def test_returns_first_match(self):
        repo = RetryRepository()
        j1   = _make_job(action_id="shared-act")
        j2   = _make_job(action_id="shared-act", case_id="case-2")
        repo.add_job(j1)
        repo.add_job(j2)
        found = repo.get_job_by_action("shared-act")
        assert found is not None


# ── list_pending ──────────────────────────────────────────────────────────────

class TestListPending:
    def test_returns_due_pending_jobs(self):
        repo = RetryRepository()
        now  = datetime.now(tz=timezone.utc)
        past = now - timedelta(seconds=10)
        job  = RetryJob.create("act-1", "case-1", "otp", {}, past)
        repo.add_job(job)
        pending = repo.list_pending(now)
        assert len(pending) == 1

    def test_excludes_future_jobs(self):
        repo   = RetryRepository()
        now    = datetime.now(tz=timezone.utc)
        future = now + timedelta(hours=1)
        job    = RetryJob.create("act-1", "case-1", "otp", {}, future)
        repo.add_job(job)
        assert repo.list_pending(now) == []

    def test_excludes_non_pending_statuses(self):
        repo = RetryRepository()
        now  = datetime.now(tz=timezone.utc)
        past = now - timedelta(seconds=10)
        for status in (RetryStatus.RUNNING, RetryStatus.SUCCEEDED, RetryStatus.DEAD_LETTERED, RetryStatus.FAILED):
            job = RetryJob.create("a", "c", "t", {}, past).with_update(status=status)
            repo.add_job(job)
        # FAILED is also not PENDING, so excluded
        assert repo.list_pending(now) == []

    def test_sorted_by_next_retry_at(self):
        repo = RetryRepository()
        now  = datetime.now(tz=timezone.utc)
        j1   = RetryJob.create("a1", "c", "t", {}, now - timedelta(seconds=5))
        j2   = RetryJob.create("a2", "c", "t", {}, now - timedelta(seconds=10))
        repo.add_job(j1)
        repo.add_job(j2)
        pending = repo.list_pending(now)
        assert pending[0].action_id == "a2"   # older = earlier retry_at

    def test_default_now_is_utc(self):
        repo = RetryRepository()
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        job  = RetryJob.create("act", "case", "type", {}, past)
        repo.add_job(job)
        assert len(repo.list_pending()) == 1


# ── list_by_status / list_by_case / list_all ─────────────────────────────────

class TestListVariants:
    def test_list_all_empty(self):
        assert RetryRepository().list_all() == []

    def test_list_all_returns_all(self):
        repo = RetryRepository()
        repo.add_job(_make_job("a1"))
        repo.add_job(_make_job("a2"))
        assert len(repo.list_all()) == 2

    def test_list_by_status_pending(self):
        repo = RetryRepository()
        j1   = _make_job("a1", status=RetryStatus.PENDING)
        j2   = _make_job("a2", status=RetryStatus.SUCCEEDED)
        repo.add_job(j1)
        repo.add_job(j2)
        pending = repo.list_by_status(RetryStatus.PENDING)
        assert len(pending) == 1
        assert pending[0].action_id == "a1"

    def test_list_by_case(self):
        repo = RetryRepository()
        repo.add_job(_make_job(case_id="case-A"))
        repo.add_job(_make_job(case_id="case-B", action_id="a2"))
        result = repo.list_by_case("case-A")
        assert len(result) == 1


# ── move_to_dlq ───────────────────────────────────────────────────────────────

class TestMoveToDLQ:
    def test_moves_to_dead_lettered(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        updated = repo.move_to_dlq(job.job_id, reason="test reason")
        assert updated is not None
        assert updated.status == RetryStatus.DEAD_LETTERED

    def test_sets_last_error(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        updated = repo.move_to_dlq(job.job_id, reason="max_attempts_exceeded")
        assert updated.last_error == "max_attempts_exceeded"

    def test_nonexistent_returns_none(self):
        repo = RetryRepository()
        assert repo.move_to_dlq("not-there", reason="x") is None

    def test_persisted_in_repo(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        repo.move_to_dlq(job.job_id, reason="r")
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.DEAD_LETTERED


# ── Counts ────────────────────────────────────────────────────────────────────

class TestCounts:
    def test_count_empty(self):
        assert RetryRepository().count() == 0

    def test_count_after_add(self):
        repo = RetryRepository()
        repo.add_job(_make_job("a1"))
        repo.add_job(_make_job("a2"))
        assert repo.count() == 2

    def test_count_after_delete(self):
        repo = RetryRepository()
        job  = _make_job()
        repo.add_job(job)
        repo.delete_job(job.job_id)
        assert repo.count() == 0

    def test_count_by_status(self):
        repo = RetryRepository()
        repo.add_job(_make_job("a1", status=RetryStatus.PENDING))
        repo.add_job(_make_job("a2", status=RetryStatus.SUCCEEDED))
        assert repo.count_by_status(RetryStatus.PENDING)   == 1
        assert repo.count_by_status(RetryStatus.SUCCEEDED) == 1
        assert repo.count_by_status(RetryStatus.FAILED)    == 0

    def test_stats_all_statuses_present(self):
        repo  = RetryRepository()
        stats = repo.stats()
        for s in RetryStatus:
            assert s.value in stats

    def test_stats_counts_correct(self):
        repo = RetryRepository()
        repo.add_job(_make_job("a1"))
        repo.add_job(_make_job("a2", status=RetryStatus.DEAD_LETTERED))
        stats = repo.stats()
        assert stats[RetryStatus.PENDING.value]       == 1
        assert stats[RetryStatus.DEAD_LETTERED.value] == 1


# ── Thread-safety smoke test ──────────────────────────────────────────────────

class TestThreadSafety:
    def test_concurrent_adds(self):
        repo    = RetryRepository()
        errors  = []
        barrier = threading.Barrier(10)

        def _add(i: int) -> None:
            try:
                barrier.wait()
                repo.add_job(_make_job(action_id=f"act-{i}", case_id=f"case-{i}"))
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_add, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert repo.count() == 10
