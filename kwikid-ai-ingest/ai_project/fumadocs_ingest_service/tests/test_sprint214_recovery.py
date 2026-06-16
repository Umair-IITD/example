"""
tests/test_sprint214_recovery.py

Sprint 2.14: Human Recovery & Operational Control — test suite.

Coverage:
  Section                                       Tests
  ─────────────────────────────────────────── ──────
  CANCELLED state machine                          8
  audit/models.py new event types                  4
  action_models.py cancelled_at field              4
  ActionGatewayRecoveryService.retry_dead_letter  12
  ActionGatewayRecoveryService.retry_failed       10
  ActionGatewayRecoveryService.cancel_action      10
  ActionGatewayRecoveryService.expire_manually    10
  ActionGatewayRecoveryService.force_rollback     10
  API: GET /actions/{id}                           8
  API: GET /actions/{id}/transitions               6
  API: GET /actions/{id}/audit                     6
  API: POST /actions/{id}/retry                    8
  API: POST /actions/{id}/cancel                   8
  API: POST /actions/{id}/expire                   8
  API: POST /actions/{id}/rollback                 8
  Assembly wiring                                  5
  ─────────────────────────────────────────── ──────
  Total                                          125
"""
from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from unittest.mock import MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from audit.models import AuditEvent, AuditEventType
from audit.repository import InMemoryAuditRepository
from audit.service import AuditService
from case_engine.action_gateway_recovery import (
    ALREADY_ROLLED_BACK,
    INTERNAL_ERROR,
    INVALID_STATE,
    LOCK_CONTENTION,
    NOT_FOUND,
    NOT_REVERSIBLE,
    NO_ROLLBACK_SPEC,
    RETRY_EXHAUSTED,
    ActionGatewayRecoveryService,
    RecoveryResult,
)
from case_engine.action_models import ActionRequest, ActionTransitionRecord
from case_engine.action_state import (
    ALLOWED_ACTION_TRANSITIONS,
    TERMINAL_ACTION_STATES,
    ActionRiskLevel,
    ActionState,
    ActionStateMachine,
)


def _now():
    return datetime.now(tz=timezone.utc)


def _new_id():
    return str(uuid.uuid4())


# ── Fake helpers ──────────────────────────────────────────────────────────────

def _make_action(
    state: ActionState = ActionState.DEAD_LETTER,
    risk_level: ActionRiskLevel = ActionRiskLevel.SAFE,
    execution_attempt: int = 3,
    max_attempts: int = 3,
    rollback_action_type: str | None = None,
    rollback_params: dict | None = None,
    is_rolled_back: bool = False,
    rollback_action_id: str | None = None,
    dead_lettered_at: datetime | None = None,
    cancelled_at: datetime | None = None,
) -> ActionRequest:
    action = ActionRequest(
        action_id=_new_id(),
        case_id=_new_id(),
        ticket_id="T-001",
        client="unity_bank",
        action_type="add_note",
        action_namespace="ticket",
        risk_level=risk_level,
        current_state=state,
        execution_attempt=execution_attempt,
        max_attempts=max_attempts,
        rollback_action_type=rollback_action_type,
        rollback_params=rollback_params,
        is_rolled_back=is_rolled_back,
        rollback_action_id=rollback_action_id,
        dead_lettered_at=dead_lettered_at or (
            _now() if state == ActionState.DEAD_LETTER else None
        ),
        cancelled_at=cancelled_at,
    )
    return action


@dataclass
class _FakeRepo:
    """Minimal stub of ActionRepository."""
    _action: ActionRequest | None = None
    _transitions: list = field(default_factory=list)
    _update_returns: bool = True
    _get_raises: bool = False
    _update_raises: bool = False

    def get_action(self, action_id: str) -> ActionRequest | None:
        if self._get_raises:
            raise RuntimeError("DB offline")
        return self._action

    def record_transition(self, record) -> bool:
        self._transitions.append(record)
        return True

    def update_action(self, action, *, expected_state=None) -> bool:
        if self._update_raises:
            raise RuntimeError("DB write failed")
        return self._update_returns

    def get_transitions(self, action_id: str) -> list:
        return self._transitions


@dataclass
class _FakeGateway:
    """Minimal stub of ActionGateway."""
    _propose_rollback_returns: ActionRequest | None = None
    _propose_rollback_raises: Exception | None = None

    def propose_rollback(self, action, case):
        if self._propose_rollback_raises is not None:
            raise self._propose_rollback_raises
        if self._propose_rollback_returns is not None:
            action.current_state = ActionState.ROLLING_BACK
            action.rollback_action_id = self._propose_rollback_returns.action_id
            return self._propose_rollback_returns
        raise RuntimeError("propose_rollback not configured")


