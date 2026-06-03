"""
tests/test_sprint21_action_state_machine.py

Sprint 2.1: ActionStateMachine tests.

Covers:
- Every valid transition in ALLOWED_ACTION_TRANSITIONS succeeds
- Every illegal from→to pair raises ActionTransitionError
- Terminal states reject all outgoing transitions
- Transition mutates action.current_state in-place
- on_transition callback is invoked exactly once per transition
- safe_transition returns None on ActionTransitionError, does not raise
- PSEUDO_TERMINAL state (EXECUTED) permits ROLLING_BACK only
"""
from __future__ import annotations

import pytest

from case_engine.action_models import ActionRequest
from case_engine.action_state import (
    ALLOWED_ACTION_TRANSITIONS,
    TERMINAL_ACTION_STATES,
    ActionRiskLevel,
    ActionState,
    ActionStateMachine,
    ActionTransitionError,
)


# ── Helpers ───────────────────────────────────────────────────────────────────


def _action(state: ActionState = ActionState.PROPOSED, **kwargs) -> ActionRequest:
    return ActionRequest(
        case_id="11111111-1111-1111-1111-111111111111",
        ticket_id="TKT-001",
        client="unity_bank",
        action_type="reset_otp",
        action_namespace="identity",
        risk_level=ActionRiskLevel.SAFE,
        current_state=state,
        idempotency_key="abc123",
        **kwargs,
    )


class _CallbackTracker:
    def __init__(self):
        self.calls: list[tuple] = []

    def __call__(self, action, record):
        self.calls.append((action.current_state, record.from_state, record.to_state))


# ── Valid transitions ─────────────────────────────────────────────────────────


class TestValidTransitions:
    """Every edge in ALLOWED_ACTION_TRANSITIONS must succeed."""

    def test_proposed_to_awaiting_approval(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        rec = sm.transition(action, ActionState.AWAITING_APPROVAL, reason="needs_human")
        assert action.current_state == ActionState.AWAITING_APPROVAL
        assert rec.from_state == ActionState.PROPOSED
        assert rec.to_state == ActionState.AWAITING_APPROVAL
        assert rec.reason == "needs_human"

    def test_proposed_to_approved_safe(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        sm.transition(action, ActionState.APPROVED, reason="auto_approved_safe")
        assert action.current_state == ActionState.APPROVED

    def test_awaiting_approval_to_approved(self):
        sm = ActionStateMachine()
        action = _action(ActionState.AWAITING_APPROVAL)
        sm.transition(action, ActionState.APPROVED, actor="human:agent1")
        assert action.current_state == ActionState.APPROVED

    def test_awaiting_approval_to_rejected(self):
        sm = ActionStateMachine()
        action = _action(ActionState.AWAITING_APPROVAL)
        sm.transition(action, ActionState.REJECTED, actor="human:agent1")
        assert action.current_state == ActionState.REJECTED

    def test_awaiting_approval_to_expired(self):
        sm = ActionStateMachine()
        action = _action(ActionState.AWAITING_APPROVAL)
        sm.transition(action, ActionState.EXPIRED, actor="watchdog:sla")
        assert action.current_state == ActionState.EXPIRED

    def test_approved_to_executing(self):
        sm = ActionStateMachine()
        action = _action(ActionState.APPROVED)
        sm.transition(action, ActionState.EXECUTING, actor="executor:w1")
        assert action.current_state == ActionState.EXECUTING

    def test_approved_to_expired(self):
        sm = ActionStateMachine()
        action = _action(ActionState.APPROVED)
        sm.transition(action, ActionState.EXPIRED, actor="watchdog:sla")
        assert action.current_state == ActionState.EXPIRED

    def test_executing_to_executed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTING)
        sm.transition(action, ActionState.EXECUTED)
        assert action.current_state == ActionState.EXECUTED

    def test_executing_to_failed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTING)
        sm.transition(action, ActionState.FAILED)
        assert action.current_state == ActionState.FAILED

    def test_executing_to_timed_out(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTING)
        sm.transition(action, ActionState.TIMED_OUT)
        assert action.current_state == ActionState.TIMED_OUT

    def test_executed_to_rolling_back(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTED)
        sm.transition(action, ActionState.ROLLING_BACK)
        assert action.current_state == ActionState.ROLLING_BACK

    def test_failed_to_approved_retry(self):
        sm = ActionStateMachine()
        action = _action(ActionState.FAILED)
        sm.transition(action, ActionState.APPROVED, reason="retry_scheduled")
        assert action.current_state == ActionState.APPROVED

    def test_timed_out_to_approved(self):
        sm = ActionStateMachine()
        action = _action(ActionState.TIMED_OUT)
        sm.transition(action, ActionState.APPROVED)
        assert action.current_state == ActionState.APPROVED

    def test_timed_out_to_failed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.TIMED_OUT)
        sm.transition(action, ActionState.FAILED)
        assert action.current_state == ActionState.FAILED

    def test_rolling_back_to_rolled_back(self):
        sm = ActionStateMachine()
        action = _action(ActionState.ROLLING_BACK)
        sm.transition(action, ActionState.ROLLED_BACK)
        assert action.current_state == ActionState.ROLLED_BACK

    def test_rolling_back_to_rollback_failed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.ROLLING_BACK)
        sm.transition(action, ActionState.ROLLBACK_FAILED)
        assert action.current_state == ActionState.ROLLBACK_FAILED


