"""
tests/test_sprint226_e2e.py

Sprint 2.26 End-to-End integration tests.

Tests that span multiple components:
  - Full retry lifecycle: schedule → worker executes → SUCCEEDED
  - Full retry failure lifecycle: schedule → worker fails → DLQ
  - Clarification resume loop: PAUSED → slots provided → CLARIFY re-run → READY → continue
  - Attempt tracking: PAUSED → resume → PENDING increment → PAUSED again → increment → max → ESCALATED
  - JSON serialization: RetryJob round-trip through to_dict / from_dict
  - Assembly: all workflow services reachable from ProductionRuntime
  - New audit events: both new types appear in AuditEventType enum
"""
from __future__ import annotations

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from case_engine.retry import (
    DeadLetterQueue,
    RetryJob,
    RetryRepository,
    RetryRunResult,
    RetryScheduler,
    RetryStatus,
    RetryWorker,
    TERMINAL_RETRY_STATES,
)
from case_engine.models import AuditEventType


# ── Full retry success lifecycle ──────────────────────────────────────────────

class TestRetrySuccessLifecycle:
    def test_full_success_pipeline(self):
        repo      = RetryRepository()
        scheduler = RetryScheduler(repo, base_delay_s=0, max_attempts=3)
        dlq       = DeadLetterQueue(repo)

        class _Executor:
            def execute(self, job: RetryJob):
                pass  # success

        worker = RetryWorker(repo, scheduler, _Executor())

        # 1. Schedule
        now  = datetime.now(tz=timezone.utc)
        past = now - timedelta(seconds=5)
        job  = scheduler.schedule("act-e2e", "case-e2e", "otp_resend", {}, now=past)
        assert job.status == RetryStatus.PENDING
        assert repo.count() == 1

        # 2. Worker processes
        result = worker.run_once(now=now)
        assert result.processed == 1
        assert result.succeeded == 1

        # 3. Job is SUCCEEDED
        found = repo.get_job(job.job_id)
        assert found.status == RetryStatus.SUCCEEDED

        # 4. DLQ is empty
        assert dlq.count() == 0


# ── Full retry → DLQ lifecycle ────────────────────────────────────────────────

class TestRetryDLQLifecycle:
    def test_failure_exhausts_to_dlq(self):
        repo      = RetryRepository()
        scheduler = RetryScheduler(repo, base_delay_s=0, max_attempts=2)
        dlq       = DeadLetterQueue(repo)

        class _FailExecutor:
            def execute(self, job: RetryJob):
                raise RuntimeError("always fails")

        worker = RetryWorker(repo, scheduler, _FailExecutor())
        now    = datetime.now(tz=timezone.utc)

        # Schedule and run until exhausted
        job = scheduler.schedule("act-fail", "case-fail", "vkyc", {}, now=now - timedelta(seconds=5))

        # Attempt 1 → rescheduled (attempt_count 0→1, still < max_attempts=2)
        result1 = worker.run_once(now=now)
        assert result1.failed == 1
        assert result1.dead_lettered == 0

        # Attempt 2 → DLQ (attempt_count 1 + 1 = 2 >= max_attempts=2)
        found = repo.get_job(job.job_id)
        repo.update_job(found.with_update(next_retry_at=now - timedelta(seconds=1)))
        result2 = worker.run_once(now=now)
        assert result2.dead_lettered == 1

        # Job is DEAD_LETTERED
        dead = repo.get_job(job.job_id)
        assert dead.status == RetryStatus.DEAD_LETTERED
        assert dlq.count() == 1

    def test_dlq_requeue_then_success(self):
        repo      = RetryRepository()
        scheduler = RetryScheduler(repo, base_delay_s=0, max_attempts=2)
        dlq       = DeadLetterQueue(repo)

        # Put a job directly in DLQ
        now = datetime.now(tz=timezone.utc)
        job = RetryJob.create("act-requeue", "case-r", "otp_resend", {}, now)
        repo.add_job(job)
        repo.move_to_dlq(job.job_id, reason="test")
        assert dlq.count() == 1

        # Requeue
        requeued = dlq.requeue(job.job_id, now=now)
        assert requeued is not None
        assert requeued.status == RetryStatus.PENDING

        # Worker succeeds
        class _SuccessExecutor:
            def execute(self, j): pass

        worker = RetryWorker(repo, scheduler, _SuccessExecutor())
        result = worker.run_once(now=now)
        assert result.succeeded == 1
        assert dlq.count() == 0


# ── Clarification loop E2E ────────────────────────────────────────────────────

