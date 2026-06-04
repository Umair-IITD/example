"""
tests/test_sprint21_action_gateway.py

Sprint 2.1: ActionGateway business logic tests.

Uses a fake ActionRepository (in-memory, no Supabase) so all tests
are deterministic and offline-safe.

Covers:
- SAFE proposal: auto-approved immediately (PROPOSED → APPROVED)
- REVERSIBLE proposal: AWAITING_APPROVAL with 4h deadline
- IRREVERSIBLE proposal: AWAITING_APPROVAL with 24h deadline, max_attempts=1
- Duplicate proposal: DuplicateActionError, existing action returned
- Invalid proposal: ValueError propagated unchanged
- approve() flow: AWAITING_APPROVAL → APPROVED, fields set
- reject() flow: AWAITING_APPROVAL → REJECTED (terminal)
- expire() flow: AWAITING_APPROVAL → EXPIRED (terminal)
- begin_execution(): APPROVED → EXECUTING, attempt incremented
- begin_execution() wrong state: ActionGatewayError
- record_success(): EXECUTING → EXECUTED, result stored
- record_failure() with retries remaining: EXECUTING → FAILED → APPROVED
- record_failure() exhausted: EXECUTING → FAILED (stays FAILED)
- record_timeout() with retries: EXECUTING → TIMED_OUT → APPROVED
- record_timeout() exhausted: EXECUTING → TIMED_OUT → FAILED
- propose_rollback(): validation + compensation action created + ROLLING_BACK
- propose_rollback() duplicate: idempotent (returns existing compensation)
- propose_rollback() wrong state: ActionGatewayError
- propose_rollback() non-reversible: ActionGatewayError
- record_rollback_success(): ROLLING_BACK → ROLLED_BACK, is_rolled_back=True
- record_rollback_failure(): ROLLING_BACK → ROLLBACK_FAILED
- build_action_gateway() factory offline mode
"""
from __future__ import annotations

from datetime import timedelta, timezone, datetime
from typing import Any
from unittest.mock import MagicMock, call

import pytest

from case_engine.action_gateway import (
    ActionGateway,
    ActionGatewayError,
    DuplicateActionError,
    build_action_gateway,
)
from case_engine.action_models import (
    APPROVAL_DEADLINE_IRREVERSIBLE,
    APPROVAL_DEADLINE_REVERSIBLE,
    ActionProposal,
    ActionRequest,
    ActionTransitionRecord,
)
from case_engine.action_repository import ActionRepository
from case_engine.action_state import ActionRiskLevel, ActionState
from case_engine.models import Case


# ── Fake repository ───────────────────────────────────────────────────────────


class FakeActionRepository(ActionRepository):
    """
    In-memory ActionRepository for tests.

    Stores actions by action_id and by idempotency_key.
    record_transition appends to a list so tests can inspect audit entries.
    """

    def __init__(self):
        super().__init__(supabase_client=None)
        self._actions: dict[str, ActionRequest] = {}
        self._by_idem: dict[str, ActionRequest] = {}
        self.transitions: list[ActionTransitionRecord] = []
        self.update_calls: list[ActionRequest] = []

    def insert_action(self, action: ActionRequest) -> ActionRequest | None:
        self._actions[action.action_id] = action
        if action.idempotency_key:
            self._by_idem[action.idempotency_key] = action
        return action

    def get_action(self, action_id: str) -> ActionRequest | None:
        return self._actions.get(action_id)

    def get_action_by_idempotency_key(self, key: str) -> ActionRequest | None:
        return self._by_idem.get(key)

    def update_action(self, action: ActionRequest, *, expected_state=None) -> bool:
        self._actions[action.action_id] = action
        self.update_calls.append(action)
        return True

    def record_transition(self, record: ActionTransitionRecord) -> bool:
        self.transitions.append(record)
        return True


# ── Fixtures ──────────────────────────────────────────────────────────────────


def _case(**kwargs) -> Case:
    defaults = dict(
        case_id="11111111-1111-1111-1111-111111111111",
        ticket_id="TKT-001",
        client="unity_bank",
    )
    defaults.update(kwargs)
    return Case(**defaults)


