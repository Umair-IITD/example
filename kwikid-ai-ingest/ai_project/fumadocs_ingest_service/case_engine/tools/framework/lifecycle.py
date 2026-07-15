"""
case_engine/tools/framework/lifecycle.py

Sprint 2.45: Tool lifecycle state machine.

States: REGISTERED → READY → DEGRADED → FAILED → DISABLED → OFFLINE → UNKNOWN
All transitions are deterministic and validated.
Thread-safe via threading.Lock.

Dependency direction:
  lifecycle.py → stdlib only
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from enum import Enum
from typing import NamedTuple

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


# ── Lifecycle states ───────────────────────────────────────────────────────────

class ToolLifecycleStatus(str, Enum):
    """
    Lifecycle state of a registered tool.

    REGISTERED: Just added to the registry; not yet validated as ready.
    READY:      Fully operational; available for execution.
    DEGRADED:   Partially functional; usable but may have elevated error rate.
    FAILED:     Consecutive failures exceeded threshold; needs recovery.
    DISABLED:   Administratively taken offline; will not be dispatched.
    OFFLINE:    Not reachable (infrastructure issue); may auto-recover.
    UNKNOWN:    Initial state before any status is known.
    """
    REGISTERED = "REGISTERED"
    READY      = "READY"
    DEGRADED   = "DEGRADED"
    FAILED     = "FAILED"
    DISABLED   = "DISABLED"
    OFFLINE    = "OFFLINE"
    UNKNOWN    = "UNKNOWN"


# Allowed transitions: from → set of valid destinations
_ALLOWED_TRANSITIONS: dict[ToolLifecycleStatus, frozenset[ToolLifecycleStatus]] = {
    ToolLifecycleStatus.REGISTERED: frozenset({
        ToolLifecycleStatus.READY,
        ToolLifecycleStatus.DISABLED,
        ToolLifecycleStatus.UNKNOWN,
    }),
    ToolLifecycleStatus.READY: frozenset({
        ToolLifecycleStatus.DEGRADED,
        ToolLifecycleStatus.FAILED,
        ToolLifecycleStatus.DISABLED,
        ToolLifecycleStatus.OFFLINE,
    }),
    ToolLifecycleStatus.DEGRADED: frozenset({
        ToolLifecycleStatus.READY,
        ToolLifecycleStatus.FAILED,
        ToolLifecycleStatus.DISABLED,
        ToolLifecycleStatus.OFFLINE,
    }),
    ToolLifecycleStatus.FAILED: frozenset({
        ToolLifecycleStatus.READY,
        ToolLifecycleStatus.DISABLED,
        ToolLifecycleStatus.OFFLINE,
    }),
    ToolLifecycleStatus.DISABLED: frozenset({
        ToolLifecycleStatus.READY,
        ToolLifecycleStatus.OFFLINE,
    }),
    ToolLifecycleStatus.OFFLINE: frozenset({
        ToolLifecycleStatus.REGISTERED,
        ToolLifecycleStatus.READY,
    }),
    ToolLifecycleStatus.UNKNOWN: frozenset({
        ToolLifecycleStatus.REGISTERED,
        ToolLifecycleStatus.READY,
        ToolLifecycleStatus.OFFLINE,
    }),
}

# States in which a tool is considered usable for execution
_USABLE_STATES: frozenset[ToolLifecycleStatus] = frozenset({
    ToolLifecycleStatus.READY,
    ToolLifecycleStatus.DEGRADED,
})


def is_valid_transition(
    from_status: ToolLifecycleStatus,
    to_status: ToolLifecycleStatus,
) -> bool:
    """Return True if the lifecycle transition from_status → to_status is allowed."""
    return to_status in _ALLOWED_TRANSITIONS.get(from_status, frozenset())


# ── Lifecycle record ───────────────────────────────────────────────────────────

class ToolLifecycleRecord(NamedTuple):
    """One entry in a tool's transition history."""
    tool_name:        str
    from_status:      ToolLifecycleStatus | None
    to_status:        ToolLifecycleStatus
    transitioned_at:  str
    reason:           str


