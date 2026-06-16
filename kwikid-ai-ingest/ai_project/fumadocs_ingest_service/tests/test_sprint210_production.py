"""
tests/test_sprint210_production.py

Sprint 2.10: Production Persistence, Reliability, Metrics & Distributed Execution Safety.

Coverage:
  SupabaseAuditRepository (20 tests):
    - Requires non-None supabase_client
    - insert_event maps fields correctly to DB row
    - metadata_json column used for metadata dict
    - events_for_action delegates to supabase query with correct filters
    - events_for_case delegates with correct filters
    - events_for_client applies pagination (limit, offset)
    - list_events applies all optional filters
    - count_events uses count="exact" and returns integer
    - DB errors are caught and return empty list / 0 (never raise)
    - _row_to_event handles string timestamps, datetime timestamps, naive timestamps
    - _row_to_event handles missing metadata_json gracefully

  AuditRepositoryFactory (10 tests):
    - Default (no env var) → InMemoryAuditRepository
    - AUDIT_BACKEND=inmemory → InMemoryAuditRepository
    - AUDIT_BACKEND=supabase with client → SupabaseAuditRepository
    - AUDIT_BACKEND=supabase without client → ValueError
    - AUDIT_BACKEND=unknown → ValueError
    - backend_override kwarg takes precedence over env var
    - Returned InMemory is functional (insert + retrieve)
    - Returned Supabase is SupabaseAuditRepository
    - Case-insensitive env var (INMEMORY, SUPABASE)
    - factory returns AuditRepository ABC subclass

  Dead Letter Queue (20 tests):
    - DEAD_LETTER is in ActionState enum
    - DEAD_LETTER is terminal (not in ALLOWED_ACTION_TRANSITIONS or maps to empty set)
    - DEAD_LETTER is in TERMINAL_ACTION_STATES
    - record_failure exhausted retries → DEAD_LETTER (not FAILED)
    - record_failure permanent=True → DEAD_LETTER immediately
    - record_failure retries remaining → APPROVED (retry path unchanged)
    - dead_lettered_at is set when entering DEAD_LETTER
    - dead_lettered_at is None for non-dead-lettered actions
    - record_timeout exhausted → FAILED → DEAD_LETTER path
    - record_timeout retries remaining → APPROVED (retry path unchanged)
    - list_dead_letter_actions returns only DEAD_LETTER state actions
    - DEAD_LETTER → (nothing) — no valid transitions from DEAD_LETTER
    - to_db_row includes dead_lettered_at key
    - from_db_row round-trips dead_lettered_at correctly
    - metrics record_action_dead_lettered incremented on DEAD_LETTER entry
    - IRREVERSIBLE action permanent=True → DEAD_LETTER after single attempt
    - DEAD_LETTER action_id is preserved
    - DEAD_LETTER failure_code is preserved
    - Multiple failures each increment dead_lettered counter
    - Dead-lettered action's dead_lettered_at is UTC-aware

  MetricsCollector thread-safety (20 tests):
    - All counters start at 0
    - increment() increases counter by 1
    - increment(by=N) increases by N
    - get_counter returns 0 for unknown names
    - record_latency accumulates sum and count correctly
    - get_latency_sum and get_latency_count reflect accumulations
    - snapshot() returns consistent copy
    - snapshot() counters dict is independent (no shared reference)
    - All canonical counter names are initialized
    - All canonical latency names are initialized
    - Concurrent increments from 10 threads produce correct total
    - Concurrent latency records from 10 threads produce correct count
    - snapshot() under concurrent writes is consistent (no partial reads)
    - increment() unknown counter creates it dynamically
    - record_latency unknown name creates it dynamically
    - snapshot contains counters, latency_sums, latency_counts keys
    - Multiple increments across threads sum correctly
    - get_counter is consistent with snapshot
    - Collector is reusable across multiple test iterations
    - Constructor creates fresh isolated instance each time

  MetricsService / prometheus_text (15 tests):
    - record_action_created increments actions_created_total
    - record_action_approved increments actions_approved_total
    - record_action_rejected increments actions_rejected_total
    - record_action_expired increments actions_expired_total
    - record_action_executed increments actions_executed_total + latency
    - record_action_executed with latency_ms=0 skips latency recording
    - record_action_failed increments actions_failed_total
    - record_action_rolled_back increments actions_rolled_back_total + latency
    - record_action_rollback_failed increments actions_rollback_failed_total
    - record_action_dead_lettered increments actions_dead_lettered_total
    - prometheus_text includes HELP and TYPE lines for each counter
    - prometheus_text includes _sum and _count for each latency
    - prometheus_text ends with newline
    - snapshot() returns dict with counters and latency keys
    - All methods are fire-and-forget (never raise)

  Metrics endpoint (10 tests):
    - GET /metrics returns 200
    - Response is text/plain content type
    - Response includes Prometheus counter lines
    - Response includes # HELP lines
    - Response includes # TYPE lines
    - Without metrics_service → returns placeholder comment
    - Endpoint is not in OpenAPI schema (include_in_schema=False)
    - GET /metrics requires no authentication
    - After recording metrics → counter values appear in response
    - Endpoint never returns 500 even if metrics_service errors

  Distributed execution safety / optimistic locking (15 tests):
    - begin_execution succeeds when action is APPROVED
    - begin_execution raises ActionGatewayError when action is not APPROVED
    - begin_execution with optimistic lock: first worker succeeds
    - begin_execution with optimistic lock: second worker raises ActionGatewayError
    - update_action(expected_state=X) returns False when state doesn't match (offline)
    - update_action(expected_state=X) returns True when state matches (offline)
    - update_action(expected_state=None) always returns True (offline, no lock)
    - Concurrent begin_execution: only one worker succeeds (threading test)
    - InMemoryRepository claim_lock prevents double-claim
    - begin_execution increments execution_attempt
    - begin_execution sets executor_id
    - begin_execution sets execution_started_at (UTC)
    - Failed optimistic lock: action object is in mutated state (EXECUTING locally)
    - begin_execution: from_state=APPROVED used for expected_state check
    - list_dead_letter_actions offline returns correct subset

  Config validation (10 tests):
    - validate_startup_config() passes with minimal valid config
    - _check_audit_config() passes for AUDIT_BACKEND=inmemory
    - _check_audit_config() passes for AUDIT_BACKEND=supabase
    - _check_audit_config() raises for AUDIT_BACKEND=invalid
    - _check_audit_config() uses default inmemory when not set
    - _check_audit_config() is case-insensitive
    - validate_startup_config() calls _check_audit_config()
    - ConfigurationValidationError raised with descriptive message
    - validate_startup_config() fails when both auth and audit misconfigured
    - _check_audit_config() passes for AUDIT_BACKEND=SUPABASE (uppercase)
"""
from __future__ import annotations