def _proposal(
    action_type: str = "reset_otp",
    action_namespace: str = "identity",
    risk_level: ActionRiskLevel = ActionRiskLevel.SAFE,
    action_params: dict | None = None,
    rollback_action_type: str | None = None,
    rollback_params: dict | None = None,
    proposed_by: str = "system",
) -> ActionProposal:
    return ActionProposal(
        action_type=action_type,
        action_namespace=action_namespace,
        risk_level=risk_level,
        action_params=action_params or {},
        rollback_action_type=rollback_action_type,
        rollback_params=rollback_params,
        proposed_by=proposed_by,
    )


def _gateway() -> tuple[ActionGateway, FakeActionRepository]:
    repo = FakeActionRepository()
    gw = ActionGateway(repository=repo)
    return gw, repo


# ── propose(): SAFE ───────────────────────────────────────────────────────────


class TestProposeSafe:

    def test_safe_is_auto_approved(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.current_state == ActionState.APPROVED

    def test_safe_approved_by_auto_approval(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.approver == "auto_approval"
        assert action.approved_at is not None

    def test_safe_approval_required_false(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.approval_required is False

    def test_safe_max_attempts_is_3(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.max_attempts == 3

    def test_safe_two_transitions_recorded(self):
        gw, repo = _gateway()
        gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        # PROPOSED → APPROVED (auto_approval): one transition
        assert len(repo.transitions) == 1
        assert repo.transitions[0].from_state == ActionState.PROPOSED
        assert repo.transitions[0].to_state == ActionState.APPROVED
        assert repo.transitions[0].actor == "auto_approval"

    def test_safe_action_persisted(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.action_id in repo._actions

    def test_safe_idempotency_key_stored(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.idempotency_key != ""
        assert action.idempotency_key in repo._by_idem


# ── propose(): REVERSIBLE ─────────────────────────────────────────────────────


class TestProposeReversible:

    def _reversible_proposal(self, **kwargs) -> ActionProposal:
        return _proposal(
            action_type="update_address",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type="restore_address",
            rollback_params={"old_address": "123 Main St"},
            **kwargs,
        )

    def test_reversible_awaiting_approval(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._reversible_proposal())
        assert action.current_state == ActionState.AWAITING_APPROVAL

    def test_reversible_approval_required_true(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._reversible_proposal())
        assert action.approval_required is True

    def test_reversible_deadline_is_4_hours(self):
        gw, repo = _gateway()
        before = datetime.now(tz=timezone.utc)
        action = gw.propose(_case(), self._reversible_proposal())
        after = datetime.now(tz=timezone.utc)
        expected_min = before + APPROVAL_DEADLINE_REVERSIBLE - timedelta(seconds=2)
        expected_max = after + APPROVAL_DEADLINE_REVERSIBLE + timedelta(seconds=2)
        assert expected_min <= action.expires_at <= expected_max

    def test_reversible_max_attempts_is_3(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._reversible_proposal())
        assert action.max_attempts == 3

    def test_reversible_one_transition_recorded(self):
        gw, repo = _gateway()
        gw.propose(_case(), self._reversible_proposal())
        assert len(repo.transitions) == 1
        assert repo.transitions[0].to_state == ActionState.AWAITING_APPROVAL

    def test_reversible_rollback_fields_stored(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._reversible_proposal())
        assert action.rollback_action_type == "restore_address"
        assert action.rollback_params == {"old_address": "123 Main St"}


# ── propose(): IRREVERSIBLE ───────────────────────────────────────────────────


class TestProposeIrreversible:

    def _irreversible_proposal(self) -> ActionProposal:
        return _proposal(
            action_type="close_account",
            action_namespace="accounts",
            risk_level=ActionRiskLevel.IRREVERSIBLE,
        )

    def test_irreversible_awaiting_approval(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._irreversible_proposal())
        assert action.current_state == ActionState.AWAITING_APPROVAL

    def test_irreversible_deadline_is_24_hours(self):
        gw, repo = _gateway()
        before = datetime.now(tz=timezone.utc)
        action = gw.propose(_case(), self._irreversible_proposal())
        after = datetime.now(tz=timezone.utc)
        expected_min = before + APPROVAL_DEADLINE_IRREVERSIBLE - timedelta(seconds=2)
        expected_max = after + APPROVAL_DEADLINE_IRREVERSIBLE + timedelta(seconds=2)
        assert expected_min <= action.expires_at <= expected_max

    def test_irreversible_max_attempts_is_1(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), self._irreversible_proposal())
        assert action.max_attempts == 1


# ── propose(): duplicate / invalid ───────────────────────────────────────────


class TestProposeDuplicate:

    def test_duplicate_raises_duplicate_action_error(self):
        gw, repo = _gateway()
        case = _case()
        p = _proposal()
        first = gw.propose(case, p)
        with pytest.raises(DuplicateActionError) as exc_info:
            gw.propose(case, p)
        assert exc_info.value.existing.action_id == first.action_id

    def test_duplicate_error_has_existing_action(self):
        gw, repo = _gateway()
        case = _case()
        p = _proposal()
        first = gw.propose(case, p)
        try:
            gw.propose(case, p)
        except DuplicateActionError as exc:
            assert exc.existing.action_id == first.action_id
            assert exc.existing.current_state == ActionState.APPROVED

    def test_invalid_proposal_raises_value_error(self):
        gw, repo = _gateway()
        p = ActionProposal(
            action_type="",  # invalid: empty
            action_namespace="identity",
            risk_level=ActionRiskLevel.SAFE,
        )
        with pytest.raises(ValueError):
            gw.propose(_case(), p)


# ── approve() ─────────────────────────────────────────────────────────────────


class TestApprove:

    def _awaiting_action(self, gw, repo) -> ActionRequest:
        p = _proposal(
            action_type="update_phone",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type="restore_phone",
        )
        return gw.propose(_case(), p)

    def test_approve_transitions_to_approved(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        gw.approve(action, approved_by="agent_007")
        assert action.current_state == ActionState.APPROVED

    def test_approve_sets_approved_by(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        gw.approve(action, approved_by="senior_agent", notes="looks good")
        assert action.approver == "senior_agent"
        assert action.approval_notes == "looks good"
        assert action.approved_at is not None

    def test_approve_records_transition(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        initial_count = len(repo.transitions)
        gw.approve(action, approved_by="agent_007")
        new_transitions = repo.transitions[initial_count:]
        assert len(new_transitions) == 1
        assert new_transitions[0].actor == "human:agent_007"
        assert new_transitions[0].to_state == ActionState.APPROVED

    def test_approve_calls_update_action(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        initial_updates = len(repo.update_calls)
        gw.approve(action, approved_by="agent_007")
        assert len(repo.update_calls) > initial_updates

    def test_approve_from_wrong_state_raises(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        # action is now APPROVED; cannot approve again
        from case_engine.action_state import ActionTransitionError
        with pytest.raises(ActionTransitionError):
            gw.approve(action, approved_by="agent_007")


# ── reject() ──────────────────────────────────────────────────────────────────


class TestReject:

    def _awaiting_action(self, gw, repo) -> ActionRequest:
        p = _proposal(
            action_type="update_phone",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type="restore_phone",
        )
        return gw.propose(_case(), p)

    def test_reject_transitions_to_rejected(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        gw.reject(action, rejected_by="supervisor")
        assert action.current_state == ActionState.REJECTED

    def test_rejected_is_terminal(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        gw.reject(action, rejected_by="supervisor")
        assert action.is_terminal

    def test_reject_records_actor(self):
        gw, repo = _gateway()
        action = self._awaiting_action(gw, repo)
        initial_count = len(repo.transitions)
        gw.reject(action, rejected_by="supervisor", notes="policy violation")
        new_t = repo.transitions[initial_count:]
        assert new_t[-1].actor == "human:supervisor"
        assert new_t[-1].to_state == ActionState.REJECTED


# ── expire() ──────────────────────────────────────────────────────────────────


class TestExpire:

    def test_expire_from_awaiting_approval(self):
        gw, repo = _gateway()
        p = _proposal(
            action_type="update_phone", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_phone",
        )
        action = gw.propose(_case(), p)
        gw.expire(action)
        assert action.current_state == ActionState.EXPIRED
        assert action.is_terminal

    def test_expire_from_approved(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        # APPROVED → EXPIRED is allowed
        gw.expire(action)
        assert action.current_state == ActionState.EXPIRED


# ── begin_execution() ─────────────────────────────────────────────────────────


class TestBeginExecution:

    def test_begin_execution_transitions_to_executing(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        gw.begin_execution(action, executor_id="worker-1")
        assert action.current_state == ActionState.EXECUTING

    def test_begin_execution_increments_attempt(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.execution_attempt == 0
        gw.begin_execution(action, executor_id="worker-1")
        assert action.execution_attempt == 1

    def test_begin_execution_sets_executor_id(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        gw.begin_execution(action, executor_id="worker-42")
        assert action.executor_id == "worker-42"
        assert action.execution_started_at is not None

    def test_begin_execution_wrong_state_raises(self):
        gw, repo = _gateway()
        p = _proposal(
            action_type="update_phone", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_phone",
        )
        action = gw.propose(_case(), p)
        # Still AWAITING_APPROVAL — cannot begin_execution
        with pytest.raises(ActionGatewayError):
            gw.begin_execution(action, executor_id="worker-1")


# ── record_success() ──────────────────────────────────────────────────────────


class TestRecordSuccess:

    def _executing_action(self, gw, repo) -> ActionRequest:
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        gw.begin_execution(action, executor_id="worker-1")
        return action

    def test_record_success_transitions_to_executed(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo)
        gw.record_success(action, result={"status": "ok"})
        assert action.current_state == ActionState.EXECUTED

    def test_record_success_stores_result(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo)
        gw.record_success(action, result={"status": "ok", "ref": "REF-123"})
        assert action.execution_result == {"status": "ok", "ref": "REF-123"}

    def test_record_success_clears_failure_fields(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo)
        action.failure_code = "PREV_ERROR"
        gw.record_success(action, result={})
        assert action.failure_code is None
        assert action.failure_reason is None

    def test_record_success_sets_completed_at(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo)
        gw.record_success(action, result={})
        assert action.execution_completed_at is not None


# ── record_failure() ──────────────────────────────────────────────────────────


class TestRecordFailure:

    def _executing_action(self, gw, repo, max_attempts=3) -> ActionRequest:
        p = _proposal(
            action_type="reset_otp",
            action_namespace="identity",
            risk_level=ActionRiskLevel.SAFE,
        )
        action = gw.propose(_case(), p)
        action.max_attempts = max_attempts
        gw.begin_execution(action, executor_id="worker-1")
        return action

    def test_failure_with_retry_transitions_to_approved(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        # attempt=1, max=3 → can retry
        gw.record_failure(action, failure_code="NETWORK_ERROR", reason="timeout")
        assert action.current_state == ActionState.APPROVED

    def test_failure_with_retry_resets_executor_fields(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        gw.record_failure(action, failure_code="NETWORK_ERROR", reason="timeout")
        assert action.executor_id is None
        assert action.execution_started_at is None
        assert action.execution_completed_at is None

    def test_failure_exhausted_dead_lettered(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=1)
        # attempt=1, max=1 → exhausted → DEAD_LETTER (Sprint 2.10)
        gw.record_failure(action, failure_code="HARD_ERROR", reason="unrecoverable")
        assert action.current_state == ActionState.DEAD_LETTER

    def test_failure_stores_failure_code(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=1)
        gw.record_failure(action, failure_code="HARD_ERROR", reason="unrecoverable")
        assert action.failure_code == "HARD_ERROR"
        assert action.failure_reason == "unrecoverable"

    def test_failure_transition_recorded(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=1)
        initial = len(repo.transitions)
        gw.record_failure(action, failure_code="ERR", reason="r")
        new_t = repo.transitions[initial:]
        states = [t.to_state for t in new_t]
        assert ActionState.FAILED in states

    def test_failure_with_retry_both_transitions_recorded(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        initial = len(repo.transitions)
        gw.record_failure(action, failure_code="ERR", reason="r")
        new_t = repo.transitions[initial:]
        states = [t.to_state for t in new_t]
        assert ActionState.FAILED in states
        assert ActionState.APPROVED in states


# ── record_timeout() ──────────────────────────────────────────────────────────


class TestRecordTimeout:

    def _executing_action(self, gw, repo, max_attempts=3) -> ActionRequest:
        p = _proposal(risk_level=ActionRiskLevel.SAFE)
        action = gw.propose(_case(), p)
        action.max_attempts = max_attempts
        gw.begin_execution(action, executor_id="worker-1")
        return action

    def test_timeout_with_retry_transitions_to_approved(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        gw.record_timeout(action, executor_id="worker-1")
        assert action.current_state == ActionState.APPROVED

    def test_timeout_with_retry_resets_executor_fields(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        gw.record_timeout(action, executor_id="worker-1")
        assert action.executor_id is None

    def test_timeout_exhausted_transitions_to_dead_letter(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=1)
        # Sprint 2.10: exhausted timeout → DEAD_LETTER
        gw.record_timeout(action, executor_id="worker-1")
        assert action.current_state == ActionState.DEAD_LETTER

    def test_timeout_sets_failure_code(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=1)
        gw.record_timeout(action, executor_id="worker-1")
        assert action.failure_code == "EXECUTOR_TIMEOUT"

    def test_timeout_transitions_through_timed_out(self):
        gw, repo = _gateway()
        action = self._executing_action(gw, repo, max_attempts=3)
        initial = len(repo.transitions)
        gw.record_timeout(action, executor_id="worker-1")
        new_states = [t.to_state for t in repo.transitions[initial:]]
        assert ActionState.TIMED_OUT in new_states


# ── propose_rollback() ────────────────────────────────────────────────────────


class TestProposeRollback:

    def _executed_reversible(self, gw, repo) -> ActionRequest:
        p = _proposal(
            action_type="update_address",
            action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE,
            rollback_action_type="restore_address",
            rollback_params={"old_address": "123 Main St"},
        )
        action = gw.propose(_case(), p)
        gw.approve(action, approved_by="agent_007")
        gw.begin_execution(action, executor_id="worker-1")
        gw.record_success(action, result={"status": "ok"})
        return action

    def test_propose_rollback_creates_compensation(self):
        gw, repo = _gateway()
        original = self._executed_reversible(gw, repo)
        compensation = gw.propose_rollback(original, _case())
        assert compensation is not None
        assert compensation.action_type == "restore_address"

    def test_propose_rollback_moves_original_to_rolling_back(self):
        gw, repo = _gateway()
        original = self._executed_reversible(gw, repo)
        gw.propose_rollback(original, _case())
        assert original.current_state == ActionState.ROLLING_BACK

    def test_propose_rollback_links_rollback_action_id(self):
        gw, repo = _gateway()
        original = self._executed_reversible(gw, repo)
        compensation = gw.propose_rollback(original, _case())
        assert original.rollback_action_id == compensation.action_id

    def test_propose_rollback_wrong_state_raises(self):
        gw, repo = _gateway()
        p = _proposal(
            action_type="update_address", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_address",
        )
        action = gw.propose(_case(), p)
        gw.approve(action, approved_by="agent_007")
        # APPROVED, not EXECUTED — should fail
        with pytest.raises(ActionGatewayError, match="EXECUTED"):
            gw.propose_rollback(action, _case())

    def test_propose_rollback_safe_action_raises(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        gw.begin_execution(action, executor_id="w1")
        gw.record_success(action, result={})
        with pytest.raises(ActionGatewayError, match="REVERSIBLE"):
            gw.propose_rollback(action, _case())

    def test_propose_rollback_already_rolled_back_raises(self):
        gw, repo = _gateway()
        original = self._executed_reversible(gw, repo)
        # Simulate a previously completed rollback: action is marked rolled back
        # but state is reset to EXECUTED so the is_rolled_back guard fires first.
        original.is_rolled_back = True
        original.rollback_action_id = "some-other-action-id"
        with pytest.raises(ActionGatewayError, match="rolled back"):
            gw.propose_rollback(original, _case())

    def test_propose_rollback_no_rollback_type_raises(self):
        gw, repo = _gateway()
        p = _proposal(
            action_type="update_address", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_address",
        )
        action = gw.propose(_case(), p)
        gw.approve(action, approved_by="agent_007")
        gw.begin_execution(action, executor_id="w1")
        gw.record_success(action, result={})
        action.rollback_action_type = None  # strip it
        with pytest.raises(ActionGatewayError, match="rollback_action_type"):
            gw.propose_rollback(action, _case())

    def test_propose_rollback_duplicate_is_idempotent(self):
        gw, repo = _gateway()
        original = self._executed_reversible(gw, repo)
        comp1 = gw.propose_rollback(original, _case())
        # Reset original to EXECUTED so we can call propose_rollback again
        original.current_state = ActionState.EXECUTED
        original.rollback_action_id = None
        original.is_rolled_back = False
        comp2 = gw.propose_rollback(original, _case())
        # The compensation action should be the same (idempotent)
        assert comp1.action_id == comp2.action_id


# ── record_rollback_success() / record_rollback_failure() ────────────────────


class TestRollbackOutcomes:

    def _rolling_back_action(self, gw, repo) -> ActionRequest:
        p = _proposal(
            action_type="update_address", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_address",
            rollback_params={},
        )
        action = gw.propose(_case(), p)
        gw.approve(action, approved_by="agent_007")
        gw.begin_execution(action, executor_id="w1")
        gw.record_success(action, result={})
        gw.propose_rollback(action, _case())
        # action is now ROLLING_BACK
        return action

    def test_rollback_success_transitions_to_rolled_back(self):
        gw, repo = _gateway()
        action = self._rolling_back_action(gw, repo)
        gw.record_rollback_success(action)
        assert action.current_state == ActionState.ROLLED_BACK

    def test_rollback_success_sets_is_rolled_back(self):
        gw, repo = _gateway()
        action = self._rolling_back_action(gw, repo)
        gw.record_rollback_success(action)
        assert action.is_rolled_back is True

    def test_rollback_success_is_terminal(self):
        gw, repo = _gateway()
        action = self._rolling_back_action(gw, repo)
        gw.record_rollback_success(action)
        assert action.is_terminal

    def test_rollback_failure_transitions_to_rollback_failed(self):
        gw, repo = _gateway()
        action = self._rolling_back_action(gw, repo)
        gw.record_rollback_failure(action, reason="compensation_api_error")
        assert action.current_state == ActionState.ROLLBACK_FAILED

    def test_rollback_failure_is_terminal(self):
        gw, repo = _gateway()
        action = self._rolling_back_action(gw, repo)
        gw.record_rollback_failure(action, reason="compensation_api_error")
        assert action.is_terminal


# ── Full happy-path lifecycle ──────────────────────────────────────────────────


class TestFullLifecycle:
    """End-to-end SAFE action lifecycle: propose → execute → succeed."""

    def test_safe_full_lifecycle(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.current_state == ActionState.APPROVED

        gw.begin_execution(action, executor_id="worker-1")
        assert action.current_state == ActionState.EXECUTING
        assert action.execution_attempt == 1

        gw.record_success(action, result={"ref": "OUT-001"})
        assert action.current_state == ActionState.EXECUTED
        assert action.execution_result == {"ref": "OUT-001"}

    def test_reversible_full_lifecycle_with_rollback(self):
        gw, repo = _gateway()
        case = _case()
        p = _proposal(
            action_type="update_address", action_namespace="profile",
            risk_level=ActionRiskLevel.REVERSIBLE, rollback_action_type="restore_address",
            rollback_params={"old": "123 Main St"},
        )
        action = gw.propose(case, p)
        assert action.current_state == ActionState.AWAITING_APPROVAL

        gw.approve(action, approved_by="agent_007")
        assert action.current_state == ActionState.APPROVED

        gw.begin_execution(action, executor_id="worker-1")
        gw.record_success(action, result={"status": "updated"})
        assert action.current_state == ActionState.EXECUTED

        compensation = gw.propose_rollback(action, case)
        assert action.current_state == ActionState.ROLLING_BACK

        gw.record_rollback_success(action)
        assert action.current_state == ActionState.ROLLED_BACK
        assert action.is_rolled_back

    def test_retry_lifecycle(self):
        gw, repo = _gateway()
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        action.max_attempts = 3

        gw.begin_execution(action, executor_id="w1")
        gw.record_failure(action, failure_code="NETWORK_ERROR", reason="timeout")
        assert action.current_state == ActionState.APPROVED
        assert action.execution_attempt == 1

        gw.begin_execution(action, executor_id="w2")
        gw.record_success(action, result={"status": "ok"})
        assert action.current_state == ActionState.EXECUTED
        assert action.execution_attempt == 2


# ── build_action_gateway factory ──────────────────────────────────────────────


class TestBuildActionGateway:

    def test_factory_offline_mode(self):
        gw = build_action_gateway(supabase_client=None)
        assert isinstance(gw, ActionGateway)

    def test_factory_offline_can_propose(self):
        gw = build_action_gateway(supabase_client=None)
        # Offline mode — insert_action returns in-memory action
        action = gw.propose(_case(), _proposal(risk_level=ActionRiskLevel.SAFE))
        assert action.current_state == ActionState.APPROVED
        assert action.action_id != ""
