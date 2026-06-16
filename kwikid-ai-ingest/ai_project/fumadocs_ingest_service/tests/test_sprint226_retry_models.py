"""
tests/test_sprint226_retry_models.py

Sprint 2.26: Retry Queue Framework — model tests.

Coverage:
  - RetryStatus values and string equivalence
  - TERMINAL_RETRY_STATES
  - RetryJob.create() factory
  - RetryJob.to_dict() / from_dict() round-trip
  - RetryJob.with_update() frozen mutation
  - RetryJob.is_terminal, attempts_remaining, is_due
  - RetryJob immutability (frozen dataclass)
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone

from case_engine.retry.models import RetryJob, RetryStatus, TERMINAL_RETRY_STATES


# ── RetryStatus ───────────────────────────────────────────────────────────────

class TestRetryStatus:
    def test_all_values_exist(self):
        vals = {s.value for s in RetryStatus}
        assert "PENDING"       in vals
        assert "RUNNING"       in vals
        assert "SUCCEEDED"     in vals
        assert "FAILED"        in vals
        assert "DEAD_LETTERED" in vals

    def test_string_equivalence(self):
        assert RetryStatus.PENDING == "PENDING"
        assert RetryStatus.DEAD_LETTERED == "DEAD_LETTERED"

    def test_terminal_states(self):
        assert RetryStatus.SUCCEEDED in TERMINAL_RETRY_STATES
        assert RetryStatus.DEAD_LETTERED in TERMINAL_RETRY_STATES
        assert RetryStatus.PENDING not in TERMINAL_RETRY_STATES
        assert RetryStatus.RUNNING not in TERMINAL_RETRY_STATES
        assert RetryStatus.FAILED not in TERMINAL_RETRY_STATES

    def test_five_statuses(self):
        assert len(RetryStatus) == 5


# ── RetryJob.create() ─────────────────────────────────────────────────────────

class TestRetryJobCreate:
    def _future(self, seconds: int = 30) -> datetime:
        return datetime.now(tz=timezone.utc) + timedelta(seconds=seconds)

    def test_create_returns_pending(self):
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        assert job.status == RetryStatus.PENDING

    def test_create_sets_attempt_count_zero(self):
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        assert job.attempt_count == 0

    def test_create_generates_uuid(self):
        j1 = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        j2 = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        assert j1.job_id != j2.job_id

    def test_create_copies_action_params(self):
        params = {"phone": "9999999999", "channel": "SMS"}
        job = RetryJob.create("act-1", "case-1", "otp_resend", params, self._future())
        assert job.action_params == params

    def test_create_no_last_error(self):
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        assert job.last_error is None

    def test_create_respects_max_attempts(self):
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future(), max_attempts=5)
        assert job.max_attempts == 5

    def test_create_default_max_attempts(self):
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, self._future())
        assert job.max_attempts == 3

    def test_create_stores_case_id(self):
        job = RetryJob.create("act-1", "case-abc", "otp_resend", {}, self._future())
        assert job.case_id == "case-abc"

    def test_create_stores_action_type(self):
        job = RetryJob.create("act-1", "case-1", "vkyc_session_reset", {}, self._future())
        assert job.action_type == "vkyc_session_reset"

    def test_create_stores_next_retry_at(self):
        future = self._future(120)
        job = RetryJob.create("act-1", "case-1", "otp_resend", {}, future)
        assert job.next_retry_at == future


# ── RetryJob serialization ────────────────────────────────────────────────────

class TestRetryJobSerialization:
    def _job(self) -> RetryJob:
        return RetryJob.create(
            "act-42", "case-99", "api_callback_retry",
            {"callback_type": "EKYC", "application_id": "APP123"},
            datetime.now(tz=timezone.utc) + timedelta(minutes=1),
            max_attempts=4,
        )

    def test_to_dict_has_all_keys(self):
        d = self._job().to_dict()
        for key in ("job_id", "action_id", "case_id", "action_type",
                    "action_params", "attempt_count", "max_attempts",
                    "next_retry_at", "created_at", "status", "last_error"):
            assert key in d, f"Missing key: {key}"

    def test_to_dict_status_is_string(self):
        d = self._job().to_dict()
        assert d["status"] == "PENDING"

    def test_to_dict_action_params_is_dict(self):
        d = self._job().to_dict()
        assert isinstance(d["action_params"], dict)

    def test_round_trip(self):
        original = self._job()
        d        = original.to_dict()
        restored = RetryJob.from_dict(d)
        assert restored.job_id        == original.job_id
        assert restored.action_id     == original.action_id
        assert restored.case_id       == original.case_id
        assert restored.action_type   == original.action_type
        assert restored.action_params == original.action_params
        assert restored.attempt_count == original.attempt_count
        assert restored.max_attempts  == original.max_attempts
        assert restored.status        == original.status
        assert restored.last_error    == original.last_error

    def test_from_dict_datetime_roundtrip(self):
        original = self._job()
        restored = RetryJob.from_dict(original.to_dict())
        assert restored.next_retry_at == original.next_retry_at
        assert restored.created_at    == original.created_at

    def test_from_dict_with_last_error(self):
        d = self._job().to_dict()
        d["last_error"] = "ConnectionError: timeout"
        job = RetryJob.from_dict(d)
        assert job.last_error == "ConnectionError: timeout"

    def test_from_dict_unknown_status_raises(self):
        d = self._job().to_dict()
        d["status"] = "UNKNOWN_STATUS"
        with pytest.raises(ValueError):
            RetryJob.from_dict(d)


# ── RetryJob.with_update() ────────────────────────────────────────────────────

class TestRetryJobWithUpdate:
    def _job(self) -> RetryJob:
        return RetryJob.create(
            "act-1", "case-1", "otp_resend", {},
            datetime.now(tz=timezone.utc) + timedelta(seconds=30),
        )

    def test_with_update_returns_new_instance(self):
        original = self._job()
        updated  = original.with_update(attempt_count=1)
        assert updated is not original

    def test_with_update_changes_field(self):
        original = self._job()
        updated  = original.with_update(status=RetryStatus.RUNNING)
        assert updated.status == RetryStatus.RUNNING

    def test_with_update_preserves_other_fields(self):
        original = self._job()
        updated  = original.with_update(attempt_count=2)
        assert updated.job_id      == original.job_id
        assert updated.action_id   == original.action_id
        assert updated.case_id     == original.case_id
        assert updated.action_type == original.action_type
        assert updated.max_attempts == original.max_attempts

    def test_original_unchanged_after_update(self):
        original = self._job()
        original.with_update(status=RetryStatus.SUCCEEDED)
        assert original.status == RetryStatus.PENDING

    def test_update_last_error(self):
        original = self._job()
        updated  = original.with_update(last_error="timeout", status=RetryStatus.FAILED)
        assert updated.last_error == "timeout"
        assert updated.status     == RetryStatus.FAILED


# ── RetryJob properties ───────────────────────────────────────────────────────

class TestRetryJobProperties:
    def test_is_terminal_pending_false(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc))
        assert not job.is_terminal

    def test_is_terminal_succeeded(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc))
        j   = job.with_update(status=RetryStatus.SUCCEEDED)
        assert j.is_terminal

    def test_is_terminal_dead_lettered(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc))
        j   = job.with_update(status=RetryStatus.DEAD_LETTERED)
        assert j.is_terminal

    def test_attempts_remaining_initial(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc), max_attempts=3)
        assert job.attempts_remaining == 3

    def test_attempts_remaining_after_one(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc), max_attempts=3)
        j   = job.with_update(attempt_count=1)
        assert j.attempts_remaining == 2

    def test_attempts_remaining_floor_zero(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc), max_attempts=2)
        j   = job.with_update(attempt_count=5)
        assert j.attempts_remaining == 0

    def test_is_due_past_next_retry_at(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        job  = RetryJob.create("a", "c", "t", {}, past)
        assert job.is_due

    def test_is_due_future_next_retry_at(self):
        future = datetime.now(tz=timezone.utc) + timedelta(hours=1)
        job    = RetryJob.create("a", "c", "t", {}, future)
        assert not job.is_due

    def test_is_due_requires_pending_status(self):
        past = datetime.now(tz=timezone.utc) - timedelta(seconds=1)
        job  = RetryJob.create("a", "c", "t", {}, past)
        j    = job.with_update(status=RetryStatus.RUNNING)
        assert not j.is_due


# ── RetryJob immutability ─────────────────────────────────────────────────────

class TestRetryJobImmutability:
    def test_is_frozen_dataclass(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc))
        with pytest.raises((AttributeError, TypeError)):
            job.status = RetryStatus.RUNNING  # type: ignore[misc]

    def test_job_id_immutable(self):
        job = RetryJob.create("a", "c", "t", {}, datetime.now(tz=timezone.utc))
        with pytest.raises((AttributeError, TypeError)):
            job.job_id = "new-id"  # type: ignore[misc]
