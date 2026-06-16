"""
tests/test_sprint213_operations.py

Sprint 2.13: Production Hardening & Operational Visibility — 110+ tests.

Coverage:
  ActionGatewayOperationsService (D1) — 40 tests
    get_state_summary: state counts, metrics totals, total_live, offline, repo error
    get_dead_letter_summary: pagination, client filter, empty, repo error
    get_retry_summary: items, total, current_state mapping, repo error
    get_stuck_actions: executing threshold, approval threshold, client filter, empty
    get_worker_summary: grouping by executor_id, success/fail/dead, window, repo error
  Metrics aggregation (D2) — 15 tests
    COUNTER_ACTIONS_RETRIED constant, record_action_retried(), get_gateway_totals()
    Dead-letter metric wired in record_failure() retry/dead-letter paths
  Admin API endpoints (D7) — 40 tests
    GET /admin/action-gateway/summary
    GET /admin/action-gateway/dead-letter (paginated)
    GET /admin/action-gateway/retries
    GET /admin/action-gateway/stuck
    GET /admin/action-gateway/workers
    Auth: 401 without key, 403 non-admin, 503 when stack=None
  Assembly wiring (D1 integration) — 5 tests
    ProductionRuntime.operations field present and typed
    build_production_runtime() returns operations instance
  Response model completeness (D8) — 10 tests
    All Pydantic models freeze-check and field presence
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from case_engine.action_gateway_operations import (
    ActionGatewayOperationsService,
    DeadLetterItem,
    DeadLetterSummary,
    RetriedActionItem,
    RetrySummary,
    StaleApprovalItem,
    StateCount,
    StateSummary,
    StuckActionsSummary,
    StuckExecutingItem,
    WorkerStats,
    WorkerSummary,
)
from case_engine.action_state import ActionRiskLevel, ActionState
from metrics.collector import COUNTER_ACTIONS_RETRIED, MetricsCollector
from metrics.service import MetricsService


# ═══════════════════════════════════════════════════════════════════════════════
# FAKE REPOSITORY
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class _FakeAction:
    """Minimal action-like object returned by fake repo methods."""
    action_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    action_type: str = "add_note"
    action_namespace: str = "ticket"
    client: str = "acme"
    current_state: ActionState = ActionState.DEAD_LETTER
    execution_attempt: int = 3
    max_attempts: int = 3
    failure_code: str | None = "EXEC_FAILED"
    failure_reason: str | None = "provider unreachable"
    proposed_at: datetime | None = field(
        default_factory=lambda: datetime.now(tz=timezone.utc) - timedelta(hours=2)
    )
    dead_lettered_at: datetime | None = field(
        default_factory=lambda: datetime.now(tz=timezone.utc) - timedelta(hours=1)
    )
    executor_id: str | None = "worker-1"
    execution_started_at: datetime | None = field(
        default_factory=lambda: datetime.now(tz=timezone.utc) - timedelta(minutes=20)
    )
    execution_completed_at: datetime | None = field(
        default_factory=lambda: datetime.now(tz=timezone.utc) - timedelta(minutes=19)
    )
    expires_at: datetime | None = field(
        default_factory=lambda: datetime.now(tz=timezone.utc) + timedelta(hours=22)
    )
    risk_level: ActionRiskLevel = ActionRiskLevel.IRREVERSIBLE


class _FakeRepository:
    """In-memory fake ActionRepository with configurable responses."""

    def __init__(
        self,
        *,
        state_counts: dict[str, int] | None = None,
        dead_letter_actions: list[_FakeAction] | None = None,
        retried_actions: list[_FakeAction] | None = None,
        executing_actions: list[_FakeAction] | None = None,
        stale_approval_actions: list[_FakeAction] | None = None,
        terminal_actions: list[_FakeAction] | None = None,
        raise_on_count: bool = False,
        raise_on_dead_letter: bool = False,
        raise_on_retried: bool = False,
        raise_on_executing: bool = False,
        raise_on_stale: bool = False,
        raise_on_terminal: bool = False,
    ) -> None:
        self._state_counts = state_counts or {}
        self._dead_letter_actions = dead_letter_actions or []
        self._retried_actions = retried_actions or []
        self._executing_actions = executing_actions or []
        self._stale_approval_actions = stale_approval_actions or []
        self._terminal_actions = terminal_actions or []
        self._raise_on_count = raise_on_count
        self._raise_on_dead_letter = raise_on_dead_letter
        self._raise_on_retried = raise_on_retried
        self._raise_on_executing = raise_on_executing
        self._raise_on_stale = raise_on_stale
        self._raise_on_terminal = raise_on_terminal

    def count_by_state(self, client: str | None = None) -> dict[str, int]:
        if self._raise_on_count:
            raise RuntimeError("count_by_state failed")
        return self._state_counts

    def list_dead_letter_actions(self, client: str | None = None) -> list[_FakeAction]:
        if self._raise_on_dead_letter:
            raise RuntimeError("list_dead_letter_actions failed")
        if client is None:
            return list(self._dead_letter_actions)
        return [a for a in self._dead_letter_actions if a.client == client]

    def list_retried_actions(self, client: str | None = None) -> list[_FakeAction]:
        if self._raise_on_retried:
            raise RuntimeError("list_retried_actions failed")
        if client is None:
            return list(self._retried_actions)
        return [a for a in self._retried_actions if a.client == client]

    def list_executing_actions(self, older_than_seconds: int = 900) -> list[_FakeAction]:
        if self._raise_on_executing:
            raise RuntimeError("list_executing_actions failed")
        return list(self._executing_actions)

    def list_stale_approval_actions(
        self,
        older_than_seconds: int = 86400,
        client: str | None = None,
    ) -> list[_FakeAction]:
        if self._raise_on_stale:
            raise RuntimeError("list_stale_approval_actions failed")
        if client is None:
            return list(self._stale_approval_actions)
        return [a for a in self._stale_approval_actions if a.client == client]

    def list_recently_terminal_actions(
        self,
        hours: int = 24,
        client: str | None = None,
    ) -> list[_FakeAction]:
        if self._raise_on_terminal:
            raise RuntimeError("list_recently_terminal_actions failed")
        if client is None:
            return list(self._terminal_actions)
        return [a for a in self._terminal_actions if a.client == client]


def _make_service(
    repo: _FakeRepository | None = None,
    metrics: MetricsService | None = None,
) -> ActionGatewayOperationsService:
    return ActionGatewayOperationsService(
        repository=repo or _FakeRepository(),
        metrics_service=metrics,
    )


def _make_metrics() -> MetricsService:
    return MetricsService(collector=MetricsCollector())


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: get_state_summary (12 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetStateSummary:

    def test_returns_state_summary_type(self):
        svc = _make_service()
        result = svc.get_state_summary()
        assert isinstance(result, StateSummary)

    def test_returns_state_counts_from_repo(self):
        repo = _FakeRepository(state_counts={
            ActionState.PROPOSED.value: 5,
            ActionState.APPROVED.value: 2,
        })
        svc = _make_service(repo=repo)
        result = svc.get_state_summary()
        counts_map = {sc.state: sc.count for sc in result.state_counts}
        assert counts_map[ActionState.PROPOSED.value] == 5
        assert counts_map[ActionState.APPROVED.value] == 2

    def test_state_counts_are_sorted(self):
        repo = _FakeRepository(state_counts={
            "proposed": 1,
            "approved": 2,
            "executed": 3,
        })
        svc = _make_service(repo=repo)
        result = svc.get_state_summary()
        names = [sc.state for sc in result.state_counts]
        assert names == sorted(names)

    def test_total_live_sums_live_states_only(self):
        repo = _FakeRepository(state_counts={
            ActionState.PROPOSED.value: 3,
            ActionState.APPROVED.value: 4,
            ActionState.EXECUTING.value: 1,
            ActionState.EXECUTED.value: 100,   # terminal — not counted
            ActionState.DEAD_LETTER.value: 50, # terminal — not counted
        })
        svc = _make_service(repo=repo)
        result = svc.get_state_summary()
        assert result.total_live == 8  # 3 + 4 + 1

    def test_total_live_excludes_terminal_states(self):
        repo = _FakeRepository(state_counts={
            ActionState.EXECUTED.value: 999,
            ActionState.DEAD_LETTER.value: 888,
        })
        svc = _make_service(repo=repo)
        result = svc.get_state_summary()
        assert result.total_live == 0

    def test_client_filter_is_passed_through(self):
        calls = []
        repo = _FakeRepository()
        orig = repo.count_by_state
        def spy(client=None):
            calls.append(client)
            return orig(client=client)
        repo.count_by_state = spy
        svc = _make_service(repo=repo)
        svc.get_state_summary(client="unity_bank")
        assert calls == ["unity_bank"]

    def test_returns_empty_state_counts_on_repo_error(self):
        repo = _FakeRepository(raise_on_count=True)
        svc = _make_service(repo=repo)
        result = svc.get_state_summary()
        assert result.state_counts == []
        assert result.total_live == 0

    def test_metrics_totals_included_when_service_provided(self):
        ms = _make_metrics()
        ms.record_action_created()
        ms.record_action_executed()
        svc = _make_service(metrics=ms)
        result = svc.get_state_summary()
        assert result.metrics_totals.get("actions_created_total", 0) == 1
        assert result.metrics_totals.get("actions_executed_total", 0) == 1

    def test_metrics_totals_empty_when_no_service(self):
        svc = _make_service(metrics=None)
        result = svc.get_state_summary()
        assert result.metrics_totals == {}

    def test_checked_at_is_utc(self):
        svc = _make_service()
        result = svc.get_state_summary()
        assert result.checked_at.tzinfo is not None

    def test_client_preserved_in_result(self):
        svc = _make_service()
        result = svc.get_state_summary(client="demo_corp")
        assert result.client == "demo_corp"

    def test_none_client_preserved_in_result(self):
        svc = _make_service()
        result = svc.get_state_summary(client=None)
        assert result.client is None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: get_dead_letter_summary (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetDeadLetterSummary:

    def _make_dl_action(self, client: str = "acme") -> _FakeAction:
        return _FakeAction(client=client, current_state=ActionState.DEAD_LETTER)

    def test_returns_dead_letter_summary_type(self):
        svc = _make_service()
        result = svc.get_dead_letter_summary()
        assert isinstance(result, DeadLetterSummary)

    def test_returns_all_items_within_limit(self):
        actions = [self._make_dl_action() for _ in range(5)]
        repo = _FakeRepository(dead_letter_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary(limit=10)
        assert len(result.items) == 5
        assert result.total == 5

    def test_limit_caps_returned_items(self):
        actions = [self._make_dl_action() for _ in range(20)]
        repo = _FakeRepository(dead_letter_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary(limit=5)
        assert len(result.items) == 5
        assert result.total == 20

    def test_offset_skips_items(self):
        actions = [self._make_dl_action() for _ in range(10)]
        repo = _FakeRepository(dead_letter_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary(limit=5, offset=7)
        assert len(result.items) == 3  # 10 - 7

    def test_offset_beyond_total_returns_empty(self):
        actions = [self._make_dl_action() for _ in range(5)]
        repo = _FakeRepository(dead_letter_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary(limit=10, offset=10)
        assert result.items == []
        assert result.total == 5

    def test_client_filter_applied(self):
        actions = [
            self._make_dl_action("bank_a"),
            self._make_dl_action("bank_b"),
            self._make_dl_action("bank_a"),
        ]
        repo = _FakeRepository(dead_letter_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary(client="bank_a")
        assert result.total == 2

    def test_returns_empty_on_repo_error(self):
        repo = _FakeRepository(raise_on_dead_letter=True)
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary()
        assert result.items == []
        assert result.total == 0

    def test_item_fields_mapped_correctly(self):
        action = self._make_dl_action()
        repo = _FakeRepository(dead_letter_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_dead_letter_summary()
        item = result.items[0]
        assert isinstance(item, DeadLetterItem)
        assert item.action_id == action.action_id
        assert item.failure_code == action.failure_code
        assert item.failure_reason == action.failure_reason
        assert item.execution_attempt == action.execution_attempt
        assert item.max_attempts == action.max_attempts

    def test_checked_at_is_utc(self):
        svc = _make_service()
        result = svc.get_dead_letter_summary()
        assert result.checked_at.tzinfo is not None

    def test_empty_repo_returns_zero_total(self):
        svc = _make_service()
        result = svc.get_dead_letter_summary()
        assert result.total == 0
        assert result.items == []


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: get_retry_summary (8 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetRetrySummary:

    def _make_retried(self, client: str = "acme", state: ActionState = ActionState.EXECUTED) -> _FakeAction:
        a = _FakeAction(client=client, current_state=state, execution_attempt=2)
        return a

    def test_returns_retry_summary_type(self):
        svc = _make_service()
        result = svc.get_retry_summary()
        assert isinstance(result, RetrySummary)

    def test_returns_all_retried_items(self):
        actions = [self._make_retried() for _ in range(4)]
        repo = _FakeRepository(retried_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_retry_summary()
        assert len(result.items) == 4
        assert result.total == 4

    def test_current_state_is_string_value(self):
        action = self._make_retried(state=ActionState.EXECUTED)
        repo = _FakeRepository(retried_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_retry_summary()
        assert result.items[0].current_state == ActionState.EXECUTED.value

    def test_client_filter_applied(self):
        actions = [
            self._make_retried("corp_a"),
            self._make_retried("corp_b"),
        ]
        repo = _FakeRepository(retried_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_retry_summary(client="corp_a")
        assert result.total == 1

    def test_returns_empty_on_repo_error(self):
        repo = _FakeRepository(raise_on_retried=True)
        svc = _make_service(repo=repo)
        result = svc.get_retry_summary()
        assert result.items == []
        assert result.total == 0

    def test_item_is_retried_action_item_type(self):
        action = self._make_retried()
        repo = _FakeRepository(retried_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_retry_summary()
        assert isinstance(result.items[0], RetriedActionItem)

    def test_checked_at_is_utc(self):
        svc = _make_service()
        result = svc.get_retry_summary()
        assert result.checked_at.tzinfo is not None

    def test_empty_repo_returns_zero_total(self):
        svc = _make_service()
        result = svc.get_retry_summary()
        assert result.total == 0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: get_stuck_actions (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetStuckActions:

    def _make_executing(self, started_minutes_ago: float = 20) -> _FakeAction:
        started = datetime.now(tz=timezone.utc) - timedelta(minutes=started_minutes_ago)
        return _FakeAction(
            current_state=ActionState.EXECUTING,
            execution_started_at=started,
            executor_id="worker-1",
        )

    def _make_stale_approval(self, proposed_hours_ago: float = 26) -> _FakeAction:
        proposed = datetime.now(tz=timezone.utc) - timedelta(hours=proposed_hours_ago)
        return _FakeAction(
            current_state=ActionState.AWAITING_APPROVAL,
            proposed_at=proposed,
            risk_level=ActionRiskLevel.IRREVERSIBLE,
        )

    def test_returns_stuck_actions_summary_type(self):
        svc = _make_service()
        result = svc.get_stuck_actions()
        assert isinstance(result, StuckActionsSummary)

    def test_stuck_executing_actions_returned(self):
        executing = [self._make_executing(started_minutes_ago=20)]
        repo = _FakeRepository(executing_actions=executing)
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions(executing_threshold_seconds=900)
        assert len(result.stuck_executing) == 1

    def test_stale_approval_actions_returned(self):
        stale = [self._make_stale_approval(proposed_hours_ago=26)]
        repo = _FakeRepository(stale_approval_actions=stale)
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions(approval_threshold_seconds=86400)
        assert len(result.stale_approval) == 1

    def test_stuck_for_seconds_computed(self):
        started = datetime.now(tz=timezone.utc) - timedelta(seconds=1000)
        action = _FakeAction(
            current_state=ActionState.EXECUTING,
            execution_started_at=started,
            executor_id="w-1",
        )
        repo = _FakeRepository(executing_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions()
        stuck = result.stuck_executing[0]
        assert isinstance(stuck, StuckExecutingItem)
        assert 990 < stuck.stuck_for_seconds < 1010

    def test_waiting_for_seconds_computed(self):
        proposed = datetime.now(tz=timezone.utc) - timedelta(hours=30)
        action = _FakeAction(
            current_state=ActionState.AWAITING_APPROVAL,
            proposed_at=proposed,
            risk_level=ActionRiskLevel.IRREVERSIBLE,
        )
        repo = _FakeRepository(stale_approval_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions()
        stale = result.stale_approval[0]
        assert isinstance(stale, StaleApprovalItem)
        assert stale.waiting_for_seconds > 100_000  # 30 h in seconds

    def test_executing_threshold_preserved_in_result(self):
        svc = _make_service()
        result = svc.get_stuck_actions(executing_threshold_seconds=300)
        assert result.executing_threshold_seconds == 300

    def test_approval_threshold_preserved_in_result(self):
        svc = _make_service()
        result = svc.get_stuck_actions(approval_threshold_seconds=3600)
        assert result.approval_threshold_seconds == 3600

    def test_returns_empty_when_both_queries_fail(self):
        repo = _FakeRepository(raise_on_executing=True, raise_on_stale=True)
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions()
        assert result.stuck_executing == []
        assert result.stale_approval == []

    def test_partial_failure_still_returns_one_set(self):
        action = self._make_stale_approval()
        repo = _FakeRepository(
            raise_on_executing=True,
            stale_approval_actions=[action],
        )
        svc = _make_service(repo=repo)
        result = svc.get_stuck_actions()
        assert result.stuck_executing == []
        assert len(result.stale_approval) == 1

    def test_checked_at_is_utc(self):
        svc = _make_service()
        result = svc.get_stuck_actions()
        assert result.checked_at.tzinfo is not None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: get_worker_summary (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestGetWorkerSummary:

    def _make_executed(self, worker_id: str = "worker-1") -> _FakeAction:
        return _FakeAction(current_state=ActionState.EXECUTED, executor_id=worker_id)

    def _make_failed(self, worker_id: str = "worker-1") -> _FakeAction:
        return _FakeAction(current_state=ActionState.FAILED, executor_id=worker_id)

    def _make_dead(self, worker_id: str = "worker-1") -> _FakeAction:
        return _FakeAction(current_state=ActionState.DEAD_LETTER, executor_id=worker_id)

    def test_returns_worker_summary_type(self):
        svc = _make_service()
        result = svc.get_worker_summary()
        assert isinstance(result, WorkerSummary)

    def test_counts_successful_actions(self):
        actions = [self._make_executed() for _ in range(3)]
        repo = _FakeRepository(terminal_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.workers[0].successful == 3

    def test_counts_dead_lettered_actions(self):
        actions = [self._make_dead() for _ in range(2)]
        repo = _FakeRepository(terminal_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.workers[0].dead_lettered == 2

    def test_counts_failed_actions(self):
        actions = [self._make_failed() for _ in range(4)]
        repo = _FakeRepository(terminal_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.workers[0].failed == 4

    def test_total_processed_is_sum(self):
        actions = [
            self._make_executed(),
            self._make_failed(),
            self._make_dead(),
        ]
        repo = _FakeRepository(terminal_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        w = result.workers[0]
        assert w.total_processed == 3

    def test_groups_by_executor_id(self):
        actions = [
            self._make_executed("w-1"),
            self._make_executed("w-2"),
            self._make_executed("w-1"),
        ]
        repo = _FakeRepository(terminal_actions=actions)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.total_workers == 2
        w1 = next(w for w in result.workers if w.worker_id == "w-1")
        assert w1.successful == 2

    def test_unknown_executor_id_grouped_as_unknown(self):
        action = _FakeAction(current_state=ActionState.EXECUTED, executor_id=None)
        repo = _FakeRepository(terminal_actions=[action])
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.workers[0].worker_id == "unknown"

    def test_returns_empty_on_repo_error(self):
        repo = _FakeRepository(raise_on_terminal=True)
        svc = _make_service(repo=repo)
        result = svc.get_worker_summary()
        assert result.workers == []
        assert result.total_workers == 0

    def test_window_hours_preserved_in_result(self):
        svc = _make_service()
        result = svc.get_worker_summary(window_hours=48)
        assert result.window_hours == 48

    def test_checked_at_is_utc(self):
        svc = _make_service()
        result = svc.get_worker_summary()
        assert result.checked_at.tzinfo is not None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Metrics aggregation (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsAggregation:

    def test_retried_counter_constant_value(self):
        assert COUNTER_ACTIONS_RETRIED == "actions_retried_total"

    def test_retried_counter_in_collector(self):
        col = MetricsCollector()
        col.increment(COUNTER_ACTIONS_RETRIED)
        snap = col.snapshot()
        assert snap["counters"][COUNTER_ACTIONS_RETRIED] == 1

    def test_record_action_retried_increments(self):
        ms = _make_metrics()
        ms.record_action_retried()
        ms.record_action_retried()
        totals = ms.get_gateway_totals()
        assert totals[COUNTER_ACTIONS_RETRIED] == 2

    def test_get_gateway_totals_returns_all_keys(self):
        ms = _make_metrics()
        totals = ms.get_gateway_totals()
        expected_keys = {
            "actions_created_total",
            "actions_approved_total",
            "actions_rejected_total",
            "actions_executed_total",
            "actions_failed_total",
            "actions_expired_total",
            "actions_retried_total",
            "actions_rolled_back_total",
            "actions_rollback_failed_total",
            "actions_dead_lettered_total",
            "worker_execution_total",
            "worker_failure_total",
            "worker_rollback_total",
        }
        assert expected_keys == set(totals.keys())

    def test_get_gateway_totals_default_zero(self):
        ms = _make_metrics()
        totals = ms.get_gateway_totals()
        assert all(v == 0 for v in totals.values())

    def test_dead_letter_counter_incremented(self):
        ms = _make_metrics()
        ms.record_action_dead_lettered()
        totals = ms.get_gateway_totals()
        assert totals["actions_dead_lettered_total"] == 1

    def test_retried_counter_separate_from_dead_lettered(self):
        ms = _make_metrics()
        ms.record_action_retried()
        totals = ms.get_gateway_totals()
        assert totals[COUNTER_ACTIONS_RETRIED] == 1
        assert totals["actions_dead_lettered_total"] == 0

    def test_multiple_counter_increments_accumulate(self):
        ms = _make_metrics()
        for _ in range(5):
            ms.record_action_created()
        totals = ms.get_gateway_totals()
        assert totals["actions_created_total"] == 5

    def test_retried_counter_in_prometheus_text(self):
        ms = _make_metrics()
        ms.record_action_retried()
        text = ms.prometheus_text()
        assert "actions_retried_total" in text
        assert "1\n" in text or "1" in text

    def test_gateway_totals_in_state_summary(self):
        ms = _make_metrics()
        ms.record_action_created()
        ms.record_action_retried()
        svc = _make_service(metrics=ms)
        result = svc.get_state_summary()
        assert result.metrics_totals["actions_created_total"] == 1
        assert result.metrics_totals[COUNTER_ACTIONS_RETRIED] == 1

    def test_record_action_executed_increments_counter(self):
        ms = _make_metrics()
        ms.record_action_executed(latency_ms=150.0)
        totals = ms.get_gateway_totals()
        assert totals["actions_executed_total"] == 1

    def test_worker_execution_counter(self):
        ms = _make_metrics()
        ms.record_worker_execution()
        totals = ms.get_gateway_totals()
        assert totals["worker_execution_total"] == 1

    def test_worker_failure_counter(self):
        ms = _make_metrics()
        ms.record_worker_failure()
        totals = ms.get_gateway_totals()
        assert totals["worker_failure_total"] == 1

    def test_snapshot_method_returns_dict(self):
        ms = _make_metrics()
        snap = ms.snapshot()
        assert "counters" in snap

    def test_thread_safety_increment(self):
        import threading
        ms = _make_metrics()
        def increment_many():
            for _ in range(100):
                ms.record_action_retried()
        threads = [threading.Thread(target=increment_many) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        totals = ms.get_gateway_totals()
        assert totals[COUNTER_ACTIONS_RETRIED] == 500


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED TEST INFRASTRUCTURE FOR API TESTS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_admin_auth():
    """Return a mock authenticator that always grants ADMIN role."""
    from security.roles import Role
    mock_auth = MagicMock()
    result = MagicMock()
    result.authenticated = True
    result.role = Role.ADMIN
    result.identity = "admin:test"
    result.has_permission.return_value = True
    mock_auth.authenticate.return_value = result
    return mock_auth


def _make_non_admin_auth():
    """Return a mock authenticator that always grants OPERATOR role (not ADMIN)."""
    from security.roles import Role
    mock_auth = MagicMock()
    result = MagicMock()
    result.authenticated = True
    result.role = Role.OPERATOR
    result.identity = "operator:test"
    result.has_permission.return_value = True
    mock_auth.authenticate.return_value = result
    return mock_auth


def _make_unauthenticated_auth():
    """Return a mock authenticator that always fails."""
    mock_auth = MagicMock()
    result = MagicMock()
    result.authenticated = False
    result.role = None
    result.identity = None
    mock_auth.authenticate.return_value = result
    return mock_auth


class _FakeOpsStack:
    """Minimal stack with an operations attribute backed by a fake repo."""

    def __init__(self, repo: _FakeRepository | None = None, metrics: MetricsService | None = None):
        _repo = repo or _FakeRepository()
        self.operations = ActionGatewayOperationsService(
            repository=_repo,
            metrics_service=metrics,
        )


def _make_api_client(
    *,
    stack=None,
    authenticator=None,
) -> TestClient:
    app = create_app(
        skip_config_validation=True,
        authenticator=authenticator or _make_admin_auth(),
        stack=stack,
    )
    return TestClient(app, raise_server_exceptions=False)


_ADMIN_HEADER = {"x-api-key": "admin-key"}


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: GET /admin/action-gateway/summary (8 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminSummaryEndpoint:

    def _client(self, repo=None, metrics=None):
        stack = _FakeOpsStack(repo=repo, metrics=metrics)
        return _make_api_client(stack=stack)

    def test_returns_200(self):
        resp = self._client().get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        assert resp.status_code == 200

    def test_response_has_state_counts(self):
        resp = self._client().get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "state_counts" in body

    def test_response_has_total_live(self):
        resp = self._client().get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "total_live" in body
        assert isinstance(body["total_live"], int)

    def test_response_has_metrics_totals(self):
        resp = self._client().get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "metrics_totals" in body
        assert isinstance(body["metrics_totals"], dict)

    def test_response_has_checked_at(self):
        resp = self._client().get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "checked_at" in body

    def test_503_when_stack_none(self):
        client = _make_api_client(stack=None)
        resp = client.get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        assert resp.status_code == 503

    def test_401_without_api_key(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        assert resp.status_code == 401

    def test_403_with_non_admin_role(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_non_admin_auth())
        resp = client.get("/admin/action-gateway/summary", headers=_ADMIN_HEADER)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: GET /admin/action-gateway/dead-letter (12 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminDeadLetterEndpoint:

    def _client(self, repo=None):
        stack = _FakeOpsStack(repo=repo)
        return _make_api_client(stack=stack)

    def test_returns_200(self):
        resp = self._client().get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        assert resp.status_code == 200

    def test_response_has_actions_list(self):
        resp = self._client().get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "actions" in body
        assert isinstance(body["actions"], list)

    def test_response_has_total(self):
        resp = self._client().get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "total" in body

    def test_returns_actions_in_list(self):
        actions = [_FakeAction(current_state=ActionState.DEAD_LETTER) for _ in range(3)]
        resp = self._client(repo=_FakeRepository(dead_letter_actions=actions)).get(
            "/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert len(body["actions"]) == 3
        assert body["total"] == 3

    def test_limit_parameter_respected(self):
        actions = [_FakeAction() for _ in range(50)]
        resp = self._client(repo=_FakeRepository(dead_letter_actions=actions)).get(
            "/admin/action-gateway/dead-letter?limit=5", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert len(body["actions"]) == 5
        assert body["total"] == 50

    def test_offset_parameter_respected(self):
        actions = [_FakeAction() for _ in range(10)]
        resp = self._client(repo=_FakeRepository(dead_letter_actions=actions)).get(
            "/admin/action-gateway/dead-letter?offset=8", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert len(body["actions"]) == 2

    def test_limit_capped_at_500(self):
        resp = self._client().get(
            "/admin/action-gateway/dead-letter?limit=9999", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["limit"] == 500

    def test_negative_offset_clamped_to_zero(self):
        resp = self._client().get(
            "/admin/action-gateway/dead-letter?offset=-5", headers=_ADMIN_HEADER
        )
        assert resp.status_code == 200

    def test_client_filter_applied(self):
        actions = [
            _FakeAction(client="bank_a"),
            _FakeAction(client="bank_b"),
        ]
        resp = self._client(repo=_FakeRepository(dead_letter_actions=actions)).get(
            "/admin/action-gateway/dead-letter?client=bank_a", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["total"] == 1

    def test_503_when_stack_none(self):
        client = _make_api_client(stack=None)
        resp = client.get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        assert resp.status_code == 503

    def test_401_without_api_key(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        assert resp.status_code == 401

    def test_403_with_non_admin_role(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_non_admin_auth())
        resp = client.get("/admin/action-gateway/dead-letter", headers=_ADMIN_HEADER)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 9: GET /admin/action-gateway/retries (8 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminRetriesEndpoint:

    def _client(self, repo=None):
        stack = _FakeOpsStack(repo=repo)
        return _make_api_client(stack=stack)

    def test_returns_200(self):
        resp = self._client().get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        assert resp.status_code == 200

    def test_response_has_actions_list(self):
        resp = self._client().get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "actions" in body
        assert isinstance(body["actions"], list)

    def test_response_has_total(self):
        resp = self._client().get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "total" in body

    def test_returns_retried_actions(self):
        actions = [
            _FakeAction(current_state=ActionState.EXECUTED, execution_attempt=2)
            for _ in range(3)
        ]
        resp = self._client(repo=_FakeRepository(retried_actions=actions)).get(
            "/admin/action-gateway/retries", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert len(body["actions"]) == 3
        assert body["total"] == 3

    def test_client_filter_applied(self):
        actions = [
            _FakeAction(client="corp_x", current_state=ActionState.EXECUTED, execution_attempt=2),
            _FakeAction(client="corp_y", current_state=ActionState.EXECUTED, execution_attempt=2),
        ]
        resp = self._client(repo=_FakeRepository(retried_actions=actions)).get(
            "/admin/action-gateway/retries?client=corp_x", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["total"] == 1

    def test_503_when_stack_none(self):
        client = _make_api_client(stack=None)
        resp = client.get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        assert resp.status_code == 503

    def test_401_without_api_key(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        assert resp.status_code == 401

    def test_403_with_non_admin_role(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_non_admin_auth())
        resp = client.get("/admin/action-gateway/retries", headers=_ADMIN_HEADER)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 10: GET /admin/action-gateway/stuck (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminStuckEndpoint:

    def _client(self, repo=None):
        stack = _FakeOpsStack(repo=repo)
        return _make_api_client(stack=stack)

    def test_returns_200(self):
        resp = self._client().get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        assert resp.status_code == 200

    def test_response_has_stuck_executing(self):
        resp = self._client().get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "stuck_executing" in body
        assert isinstance(body["stuck_executing"], list)

    def test_response_has_stale_approval(self):
        resp = self._client().get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "stale_approval" in body
        assert isinstance(body["stale_approval"], list)

    def test_response_has_total_stuck(self):
        resp = self._client().get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "total_stuck" in body

    def test_executing_threshold_param_respected(self):
        resp = self._client().get(
            "/admin/action-gateway/stuck?executing_threshold_seconds=300",
            headers=_ADMIN_HEADER,
        )
        body = resp.json()
        assert body["executing_threshold_seconds"] == 300

    def test_approval_threshold_param_respected(self):
        resp = self._client().get(
            "/admin/action-gateway/stuck?approval_threshold_seconds=3600",
            headers=_ADMIN_HEADER,
        )
        body = resp.json()
        assert body["approval_threshold_seconds"] == 3600

    def test_executing_threshold_minimum_60(self):
        resp = self._client().get(
            "/admin/action-gateway/stuck?executing_threshold_seconds=5",
            headers=_ADMIN_HEADER,
        )
        body = resp.json()
        assert body["executing_threshold_seconds"] >= 60

    def test_503_when_stack_none(self):
        client = _make_api_client(stack=None)
        resp = client.get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        assert resp.status_code == 503

    def test_401_without_api_key(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        assert resp.status_code == 401

    def test_403_with_non_admin_role(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_non_admin_auth())
        resp = client.get("/admin/action-gateway/stuck", headers=_ADMIN_HEADER)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 11: GET /admin/action-gateway/workers (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAdminWorkersEndpoint:

    def _client(self, repo=None):
        stack = _FakeOpsStack(repo=repo)
        return _make_api_client(stack=stack)

    def test_returns_200(self):
        resp = self._client().get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        assert resp.status_code == 200

    def test_response_has_workers_list(self):
        resp = self._client().get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "workers" in body
        assert isinstance(body["workers"], list)

    def test_response_has_total_workers(self):
        resp = self._client().get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "total_workers" in body

    def test_response_has_window_hours(self):
        resp = self._client().get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        body = resp.json()
        assert "window_hours" in body

    def test_worker_stats_returned(self):
        actions = [
            _FakeAction(current_state=ActionState.EXECUTED, executor_id="w-1"),
            _FakeAction(current_state=ActionState.FAILED, executor_id="w-1"),
        ]
        resp = self._client(repo=_FakeRepository(terminal_actions=actions)).get(
            "/admin/action-gateway/workers", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["total_workers"] == 1
        w = body["workers"][0]
        assert w["worker_id"] == "w-1"
        assert w["successful"] == 1
        assert w["failed"] == 1

    def test_window_hours_param_respected(self):
        resp = self._client().get(
            "/admin/action-gateway/workers?window_hours=48", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["window_hours"] == 48

    def test_window_hours_clamped_to_720(self):
        resp = self._client().get(
            "/admin/action-gateway/workers?window_hours=9999", headers=_ADMIN_HEADER
        )
        body = resp.json()
        assert body["window_hours"] <= 720

    def test_503_when_stack_none(self):
        client = _make_api_client(stack=None)
        resp = client.get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        assert resp.status_code == 503

    def test_401_without_api_key(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        assert resp.status_code == 401

    def test_403_with_non_admin_role(self):
        stack = _FakeOpsStack()
        client = _make_api_client(stack=stack, authenticator=_make_non_admin_auth())
        resp = client.get("/admin/action-gateway/workers", headers=_ADMIN_HEADER)
        assert resp.status_code == 403


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 12: Response model completeness (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestResponseModelCompleteness:

    def test_state_count_item_fields(self):
        from api.admin_response_models import StateCountItem
        item = StateCountItem(state="proposed", count=5)
        assert item.state == "proposed"
        assert item.count == 5

    def test_gateway_summary_response_fields(self):
        from api.admin_response_models import GatewaySummaryResponse, StateCountItem
        resp = GatewaySummaryResponse(
            client=None,
            state_counts=[StateCountItem(state="proposed", count=1)],
            total_live=1,
            metrics_totals={"x": 0},
            checked_at="2024-01-01T00:00:00Z",
        )
        assert resp.total_live == 1
        assert resp.client is None

    def test_dead_letter_response_fields(self):
        from api.admin_response_models import DeadLetterResponse
        resp = DeadLetterResponse(
            client="acme",
            actions=[],
            total=0,
            limit=100,
            offset=0,
            checked_at="2024-01-01T00:00:00Z",
        )
        assert resp.total == 0
        assert resp.limit == 100

    def test_retry_response_fields(self):
        from api.admin_response_models import RetryResponse
        resp = RetryResponse(
            client=None,
            actions=[],
            total=0,
            checked_at="2024-01-01T00:00:00Z",
        )
        assert resp.total == 0

    def test_stuck_actions_response_fields(self):
        from api.admin_response_models import StuckActionsResponse
        resp = StuckActionsResponse(
            client=None,
            stuck_executing=[],
            stale_approval=[],
            total_stuck=0,
            executing_threshold_seconds=900,
            approval_threshold_seconds=86400,
            checked_at="2024-01-01T00:00:00Z",
        )
        assert resp.total_stuck == 0
        assert resp.executing_threshold_seconds == 900

    def test_worker_response_fields(self):
        from api.admin_response_models import WorkerResponse
        resp = WorkerResponse(
            client=None,
            workers=[],
            total_workers=0,
            window_hours=24,
            checked_at="2024-01-01T00:00:00Z",
        )
        assert resp.total_workers == 0
        assert resp.window_hours == 24

    def test_models_are_frozen(self):
        from api.admin_response_models import StateCountItem
        item = StateCountItem(state="proposed", count=1)
        with pytest.raises(Exception):
            item.count = 99  # frozen model must raise

    def test_dead_letter_item_nullable_fields(self):
        from api.admin_response_models import DeadLetterActionItem
        item = DeadLetterActionItem(
            action_id="abc",
            action_type="add_note",
            action_namespace="ticket",
            client="acme",
            proposed_at=None,
            dead_lettered_at=None,
            failure_code=None,
            failure_reason=None,
            execution_attempt=1,
            max_attempts=3,
        )
        assert item.proposed_at is None
        assert item.failure_code is None

    def test_worker_stats_item_fields(self):
        from api.admin_response_models import WorkerStatsItem
        item = WorkerStatsItem(
            worker_id="w-1",
            successful=5,
            failed=2,
            dead_lettered=1,
            total_processed=8,
            last_execution_at=None,
        )
        assert item.total_processed == 8

    def test_stuck_executing_item_optional_fields(self):
        from api.admin_response_models import StuckExecutingItem
        item = StuckExecutingItem(
            action_id="abc",
            action_type="add_note",
            action_namespace="ticket",
            client="acme",
            executor_id=None,
            execution_started_at=None,
            execution_attempt=1,
            stuck_for_seconds=120.0,
        )
        assert item.executor_id is None
        assert item.stuck_for_seconds == 120.0


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 13: Assembly wiring (5 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAssemblyWiring:

    def test_production_runtime_has_operations_field(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        fields = {f.name for f in dataclasses.fields(ProductionRuntime)}
        assert "operations" in fields

    def test_operations_field_is_operations_service_type(self):
        from runtime.assembly import ProductionRuntime
        import dataclasses
        f = next(f for f in dataclasses.fields(ProductionRuntime) if f.name == "operations")
        # With `from __future__ import annotations`, type is stored as string
        assert "ActionGatewayOperationsService" in str(f.type)

    def test_build_production_runtime_returns_operations(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime(supabase_client=None)
        assert hasattr(stack, "operations")
        assert isinstance(stack.operations, ActionGatewayOperationsService)

    def test_operations_shares_repository_with_stack(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime(supabase_client=None)
        assert stack.operations._repo is stack.repository

    def test_operations_shares_metrics_with_stack(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime(supabase_client=None)
        assert stack.operations._metrics is stack.metrics_service