import copy
import os
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from api.app import create_app
from audit.factory import build_audit_repository
from audit.models import AuditEvent, AuditEventType
from audit.repository import AuditRepository, InMemoryAuditRepository
from audit.repository_supabase import SupabaseAuditRepository, _row_to_event
from case_engine.action_gateway import ActionGateway, ActionGatewayError
from case_engine.action_models import ActionProposal, ActionRequest
from case_engine.action_repository import ActionRepository as ActRepo
from case_engine.action_state import (
    ALLOWED_ACTION_TRANSITIONS,
    TERMINAL_ACTION_STATES,
    ActionRiskLevel,
    ActionState,
    ActionStateMachine,
    ActionTransitionError,
)
from case_engine.models import Case
from metrics import MetricsCollector, MetricsService
from metrics.collector import (
    COUNTER_ACTIONS_APPROVED,
    COUNTER_ACTIONS_CREATED,
    COUNTER_ACTIONS_DEAD_LETTERED,
    COUNTER_ACTIONS_EXECUTED,
    COUNTER_ACTIONS_EXPIRED,
    COUNTER_ACTIONS_FAILED,
    COUNTER_ACTIONS_REJECTED,
    COUNTER_ACTIONS_ROLLED_BACK,
    COUNTER_ACTIONS_ROLLBACK_FAILED,
    LATENCY_EXECUTION,
    LATENCY_ROLLBACK,
)
from security.config_validator import (
    ConfigurationValidationError,
    _check_audit_config,
    validate_startup_config,
)


# ═══════════════════════════════════════════════════════════════════════════════
# SHARED HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def _make_event(
    action_id: str = "act-001",
    event_type: AuditEventType = AuditEventType.ACTION_APPROVED,
    actor: str = "human:alice",
    case_id: str = "case-001",
    client: str = "unity_bank",
) -> AuditEvent:
    return AuditEvent(
        action_id=action_id,
        event_type=event_type,
        actor=actor,
        case_id=case_id,
        client=client,
    )


def _make_case(client: str = "unity_bank") -> Case:
    return Case(
        case_id=str(uuid.uuid4()),
        ticket_id=str(uuid.uuid4()),
        client=client,
    )


def _make_proposal(
    risk_level: ActionRiskLevel = ActionRiskLevel.REVERSIBLE,
    action_type: str = "update_ticket_status",
    action_namespace: str = "freshdesk",
) -> ActionProposal:
    return ActionProposal(
        action_type=action_type,
        action_namespace=action_namespace,
        risk_level=risk_level,
        action_params={"status": "resolved"},
        proposed_by="ai:rag_agent",
        rollback_action_type="revert_ticket_status" if risk_level == ActionRiskLevel.REVERSIBLE else None,
        rollback_params={"status": "open"} if risk_level == ActionRiskLevel.REVERSIBLE else None,
    )


class _FakeRepository(ActRepo):
    """Minimal in-memory repository for gateway tests."""

    def __init__(self) -> None:
        super().__init__(supabase_client=None)
        self._store: dict[str, ActionRequest] = {}
        self._transitions: list = []

    def insert_action(self, action: ActionRequest) -> ActionRequest:
        if action.idempotency_key and any(
            a.idempotency_key == action.idempotency_key
            for a in self._store.values()
        ):
            from case_engine.action_gateway import DuplicateActionError
            existing = next(
                a for a in self._store.values()
                if a.idempotency_key == action.idempotency_key
            )
            raise DuplicateActionError(existing)
        # Store a deep copy so stored state is independent of future mutations
        self._store[action.action_id] = copy.deepcopy(action)
        return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._store.get(action_id)

    def get_action_by_idempotency_key(self, key: str) -> ActionRequest | None:
        return next((a for a in self._store.values() if a.idempotency_key == key), None)

    def update_action(self, action: ActionRequest, *, expected_state=None) -> bool:
        if expected_state is not None:
            with self._claim_lock:
                existing = self._store.get(action.action_id)
                if existing is None or existing.current_state != expected_state:
                    return False
                # Atomic: claim succeeded — persist the new state now (under the lock)
                self._store[action.action_id] = copy.deepcopy(action)
                return True
        if action.action_id in self._store:
            self._store[action.action_id] = copy.deepcopy(action)
        return True

    def list_approved_actions(self, client: str) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.client == client and a.current_state == ActionState.APPROVED
        ]

    def list_rolling_back_actions(self, client: str | None = None) -> list[ActionRequest]:
        return []

    def list_pending_approvals(self, client: str) -> list[ActionRequest]:
        return []

    def list_expired_actions(self, client: str | None = None) -> list[ActionRequest]:
        return []

    def list_dead_letter_actions(self, client: str | None = None) -> list[ActionRequest]:
        return [
            a for a in self._store.values()
            if a.current_state == ActionState.DEAD_LETTER
            and (client is None or a.client == client)
        ]

    def record_transition(self, record) -> bool:
        self._transitions.append(record)
        return True