def _make_recovery_service(
    action: ActionRequest | None = None,
    update_returns: bool = True,
    get_raises: bool = False,
    update_raises: bool = False,
    gateway: _FakeGateway | None = None,
    audit_service: AuditService | None = None,
) -> tuple[ActionGatewayRecoveryService, _FakeRepo]:
    repo = _FakeRepo(
        _action=action,
        _update_returns=update_returns,
        _get_raises=get_raises,
        _update_raises=update_raises,
    )
    gw = gateway or _FakeGateway()
    svc = ActionGatewayRecoveryService(
        repository=repo,
        gateway=gw,
        audit_service=audit_service,
    )
    return svc, repo


# ── Test client helpers ───────────────────────────────────────────────────────

def _make_admin_auth():
    from security.auth import AuthContext
    from security.roles import Role
    ctx = MagicMock(spec=AuthContext)
    ctx.role = Role.ADMIN
    authenticator = MagicMock()
    result = MagicMock()
    result.authenticated = True
    result.role = Role.ADMIN
    result.has_permission.return_value = True
    authenticator.authenticate.return_value = result
    return authenticator


def _make_unauthenticated_auth():
    authenticator = MagicMock()
    result = MagicMock()
    result.authenticated = False
    result.role = None
    authenticator.authenticate.return_value = result
    return authenticator


@dataclass
class _FakeStack:
    repository: Any = None
    recovery: Any = None
    audit_repository: Any = None


def _make_api_client(
    action: ActionRequest | None = None,
    transitions: list | None = None,
    audit_events: list | None = None,
    recovery_svc: Any = None,
    stack_none: bool = False,
    authenticator: Any = None,
) -> TestClient:
    from api.routes import recovery_admin
    from security.dependencies import require_admin

    app = FastAPI()

    repo = _FakeRepo(_action=action, _transitions=transitions or [])
    audit_repo = InMemoryAuditRepository()
    for ev in (audit_events or []):
        audit_repo.insert_event(ev)

    if stack_none:
        app.state.stack = None
    else:
        stack = _FakeStack(
            repository=repo,
            recovery=recovery_svc,
            audit_repository=audit_repo,
        )
        app.state.stack = stack

    authenticator = authenticator or _make_admin_auth()
    app.state.authenticator = authenticator

    app.include_router(recovery_admin.router)
    return TestClient(app, raise_server_exceptions=False)


# ═════════════════════════════════════════════════════════════════════════════
# Section 1: CANCELLED state machine (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelledStateMachine:
    def test_cancelled_exists_in_enum(self):
        assert ActionState.CANCELLED == ActionState("CANCELLED")

    def test_cancelled_is_terminal(self):
        assert ActionState.CANCELLED in TERMINAL_ACTION_STATES

    def test_proposed_can_transition_to_cancelled(self):
        sm = ActionStateMachine()
        assert sm.can_transition(ActionState.PROPOSED, ActionState.CANCELLED)

    def test_awaiting_approval_can_transition_to_cancelled(self):
        sm = ActionStateMachine()
        assert sm.can_transition(ActionState.AWAITING_APPROVAL, ActionState.CANCELLED)

    def test_approved_can_transition_to_cancelled(self):
        sm = ActionStateMachine()
        assert sm.can_transition(ActionState.APPROVED, ActionState.CANCELLED)

    def test_executing_cannot_transition_to_cancelled(self):
        sm = ActionStateMachine()
        assert not sm.can_transition(ActionState.EXECUTING, ActionState.CANCELLED)

    def test_cancelled_has_no_outgoing_transitions(self):
        assert ALLOWED_ACTION_TRANSITIONS[ActionState.CANCELLED] == frozenset()

    def test_cancelled_can_transition_returns_false_from_cancelled(self):
        sm = ActionStateMachine()
        assert not sm.can_transition(ActionState.CANCELLED, ActionState.PROPOSED)


# ═════════════════════════════════════════════════════════════════════════════
# Section 2: Audit event types (4 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestAuditEventTypes:
    def test_action_cancelled_exists(self):
        assert AuditEventType.ACTION_CANCELLED == AuditEventType("ACTION_CANCELLED")

    def test_action_recovered_from_dead_letter_exists(self):
        assert AuditEventType.ACTION_RECOVERED_FROM_DEAD_LETTER.value == "ACTION_RECOVERED_FROM_DEAD_LETTER"

    def test_action_manually_expired_exists(self):
        assert AuditEventType.ACTION_MANUALLY_EXPIRED.value == "ACTION_MANUALLY_EXPIRED"

    def test_action_rollback_triggered_exists(self):
        assert AuditEventType.ACTION_ROLLBACK_TRIGGERED.value == "ACTION_ROLLBACK_TRIGGERED"


