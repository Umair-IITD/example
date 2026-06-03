"""
tests/test_sprint1_state_machine.py

State machine transition tests.

Covers:
- All legal transitions succeed
- All illegal transitions raise TransitionError
- Terminal states reject all transitions
- Transition mutates case in-place correctly
- Callback is invoked on every transition
- safe_transition swallows errors
"""
from __future__ import annotations

import pytest

from case_engine.case_state import ALLOWED_TRANSITIONS, TERMINAL_STATES, CaseState
from case_engine.models import Case
from case_engine.state_machine import CaseStateMachine, TransitionError


def _case(**kwargs) -> Case:
    return Case(ticket_id="TKT-001", client="unity_bank", **kwargs)


class TestAllowedTransitions:
    """Every transition in ALLOWED_TRANSITIONS must succeed."""

    def test_new_to_classifying(self):
        sm   = CaseStateMachine()
        case = _case()
        t    = sm.transition(case, CaseState.CLASSIFYING, reason="test")
        assert case.current_state == CaseState.CLASSIFYING
        assert t.from_state == CaseState.NEW
        assert t.to_state   == CaseState.CLASSIFYING
        assert t.reason == "test"

    def test_classifying_to_triage_complete(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.CLASSIFYING)
        sm.transition(case, CaseState.TRIAGE_COMPLETE)
        assert case.current_state == CaseState.TRIAGE_COMPLETE

    def test_classifying_to_escalated(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.CLASSIFYING)
        sm.transition(case, CaseState.ESCALATED, reason="below_threshold")
        assert case.current_state == CaseState.ESCALATED

    def test_triage_complete_to_workflow_active(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.TRIAGE_COMPLETE)
        sm.transition(case, CaseState.WORKFLOW_ACTIVE)
        assert case.current_state == CaseState.WORKFLOW_ACTIVE

    def test_triage_complete_to_closed(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.TRIAGE_COMPLETE)
        sm.transition(case, CaseState.CLOSED)
        assert case.current_state == CaseState.CLOSED
        assert case.closed_at is not None

    def test_workflow_active_to_awaiting_input(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.WORKFLOW_ACTIVE)
        sm.transition(case, CaseState.AWAITING_INPUT)
        assert case.current_state == CaseState.AWAITING_INPUT

    def test_awaiting_input_to_workflow_active(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.AWAITING_INPUT)
        sm.transition(case, CaseState.WORKFLOW_ACTIVE)
        assert case.current_state == CaseState.WORKFLOW_ACTIVE

    def test_workflow_active_to_resolved(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.WORKFLOW_ACTIVE)
        sm.transition(case, CaseState.RESOLVED)
        assert case.current_state == CaseState.RESOLVED

    def test_workflow_active_to_action_pending(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.WORKFLOW_ACTIVE)
        sm.transition(case, CaseState.ACTION_PENDING)
        assert case.current_state == CaseState.ACTION_PENDING

    def test_action_pending_to_escalated(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.ACTION_PENDING)
        sm.transition(case, CaseState.ESCALATED)
        assert case.current_state == CaseState.ESCALATED

    def test_action_pending_to_workflow_active(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.ACTION_PENDING)
        sm.transition(case, CaseState.WORKFLOW_ACTIVE)
        assert case.current_state == CaseState.WORKFLOW_ACTIVE

    def test_escalated_to_closed(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.ESCALATED)
        sm.transition(case, CaseState.CLOSED)
        assert case.current_state == CaseState.CLOSED

    def test_resolved_to_closed(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.RESOLVED)
        sm.transition(case, CaseState.CLOSED)
        assert case.current_state == CaseState.CLOSED


class TestIllegalTransitions:
    """Illegal transitions must raise TransitionError without mutating case."""

    def _assert_blocked(self, from_state: CaseState, to_state: CaseState):
        sm   = CaseStateMachine()
        case = _case(current_state=from_state)
        with pytest.raises(TransitionError):
            sm.transition(case, to_state)
        # State must not have changed
        assert case.current_state == from_state

    def test_new_to_resolved_blocked(self):
        self._assert_blocked(CaseState.NEW, CaseState.RESOLVED)

    def test_new_to_triage_complete_blocked(self):
        self._assert_blocked(CaseState.NEW, CaseState.TRIAGE_COMPLETE)

    def test_classifying_to_new_blocked(self):
        self._assert_blocked(CaseState.CLASSIFYING, CaseState.NEW)

    def test_triage_complete_to_classifying_blocked(self):
        self._assert_blocked(CaseState.TRIAGE_COMPLETE, CaseState.CLASSIFYING)

    def test_resolved_to_workflow_active_blocked(self):
        self._assert_blocked(CaseState.RESOLVED, CaseState.WORKFLOW_ACTIVE)

    def test_failed_to_new_blocked(self):
        self._assert_blocked(CaseState.FAILED, CaseState.NEW)

    def test_closed_to_any_blocked(self):
        """CLOSED is terminal — no transition out."""
        for to_state in CaseState:
            if to_state == CaseState.CLOSED:
                continue
            sm   = CaseStateMachine()
            case = _case(current_state=CaseState.CLOSED)
            with pytest.raises(TransitionError):
                sm.transition(case, to_state)

    def test_escalated_to_workflow_active_blocked(self):
        self._assert_blocked(CaseState.ESCALATED, CaseState.WORKFLOW_ACTIVE)


