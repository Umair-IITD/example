"""
tests/test_sprint22_executor_registry.py

Sprint 2.2 — ActionExecutorRegistry contract tests.

Covers:
  - Registration of a valid executor succeeds
  - Duplicate (namespace, action_type) raises ExecutorRegistrationError
  - get_executor returns the correct executor
  - get_executor raises UnknownExecutorError for missing keys
  - executor_exists works correctly
  - registered_count and registered_keys are consistent
  - UnknownExecutorError carries namespace and action_type attributes
  - Multiple executors across different namespaces coexist
"""
from __future__ import annotations

import pytest

from case_engine.action_executor import (
    ActionExecutor,
    ExecutionContext,
    ExecutionResult,
)
from case_engine.executor_registry import (
    ActionExecutorRegistry,
    ExecutorRegistrationError,
    UnknownExecutorError,
)


# ── Fixtures / helpers ─────────────────────────────────────────────────────────


def _make_executor(namespace: str, action_type: str) -> ActionExecutor:
    """Build a minimal stub executor with the given namespace and action_type."""

    class _StubExecutor(ActionExecutor):
        @property
        def action_namespace(self) -> str:
            return namespace

        @property
        def action_type(self) -> str:
            return action_type

        def execute(self, context: ExecutionContext, payload: dict) -> ExecutionResult:
            return ExecutionResult(success=True)

        def rollback(self, context: ExecutionContext, payload: dict) -> ExecutionResult:
            return ExecutionResult(success=True)

        def health_check(self) -> bool:
            return True

    return _StubExecutor()


# ── Registration ───────────────────────────────────────────────────────────────


class TestRegistration:
    def test_register_single_executor_succeeds(self) -> None:
        reg = ActionExecutorRegistry()
        executor = _make_executor("identity", "reset_otp")
        reg.register_executor(executor)
        assert reg.registered_count() == 1

    def test_registered_keys_contains_entry(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        assert ("identity", "reset_otp") in reg.registered_keys()

    def test_register_two_different_executors(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        reg.register_executor(_make_executor("accounts", "freeze_account"))
        assert reg.registered_count() == 2

    def test_registered_keys_sorted(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        reg.register_executor(_make_executor("accounts", "freeze_account"))
        keys = reg.registered_keys()
        assert keys == sorted(keys)

    def test_register_same_namespace_different_type(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        reg.register_executor(_make_executor("identity", "unlock_account"))
        assert reg.registered_count() == 2

    def test_register_same_type_different_namespace(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset"))
        reg.register_executor(_make_executor("accounts", "reset"))
        assert reg.registered_count() == 2


# ── Duplicate registration ─────────────────────────────────────────────────────


class TestDuplicateRegistration:
    def test_duplicate_raises_executor_registration_error(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        with pytest.raises(ExecutorRegistrationError):
            reg.register_executor(_make_executor("identity", "reset_otp"))

    def test_duplicate_error_message_contains_namespace_and_type(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        with pytest.raises(ExecutorRegistrationError, match="identity"):
            reg.register_executor(_make_executor("identity", "reset_otp"))

    def test_duplicate_does_not_overwrite_original(self) -> None:
        reg = ActionExecutorRegistry()
        first = _make_executor("identity", "reset_otp")
        reg.register_executor(first)
        try:
            reg.register_executor(_make_executor("identity", "reset_otp"))
        except ExecutorRegistrationError:
            pass
        assert reg.get_executor("identity", "reset_otp") is first

    def test_registry_count_unchanged_after_duplicate(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        try:
            reg.register_executor(_make_executor("identity", "reset_otp"))
        except ExecutorRegistrationError:
            pass
        assert reg.registered_count() == 1


# ── Lookup ─────────────────────────────────────────────────────────────────────


class TestLookup:
    def test_get_executor_returns_correct_instance(self) -> None:
        reg = ActionExecutorRegistry()
        e1 = _make_executor("identity", "reset_otp")
        e2 = _make_executor("accounts", "freeze_account")
        reg.register_executor(e1)
        reg.register_executor(e2)
        assert reg.get_executor("identity", "reset_otp") is e1
        assert reg.get_executor("accounts", "freeze_account") is e2

    def test_get_executor_raises_unknown_executor_error(self) -> None:
        reg = ActionExecutorRegistry()
        with pytest.raises(UnknownExecutorError):
            reg.get_executor("identity", "reset_otp")

    def test_get_executor_raises_for_wrong_namespace(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        with pytest.raises(UnknownExecutorError):
            reg.get_executor("accounts", "reset_otp")

    def test_get_executor_raises_for_wrong_type(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        with pytest.raises(UnknownExecutorError):
            reg.get_executor("identity", "unlock_account")


# ── UnknownExecutorError attributes ───────────────────────────────────────────


class TestUnknownExecutorError:
    def test_carries_namespace(self) -> None:
        reg = ActionExecutorRegistry()
        try:
            reg.get_executor("billing", "refund")
        except UnknownExecutorError as exc:
            assert exc.action_namespace == "billing"
        else:
            pytest.fail("Expected UnknownExecutorError")

    def test_carries_action_type(self) -> None:
        reg = ActionExecutorRegistry()
        try:
            reg.get_executor("billing", "refund")
        except UnknownExecutorError as exc:
            assert exc.action_type == "refund"
        else:
            pytest.fail("Expected UnknownExecutorError")

    def test_error_message_mentions_namespace_and_type(self) -> None:
        reg = ActionExecutorRegistry()
        with pytest.raises(UnknownExecutorError, match="billing"):
            reg.get_executor("billing", "refund")

    def test_unknown_executor_error_is_key_error(self) -> None:
        reg = ActionExecutorRegistry()
        with pytest.raises(KeyError):
            reg.get_executor("billing", "refund")


# ── executor_exists ────────────────────────────────────────────────────────────


class TestExecutorExists:
    def test_exists_true_after_registration(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        assert reg.executor_exists("identity", "reset_otp") is True

    def test_exists_false_for_unregistered(self) -> None:
        reg = ActionExecutorRegistry()
        assert reg.executor_exists("identity", "reset_otp") is False

    def test_exists_false_for_wrong_namespace(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        assert reg.executor_exists("accounts", "reset_otp") is False

    def test_exists_false_for_wrong_type(self) -> None:
        reg = ActionExecutorRegistry()
        reg.register_executor(_make_executor("identity", "reset_otp"))
        assert reg.executor_exists("identity", "freeze") is False


# ── Empty registry ─────────────────────────────────────────────────────────────


class TestEmptyRegistry:
    def test_initial_count_is_zero(self) -> None:
        assert ActionExecutorRegistry().registered_count() == 0

    def test_initial_keys_is_empty(self) -> None:
        assert ActionExecutorRegistry().registered_keys() == []

    def test_independent_registries_do_not_share_state(self) -> None:
        r1 = ActionExecutorRegistry()
        r2 = ActionExecutorRegistry()
        r1.register_executor(_make_executor("identity", "reset_otp"))
        assert r2.registered_count() == 0