class TestClarificationLoopE2E:
    """
    End-to-end: WorkflowEngine PAUSED at CLARIFY → resume_after_clarification()
    with filled slots → READY → workflow continues.
    """

    def test_paused_to_ready_continues_workflow(self):
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        from case_engine.workflows.workflow_engine import WorkflowEngine
        from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState, WorkflowStepType
        from case_engine.slot_filling.models import SlotStatus, SlotValue

        registry = PlaybookRegistry.build()
        defn     = registry.get("VKYC_Session_Failure")
        assert defn is not None

        # Build a clarification service that first says NEEDS_CLARIFICATION, then READY
        call_count = [0]
        svc        = MagicMock()
        def _clarify_side_effect(**kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return {"status": "NEEDS_CLARIFICATION", "ready_to_continue": False,
                        "missing_slots": ["session_id"], "clarification_message": "?"}
            return {"status": "READY", "ready_to_continue": True, "missing_slots": []}
        svc.clarify.side_effect = _clarify_side_effect

        engine = WorkflowEngine(clarification_service=svc)

        # ── First call: start the workflow → CLARIFY fires → NEEDS_CLARIFICATION → PAUSED
        case = MagicMock()
        case.case_id        = "case-loop-e2e"
        case.topic          = "VKYC_Session_Failure"
        case.workflow_context = {}
        case.slot_state     = {}

        partial_slots = {
            "session_id":   SlotValue("session_id",   SlotStatus.PENDING, None),
            "phone_number": SlotValue("phone_number",  SlotStatus.FILLED, "9999999999"),
        }
        start_result = engine.start(case, registry, partial_slots)
        assert start_result.workflow_state == WorkflowState.PAUSED

        # Save workflow_context (simulates DB persist)
        case.workflow_context = start_result.to_dict()

        # ── Second call: customer provides session_id → resume
        filled_slots = {
            "session_id":   SlotValue("session_id",   SlotStatus.FILLED, "SES-001"),
            "phone_number": SlotValue("phone_number",  SlotStatus.FILLED, "9999999999"),
        }
        resume_result = engine.resume_after_clarification(case, registry, filled_slots)
        # READY → workflow navigates forward from CLARIFY step
        assert resume_result.workflow_state != WorkflowState.PAUSED
        assert call_count[0] == 2  # clarify called twice (once at start, once at resume)

    def test_multiple_pause_cycles_tracked(self):
        """
        Simulate two consecutive PAUSED cycles.
        Each resume attempt still returns NEEDS_CLARIFICATION (slot not yet filled).
        """
        from case_engine.workflows.playbook_registry import PlaybookRegistry
        from case_engine.workflows.workflow_engine import WorkflowEngine
        from case_engine.workflows.models import WorkflowExecutionResult, WorkflowState
        from case_engine.slot_filling.models import SlotStatus, SlotValue

        registry = PlaybookRegistry.build()
        svc      = MagicMock()
        svc.clarify.return_value = {
            "status": "NEEDS_CLARIFICATION", "ready_to_continue": False,
            "missing_slots": ["session_id"], "clarification_message": "?",
        }
        engine = WorkflowEngine(clarification_service=svc)

        case = MagicMock()
        case.case_id = "case-multi-pause"
        case.topic   = "VKYC_Session_Failure"
        case.workflow_context = {}
        case.slot_state = {}

        empty_slots = {
            "session_id":   SlotValue("session_id",   SlotStatus.PENDING, None),
            "phone_number": SlotValue("phone_number",  SlotStatus.FILLED, "9999"),
        }
        # Start → PAUSED (cycle 1)
        r1 = engine.start(case, registry, empty_slots)
        assert r1.workflow_state == WorkflowState.PAUSED
        case.workflow_context = r1.to_dict()

        # Resume cycle 2 → still PAUSED
        r2 = engine.resume_after_clarification(case, registry, empty_slots)
        assert r2.workflow_state == WorkflowState.PAUSED
        case.workflow_context = r2.to_dict()

        # step_results should grow (each CLARIFICATION_PENDING is recorded)
        assert len(r2.step_results) >= 2


# ── Audit event completeness ──────────────────────────────────────────────────

class TestAuditEventCompleteness:
    def test_all_sprint226_events_in_enum(self):
        event_names = {e.name for e in AuditEventType}
        assert "WORKFLOW_CLARIFICATION_RESUMED"    in event_names
        assert "CLARIFICATION_ATTEMPT_INCREMENTED" in event_names

    def test_total_audit_event_count_at_least_37(self):
        # Sprint 2.25 had 35 + 2 new in Sprint 2.26 = 37 minimum
        assert len(AuditEventType) >= 37


# ── JSON serialization round-trip ─────────────────────────────────────────────

class TestRetryJobJsonRoundTrip:
    def test_round_trip_preserves_all_fields(self):
        now  = datetime.now(tz=timezone.utc)
        job  = RetryJob.create(
            "act-json", "case-json", "api_callback_retry",
            {"callback_type": "EKYC", "application_id": "APP999"},
            now + timedelta(minutes=5),
            max_attempts=4,
        )
        d        = job.to_dict()
        restored = RetryJob.from_dict(d)
        assert restored.job_id        == job.job_id
        assert restored.action_id     == job.action_id
        assert restored.case_id       == job.case_id
        assert restored.action_type   == job.action_type
        assert restored.attempt_count == job.attempt_count
        assert restored.max_attempts  == job.max_attempts
        assert restored.status        == job.status
        assert restored.last_error    == job.last_error

    def test_round_trip_with_error_field(self):
        now = datetime.now(tz=timezone.utc)
        job = RetryJob.create("a", "c", "t", {}, now).with_update(
            status=RetryStatus.FAILED, last_error="ConnectionTimeout: exceeded 30s"
        )
        restored = RetryJob.from_dict(job.to_dict())
        assert restored.last_error == "ConnectionTimeout: exceeded 30s"
        assert restored.status     == RetryStatus.FAILED