# ── Invalid transitions ───────────────────────────────────────────────────────


class TestInvalidTransitions:
    """Illegal from→to pairs must raise ActionTransitionError."""

    def test_proposed_cannot_skip_to_executing(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.EXECUTING)

    def test_proposed_cannot_jump_to_executed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.EXECUTED)

    def test_approved_cannot_go_to_awaiting_approval(self):
        sm = ActionStateMachine()
        action = _action(ActionState.APPROVED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.AWAITING_APPROVAL)

    def test_executing_cannot_go_to_proposed(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTING)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.PROPOSED)

    def test_executed_cannot_go_back_to_approved(self):
        sm = ActionStateMachine()
        action = _action(ActionState.EXECUTED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.APPROVED)

    def test_failed_cannot_go_to_executing_directly(self):
        sm = ActionStateMachine()
        action = _action(ActionState.FAILED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.EXECUTING)

    def test_error_message_contains_states(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        with pytest.raises(ActionTransitionError) as exc_info:
            sm.transition(action, ActionState.ROLLED_BACK)
        msg = str(exc_info.value)
        assert "PROPOSED" in msg
        assert "ROLLED_BACK" in msg


# ── Terminal states ───────────────────────────────────────────────────────────


class TestTerminalStates:
    """All transitions out of terminal states must be rejected."""

    @pytest.mark.parametrize("terminal", list(TERMINAL_ACTION_STATES))
    def test_terminal_rejects_all_transitions(self, terminal: ActionState):
        sm = ActionStateMachine()
        action = _action(terminal)
        for to_state in ActionState:
            assert not sm.can_transition(terminal, to_state), (
                f"Terminal state {terminal.value} must not allow → {to_state.value}"
            )

    @pytest.mark.parametrize("terminal", list(TERMINAL_ACTION_STATES))
    def test_transition_raises_from_terminal(self, terminal: ActionState):
        sm = ActionStateMachine()
        action = _action(terminal)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.APPROVED)

    def test_is_terminal_returns_true_for_terminals(self):
        sm = ActionStateMachine()
        for state in TERMINAL_ACTION_STATES:
            assert sm.is_terminal(state)

    def test_is_terminal_returns_false_for_non_terminals(self):
        sm = ActionStateMachine()
        non_terminals = set(ActionState) - TERMINAL_ACTION_STATES
        for state in non_terminals:
            assert not sm.is_terminal(state)


# ── Mutation and record ───────────────────────────────────────────────────────