class TestTerminalStates:
    def test_closed_is_terminal(self):
        sm = CaseStateMachine()
        assert sm.is_terminal(CaseState.CLOSED) is True

    def test_non_terminal_states(self):
        sm = CaseStateMachine()
        for state in CaseState:
            if state != CaseState.CLOSED:
                assert sm.is_terminal(state) is False, f"{state} should not be terminal"

    def test_can_transition_from_terminal_returns_false(self):
        sm = CaseStateMachine()
        for to_state in CaseState:
            assert sm.can_transition(CaseState.CLOSED, to_state) is False


class TestTransitionCallback:
    def test_callback_called_on_every_transition(self):
        calls = []
        sm = CaseStateMachine(on_transition=lambda c, t: calls.append((c.case_id, t.to_state)))
        case = _case()
        sm.transition(case, CaseState.CLASSIFYING)
        sm.transition(case, CaseState.TRIAGE_COMPLETE)
        assert len(calls) == 2
        assert calls[0][1] == CaseState.CLASSIFYING
        assert calls[1][1] == CaseState.TRIAGE_COMPLETE

    def test_callback_failure_does_not_block_transition(self):
        """A failing callback must not prevent the transition from completing."""
        def _bad_callback(case, transition):
            raise RuntimeError("Callback failed")

        sm   = CaseStateMachine(on_transition=_bad_callback)
        case = _case()
        # Should not raise
        sm.transition(case, CaseState.CLASSIFYING)
        assert case.current_state == CaseState.CLASSIFYING

    def test_callback_receives_correct_transition(self):
        received = []
        sm = CaseStateMachine(on_transition=lambda c, t: received.append(t))
        case = _case()
        sm.transition(case, CaseState.CLASSIFYING, reason="test_reason", actor="test_actor")
        assert received[0].from_state == CaseState.NEW
        assert received[0].to_state   == CaseState.CLASSIFYING
        assert received[0].reason     == "test_reason"
        assert received[0].actor      == "test_actor"
        assert received[0].case_id    == case.case_id


class TestSafeTransition:
    def test_safe_transition_returns_none_on_illegal(self):
        sm   = CaseStateMachine()
        case = _case()
        result = sm.safe_transition(case, CaseState.RESOLVED)  # NEW → RESOLVED illegal
        assert result is None
        assert case.current_state == CaseState.NEW

    def test_safe_transition_returns_transition_on_legal(self):
        sm   = CaseStateMachine()
        case = _case()
        result = sm.safe_transition(case, CaseState.CLASSIFYING)
        assert result is not None
        assert result.to_state == CaseState.CLASSIFYING


class TestMutation:
    def test_transition_sets_updated_at(self):
        import time
        sm   = CaseStateMachine()
        case = _case()
        before = case.updated_at
        time.sleep(0.01)
        sm.transition(case, CaseState.CLASSIFYING)
        assert case.updated_at > before

    def test_transition_to_closed_sets_closed_at(self):
        sm   = CaseStateMachine()
        case = _case(current_state=CaseState.RESOLVED)
        assert case.closed_at is None
        sm.transition(case, CaseState.CLOSED)
        assert case.closed_at is not None

    def test_transition_without_close_does_not_set_closed_at(self):
        sm   = CaseStateMachine()
        case = _case()
        sm.transition(case, CaseState.CLASSIFYING)
        assert case.closed_at is None


class TestAllowedTransitionsTable:
    """Verify the ALLOWED_TRANSITIONS table itself is internally consistent."""

    def test_every_target_is_a_valid_state(self):
        all_states = set(CaseState)
        for from_state, targets in ALLOWED_TRANSITIONS.items():
            assert from_state in all_states
            for target in targets:
                assert target in all_states, f"{target} in ALLOWED_TRANSITIONS is not a valid CaseState"

    def test_terminal_states_have_empty_allowed_transitions(self):
        for state in TERMINAL_STATES:
            targets = ALLOWED_TRANSITIONS.get(state, frozenset())
            assert len(targets) == 0, f"Terminal state {state} must have no allowed transitions"
