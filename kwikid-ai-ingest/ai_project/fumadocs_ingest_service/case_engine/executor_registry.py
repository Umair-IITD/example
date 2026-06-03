"""
case_engine/executor_registry.py

Sprint 2.2: ActionExecutorRegistry — provider executor lookup table.

The registry maps (action_namespace, action_type) → ActionExecutor instance.
It is the single source of truth for which executor handles which action.

Design decisions
────────────────
- Duplicate registrations are rejected at registration time (fail-fast).
  The alternative — silent override — masks misconfiguration bugs.
- Registry is not a singleton. It is an injectable dependency, constructed
  once and shared via ActionRuntime.
- Thread-safe for reads (dict lookup is GIL-protected in CPython).
  Write-time (registration) happens at startup, before concurrent access.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from case_engine.action_executor import ActionExecutor

LOGGER = logging.getLogger(__name__)


class ExecutorRegistrationError(RuntimeError):
    """
    Raised when executor registration violates registry invariants.

    Causes:
      - Duplicate (namespace, action_type) key — one executor per action type.
    """


class UnknownExecutorError(KeyError):
    """
    Raised when the registry cannot find an executor for (namespace, action_type).

    The runtime raises this before attempting begin_execution() so the action
    state is NOT mutated. The caller can safely retry when the executor is
    registered (e.g. after a deployment).
    """

    def __init__(self, action_namespace: str, action_type: str) -> None:
        self.action_namespace = action_namespace
        self.action_type = action_type
        super().__init__(
            f"No executor registered for namespace={action_namespace!r} "
            f"action_type={action_type!r}. "
            "Register an ActionExecutor implementation before executing actions "
            "in this namespace."
        )


class ActionExecutorRegistry:
    """
    Registry mapping (action_namespace, action_type) → ActionExecutor.

    Usage
    ─────
    At application startup, register all executor implementations:

        registry = ActionExecutorRegistry()
        registry.register_executor(IdentityResetOtpExecutor())
        registry.register_executor(AccountsFreezeAccountExecutor())

    At execution time, the ActionRuntime resolves the right executor:

        executor = registry.get_executor("identity", "reset_otp")

    Invariants
    ──────────
    - Each (namespace, action_type) pair maps to exactly one executor.
    - Duplicate registration raises ExecutorRegistrationError immediately.
    - Lookup of an unregistered key raises UnknownExecutorError.
    """

    def __init__(self) -> None:
        self._executors: dict[tuple[str, str], "ActionExecutor"] = {}

    def register_executor(self, executor: "ActionExecutor") -> None:
        """
        Register an executor for its declared (action_namespace, action_type).

        Raises:
            ExecutorRegistrationError: if a different executor is already registered
                for the same (namespace, action_type) key.
        """
        key = (executor.action_namespace, executor.action_type)
        if key in self._executors:
            existing = self._executors[key]
            raise ExecutorRegistrationError(
                f"Executor already registered for namespace={executor.action_namespace!r} "
                f"action_type={executor.action_type!r}. "
                f"Existing: {type(existing).__name__!r}, "
                f"Attempted: {type(executor).__name__!r}. "
                "De-register the existing executor or use a different action_type."
            )
        self._executors[key] = executor
        LOGGER.info(
            "executor_registry: registered %s for (%s, %s)",
            type(executor).__name__, executor.action_namespace, executor.action_type,
        )

    def get_executor(self, action_namespace: str, action_type: str) -> "ActionExecutor":
        """
        Retrieve the executor for (action_namespace, action_type).

        Raises:
            UnknownExecutorError: if no executor is registered for this key.
        """
        key = (action_namespace, action_type)
        executor = self._executors.get(key)
        if executor is None:
            raise UnknownExecutorError(action_namespace, action_type)
        return executor

    def executor_exists(self, action_namespace: str, action_type: str) -> bool:
        """Return True if an executor is registered for (action_namespace, action_type)."""
        return (action_namespace, action_type) in self._executors

    def registered_count(self) -> int:
        """Return the total number of registered (namespace, action_type) entries."""
        return len(self._executors)

    def registered_keys(self) -> list[tuple[str, str]]:
        """Return all registered (action_namespace, action_type) keys, sorted."""
        return sorted(self._executors.keys())