# ═════════════════════════════════════════════════════════════════════════════
# Section 3: action_models.py cancelled_at field (4 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelledAtField:
    def test_cancelled_at_defaults_to_none(self):
        action = ActionRequest()
        assert action.cancelled_at is None

    def test_to_db_row_includes_cancelled_at(self):
        action = ActionRequest(cancelled_at=_now())
        row = action.to_db_row()
        assert "cancelled_at" in row
        assert row["cancelled_at"] is not None

    def test_to_db_row_cancelled_at_none(self):
        action = ActionRequest()
        row = action.to_db_row()
        assert row["cancelled_at"] is None

    def test_from_db_row_reads_cancelled_at(self):
        ts = _now()
        row = ActionRequest().to_db_row()
        row["cancelled_at"] = ts.isoformat()
        action = ActionRequest.from_db_row(row)
        assert action.cancelled_at is not None


# ═════════════════════════════════════════════════════════════════════════════
# Section 4: retry_dead_letter_action (12 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestRetryDeadLetterAction:
    def test_success_returns_approved(self):
        action = _make_action(state=ActionState.DEAD_LETTER, execution_attempt=3)
        svc, repo = _make_recovery_service(action=action)
        result = svc.retry_dead_letter_action(action.action_id, actor="operator:alice")
        assert result.success is True
        assert result.old_state == "DEAD_LETTER"
        assert result.new_state == "APPROVED"

    def test_resets_execution_attempt_to_zero(self):
        action = _make_action(state=ActionState.DEAD_LETTER, execution_attempt=3)
        svc, repo = _make_recovery_service(action=action)
        svc.retry_dead_letter_action(action.action_id, actor="op")
        assert action.execution_attempt == 0

    def test_clears_dead_lettered_at(self):
        action = _make_action(state=ActionState.DEAD_LETTER, dead_lettered_at=_now())
        svc, repo = _make_recovery_service(action=action)
        svc.retry_dead_letter_action(action.action_id, actor="op")
        assert action.dead_lettered_at is None

    def test_clears_failure_fields(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        action.failure_code = "EXEC_FAILED"
        action.failure_reason = "Provider returned 503"
        svc, repo = _make_recovery_service(action=action)
        svc.retry_dead_letter_action(action.action_id, actor="op")
        assert action.failure_code is None
        assert action.failure_reason is None

    def test_records_transition(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, repo = _make_recovery_service(action=action)
        svc.retry_dead_letter_action(action.action_id, actor="op")
        assert len(repo._transitions) == 1
        t = repo._transitions[0]
        assert t.from_state == ActionState.DEAD_LETTER
        assert t.to_state == ActionState.APPROVED

    def test_not_found_returns_error(self):
        svc, _ = _make_recovery_service(action=None)
        result = svc.retry_dead_letter_action("missing-id", actor="op")
        assert result.success is False
        assert result.error_code == NOT_FOUND

    def test_wrong_state_returns_invalid_state(self):
        action = _make_action(state=ActionState.FAILED)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_dead_letter_action(action.action_id, actor="op")
        assert result.success is False
        assert result.error_code == INVALID_STATE
        assert result.old_state == "FAILED"

    def test_lock_contention_returns_error(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        result = svc.retry_dead_letter_action(action.action_id, actor="op")
        assert result.success is False
        assert result.error_code == LOCK_CONTENTION

    def test_repo_get_exception_returns_internal_error(self):
        svc, _ = _make_recovery_service(action=None, get_raises=True)
        result = svc.retry_dead_letter_action("any-id", actor="op")
        assert result.success is False
        assert result.error_code == INTERNAL_ERROR

    def test_emits_audit_event(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        audit_repo = InMemoryAuditRepository()
        audit_svc = AuditService(repository=audit_repo)
        svc, _ = _make_recovery_service(action=action, audit_service=audit_svc)
        svc.retry_dead_letter_action(action.action_id, actor="op:alice")
        events = audit_repo.events_for_action(action.action_id)
        assert len(events) == 1
        assert events[0].event_type == AuditEventType.ACTION_RECOVERED_FROM_DEAD_LETTER

    def test_result_is_frozen(self):
        result = RecoveryResult(success=True, action_id="x", old_state="DEAD_LETTER", new_state="APPROVED")
        with pytest.raises((dataclasses.FrozenInstanceError, AttributeError)):
            result.success = False  # type: ignore[misc]

    def test_action_state_set_to_approved_in_memory(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        svc.retry_dead_letter_action(action.action_id, actor="op")
        assert action.current_state == ActionState.APPROVED


# ═════════════════════════════════════════════════════════════════════════════
# Section 5: retry_failed_action (10 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestRetryFailedAction:
    def test_success_returns_approved(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.success is True
        assert result.new_state == "APPROVED"

    def test_not_found_returns_error(self):
        svc, _ = _make_recovery_service(action=None)
        result = svc.retry_failed_action("missing", actor="op")
        assert result.error_code == NOT_FOUND

    def test_wrong_state_returns_invalid_state(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_exhausted_budget_returns_retry_exhausted(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=3, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.error_code == RETRY_EXHAUSTED

    def test_records_transition(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=3)
        svc, repo = _make_recovery_service(action=action)
        svc.retry_failed_action(action.action_id, actor="op")
        assert len(repo._transitions) == 1
        assert repo._transitions[0].to_state == ActionState.APPROVED

    def test_lock_contention_returns_error(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=3)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.error_code == LOCK_CONTENTION

    def test_partial_budget_still_retryable(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=2, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.success is True

    def test_single_attempt_exhausted_not_retryable(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=1)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.error_code == RETRY_EXHAUSTED

    def test_repo_exception_returns_internal_error(self):
        svc, _ = _make_recovery_service(action=None, get_raises=True)
        result = svc.retry_failed_action("x", actor="op")
        assert result.error_code == INTERNAL_ERROR

    def test_old_state_preserved_in_result(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.retry_failed_action(action.action_id, actor="op")
        assert result.old_state == "FAILED"


# ═════════════════════════════════════════════════════════════════════════════
# Section 6: cancel_action (10 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelAction:
    @pytest.mark.parametrize("state", [
        ActionState.PROPOSED,
        ActionState.AWAITING_APPROVAL,
        ActionState.APPROVED,
    ])
    def test_success_from_valid_states(self, state):
        action = _make_action(state=state, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.cancel_action(action.action_id, actor="op")
        assert result.success is True
        assert result.new_state == "CANCELLED"

    def test_sets_cancelled_at_on_action(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        svc.cancel_action(action.action_id, actor="op")
        assert action.cancelled_at is not None

    def test_executing_cannot_be_cancelled(self):
        action = _make_action(state=ActionState.EXECUTING)
        svc, _ = _make_recovery_service(action=action)
        result = svc.cancel_action(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_dead_letter_cannot_be_cancelled(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        result = svc.cancel_action(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        result = svc.cancel_action("missing", actor="op")
        assert result.error_code == NOT_FOUND

    def test_records_transition(self):
        action = _make_action(state=ActionState.PROPOSED, execution_attempt=0, max_attempts=3)
        svc, repo = _make_recovery_service(action=action)
        svc.cancel_action(action.action_id, actor="op")
        assert len(repo._transitions) == 1
        assert repo._transitions[0].to_state == ActionState.CANCELLED

    def test_lock_contention(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        result = svc.cancel_action(action.action_id, actor="op")
        assert result.error_code == LOCK_CONTENTION

    def test_emits_audit_event(self):
        action = _make_action(state=ActionState.PROPOSED, execution_attempt=0, max_attempts=3)
        audit_repo = InMemoryAuditRepository()
        svc, _ = _make_recovery_service(
            action=action,
            audit_service=AuditService(repository=audit_repo),
        )
        svc.cancel_action(action.action_id, actor="op")
        events = audit_repo.events_for_action(action.action_id)
        assert any(e.event_type == AuditEventType.ACTION_CANCELLED for e in events)

    def test_exception_returns_internal_error(self):
        svc, _ = _make_recovery_service(action=None, get_raises=True)
        result = svc.cancel_action("x", actor="op")
        assert result.error_code == INTERNAL_ERROR

    def test_old_state_in_result(self):
        action = _make_action(state=ActionState.AWAITING_APPROVAL, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.cancel_action(action.action_id, actor="op")
        assert result.old_state == "AWAITING_APPROVAL"


# ═════════════════════════════════════════════════════════════════════════════
# Section 7: expire_action_manually (10 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestExpireActionManually:
    @pytest.mark.parametrize("state", [
        ActionState.AWAITING_APPROVAL,
        ActionState.APPROVED,
    ])
    def test_success_from_valid_states(self, state):
        action = _make_action(state=state, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.expire_action_manually(action.action_id, actor="op")
        assert result.success is True
        assert result.new_state == "EXPIRED"

    def test_executing_cannot_be_expired_manually(self):
        action = _make_action(state=ActionState.EXECUTING)
        svc, _ = _make_recovery_service(action=action)
        result = svc.expire_action_manually(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_terminal_state_cannot_be_expired(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        result = svc.expire_action_manually(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        result = svc.expire_action_manually("missing", actor="op")
        assert result.error_code == NOT_FOUND

    def test_records_transition(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, repo = _make_recovery_service(action=action)
        svc.expire_action_manually(action.action_id, actor="op")
        assert len(repo._transitions) == 1
        assert repo._transitions[0].to_state == ActionState.EXPIRED

    def test_lock_contention(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        result = svc.expire_action_manually(action.action_id, actor="op")
        assert result.error_code == LOCK_CONTENTION

    def test_emits_audit_event(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        audit_repo = InMemoryAuditRepository()
        svc, _ = _make_recovery_service(
            action=action,
            audit_service=AuditService(repository=audit_repo),
        )
        svc.expire_action_manually(action.action_id, actor="op")
        events = audit_repo.events_for_action(action.action_id)
        assert any(e.event_type == AuditEventType.ACTION_MANUALLY_EXPIRED for e in events)

    def test_old_state_in_result(self):
        action = _make_action(state=ActionState.AWAITING_APPROVAL, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        result = svc.expire_action_manually(action.action_id, actor="op")
        assert result.old_state == "AWAITING_APPROVAL"

    def test_actor_preserved_in_transition(self):
        action = _make_action(state=ActionState.AWAITING_APPROVAL, execution_attempt=0, max_attempts=3)
        svc, repo = _make_recovery_service(action=action)
        svc.expire_action_manually(action.action_id, actor="human:bob")
        assert repo._transitions[0].actor == "human:bob"

    def test_exception_returns_internal_error(self):
        svc, _ = _make_recovery_service(action=None, get_raises=True)
        result = svc.expire_action_manually("x", actor="op")
        assert result.error_code == INTERNAL_ERROR


# ═════════════════════════════════════════════════════════════════════════════
# Section 8: force_rollback_action (10 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestForceRollbackAction:
    def _make_reversible_executed(self) -> ActionRequest:
        return _make_action(
            state=ActionState.EXECUTED,
            risk_level=ActionRiskLevel.REVERSIBLE,
            execution_attempt=1,
            max_attempts=3,
            rollback_action_type="remove_note",
            rollback_params={"note_id": "123"},
        )

    def test_success_returns_rolling_back(self):
        action = self._make_reversible_executed()
        compensation = _make_action(state=ActionState.APPROVED)
        gw = _FakeGateway(_propose_rollback_returns=compensation)
        svc, _ = _make_recovery_service(action=action, gateway=gw)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.success is True
        assert result.new_state == "ROLLING_BACK"

    def test_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        result = svc.force_rollback_action("missing", actor="op")
        assert result.error_code == NOT_FOUND

    def test_wrong_state_returns_invalid_state(self):
        action = _make_action(state=ActionState.APPROVED)
        svc, _ = _make_recovery_service(action=action)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.error_code == INVALID_STATE

    def test_safe_risk_level_not_reversible(self):
        action = _make_action(
            state=ActionState.EXECUTED,
            risk_level=ActionRiskLevel.SAFE,
            rollback_action_type="remove_note",
        )
        svc, _ = _make_recovery_service(action=action)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.error_code == NOT_REVERSIBLE

    def test_no_rollback_spec_returns_error(self):
        action = _make_action(
            state=ActionState.EXECUTED,
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type=None,
        )
        svc, _ = _make_recovery_service(action=action)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.error_code == NO_ROLLBACK_SPEC

    def test_already_rolled_back_returns_error(self):
        action = self._make_reversible_executed()
        action.is_rolled_back = True
        action.rollback_action_id = _new_id()
        svc, _ = _make_recovery_service(action=action)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.error_code == ALREADY_ROLLED_BACK

    def test_gateway_exception_returns_internal_error(self):
        action = self._make_reversible_executed()
        gw = _FakeGateway(_propose_rollback_raises=RuntimeError("gateway failed"))
        svc, _ = _make_recovery_service(action=action, gateway=gw)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.error_code == INTERNAL_ERROR

    def test_emits_audit_event(self):
        action = self._make_reversible_executed()
        compensation = _make_action(state=ActionState.APPROVED)
        gw = _FakeGateway(_propose_rollback_returns=compensation)
        audit_repo = InMemoryAuditRepository()
        svc, _ = _make_recovery_service(
            action=action, gateway=gw,
            audit_service=AuditService(repository=audit_repo),
        )
        svc.force_rollback_action(action.action_id, actor="op")
        events = audit_repo.events_for_action(action.action_id)
        assert any(e.event_type == AuditEventType.ACTION_ROLLBACK_TRIGGERED for e in events)

    def test_old_state_in_result(self):
        action = self._make_reversible_executed()
        compensation = _make_action(state=ActionState.APPROVED)
        gw = _FakeGateway(_propose_rollback_returns=compensation)
        svc, _ = _make_recovery_service(action=action, gateway=gw)
        result = svc.force_rollback_action(action.action_id, actor="op")
        assert result.old_state == "EXECUTED"

    def test_get_raises_returns_internal_error(self):
        svc, _ = _make_recovery_service(action=None, get_raises=True)
        result = svc.force_rollback_action("x", actor="op")
        assert result.error_code == INTERNAL_ERROR


# ═════════════════════════════════════════════════════════════════════════════
# Section 9: API — GET /actions/{action_id} (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestGetActionEndpoint:
    def test_200_returns_action(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["action_id"] == action.action_id
        assert data["current_state"] == "APPROVED"

    def test_404_when_not_found(self):
        client = _make_api_client(action=None)
        resp = client.get("/admin/action-gateway/actions/nonexistent-id")
        assert resp.status_code == 404

    def test_503_when_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.get("/admin/action-gateway/actions/any-id")
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action, authenticator=_make_unauthenticated_auth())
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        assert resp.status_code == 401

    def test_includes_cancelled_at_field(self):
        action = _make_action(state=ActionState.CANCELLED, execution_attempt=0, max_attempts=3)
        action.cancelled_at = _now()
        client = _make_api_client(action=action)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        assert resp.status_code == 200
        assert "cancelled_at" in resp.json()

    def test_includes_dead_lettered_at(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        client = _make_api_client(action=action)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        assert resp.status_code == 200
        assert "dead_lettered_at" in resp.json()

    def test_response_has_risk_level(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        data = resp.json()
        assert "risk_level" in data

    def test_response_has_client_field(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}")
        assert resp.json()["client"] == "unity_bank"


# ═════════════════════════════════════════════════════════════════════════════
# Section 10: API — GET /actions/{id}/transitions (6 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestGetTransitionsEndpoint:
    def _make_transition(self, action_id: str) -> ActionTransitionRecord:
        return ActionTransitionRecord(
            action_id=action_id,
            case_id=_new_id(),
            ticket_id="T-001",
            client="unity_bank",
            from_state=ActionState.PROPOSED,
            to_state=ActionState.APPROVED,
            actor="system",
            reason="auto_approved",
        )

    def test_200_returns_transitions(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        t = self._make_transition(action.action_id)
        client = _make_api_client(action=action, transitions=[t])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/transitions")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["transitions"][0]["from_state"] == "PROPOSED"

    def test_empty_transitions(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action, transitions=[])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/transitions")
        assert resp.status_code == 200
        assert resp.json()["total"] == 0

    def test_503_when_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.get("/admin/action-gateway/actions/any/transitions")
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/actions/any/transitions")
        assert resp.status_code == 401

    def test_response_has_action_id_key(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action, transitions=[])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/transitions")
        assert "action_id" in resp.json()

    def test_transition_fields_present(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        t = self._make_transition(action.action_id)
        client = _make_api_client(action=action, transitions=[t])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/transitions")
        item = resp.json()["transitions"][0]
        for f in ("transition_id", "from_state", "to_state", "actor", "reason", "created_at"):
            assert f in item, f"missing field: {f}"


# ═════════════════════════════════════════════════════════════════════════════
# Section 11: API — GET /actions/{id}/audit (6 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestGetAuditEndpoint:
    def _make_event(self, action_id: str) -> AuditEvent:
        return AuditEvent(
            action_id=action_id,
            event_type=AuditEventType.ACTION_CANCELLED,
            actor="op:alice",
            case_id=_new_id(),
            client="unity_bank",
        )

    def test_200_returns_events(self):
        action = _make_action(state=ActionState.CANCELLED, execution_attempt=0, max_attempts=3)
        ev = self._make_event(action.action_id)
        client = _make_api_client(action=action, audit_events=[ev])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/audit")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 1
        assert data["events"][0]["event_type"] == "ACTION_CANCELLED"

    def test_empty_audit(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        client = _make_api_client(action=action, audit_events=[])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/audit")
        assert resp.json()["total"] == 0

    def test_503_when_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.get("/admin/action-gateway/actions/any/audit")
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.get("/admin/action-gateway/actions/any/audit")
        assert resp.status_code == 401

    def test_event_fields_present(self):
        action = _make_action(state=ActionState.CANCELLED, execution_attempt=0, max_attempts=3)
        ev = self._make_event(action.action_id)
        client = _make_api_client(action=action, audit_events=[ev])
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/audit")
        item = resp.json()["events"][0]
        for f in ("event_id", "action_id", "event_type", "actor", "timestamp"):
            assert f in item

    def test_multiple_events_returned(self):
        action = _make_action(state=ActionState.CANCELLED, execution_attempt=0, max_attempts=3)
        events = [self._make_event(action.action_id) for _ in range(3)]
        client = _make_api_client(action=action, audit_events=events)
        resp = client.get(f"/admin/action-gateway/actions/{action.action_id}/audit")
        assert resp.json()["total"] == 3


# ═════════════════════════════════════════════════════════════════════════════
# Section 12: API — POST /actions/{id}/retry (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestRetryEndpoint:
    def _make_svc_dead_letter(self) -> tuple[ActionGatewayRecoveryService, ActionRequest]:
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        return svc, action

    def test_200_dead_letter_retry(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/retry",
            json={"actor": "op:alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["success"] is True
        assert resp.json()["new_state"] == "APPROVED"

    def test_200_failed_retry(self):
        action = _make_action(state=ActionState.FAILED, execution_attempt=1, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/retry",
            json={"actor": "op:alice"},
        )
        assert resp.status_code == 200

    def test_422_missing_actor(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/retry",
            json={},
        )
        assert resp.status_code == 422

    def test_404_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        client = _make_api_client(action=None, recovery_svc=svc)
        resp = client.post(
            "/admin/action-gateway/actions/nonexistent/retry",
            json={"actor": "op"},
        )
        assert resp.status_code == 404

    def test_409_invalid_state(self):
        action = _make_action(state=ActionState.EXECUTING)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/retry",
            json={"actor": "op"},
        )
        assert resp.status_code == 409

    def test_503_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.post("/admin/action-gateway/actions/x/retry", json={"actor": "op"})
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.post("/admin/action-gateway/actions/x/retry", json={"actor": "op"})
        assert resp.status_code == 401

    def test_response_has_old_and_new_state(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/retry",
            json={"actor": "op"},
        )
        data = resp.json()
        assert "old_state" in data and "new_state" in data


# ═════════════════════════════════════════════════════════════════════════════
# Section 13: API — POST /actions/{id}/cancel (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestCancelEndpoint:
    def test_200_success(self):
        action = _make_action(state=ActionState.PROPOSED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/cancel",
            json={"actor": "op:alice", "reason": "wrong_action"},
        )
        assert resp.status_code == 200
        assert resp.json()["new_state"] == "CANCELLED"

    def test_422_missing_actor(self):
        action = _make_action(state=ActionState.PROPOSED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/cancel",
            json={},
        )
        assert resp.status_code == 422

    def test_409_invalid_state(self):
        action = _make_action(state=ActionState.DEAD_LETTER)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/cancel",
            json={"actor": "op"},
        )
        assert resp.status_code == 409

    def test_404_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        client = _make_api_client(action=None, recovery_svc=svc)
        resp = client.post(
            "/admin/action-gateway/actions/nonexistent/cancel",
            json={"actor": "op"},
        )
        assert resp.status_code == 404

    def test_503_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.post("/admin/action-gateway/actions/x/cancel", json={"actor": "op"})
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.post("/admin/action-gateway/actions/x/cancel", json={"actor": "op"})
        assert resp.status_code == 401

    def test_response_success_flag(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/cancel",
            json={"actor": "op"},
        )
        assert resp.json()["success"] is True

    def test_409_lock_contention(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/cancel",
            json={"actor": "op"},
        )
        assert resp.status_code == 409


# ═════════════════════════════════════════════════════════════════════════════
# Section 14: API — POST /actions/{id}/expire (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestExpireEndpoint:
    def test_200_success(self):
        action = _make_action(state=ActionState.AWAITING_APPROVAL, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/expire",
            json={"actor": "op:alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["new_state"] == "EXPIRED"

    def test_422_missing_actor(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/expire",
            json={},
        )
        assert resp.status_code == 422

    def test_409_invalid_state(self):
        action = _make_action(state=ActionState.EXECUTING)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/expire",
            json={"actor": "op"},
        )
        assert resp.status_code == 409

    def test_404_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        client = _make_api_client(action=None, recovery_svc=svc)
        resp = client.post(
            "/admin/action-gateway/actions/nonexistent/expire",
            json={"actor": "op"},
        )
        assert resp.status_code == 404

    def test_503_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.post("/admin/action-gateway/actions/x/expire", json={"actor": "op"})
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.post("/admin/action-gateway/actions/x/expire", json={"actor": "op"})
        assert resp.status_code == 401

    def test_old_state_in_response(self):
        action = _make_action(state=ActionState.AWAITING_APPROVAL, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/expire",
            json={"actor": "op"},
        )
        assert resp.json()["old_state"] == "AWAITING_APPROVAL"

    def test_409_lock_contention(self):
        action = _make_action(state=ActionState.APPROVED, execution_attempt=0, max_attempts=3)
        svc, _ = _make_recovery_service(action=action, update_returns=False)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/expire",
            json={"actor": "op"},
        )
        assert resp.status_code == 409


# ═════════════════════════════════════════════════════════════════════════════
# Section 15: API — POST /actions/{id}/rollback (8 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestRollbackEndpoint:
    def _setup_rollback(self):
        action = _make_action(
            state=ActionState.EXECUTED,
            risk_level=ActionRiskLevel.REVERSIBLE,
            execution_attempt=1,
            max_attempts=3,
            rollback_action_type="remove_note",
            rollback_params={"note_id": "123"},
        )
        compensation = _make_action(state=ActionState.APPROVED)
        gw = _FakeGateway(_propose_rollback_returns=compensation)
        svc, _ = _make_recovery_service(action=action, gateway=gw)
        return action, svc

    def test_200_success(self):
        action, svc = self._setup_rollback()
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/rollback",
            json={"actor": "op:alice"},
        )
        assert resp.status_code == 200
        assert resp.json()["new_state"] == "ROLLING_BACK"

    def test_422_missing_actor(self):
        action, svc = self._setup_rollback()
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/rollback",
            json={},
        )
        assert resp.status_code == 422

    def test_409_not_reversible(self):
        action = _make_action(
            state=ActionState.EXECUTED,
            risk_level=ActionRiskLevel.SAFE,
        )
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/rollback",
            json={"actor": "op"},
        )
        assert resp.status_code == 409

    def test_404_not_found(self):
        svc, _ = _make_recovery_service(action=None)
        client = _make_api_client(action=None, recovery_svc=svc)
        resp = client.post(
            "/admin/action-gateway/actions/nonexistent/rollback",
            json={"actor": "op"},
        )
        assert resp.status_code == 404

    def test_503_stack_none(self):
        client = _make_api_client(stack_none=True)
        resp = client.post("/admin/action-gateway/actions/x/rollback", json={"actor": "op"})
        assert resp.status_code == 503

    def test_401_unauthenticated(self):
        client = _make_api_client(authenticator=_make_unauthenticated_auth())
        resp = client.post("/admin/action-gateway/actions/x/rollback", json={"actor": "op"})
        assert resp.status_code == 401

    def test_409_wrong_state(self):
        action = _make_action(state=ActionState.APPROVED)
        svc, _ = _make_recovery_service(action=action)
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/rollback",
            json={"actor": "op"},
        )
        assert resp.status_code == 409

    def test_response_has_action_id(self):
        action, svc = self._setup_rollback()
        client = _make_api_client(action=action, recovery_svc=svc)
        resp = client.post(
            f"/admin/action-gateway/actions/{action.action_id}/rollback",
            json={"actor": "op"},
        )
        assert resp.json()["action_id"] == action.action_id


# ═════════════════════════════════════════════════════════════════════════════
# Section 16: Assembly wiring (5 tests)
# ═════════════════════════════════════════════════════════════════════════════

class TestAssemblyWiring:
    def test_production_runtime_has_recovery_field(self):
        import dataclasses as dc
        from runtime.assembly import ProductionRuntime
        field_names = {f.name for f in dc.fields(ProductionRuntime)}
        assert "recovery" in field_names

    def test_recovery_field_type_is_recovery_service(self):
        import dataclasses as dc
        from runtime.assembly import ProductionRuntime
        fields = {f.name: f for f in dc.fields(ProductionRuntime)}
        assert "ActionGatewayRecoveryService" in str(fields["recovery"].type)

    def test_build_production_runtime_includes_recovery(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime()
        assert hasattr(stack, "recovery")
        assert stack.recovery is not None

    def test_recovery_shares_repository(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime()
        assert stack.recovery._repo is stack.repository

    def test_recovery_shares_gateway(self):
        from runtime.assembly import build_production_runtime
        stack = build_production_runtime()
        assert stack.recovery._gateway is stack.gateway