# ── Lifecycle manager ──────────────────────────────────────────────────────────

class ToolLifecycleManager:
    """
    Thread-safe lifecycle state machine for all registered tools.

    One instance is owned by ProductionToolRegistry.
    """

    def __init__(self) -> None:
        self._lock:    threading.Lock = threading.Lock()
        self._states:  dict[str, ToolLifecycleStatus]        = {}
        self._history: dict[str, list[ToolLifecycleRecord]]  = {}

    # ── Mutation ───────────────────────────────────────────────────────────────

    def register(self, tool_name: str) -> None:
        """Mark tool as REGISTERED (initial state)."""
        with self._lock:
            prev = self._states.get(tool_name)
            self._states[tool_name] = ToolLifecycleStatus.REGISTERED
            record = ToolLifecycleRecord(
                tool_name=tool_name,
                from_status=prev,
                to_status=ToolLifecycleStatus.REGISTERED,
                transitioned_at=_now_iso(),
                reason="registered",
            )
            self._history.setdefault(tool_name, []).append(record)
        LOGGER.debug("tool_lifecycle.registered tool=%s", tool_name)

    def transition(
        self,
        tool_name:  str,
        new_status: ToolLifecycleStatus,
        reason:     str = "",
    ) -> bool:
        """
        Attempt a lifecycle transition.

        Returns True if the transition was applied.
        Returns False if the transition is invalid or the tool is not registered.
        """
        with self._lock:
            current = self._states.get(tool_name, ToolLifecycleStatus.UNKNOWN)
            if not is_valid_transition(current, new_status):
                LOGGER.warning(
                    "tool_lifecycle.invalid_transition tool=%s %s → %s",
                    tool_name, current.value, new_status.value,
                )
                return False
            self._states[tool_name] = new_status
            record = ToolLifecycleRecord(
                tool_name=tool_name,
                from_status=current,
                to_status=new_status,
                transitioned_at=_now_iso(),
                reason=reason or f"{current.value} → {new_status.value}",
            )
            self._history.setdefault(tool_name, []).append(record)

        LOGGER.debug(
            "tool_lifecycle.transition tool=%s %s → %s reason=%r",
            tool_name, current.value, new_status.value, reason,
        )
        return True

    def force_set(self, tool_name: str, status: ToolLifecycleStatus, reason: str = "") -> None:
        """
        Forcibly set status regardless of transition rules.
        Used for administrative override and test fixtures only.
        """
        with self._lock:
            prev = self._states.get(tool_name)
            self._states[tool_name] = status
            record = ToolLifecycleRecord(
                tool_name=tool_name,
                from_status=prev,
                to_status=status,
                transitioned_at=_now_iso(),
                reason=f"force_set: {reason}" if reason else "force_set",
            )
            self._history.setdefault(tool_name, []).append(record)

    def remove(self, tool_name: str) -> None:
        """Remove all lifecycle state for a tool (called on unregister)."""
        with self._lock:
            self._states.pop(tool_name, None)
            self._history.pop(tool_name, None)

    # ── Read ───────────────────────────────────────────────────────────────────

    def get_status(self, tool_name: str) -> ToolLifecycleStatus:
        """Return current status. UNKNOWN if not registered."""
        with self._lock:
            return self._states.get(tool_name, ToolLifecycleStatus.UNKNOWN)

    def is_usable(self, tool_name: str) -> bool:
        """Return True if the tool is in a usable state (READY or DEGRADED)."""
        return self.get_status(tool_name) in _USABLE_STATES

    def get_history(self, tool_name: str) -> list[ToolLifecycleRecord]:
        """Return a copy of the transition history for a tool."""
        with self._lock:
            return list(self._history.get(tool_name, []))

    def all_statuses(self) -> dict[str, ToolLifecycleStatus]:
        """Return a snapshot of all current tool statuses."""
        with self._lock:
            return dict(self._states)

    def usable_tools(self) -> list[str]:
        """Return names of all tools currently in a usable state."""
        with self._lock:
            return [
                name for name, status in self._states.items()
                if status in _USABLE_STATES
            ]