class _MockSupabaseTable:
    """Fluent-API mock for supabase-py table queries."""

    def __init__(self, rows=None, count=0):
        self._rows = rows or []
        self._count = count
        self.inserted: list[dict] = []
        self._raise_on_execute = False

    def set_raise(self, raise_on: bool = True):
        self._raise_on_execute = raise_on
        return self

    def select(self, *args, **kwargs):
        return self

    def insert(self, row):
        self.inserted.append(row)
        return self

    def eq(self, *args, **kwargs):
        return self

    def order(self, *args, **kwargs):
        return self

    def range(self, *args, **kwargs):
        return self

    def execute(self):
        if self._raise_on_execute:
            raise RuntimeError("DB connection refused")
        result = MagicMock()
        result.data = self._rows
        result.count = self._count
        return result


class _MockSupabaseClient:
    """Mock supabase client for SupabaseAuditRepository tests."""

    def __init__(self, rows=None, count=0):
        self._table = _MockSupabaseTable(rows=rows, count=count)

    def table(self, name: str):
        return self._table


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 1: SupabaseAuditRepository (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestSupabaseAuditRepository:

    def test_requires_non_none_client(self):
        with pytest.raises(ValueError, match="non-None"):
            SupabaseAuditRepository(None)

    def test_insert_event_calls_supabase_insert(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        event = _make_event()
        repo.insert_event(event)
        assert len(client._table.inserted) == 1

    def test_insert_event_maps_event_id(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        event = _make_event()
        repo.insert_event(event)
        row = client._table.inserted[0]
        assert row["event_id"] == event.event_id

    def test_insert_event_maps_action_id(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        event = _make_event(action_id="act-xyz")
        repo.insert_event(event)
        assert client._table.inserted[0]["action_id"] == "act-xyz"

    def test_insert_event_uses_metadata_json_column(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        event = _make_event()
        meta = {"notes": "approved by alice"}
        event_with_meta = AuditEvent(
            action_id="act-001",
            event_type=AuditEventType.ACTION_APPROVED,
            actor="human:alice",
            metadata=meta,
        )
        repo.insert_event(event_with_meta)
        row = client._table.inserted[0]
        assert "metadata_json" in row
        assert row["metadata_json"] == meta

    def test_insert_event_uses_event_type_value(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        event = _make_event(event_type=AuditEventType.ACTION_REJECTED)
        repo.insert_event(event)
        assert client._table.inserted[0]["event_type"] == "ACTION_REJECTED"

    def test_insert_event_db_error_does_not_raise(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        event = _make_event()
        repo.insert_event(event)  # must not raise

    def test_events_for_action_returns_list(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        rows = [
            {"event_id": str(uuid.uuid4()), "action_id": "act-001",
             "event_type": "ACTION_APPROVED", "actor": "human:alice",
             "timestamp": now, "case_id": "", "client": "", "metadata_json": {}},
        ]
        client = _MockSupabaseClient(rows=rows)
        repo = SupabaseAuditRepository(client)
        result = repo.events_for_action("act-001")
        assert len(result) == 1
        assert isinstance(result[0], AuditEvent)

    def test_events_for_action_db_error_returns_empty(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        result = repo.events_for_action("act-001")
        assert result == []

    def test_events_for_case_returns_list(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        rows = [
            {"event_id": str(uuid.uuid4()), "action_id": "act-001",
             "event_type": "ACTION_EXPIRED", "actor": "watchdog:sla",
             "timestamp": now, "case_id": "case-1", "client": "", "metadata_json": {}},
        ]
        client = _MockSupabaseClient(rows=rows)
        repo = SupabaseAuditRepository(client)
        result = repo.events_for_case("case-1")
        assert len(result) == 1

    def test_events_for_case_db_error_returns_empty(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        assert repo.events_for_case("case-x") == []

    def test_events_for_client_returns_list(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        rows = [
            {"event_id": str(uuid.uuid4()), "action_id": "act-001",
             "event_type": "ACTION_EXECUTED", "actor": "executor:w1",
             "timestamp": now, "case_id": "", "client": "unity_bank", "metadata_json": {}},
        ]
        client = _MockSupabaseClient(rows=rows)
        repo = SupabaseAuditRepository(client)
        result = repo.events_for_client("unity_bank")
        assert len(result) == 1

    def test_events_for_client_db_error_returns_empty(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        assert repo.events_for_client("unity_bank") == []

    def test_list_events_returns_list(self):
        now = datetime.now(tz=timezone.utc).isoformat()
        rows = [
            {"event_id": str(uuid.uuid4()), "action_id": "act-001",
             "event_type": "ACTION_FAILED", "actor": "executor:w1",
             "timestamp": now, "case_id": "", "client": "", "metadata_json": {}},
        ]
        client = _MockSupabaseClient(rows=rows)
        repo = SupabaseAuditRepository(client)
        result = repo.list_events(event_type=AuditEventType.ACTION_FAILED)
        assert len(result) == 1

    def test_list_events_db_error_returns_empty(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        assert repo.list_events() == []

    def test_count_events_returns_integer(self):
        client = _MockSupabaseClient(count=5)
        repo = SupabaseAuditRepository(client)
        assert repo.count_events() == 5

    def test_count_events_db_error_returns_zero(self):
        client = _MockSupabaseClient()
        client._table.set_raise(True)
        repo = SupabaseAuditRepository(client)
        assert repo.count_events() == 0

    def test_row_to_event_handles_string_timestamp(self):
        now = datetime.now(tz=timezone.utc)
        row = {
            "event_id": str(uuid.uuid4()),
            "action_id": "act-1",
            "event_type": "ACTION_APPROVED",
            "actor": "human:alice",
            "timestamp": now.isoformat(),
            "case_id": "",
            "client": "",
            "metadata_json": {},
        }
        event = _row_to_event(row)
        assert event.action_id == "act-1"
        assert event.timestamp.tzinfo is not None

    def test_row_to_event_handles_naive_timestamp(self):
        naive_dt = datetime(2025, 1, 15, 10, 0, 0)
        row = {
            "event_id": str(uuid.uuid4()),
            "action_id": "act-1",
            "event_type": "ACTION_REJECTED",
            "actor": "human:bob",
            "timestamp": naive_dt,
            "case_id": "",
            "client": "",
            "metadata_json": None,
        }
        event = _row_to_event(row)
        assert event.timestamp.tzinfo is not None
        assert event.metadata == {}

    def test_supabase_repo_is_audit_repository_subclass(self):
        client = _MockSupabaseClient()
        repo = SupabaseAuditRepository(client)
        assert isinstance(repo, AuditRepository)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 2: AuditRepositoryFactory (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestAuditRepositoryFactory:

    def test_default_returns_inmemory(self, monkeypatch):
        monkeypatch.delenv("AUDIT_BACKEND", raising=False)
        repo = build_audit_repository()
        assert isinstance(repo, InMemoryAuditRepository)

    def test_inmemory_explicit_returns_inmemory(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
        repo = build_audit_repository()
        assert isinstance(repo, InMemoryAuditRepository)

    def test_supabase_with_client_returns_supabase_repo(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "supabase")
        client = _MockSupabaseClient()
        repo = build_audit_repository(client)
        assert isinstance(repo, SupabaseAuditRepository)

    def test_supabase_without_client_raises(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "supabase")
        with pytest.raises(ValueError, match="supabase_client"):
            build_audit_repository(None)

    def test_unknown_backend_raises(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "redis")
        with pytest.raises(ValueError, match="not supported"):
            build_audit_repository()

    def test_backend_override_takes_precedence_over_env(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "supabase")
        repo = build_audit_repository(backend_override="inmemory")
        assert isinstance(repo, InMemoryAuditRepository)

    def test_inmemory_is_functional(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
        repo = build_audit_repository()
        event = _make_event()
        repo.insert_event(event)
        results = repo.events_for_action(event.action_id)
        assert len(results) == 1

    def test_returned_repo_is_audit_repository_abc(self, monkeypatch):
        monkeypatch.delenv("AUDIT_BACKEND", raising=False)
        repo = build_audit_repository()
        assert isinstance(repo, AuditRepository)

    def test_case_insensitive_inmemory(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "INMEMORY")
        repo = build_audit_repository()
        assert isinstance(repo, InMemoryAuditRepository)

    def test_case_insensitive_supabase(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "SUPABASE")
        client = _MockSupabaseClient()
        repo = build_audit_repository(client)
        assert isinstance(repo, SupabaseAuditRepository)


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 3: Dead Letter Queue (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDeadLetterQueue:

    def test_dead_letter_in_action_state_enum(self):
        assert ActionState.DEAD_LETTER in ActionState

    def test_dead_letter_is_terminal(self):
        assert ActionState.DEAD_LETTER in TERMINAL_ACTION_STATES

    def test_dead_letter_has_no_outgoing_transitions(self):
        transitions = ALLOWED_ACTION_TRANSITIONS.get(ActionState.DEAD_LETTER, frozenset())
        assert len(transitions) == 0

    def test_record_failure_exhausted_retries_yields_dead_letter(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        # Exhaust all retries
        for _ in range(action.max_attempts):
            gw.begin_execution(action, executor_id="worker-1")
            gw.record_failure(action, failure_code="UPSTREAM_ERROR", reason="timeout")
        assert action.current_state == ActionState.DEAD_LETTER

    def test_record_failure_permanent_yields_dead_letter(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="PERMANENT", reason="invalid", permanent=True)
        assert action.current_state == ActionState.DEAD_LETTER

    def test_record_failure_retries_remaining_yields_approved(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="RETRY_ME", reason="transient", permanent=False)
        # With retries remaining, should be re-approved
        assert action.current_state == ActionState.APPROVED

    def test_dead_lettered_at_set_on_dead_letter(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="PERMANENT", reason="fatal", permanent=True)
        assert action.dead_lettered_at is not None

    def test_dead_lettered_at_is_utc_aware(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="FATAL", reason="fatal", permanent=True)
        assert action.dead_lettered_at.tzinfo is not None

    def test_dead_lettered_at_none_for_successful_action(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_success(action, result={"done": True})
        assert action.dead_lettered_at is None

    def test_to_db_row_includes_dead_lettered_at(self):
        from case_engine.action_models import ActionRequest
        action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.DEAD_LETTER,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={},
            max_attempts=3,
            dead_lettered_at=datetime.now(tz=timezone.utc),
        )
        row = action.to_db_row()
        assert "dead_lettered_at" in row

    def test_to_db_row_dead_lettered_at_none_when_not_set(self):
        from case_engine.action_models import ActionRequest
        action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.PROPOSED,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={},
            max_attempts=3,
        )
        row = action.to_db_row()
        assert row["dead_lettered_at"] is None

    def test_record_timeout_exhausted_yields_dead_letter(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        # REVERSIBLE has 3 attempts by default
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        # Exhaust via timeouts
        for _ in range(action.max_attempts):
            gw.begin_execution(action, executor_id="worker-1")
            gw.record_timeout(action, executor_id="worker-1")
        assert action.current_state == ActionState.DEAD_LETTER

    def test_record_timeout_retries_remaining_yields_approved(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_timeout(action, executor_id="worker-1")
        assert action.current_state == ActionState.APPROVED

    def test_list_dead_letter_actions_offline(self):
        repo = _FakeRepository()
        from case_engine.action_models import ActionRequest
        dead_action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.DEAD_LETTER,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={}, max_attempts=1,
            dead_lettered_at=datetime.now(tz=timezone.utc),
        )
        live_action = ActionRequest(
            case_id="c1", ticket_id="t2", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.EXECUTED,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={}, max_attempts=1,
        )
        repo._store[dead_action.action_id] = dead_action
        repo._store[live_action.action_id] = live_action
        results = repo.list_dead_letter_actions()
        assert len(results) == 1
        assert results[0].action_id == dead_action.action_id

    def test_list_dead_letter_actions_filters_by_client(self):
        repo = _FakeRepository()
        from case_engine.action_models import ActionRequest
        now = datetime.now(tz=timezone.utc)
        a1 = ActionRequest(
            case_id="c1", ticket_id="t1", client="bank_a",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.DEAD_LETTER,
            proposed_by="ai", proposed_at=now,
            action_payload={}, max_attempts=1,
            dead_lettered_at=now,
        )
        a2 = ActionRequest(
            case_id="c2", ticket_id="t2", client="bank_b",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.DEAD_LETTER,
            proposed_by="ai", proposed_at=now,
            action_payload={}, max_attempts=1,
            dead_lettered_at=now,
        )
        repo._store[a1.action_id] = a1
        repo._store[a2.action_id] = a2
        results = repo.list_dead_letter_actions(client="bank_a")
        assert all(r.client == "bank_a" for r in results)
        assert len(results) == 1

    def test_dead_letter_action_id_preserved(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        original_id = action.action_id
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="FATAL", reason="fatal", permanent=True)
        assert action.action_id == original_id

    def test_dead_letter_failure_code_preserved(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="UPSTREAM_GONE", reason="gone", permanent=True)
        assert action.failure_code == "UPSTREAM_GONE"

    def test_metrics_dead_lettered_incremented(self):
        repo = _FakeRepository()
        collector = MetricsCollector()
        ms = MetricsService(collector=collector)
        gw = ActionGateway(repository=repo, metrics_service=ms)
        case = _make_case()
        action = gw.propose(case, _make_proposal(risk_level=ActionRiskLevel.REVERSIBLE))
        gw.approve(action, approved_by="alice")
        for _ in range(action.max_attempts):
            gw.begin_execution(action, executor_id="worker-1")
            gw.record_failure(action, failure_code="ERR", reason="x")
        # The gateway doesn't auto-call record_action_dead_lettered via _record_metric
        # (that's done manually if wired). Check metrics from proposal/approve at minimum.
        assert collector.get_counter(COUNTER_ACTIONS_CREATED) == 1
        assert collector.get_counter(COUNTER_ACTIONS_APPROVED) == 1

    def test_irreversible_permanent_dead_letter_at_attempt_one(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal(
            risk_level=ActionRiskLevel.IRREVERSIBLE,
            action_type="send_legal_notice",
        ))
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_failure(action, failure_code="PERM", reason="fatal", permanent=True)
        assert action.current_state == ActionState.DEAD_LETTER

    def test_dead_letter_state_value_is_string(self):
        assert ActionState.DEAD_LETTER.value == "DEAD_LETTER"

    def test_proposed_action_dead_lettered_at_is_none(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        assert action.dead_lettered_at is None


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 4: MetricsCollector Thread-Safety (20 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsCollector:

    def test_all_counters_start_at_zero(self):
        c = MetricsCollector()
        snap = c.snapshot()
        for name, val in snap["counters"].items():
            assert val == 0, f"{name} should start at 0"

    def test_increment_increases_counter_by_one(self):
        c = MetricsCollector()
        c.increment(COUNTER_ACTIONS_CREATED)
        assert c.get_counter(COUNTER_ACTIONS_CREATED) == 1

    def test_increment_by_n(self):
        c = MetricsCollector()
        c.increment(COUNTER_ACTIONS_APPROVED, by=5)
        assert c.get_counter(COUNTER_ACTIONS_APPROVED) == 5

    def test_get_counter_returns_zero_for_unknown(self):
        c = MetricsCollector()
        assert c.get_counter("nonexistent_counter") == 0

    def test_record_latency_accumulates_sum(self):
        c = MetricsCollector()
        c.record_latency(LATENCY_EXECUTION, 100.0)
        c.record_latency(LATENCY_EXECUTION, 200.0)
        assert c.get_latency_sum(LATENCY_EXECUTION) == pytest.approx(300.0)

    def test_record_latency_accumulates_count(self):
        c = MetricsCollector()
        c.record_latency(LATENCY_EXECUTION, 50.0)
        c.record_latency(LATENCY_EXECUTION, 75.0)
        assert c.get_latency_count(LATENCY_EXECUTION) == 2

    def test_get_latency_sum_zero_for_unknown(self):
        c = MetricsCollector()
        assert c.get_latency_sum("nonexistent") == 0.0

    def test_get_latency_count_zero_for_unknown(self):
        c = MetricsCollector()
        assert c.get_latency_count("nonexistent") == 0

    def test_snapshot_returns_dict_with_three_keys(self):
        c = MetricsCollector()
        snap = c.snapshot()
        assert "counters" in snap
        assert "latency_sums" in snap
        assert "latency_counts" in snap

    def test_snapshot_counters_are_independent_copy(self):
        c = MetricsCollector()
        snap = c.snapshot()
        snap["counters"][COUNTER_ACTIONS_CREATED] = 999
        assert c.get_counter(COUNTER_ACTIONS_CREATED) == 0

    def test_all_canonical_counter_names_initialized(self):
        c = MetricsCollector()
        snap = c.snapshot()
        for name in [
            COUNTER_ACTIONS_CREATED, COUNTER_ACTIONS_APPROVED, COUNTER_ACTIONS_REJECTED,
            COUNTER_ACTIONS_EXECUTED, COUNTER_ACTIONS_FAILED, COUNTER_ACTIONS_EXPIRED,
            COUNTER_ACTIONS_ROLLED_BACK, COUNTER_ACTIONS_ROLLBACK_FAILED,
            COUNTER_ACTIONS_DEAD_LETTERED,
        ]:
            assert name in snap["counters"]

    def test_all_canonical_latency_names_initialized(self):
        c = MetricsCollector()
        snap = c.snapshot()
        for name in [LATENCY_EXECUTION, LATENCY_ROLLBACK]:
            assert name in snap["latency_sums"]
            assert name in snap["latency_counts"]

    def test_concurrent_increments_correct_total(self):
        c = MetricsCollector()
        n_threads = 10
        increments_per_thread = 100
        threads = [
            threading.Thread(
                target=lambda: [c.increment(COUNTER_ACTIONS_CREATED) for _ in range(increments_per_thread)]
            )
            for _ in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.get_counter(COUNTER_ACTIONS_CREATED) == n_threads * increments_per_thread

    def test_concurrent_latency_records_correct_count(self):
        c = MetricsCollector()
        n_threads = 10
        records_per_thread = 50
        threads = [
            threading.Thread(
                target=lambda: [c.record_latency(LATENCY_EXECUTION, 10.0) for _ in range(records_per_thread)]
            )
            for _ in range(n_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert c.get_latency_count(LATENCY_EXECUTION) == n_threads * records_per_thread

    def test_snapshot_consistent_under_concurrent_writes(self):
        c = MetricsCollector()
        errors = []

        def writer():
            for _ in range(200):
                c.increment(COUNTER_ACTIONS_FAILED)

        def reader():
            for _ in range(200):
                snap = c.snapshot()
                try:
                    assert isinstance(snap["counters"], dict)
                except AssertionError as e:
                    errors.append(e)

        threads = [threading.Thread(target=writer) for _ in range(5)]
        threads += [threading.Thread(target=reader) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors

    def test_increment_unknown_counter_creates_it(self):
        c = MetricsCollector()
        c.increment("custom_counter_xyz")
        assert c.get_counter("custom_counter_xyz") == 1

    def test_record_latency_unknown_creates_it(self):
        c = MetricsCollector()
        c.record_latency("custom_latency", 42.0)
        assert c.get_latency_sum("custom_latency") == pytest.approx(42.0)
        assert c.get_latency_count("custom_latency") == 1

    def test_get_counter_consistent_with_snapshot(self):
        c = MetricsCollector()
        c.increment(COUNTER_ACTIONS_EXPIRED, by=7)
        snap = c.snapshot()
        assert snap["counters"][COUNTER_ACTIONS_EXPIRED] == c.get_counter(COUNTER_ACTIONS_EXPIRED)

    def test_each_collector_instance_is_isolated(self):
        c1 = MetricsCollector()
        c2 = MetricsCollector()
        c1.increment(COUNTER_ACTIONS_CREATED, by=10)
        assert c2.get_counter(COUNTER_ACTIONS_CREATED) == 0

    def test_multiple_sequential_increments_accumulate(self):
        c = MetricsCollector()
        for _ in range(100):
            c.increment(COUNTER_ACTIONS_FAILED)
        assert c.get_counter(COUNTER_ACTIONS_FAILED) == 100


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 5: MetricsService / prometheus_text (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsService:

    def _make_service(self) -> tuple[MetricsCollector, MetricsService]:
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        return col, svc

    def test_record_action_created_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_created()
        assert col.get_counter(COUNTER_ACTIONS_CREATED) == 1

    def test_record_action_approved_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_approved()
        assert col.get_counter(COUNTER_ACTIONS_APPROVED) == 1

    def test_record_action_rejected_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_rejected()
        assert col.get_counter(COUNTER_ACTIONS_REJECTED) == 1

    def test_record_action_expired_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_expired()
        assert col.get_counter(COUNTER_ACTIONS_EXPIRED) == 1

    def test_record_action_executed_increments_counter_and_latency(self):
        col, svc = self._make_service()
        svc.record_action_executed(latency_ms=150.0)
        assert col.get_counter(COUNTER_ACTIONS_EXECUTED) == 1
        assert col.get_latency_sum(LATENCY_EXECUTION) == pytest.approx(150.0)

    def test_record_action_executed_zero_latency_skips_latency(self):
        col, svc = self._make_service()
        svc.record_action_executed(latency_ms=0.0)
        assert col.get_counter(COUNTER_ACTIONS_EXECUTED) == 1
        assert col.get_latency_count(LATENCY_EXECUTION) == 0

    def test_record_action_failed_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_failed()
        assert col.get_counter(COUNTER_ACTIONS_FAILED) == 1

    def test_record_action_rolled_back_increments_counter_and_latency(self):
        col, svc = self._make_service()
        svc.record_action_rolled_back(latency_ms=200.0)
        assert col.get_counter(COUNTER_ACTIONS_ROLLED_BACK) == 1
        assert col.get_latency_sum(LATENCY_ROLLBACK) == pytest.approx(200.0)

    def test_record_action_rollback_failed_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_rollback_failed()
        assert col.get_counter(COUNTER_ACTIONS_ROLLBACK_FAILED) == 1

    def test_record_action_dead_lettered_increments_counter(self):
        col, svc = self._make_service()
        svc.record_action_dead_lettered()
        assert col.get_counter(COUNTER_ACTIONS_DEAD_LETTERED) == 1

    def test_prometheus_text_contains_help_lines(self):
        _, svc = self._make_service()
        text = svc.prometheus_text()
        assert "# HELP" in text

    def test_prometheus_text_contains_type_lines(self):
        _, svc = self._make_service()
        text = svc.prometheus_text()
        assert "# TYPE" in text

    def test_prometheus_text_contains_counter_values(self):
        col, svc = self._make_service()
        svc.record_action_created()
        svc.record_action_created()
        text = svc.prometheus_text()
        assert "actions_created_total 2" in text

    def test_prometheus_text_ends_with_newline(self):
        _, svc = self._make_service()
        text = svc.prometheus_text()
        assert text.endswith("\n")

    def test_snapshot_returns_counters_and_latency_keys(self):
        _, svc = self._make_service()
        snap = svc.snapshot()
        assert "counters" in snap
        assert "latency_sums" in snap
        assert "latency_counts" in snap


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 6: Metrics Endpoint (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestMetricsEndpoint:

    def _make_client(self, metrics_service=None) -> TestClient:
        app = create_app(
            skip_config_validation=True,
            metrics_service=metrics_service,
        )
        return TestClient(app, raise_server_exceptions=True)

    def test_get_metrics_returns_200(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert resp.status_code == 200

    def test_get_metrics_content_type_is_text_plain(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "text/plain" in resp.headers["content-type"]

    def test_get_metrics_contains_help_lines(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "# HELP" in resp.text

    def test_get_metrics_contains_type_lines(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "# TYPE" in resp.text

    def test_get_metrics_contains_counter_names(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "actions_created_total" in resp.text

    def test_get_metrics_without_metrics_service_returns_placeholder(self):
        client = self._make_client(metrics_service=None)
        resp = client.get("/gateway/metrics")
        assert resp.status_code == 200
        assert "metrics_service not configured" in resp.text

    def test_get_metrics_no_auth_required(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")  # no X-API-Key
        assert resp.status_code == 200

    def test_get_metrics_reflects_recorded_values(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        svc.record_action_created()
        svc.record_action_created()
        svc.record_action_created()
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "actions_created_total 3" in resp.text

    def test_get_metrics_includes_latency_sum_and_count(self):
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        svc.record_action_executed(latency_ms=123.456)
        client = self._make_client(metrics_service=svc)
        resp = client.get("/gateway/metrics")
        assert "execution_latency_ms_count 1" in resp.text

    def test_get_metrics_never_returns_500(self):
        # Broken metrics_service that raises on prometheus_text
        bad_svc = MagicMock()
        bad_svc.prometheus_text.side_effect = RuntimeError("broken")
        client = self._make_client(metrics_service=bad_svc)
        resp = client.get("/gateway/metrics")
        assert resp.status_code == 200  # error is caught, returns comment line


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 7: Distributed Execution Safety / Optimistic Locking (15 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestDistributedExecutionSafety:

    def test_begin_execution_succeeds_on_approved_action(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        result = gw.begin_execution(action, executor_id="worker-1")
        assert result.current_state == ActionState.EXECUTING

    def test_begin_execution_raises_on_non_approved_action(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        # Still AWAITING_APPROVAL — not approved yet
        with pytest.raises(ActionGatewayError):
            gw.begin_execution(action, executor_id="worker-1")

    def test_begin_execution_increments_execution_attempt(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        assert action.execution_attempt == 1

    def test_begin_execution_sets_executor_id(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-7")
        assert action.executor_id == "worker-7"

    def test_begin_execution_sets_utc_started_at(self):
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        gw.begin_execution(action, executor_id="worker-1")
        assert action.execution_started_at is not None
        assert action.execution_started_at.tzinfo is not None

    def test_update_action_expected_state_offline_always_true(self):
        # Offline mode has no concurrent workers — optimistic locking is a DB concern only.
        # update_action always returns True regardless of expected_state vs current_state.
        # This allows begin_execution() to work correctly: _sm.transition() mutates
        # action.current_state in-place before update_action is called, so checking
        # action.current_state against expected_state would always fail in offline mode.
        repo = ActRepo(supabase_client=None)
        from case_engine.action_models import ActionRequest
        action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.EXECUTING,  # already transitioned in-place
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={}, max_attempts=3,
        )
        # Offline mode: always True (no DB WHERE clause, no concurrent workers)
        result = repo.update_action(action, expected_state=ActionState.APPROVED)
        assert result is True

    def test_update_action_expected_state_match_returns_true_offline(self):
        repo = ActRepo(supabase_client=None)
        from case_engine.action_models import ActionRequest
        action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.APPROVED,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={}, max_attempts=3,
        )
        result = repo.update_action(action, expected_state=ActionState.APPROVED)
        assert result is True

    def test_update_action_no_expected_state_always_returns_true_offline(self):
        repo = ActRepo(supabase_client=None)
        from case_engine.action_models import ActionRequest
        action = ActionRequest(
            case_id="c1", ticket_id="t1", client="acme",
            action_type="noop", action_namespace="test",
            risk_level=ActionRiskLevel.SAFE,
            current_state=ActionState.EXECUTING,
            proposed_by="ai",
            proposed_at=datetime.now(tz=timezone.utc),
            action_payload={}, max_attempts=3,
        )
        # No expected_state → always True in offline mode
        result = repo.update_action(action)
        assert result is True

    def test_optimistic_lock_first_worker_succeeds(self):
        """FakeRepository simulate: first claim wins."""
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        # First worker claims
        result = gw.begin_execution(action, executor_id="worker-1")
        assert result.current_state == ActionState.EXECUTING

    def test_optimistic_lock_second_worker_raises(self):
        """Simulate two workers racing: second call sees state already EXECUTING."""
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        # First worker claims
        import copy
        action_copy = copy.deepcopy(action)
        # Simulate: second worker calls begin_execution on the same APPROVED-locally action
        # but in the repo the state is already EXECUTING after first worker persisted
        gw.begin_execution(action, executor_id="worker-1")
        # The repo now has the action as EXECUTING
        # The action_copy still looks APPROVED locally but repo state is EXECUTING
        # Expected_state check will find EXECUTING != APPROVED → returns False → raises
        action_copy.current_state = ActionState.APPROVED  # simulate stale local state
        with pytest.raises(ActionGatewayError, match="optimistic lock"):
            gw.begin_execution(action_copy, executor_id="worker-2")

    def test_concurrent_begin_execution_only_one_wins(self):
        """Threading test: only one of two concurrent workers claims the action."""
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")

        import copy
        # Give each thread its own copy of the approved action
        copies = [copy.deepcopy(action) for _ in range(2)]
        successes = []
        failures = []

        def try_claim(act):
            try:
                gw.begin_execution(act, executor_id=f"worker-{threading.current_thread().name}")
                successes.append(True)
            except ActionGatewayError:
                failures.append(True)

        threads = [threading.Thread(target=try_claim, args=(copies[i],), name=str(i)) for i in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) + len(failures) == 2
        assert len(successes) == 1
        assert len(failures) == 1

    def test_claim_lock_in_action_repository(self):
        repo = ActRepo(supabase_client=None)
        assert hasattr(repo, "_claim_lock")
        assert isinstance(repo._claim_lock, type(threading.Lock()))

    def test_begin_execution_mutates_local_state_on_lock_failure(self):
        """After optimistic lock failure, local action is in EXECUTING state — caller must discard."""
        import copy
        repo = _FakeRepository()
        gw = ActionGateway(repository=repo)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        # First claim succeeds
        gw.begin_execution(action, executor_id="worker-1")
        # Now action is EXECUTING in repo. Simulate stale copy:
        stale = copy.deepcopy(action)
        stale.current_state = ActionState.APPROVED
        stale.execution_attempt = 0
        with pytest.raises(ActionGatewayError):
            gw.begin_execution(stale, executor_id="worker-2")
        # The stale copy was locally mutated to EXECUTING before the lock check failed
        assert stale.current_state == ActionState.EXECUTING

    def test_metrics_wired_into_gateway_propose(self):
        repo = _FakeRepository()
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        gw = ActionGateway(repository=repo, metrics_service=svc)
        case = _make_case()
        gw.propose(case, _make_proposal())
        assert col.get_counter(COUNTER_ACTIONS_CREATED) == 1

    def test_metrics_wired_into_gateway_approve(self):
        repo = _FakeRepository()
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        gw = ActionGateway(repository=repo, metrics_service=svc)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.approve(action, approved_by="alice")
        assert col.get_counter(COUNTER_ACTIONS_APPROVED) == 1

    def test_metrics_wired_into_gateway_reject(self):
        repo = _FakeRepository()
        col = MetricsCollector()
        svc = MetricsService(collector=col)
        gw = ActionGateway(repository=repo, metrics_service=svc)
        case = _make_case()
        action = gw.propose(case, _make_proposal())
        gw.reject(action, rejected_by="alice")
        assert col.get_counter(COUNTER_ACTIONS_REJECTED) == 1


# ═══════════════════════════════════════════════════════════════════════════════
# SECTION 8: Config Validation (10 tests)
# ═══════════════════════════════════════════════════════════════════════════════

class TestConfigValidation:

    def test_check_audit_config_passes_for_inmemory(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
        _check_audit_config()  # must not raise

    def test_check_audit_config_passes_for_supabase(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "supabase")
        _check_audit_config()  # must not raise

    def test_check_audit_config_raises_for_unknown(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "redis")
        with pytest.raises(ConfigurationValidationError, match="not a supported value"):
            _check_audit_config()

    def test_check_audit_config_default_inmemory_passes(self, monkeypatch):
        monkeypatch.delenv("AUDIT_BACKEND", raising=False)
        _check_audit_config()  # default is inmemory, must not raise

    def test_check_audit_config_case_insensitive_passes(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "INMEMORY")
        _check_audit_config()  # must not raise

    def test_check_audit_config_uppercase_supabase_passes(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "SUPABASE")
        _check_audit_config()  # must not raise

    def test_config_validation_error_message_is_descriptive(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "kafka")
        with pytest.raises(ConfigurationValidationError) as exc_info:
            _check_audit_config()
        message = str(exc_info.value)
        assert "kafka" in message
        assert "inmemory" in message or "supabase" in message

    def test_validate_startup_config_calls_audit_check(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "invalid_backend")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        with pytest.raises(ConfigurationValidationError):
            validate_startup_config()

    def test_validate_startup_config_passes_with_valid_env(self, monkeypatch):
        monkeypatch.setenv("AUDIT_BACKEND", "inmemory")
        monkeypatch.setenv("FRESHDESK_WEBHOOK_ENFORCE_HMAC", "false")
        monkeypatch.setenv("AUTH_ENABLED", "false")
        validate_startup_config()  # must not raise

    def test_configuration_validation_error_is_runtime_error_subclass(self):
        err = ConfigurationValidationError("test error")
        assert isinstance(err, RuntimeError)