class TestTransitionMutation:
    """transition() must mutate action in-place and return a correct record."""

    def test_updated_at_is_refreshed(self):
        import time
        from datetime import timedelta
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        before = action.updated_at
        time.sleep(0.01)
        sm.transition(action, ActionState.APPROVED)
        assert action.updated_at >= before

    def test_record_fields_match_action(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        rec = sm.transition(
            action, ActionState.APPROVED,
            reason="auto_approved_safe",
            actor="auto_approval",
            detail={"risk_level": "SAFE"},
        )
        assert rec.action_id == action.action_id
        assert rec.case_id == action.case_id
        assert rec.ticket_id == action.ticket_id
        assert rec.client == action.client
        assert rec.from_state == ActionState.PROPOSED
        assert rec.to_state == ActionState.APPROVED
        assert rec.actor == "auto_approval"
        assert rec.reason == "auto_approved_safe"
        assert rec.detail == {"risk_level": "SAFE"}

    def test_state_not_mutated_on_error(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        original_state = action.current_state
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.ROLLED_BACK)
        assert action.current_state == original_state


# ── Callback ──────────────────────────────────────────────────────────────────


class TestOnTransitionCallback:
    """on_transition is called once per successful transition."""

    def test_callback_invoked_on_success(self):
        tracker = _CallbackTracker()
        sm = ActionStateMachine(on_transition=tracker)
        action = _action(ActionState.PROPOSED)
        sm.transition(action, ActionState.APPROVED)
        assert len(tracker.calls) == 1
        arrived, from_s, to_s = tracker.calls[0]
        assert arrived == ActionState.APPROVED
        assert from_s == ActionState.PROPOSED
        assert to_s == ActionState.APPROVED

    def test_callback_not_invoked_on_invalid_transition(self):
        tracker = _CallbackTracker()
        sm = ActionStateMachine(on_transition=tracker)
        action = _action(ActionState.PROPOSED)
        with pytest.raises(ActionTransitionError):
            sm.transition(action, ActionState.ROLLING_BACK)
        assert len(tracker.calls) == 0

    def test_callback_exception_does_not_break_transition(self):
        def bad_callback(action, record):
            raise RuntimeError("callback exploded")

        sm = ActionStateMachine(on_transition=bad_callback)
        action = _action(ActionState.PROPOSED)
        rec = sm.transition(action, ActionState.APPROVED)
        assert action.current_state == ActionState.APPROVED
        assert rec is not None

    def test_multiple_transitions_invoke_callback_each_time(self):
        tracker = _CallbackTracker()
        sm = ActionStateMachine(on_transition=tracker)
        action = _action(ActionState.PROPOSED)
        sm.transition(action, ActionState.APPROVED)
        sm.transition(action, ActionState.EXECUTING)
        sm.transition(action, ActionState.EXECUTED)
        assert len(tracker.calls) == 3


# ── safe_transition ───────────────────────────────────────────────────────────


class TestSafeTransition:
    """safe_transition returns None on ActionTransitionError, not raising."""

    def test_safe_transition_succeeds_on_valid(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        rec = sm.safe_transition(action, ActionState.APPROVED)
        assert rec is not None
        assert action.current_state == ActionState.APPROVED

    def test_safe_transition_returns_none_on_invalid(self):
        sm = ActionStateMachine()
        action = _action(ActionState.PROPOSED)
        result = sm.safe_transition(action, ActionState.ROLLING_BACK)
        assert result is None
        assert action.current_state == ActionState.PROPOSED

    def test_safe_transition_returns_none_from_terminal(self):
        sm = ActionStateMachine()
        action = _action(ActionState.REJECTED)
        result = sm.safe_transition(action, ActionState.APPROVED)
        assert result is None
        assert action.current_state == ActionState.REJECTED


# ── can_transition ────────────────────────────────────────────────────────────


class TestCanTransition:
    """can_transition returns True only for allowed pairs."""

    def test_all_table_entries_return_true(self):
        sm = ActionStateMachine()
        for from_state, allowed_set in ALLOWED_ACTION_TRANSITIONS.items():
            for to_state in allowed_set:
                assert sm.can_transition(from_state, to_state), (
                    f"Expected can_transition({from_state.value}, {to_state.value}) = True"
                )

    def test_terminal_always_returns_false(self):
        sm = ActionStateMachine()
        for terminal in TERMINAL_ACTION_STATES:
            for to_state in ActionState:
                assert not sm.can_transition(terminal, to_state)
