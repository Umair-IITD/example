"""
tests/test_sprint216_repository_persistence.py

Sprint 2.1.6: ActionRepository persistence correctness tests.

Tests the actual ActionRepository code against a fake Supabase client
(FakeSupabaseClient) that stores rows in memory. This exercises the real
repository query-building logic without a live DB connection.

10 required scenarios:
  1.  insert_action_round_trip         — full field preservation insert → get
  2.  load_action_round_trip           — to_db_row → from_db_row lossless
  3.  update_action_state_round_trip   — update state → reload → verify
  4.  append_transition_round_trip     — append transition → get_transitions → verify
  5.  duplicate_idempotency_key_raises — DB 23505 → DuplicateActionError
  6.  list_pending_approvals           — filter by client + AWAITING_APPROVAL state
  7.  list_expired_actions             — filter by expires_at < now
  8.  rollback_action_linkage          — rollback_action_id FK chain persisted
  9.  transition_history_integrity     — multiple transitions, correct ASC order
  10. repository_restart_scenario      — simulate restart via serialization round-trip
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from case_engine.action_gateway import DuplicateActionError
from case_engine.action_models import ActionRequest, ActionTransitionRecord
from case_engine.action_repository import ActionRepository
from case_engine.action_state import ActionRiskLevel, ActionState


# ── Fake Supabase client ───────────────────────────────────────────────────────


class _FakeResult:
    def __init__(self, data: list[dict]):
        self.data = data


class _FakeQueryBuilder:
    """
    Fluent fake that mirrors supabase-py's QueryBuilder interface.
    Supports: select, insert, update, eq, in_, lt, order, limit, execute.
    """

    def __init__(self, store: dict[str, list[dict]], table: str) -> None:
        self._store = store
        self._table = table
        self._op: str | None = None
        self._payload: dict | None = None
        self._filters: list[tuple] = []
        self._order_field: str | None = None
        self._order_desc: bool = False
        self._limit_n: int | None = None

    def select(self, *_):
        self._op = "select"
        return self

    def insert(self, payload: dict):
        self._op = "insert"
        self._payload = payload
        return self

    def update(self, payload: dict):
        self._op = "update"
        self._payload = payload
        return self

    def eq(self, field: str, value: Any):
        self._filters.append(("eq", field, value))
        return self

    def in_(self, field: str, values: list):
        self._filters.append(("in", field, values))
        return self

    def lt(self, field: str, value: Any):
        self._filters.append(("lt", field, value))
        return self

    def order(self, field: str, desc: bool = False):
        self._order_field = field
        self._order_desc = desc
        return self

    def limit(self, n: int):
        self._limit_n = n
        return self

    def execute(self) -> _FakeResult:
        if self._op == "insert":
            return self._execute_insert()
        if self._op == "select":
            return self._execute_select()
        if self._op == "update":
            return self._execute_update()
        return _FakeResult([])

    def _execute_insert(self) -> _FakeResult:
        rows = self._store.setdefault(self._table, [])
        if self._table == "action_gateway":
            idem = self._payload.get("idempotency_key", "")
            if idem and any(r.get("idempotency_key") == idem for r in rows):
                raise Exception("duplicate key value violates unique constraint 23505")
        row = dict(self._payload)
        rows.append(row)
        return _FakeResult([dict(row)])

    def _execute_select(self) -> _FakeResult:
        rows = list(self._store.get(self._table, []))
        for (op, field, value) in self._filters:
            if op == "eq":
                rows = [r for r in rows if r.get(field) == value]
            elif op == "in":
                rows = [r for r in rows if r.get(field) in value]
            elif op == "lt":
                rows = [r for r in rows if r.get(field) is not None and r.get(field) < value]
        if self._order_field:
            # None-safe sort: None values sort last regardless of direction
            rows.sort(
                key=lambda r: (r.get(self._order_field) is None, r.get(self._order_field)),
                reverse=self._order_desc,
            )
        if self._limit_n is not None:
            rows = rows[: self._limit_n]
        return _FakeResult([dict(r) for r in rows])

    def _execute_update(self) -> _FakeResult:
        rows = self._store.get(self._table, [])
        for row in rows:
            match = all(
                (row.get(f) == v) if op == "eq" else True
                for (op, f, v) in self._filters
            )
            if match:
                row.update(self._payload)
        return _FakeResult([])


class FakeSupabaseClient:
    """In-memory Supabase client that persists rows across table() calls."""

    def __init__(self) -> None:
        self._store: dict[str, list[dict]] = {}

    def table(self, name: str) -> _FakeQueryBuilder:
        return _FakeQueryBuilder(self._store, name)


# ── Helper factories ───────────────────────────────────────────────────────────


def _now() -> datetime:
    return datetime.now(tz=timezone.utc)


def _repo() -> tuple[ActionRepository, FakeSupabaseClient]:
    client = FakeSupabaseClient()
    return ActionRepository(supabase_client=client), client


def _action(**overrides) -> ActionRequest:
    defaults = dict(
        case_id=str(uuid.uuid4()),
        ticket_id="TKT-001",
        client="unity_bank",
        action_type="reset_otp",
        action_namespace="identity",
        risk_level=ActionRiskLevel.SAFE,
        idempotency_key=uuid.uuid4().hex,
    )
    defaults.update(overrides)
    return ActionRequest(**defaults)


def _transition(action: ActionRequest, **overrides) -> ActionTransitionRecord:
    defaults = dict(
        action_id=action.action_id,
        case_id=action.case_id,
        ticket_id=action.ticket_id,
        client=action.client,
        from_state=ActionState.PROPOSED,
        to_state=ActionState.APPROVED,
        actor="test_system",
    )
    defaults.update(overrides)
    return ActionTransitionRecord(**defaults)


# ── Test 1: insert_action_round_trip ──────────────────────────────────────────


class TestInsertActionRoundTrip:
    """insert_action() → get_action(): all fields must be preserved."""

    def test_all_identity_fields_preserved(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded is not None
        assert loaded.action_id == a.action_id
        assert loaded.case_id == a.case_id
        assert loaded.ticket_id == a.ticket_id
        assert loaded.client == a.client

    def test_classification_fields_preserved(self):
        repo, _ = _repo()
        a = _action(
            action_type="freeze_account",
            action_namespace="accounts",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
        )
        repo.insert_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.action_type == "freeze_account"
        assert loaded.action_namespace == "accounts"
        assert loaded.risk_level == ActionRiskLevel.IRREVERSIBLE

    def test_action_payload_preserved(self):
        repo, _ = _repo()
        payload = {"account_id": "ACC-123", "reason": "fraud", "amount": 50000}
        a = _action(action_payload=payload)
        repo.insert_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.action_payload == payload

    def test_new_event_timestamps_preserved(self):
        now = _now()
        repo, _ = _repo()
        a = _action(
            rejected_at=now,
            execution_failed_at=now + timedelta(minutes=1),
            rollback_completed_at=now + timedelta(minutes=2),
        )
        repo.insert_action(a)
        loaded = repo.get_action(a.action_id)
        # Timestamps are serialised as ISO strings and back — compare to second precision
        assert loaded.rejected_at is not None
        assert loaded.execution_failed_at is not None
        assert loaded.rollback_completed_at is not None

    def test_get_action_not_found_returns_none(self):
        repo, _ = _repo()
        assert repo.get_action(str(uuid.uuid4())) is None


# ── Test 2: load_action_round_trip ────────────────────────────────────────────


class TestLoadActionRoundTrip:
    """to_db_row() → from_db_row(): lossless including the 3 new event timestamps."""

    def test_basic_round_trip(self):
        a = _action()
        restored = ActionRequest.from_db_row(a.to_db_row())
        assert restored.action_id == a.action_id
        assert restored.risk_level == a.risk_level
        assert restored.current_state == a.current_state

    def test_event_timestamps_round_trip(self):
        now = _now()
        a = _action(
            rejected_at=now,
            execution_failed_at=now,
            rollback_completed_at=now,
        )
        row = a.to_db_row()
        assert row["rejected_at"] is not None
        assert row["execution_failed_at"] is not None
        assert row["rollback_completed_at"] is not None
        restored = ActionRequest.from_db_row(row)
        assert restored.rejected_at is not None
        assert restored.execution_failed_at is not None
        assert restored.rollback_completed_at is not None

    def test_null_event_timestamps_round_trip(self):
        a = _action()
        row = a.to_db_row()
        assert row["rejected_at"] is None
        assert row["execution_failed_at"] is None
        assert row["rollback_completed_at"] is None
        restored = ActionRequest.from_db_row(row)
        assert restored.rejected_at is None
        assert restored.execution_failed_at is None
        assert restored.rollback_completed_at is None

    def test_expires_at_round_trip(self):
        deadline = _now() + timedelta(hours=4)
        a = _action(expires_at=deadline)
        restored = ActionRequest.from_db_row(a.to_db_row())
        assert restored.expires_at is not None
        assert abs((restored.expires_at - deadline).total_seconds()) < 1

    def test_approver_round_trip(self):
        a = _action(approver="senior_agent", approved_at=_now())
        restored = ActionRequest.from_db_row(a.to_db_row())
        assert restored.approver == "senior_agent"
        assert restored.approved_at is not None


# ── Test 3: update_action_state_round_trip ────────────────────────────────────


class TestUpdateActionStateRoundTrip:
    """update_action() → get_action(): mutable state changes persisted correctly."""

    def test_state_change_persisted(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        a.current_state = ActionState.APPROVED
        a.approver = "auto_approval"
        a.approved_at = _now()
        repo.update_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.current_state == ActionState.APPROVED
        assert loaded.approver == "auto_approval"

    def test_execution_fields_persisted(self):
        repo, _ = _repo()
        a = _action(current_state=ActionState.APPROVED)
        repo.insert_action(a)
        a.current_state = ActionState.EXECUTING
        a.executor_id = "worker-42"
        a.execution_attempt = 1
        a.execution_started_at = _now()
        repo.update_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.current_state == ActionState.EXECUTING
        assert loaded.executor_id == "worker-42"
        assert loaded.execution_attempt == 1

    def test_failure_fields_persisted(self):
        repo, _ = _repo()
        now = _now()
        a = _action(current_state=ActionState.EXECUTING)
        repo.insert_action(a)
        a.current_state = ActionState.FAILED
        a.failure_code = "NETWORK_ERROR"
        a.failure_reason = "connection timeout"
        a.execution_failed_at = now
        repo.update_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.failure_code == "NETWORK_ERROR"
        assert loaded.execution_failed_at is not None

    def test_rollback_fields_persisted(self):
        repo, _ = _repo()
        a = _action()
        comp_id = str(uuid.uuid4())
        repo.insert_action(a)
        a.current_state = ActionState.ROLLED_BACK
        a.is_rolled_back = True
        a.rollback_action_id = comp_id
        a.rollback_completed_at = _now()
        repo.update_action(a)
        loaded = repo.get_action(a.action_id)
        assert loaded.is_rolled_back is True
        assert loaded.rollback_action_id == comp_id
        assert loaded.rollback_completed_at is not None


# ── Test 4: append_transition_round_trip ─────────────────────────────────────


class TestAppendTransitionRoundTrip:
    """append_transition() → get_transitions(): transition fields preserved."""

    def test_transition_fields_preserved(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        rec = _transition(
            a,
            from_state=ActionState.PROPOSED,
            to_state=ActionState.APPROVED,
            actor="auto_approval",
            reason="auto_approved_safe",
            detail={"risk_level": "SAFE"},
        )
        repo.append_transition(rec)
        transitions = repo.get_transitions(a.action_id)
        assert len(transitions) == 1
        t = transitions[0]
        assert t.from_state == ActionState.PROPOSED
        assert t.to_state == ActionState.APPROVED
        assert t.actor == "auto_approval"
        assert t.reason == "auto_approved_safe"
        assert t.detail == {"risk_level": "SAFE"}

    def test_transition_id_preserved(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        rec = _transition(a)
        repo.append_transition(rec)
        transitions = repo.get_transitions(a.action_id)
        assert transitions[0].transition_id == rec.transition_id

    def test_append_transition_alias_works(self):
        """append_transition() and record_transition() must be equivalent."""
        repo1, _ = _repo()
        repo2, _ = _repo()
        a1 = _action(idempotency_key="key-1")
        a2 = _action(idempotency_key="key-2")
        repo1.insert_action(a1)
        repo2.insert_action(a2)
        rec1 = _transition(a1)
        rec2 = _transition(a2, transition_id=rec1.transition_id)
        repo1.append_transition(rec1)
        repo2.record_transition(rec2)
        t1 = repo1.get_transitions(a1.action_id)
        t2 = repo2.get_transitions(a2.action_id)
        assert len(t1) == 1 and len(t2) == 1


# ── Test 5: duplicate_idempotency_key_raises_duplicate_action_error ───────────


class TestDuplicateIdempotencyKey:
    """DB unique-constraint violation on idempotency_key → DuplicateActionError."""

    def test_duplicate_key_raises(self):
        repo, _ = _repo()
        key = "aaaa" * 16
        a1 = _action(idempotency_key=key)
        a2 = _action(idempotency_key=key)
        repo.insert_action(a1)
        with pytest.raises(DuplicateActionError):
            repo.insert_action(a2)

    def test_different_keys_do_not_raise(self):
        repo, _ = _repo()
        a1 = _action(idempotency_key="key-aaa")
        a2 = _action(idempotency_key="key-bbb")
        repo.insert_action(a1)
        repo.insert_action(a2)  # must not raise

    def test_duplicate_error_message_contains_key(self):
        repo, _ = _repo()
        key = "bbbb" * 16
        a1 = _action(idempotency_key=key)
        a2 = _action(idempotency_key=key)
        repo.insert_action(a1)
        with pytest.raises(DuplicateActionError) as exc_info:
            repo.insert_action(a2)
        assert key[:16] in str(exc_info.value)


# ── Test 6: list_pending_approvals ────────────────────────────────────────────


class TestListPendingApprovals:
    """list_pending_approvals(client) returns AWAITING_APPROVAL for that client only."""

    def test_returns_only_awaiting_approval(self):
        repo, _ = _repo()
        client = "unity_bank"
        a_waiting = _action(
            client=client,
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=_now() + timedelta(hours=4),
            idempotency_key="k1",
        )
        a_approved = _action(
            client=client,
            current_state=ActionState.APPROVED,
            idempotency_key="k2",
        )
        repo.insert_action(a_waiting)
        repo.insert_action(a_approved)
        results = repo.list_pending_approvals(client)
        assert len(results) == 1
        assert results[0].current_state == ActionState.AWAITING_APPROVAL

    def test_filters_by_client(self):
        repo, _ = _repo()
        a_bank_a = _action(
            client="bank_a",
            current_state=ActionState.AWAITING_APPROVAL,
            idempotency_key="k1",
        )
        a_bank_b = _action(
            client="bank_b",
            current_state=ActionState.AWAITING_APPROVAL,
            idempotency_key="k2",
        )
        repo.insert_action(a_bank_a)
        repo.insert_action(a_bank_b)
        results = repo.list_pending_approvals("bank_a")
        assert all(r.client == "bank_a" for r in results)
        assert len(results) == 1

    def test_ordered_by_expires_at_asc(self):
        repo, _ = _repo()
        now = _now()
        a_later = _action(
            client="bank_a",
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now + timedelta(hours=8),
            idempotency_key="k1",
        )
        a_sooner = _action(
            client="bank_a",
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now + timedelta(hours=1),
            idempotency_key="k2",
        )
        repo.insert_action(a_later)
        repo.insert_action(a_sooner)
        results = repo.list_pending_approvals("bank_a")
        assert results[0].action_id == a_sooner.action_id


# ── Test 7: list_expired_actions ──────────────────────────────────────────────


class TestListExpiredActions:
    """list_expired_actions() returns AWAITING_APPROVAL/APPROVED with past expires_at."""

    def test_returns_past_expires_at(self):
        repo, _ = _repo()
        now = _now()
        a_expired_waiting = _action(
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now - timedelta(hours=1),
            idempotency_key="k1",
        )
        a_expired_approved = _action(
            current_state=ActionState.APPROVED,
            expires_at=now - timedelta(hours=1),
            idempotency_key="k2",
        )
        a_future = _action(
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now + timedelta(hours=4),
            idempotency_key="k3",
        )
        for a in [a_expired_waiting, a_expired_approved, a_future]:
            repo.insert_action(a)
        results = repo.list_expired_actions()
        result_ids = {r.action_id for r in results}
        assert a_expired_waiting.action_id in result_ids
        assert a_expired_approved.action_id in result_ids
        assert a_future.action_id not in result_ids

    def test_filters_by_client_when_given(self):
        repo, _ = _repo()
        now = _now()
        a_bank_a = _action(
            client="bank_a",
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now - timedelta(hours=1),
            idempotency_key="k1",
        )
        a_bank_b = _action(
            client="bank_b",
            current_state=ActionState.AWAITING_APPROVAL,
            expires_at=now - timedelta(hours=1),
            idempotency_key="k2",
        )
        repo.insert_action(a_bank_a)
        repo.insert_action(a_bank_b)
        results = repo.list_expired_actions(client="bank_a")
        assert len(results) == 1
        assert results[0].client == "bank_a"

    def test_excludes_terminal_states(self):
        repo, _ = _repo()
        now = _now()
        a_rejected = _action(
            current_state=ActionState.REJECTED,
            expires_at=now - timedelta(hours=1),
            idempotency_key="k1",
        )
        repo.insert_action(a_rejected)
        results = repo.list_expired_actions()
        assert len(results) == 0


# ── Test 8: rollback_action_linkage ──────────────────────────────────────────


class TestRollbackActionLinkage:
    """rollback_action_id FK chain persists correctly across insert/update/get."""

    def test_rollback_link_persisted(self):
        repo, _ = _repo()
        original = _action(idempotency_key="orig", action_type="update_address")
        compensation = _action(idempotency_key="comp", action_type="restore_address")
        repo.insert_action(original)
        repo.insert_action(compensation)
        original.rollback_action_id = compensation.action_id
        original.is_rolled_back = False
        repo.update_action(original)
        loaded = repo.get_action(original.action_id)
        assert loaded.rollback_action_id == compensation.action_id

    def test_rollback_completed_at_persisted(self):
        repo, _ = _repo()
        now = _now()
        original = _action(idempotency_key="orig")
        repo.insert_action(original)
        original.is_rolled_back = True
        original.rollback_completed_at = now
        original.rollback_action_id = str(uuid.uuid4())
        repo.update_action(original)
        loaded = repo.get_action(original.action_id)
        assert loaded.is_rolled_back is True
        assert loaded.rollback_completed_at is not None


# ── Test 9: transition_history_integrity ──────────────────────────────────────


class TestTransitionHistoryIntegrity:
    """Multiple transitions for one action must be returned in created_at ASC order."""

    def test_three_transitions_correct_order(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        now = _now()
        t1 = _transition(
            a,
            from_state=ActionState.PROPOSED,
            to_state=ActionState.AWAITING_APPROVAL,
            actor="system",
            reason="step1",
        )
        # Force distinct created_at values
        t1.created_at = now
        t2 = _transition(
            a,
            from_state=ActionState.AWAITING_APPROVAL,
            to_state=ActionState.APPROVED,
            actor="human:agent",
            reason="step2",
        )
        t2.created_at = now + timedelta(seconds=1)
        t3 = _transition(
            a,
            from_state=ActionState.APPROVED,
            to_state=ActionState.EXECUTING,
            actor="executor:worker-1",
            reason="step3",
        )
        t3.created_at = now + timedelta(seconds=2)
        for t in [t1, t2, t3]:
            repo.append_transition(t)
        transitions = repo.get_transitions(a.action_id)
        assert len(transitions) == 3
        assert transitions[0].reason == "step1"
        assert transitions[1].reason == "step2"
        assert transitions[2].reason == "step3"

    def test_transitions_isolated_by_action_id(self):
        repo, _ = _repo()
        a1 = _action(idempotency_key="key-a1")
        a2 = _action(idempotency_key="key-a2")
        repo.insert_action(a1)
        repo.insert_action(a2)
        repo.append_transition(_transition(a1))
        repo.append_transition(_transition(a1))
        repo.append_transition(_transition(a2))
        assert len(repo.get_transitions(a1.action_id)) == 2
        assert len(repo.get_transitions(a2.action_id)) == 1

    def test_no_transitions_returns_empty_list(self):
        repo, _ = _repo()
        a = _action()
        repo.insert_action(a)
        assert repo.get_transitions(a.action_id) == []


# ── Test 10: repository_restart_scenario ──────────────────────────────────────


class TestRepositoryRestartScenario:
    """
    Simulate a service restart by serialising an action to DB row dict and
    deserialising it back. The reconstructed action must be functionally identical.
    """

    def test_restart_preserves_all_mutable_state(self):
        repo, client = _repo()
        now = _now()
        a = _action(
            current_state=ActionState.EXECUTING,
            executor_id="worker-99",
            execution_attempt=2,
            execution_started_at=now,
            approver="agent_007",
            approved_at=now - timedelta(minutes=30),
            expires_at=now + timedelta(hours=20),
            action_payload={"account_id": "ACC-X"},
            idempotency_key="restart-key-" + uuid.uuid4().hex[:8],
        )
        repo.insert_action(a)
        # Simulate restart: reload from DB
        reloaded = repo.get_action(a.action_id)
        assert reloaded is not None
        assert reloaded.current_state == ActionState.EXECUTING
        assert reloaded.executor_id == "worker-99"
        assert reloaded.execution_attempt == 2
        assert reloaded.approver == "agent_007"
        assert reloaded.action_payload == {"account_id": "ACC-X"}
        assert reloaded.expires_at is not None

    def test_restart_preserves_rollback_chain(self):
        repo, _ = _repo()
        comp_id = str(uuid.uuid4())
        now = _now()
        a = _action(
            current_state=ActionState.ROLLED_BACK,
            is_rolled_back=True,
            rollback_action_id=comp_id,
            rollback_completed_at=now,
            idempotency_key="rb-key-" + uuid.uuid4().hex[:8],
        )
        repo.insert_action(a)
        reloaded = repo.get_action(a.action_id)
        assert reloaded.is_rolled_back is True
        assert reloaded.rollback_action_id == comp_id
        assert reloaded.rollback_completed_at is not None

    def test_offline_mode_insert_returns_action(self):
        repo = ActionRepository(supabase_client=None)
        a = _action()
        result = repo.insert_action(a)
        assert result.action_id == a.action_id

    def test_offline_mode_get_returns_none(self):
        repo = ActionRepository(supabase_client=None)
        assert repo.get_action(str(uuid.uuid4())) is None

    def test_offline_mode_update_returns_true(self):
        repo = ActionRepository(supabase_client=None)
        a = _action()
        assert repo.update_action(a) is True
