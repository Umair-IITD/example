"""
case_engine/tools/framework/registry.py

Sprint 2.45: ProductionToolRegistry — thread-safe, capability-aware Tool Registry.

Capabilities:
  - register / unregister tools
  - resolve tool by name
  - resolve tool by capability (evidence_kind → tool_name → BaseTool)
  - health lookup and update
  - rich metadata storage
  - statistics aggregation
  - lifecycle state tracking (delegates to ToolLifecycleManager)
  - version tracking (per-tool version strings)

Thread-safe: all mutation and read operations under threading.Lock.

Dependency direction:
  registry.py → framework/models.py (ToolMetadata, ToolHealth, ToolAvailability, …)
  registry.py → framework/lifecycle.py (ToolLifecycleManager, ToolLifecycleStatus)
  registry.py → case_engine/tools/tool_executor.py (BaseTool) — TYPE_CHECKING
  registry.py → case_engine/tools/tool_models.py (ToolDefinition) — TYPE_CHECKING
  registry.py → stdlib only for logic
"""
from __future__ import annotations

import logging
import threading
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from case_engine.tools.framework.lifecycle import ToolLifecycleManager, ToolLifecycleStatus
from case_engine.tools.framework.models import (
    ToolAvailability,
    ToolHealth,
    ToolMetadata,
    ToolStatus,
)

if TYPE_CHECKING:
    from case_engine.tools.tool_executor import BaseTool
    from case_engine.tools.tool_models import ToolDefinition

LOGGER = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


class ProductionToolRegistry:
    """
    Thread-safe, production-grade Tool Registry.

    Responsibilities:
      - Store BaseTool implementations keyed by tool_name
      - Map evidence_kind → tool_name (capability routing)
      - Track health and availability per tool
      - Maintain per-tool execution statistics
      - Manage lifecycle state via ToolLifecycleManager
      - Provide rich metadata when registered
    """

    def __init__(self) -> None:
        self._lock:          threading.Lock                      = threading.Lock()
        self._tools:         dict[str, BaseTool]                = {}
        self._metadata:      dict[str, ToolMetadata]            = {}
        self._capability:    dict[str, str]                     = {}  # kind → tool_name
        self._health_data:   dict[str, dict[str, Any]]          = {}  # for ToolHealth construction
        self._stats:         dict[str, dict[str, int | float]]  = {}  # tool_name → stats
        self._versions:      dict[str, str]                     = {}  # tool_name → version string
        self._lifecycle:     ToolLifecycleManager               = ToolLifecycleManager()

    # ── Registration ───────────────────────────────────────────────────────────

    def register(
        self,
        tool:     "BaseTool",
        metadata: ToolMetadata | None = None,
    ) -> None:
        """
        Register a tool. Re-registration replaces the prior entry and resets to REGISTERED.
        """
        defn = tool.definition
        name = defn.tool_name
        with self._lock:
            is_replace = name in self._tools
            self._tools[name]   = tool
            self._versions[name] = defn.version
            self._stats.setdefault(name, {
                "executions": 0,
                "successes":  0,
                "failures":   0,
                "timeouts":   0,
                "total_ms":   0,
            })
            self._health_data.setdefault(name, {
                "consecutive_failures": 0,
                "last_success_at":      None,
                "last_failure_at":      None,
                "total_ms":             0,
                "exec_count":           0,
            })
            if metadata is not None:
                self._metadata[name] = metadata
        self._lifecycle.register(name)
        LOGGER.info(
            "tool_framework_registry.%s tool=%s version=%s",
            "replaced" if is_replace else "registered",
            name, defn.version if hasattr(defn, "version") else "unknown",
        )

    def register_capability(self, evidence_kind: str, tool_name: str) -> None:
        """
        Map an EvidenceKind value string to a tool_name.

        Enables capability-based routing: executor receives evidence_kind
        and resolves it to the registered tool without the caller knowing
        the tool name.
        """
        with self._lock:
            prev = self._capability.get(evidence_kind)
            self._capability[evidence_kind] = tool_name
        if prev and prev != tool_name:
            LOGGER.info(
                "tool_framework_registry.capability_overwrite kind=%s %s → %s",
                evidence_kind, prev, tool_name,
            )
        else:
            LOGGER.debug(
                "tool_framework_registry.capability_registered kind=%s tool=%s",
                evidence_kind, tool_name,
            )

    def unregister(self, tool_name: str) -> bool:
        """
        Remove a tool from the registry. Returns True if it was present.
        Does NOT automatically remove capability mappings that pointed to it.
        """
        with self._lock:
            if tool_name not in self._tools:
                return False
            del self._tools[tool_name]
            self._metadata.pop(tool_name, None)
            self._stats.pop(tool_name, None)
            self._health_data.pop(tool_name, None)
            self._versions.pop(tool_name, None)
        self._lifecycle.remove(tool_name)
        LOGGER.info("tool_framework_registry.unregistered tool=%s", tool_name)
        return True

    # ── Resolution ─────────────────────────────────────────────────────────────

    def get(self, tool_name: str) -> "BaseTool | None":
        """Return the registered BaseTool by tool_name, or None."""
        with self._lock:
            return self._tools.get(tool_name)

    def has_tool(self, tool_name: str) -> bool:
        """Return True if a tool with this name is registered."""
        with self._lock:
            return tool_name in self._tools

    def resolve_capability(self, evidence_kind: str) -> str | None:
        """Return the tool_name registered for evidence_kind, or None."""
        with self._lock:
            return self._capability.get(evidence_kind)

    def get_tool_for_capability(self, evidence_kind: str) -> "BaseTool | None":
        """
        Return the BaseTool registered for evidence_kind, or None.

        Two-step: evidence_kind → tool_name → BaseTool.
        """
        with self._lock:
            tool_name = self._capability.get(evidence_kind)
            if tool_name is None:
                return None
            return self._tools.get(tool_name)

    def list_capabilities(self) -> dict[str, str]:
        """Return a copy of the evidence_kind → tool_name mapping."""
        with self._lock:
            return dict(self._capability)

    # ── Metadata ───────────────────────────────────────────────────────────────

    def get_metadata(self, tool_name: str) -> ToolMetadata | None:
        """Return rich ToolMetadata if registered alongside the tool."""
        with self._lock:
            return self._metadata.get(tool_name)

    def get_version(self, tool_name: str) -> str | None:
        """Return the version string for a registered tool."""
        with self._lock:
            return self._versions.get(tool_name)

    # ── Health ─────────────────────────────────────────────────────────────────

    def update_health(
        self,
        tool_name:   str,
        success:     bool,
        duration_ms: int,
    ) -> None:
        """
        Record one execution outcome for health tracking.

        Updates consecutive_failures, last_success/failure timestamps,
        and aggregates latency. Automatically transitions lifecycle:
          - ≥5 consecutive failures → DEGRADED
          - ≥10 consecutive failures → FAILED
          - success after failures → READY
        """
        now = _now_iso()
        with self._lock:
            h = self._health_data.get(tool_name)
            if h is None:
                return  # tool not registered

            h["exec_count"] += 1
            h["total_ms"]   += duration_ms

            if success:
                h["consecutive_failures"] = 0
                h["last_success_at"]      = now
            else:
                h["consecutive_failures"] = h["consecutive_failures"] + 1
                h["last_failure_at"]      = now

            consecutive = h["consecutive_failures"]

        # lifecycle transitions outside lock to avoid deadlock
        current = self._lifecycle.get_status(tool_name)
        if success and current not in (
            ToolLifecycleStatus.READY, ToolLifecycleStatus.REGISTERED
        ):
            self._lifecycle.transition(tool_name, ToolLifecycleStatus.READY, "recovery")
        elif consecutive >= 10:
            if current != ToolLifecycleStatus.FAILED:
                self._lifecycle.transition(tool_name, ToolLifecycleStatus.FAILED, "too many failures")
        elif consecutive >= 5:
            if current == ToolLifecycleStatus.READY:
                self._lifecycle.transition(tool_name, ToolLifecycleStatus.DEGRADED, "elevated failures")

    def get_health(self, tool_name: str) -> ToolHealth:
        """
        Return a ToolHealth snapshot for a registered tool.

        Returns an UNKNOWN health record if the tool is not registered.
        """
        with self._lock:
            h   = self._health_data.get(tool_name, {})
            lc  = self._lifecycle.get_status(tool_name)
            cnt = h.get("exec_count", 0)
            ms  = h.get("total_ms", 0)
            avg = (ms // cnt) if cnt > 0 else 0
            confs = h.get("consecutive_failures", 0)
            last_ok  = h.get("last_success_at")
            last_err = h.get("last_failure_at")

        # availability_pct approximation from stats
        st = self._get_stats_locked(tool_name)
        total = st.get("executions", 0)
        ok    = st.get("successes", 0)
        avail = (ok / total * 100.0) if total > 0 else 100.0

        return ToolHealth(
            tool_name=tool_name,
            status=lc.value,
            last_checked_at=_now_iso(),
            consecutive_failures=confs,
            last_success_at=last_ok,
            last_failure_at=last_err,
            latency_p50_ms=avg,
            latency_p99_ms=avg,  # simplified; real P99 needs ring buffer
            availability_pct=round(avail, 2),
        )

    def get_availability(self, tool_name: str) -> ToolAvailability:
        """Return a simple ToolAvailability for a tool."""
        status = self._lifecycle.get_status(tool_name)
        usable = status in (ToolLifecycleStatus.READY, ToolLifecycleStatus.DEGRADED)
        return ToolAvailability(
            tool_name=tool_name,
            available=usable,
            reason=f"lifecycle_status={status.value}",
        )

    # ── Statistics ─────────────────────────────────────────────────────────────

    def record_execution_stats(
        self,
        tool_name:   str,
        duration_ms: int,
        *,
        success:     bool,
        timed_out:   bool = False,
        retries:     int  = 0,
    ) -> None:
        """Record execution statistics for a tool."""
        with self._lock:
            s = self._stats.get(tool_name)
            if s is None:
                return
            s["executions"] += 1
            s["total_ms"]   += duration_ms
            if success:
                s["successes"] += 1
            else:
                s["failures"] += 1
            if timed_out:
                s["timeouts"] += 1

    def get_statistics(self) -> dict[str, Any]:
        """Return aggregate and per-tool statistics."""
        with self._lock:
            total_exec     = sum(s["executions"] for s in self._stats.values())
            total_success  = sum(s["successes"]  for s in self._stats.values())
            total_failures = sum(s["failures"]   for s in self._stats.values())
            return {
                "total_registered":  len(self._tools),
                "total_executions":  total_exec,
                "total_successes":   total_success,
                "total_failures":    total_failures,
                "success_rate":      (
                    total_success / total_exec if total_exec else 0.0
                ),
                "capabilities_registered": len(self._capability),
                "per_tool": dict(self._stats),
            }

    def _get_stats_locked(self, tool_name: str) -> dict[str, int | float]:
        with self._lock:
            return dict(self._stats.get(tool_name, {}))

    # ── Lifecycle ──────────────────────────────────────────────────────────────

    def get_lifecycle_status(self, tool_name: str) -> ToolLifecycleStatus:
        """Return the current lifecycle status of a tool."""
        return self._lifecycle.get_status(tool_name)

    def set_lifecycle_status(
        self,
        tool_name:  str,
        status:     ToolLifecycleStatus,
        reason:     str = "",
    ) -> bool:
        """
        Attempt to transition a tool to the given lifecycle status.
        Returns True if the transition was applied.
        """
        return self._lifecycle.transition(tool_name, status, reason)

    def force_lifecycle_status(
        self,
        tool_name:  str,
        status:     ToolLifecycleStatus,
        reason:     str = "",
    ) -> None:
        """Force a lifecycle status (bypasses transition rules — for admin/test use)."""
        self._lifecycle.force_set(tool_name, status, reason)

    def available_tools(self) -> list[str]:
        """Return names of tools in a usable lifecycle state (READY or DEGRADED)."""
        return self._lifecycle.usable_tools()

    # ── Enumeration ────────────────────────────────────────────────────────────

    def list_tools(self) -> "list[ToolDefinition]":
        """Return definitions of all registered tools, sorted by name."""
        with self._lock:
            return sorted(
                (t.definition for t in self._tools.values()),
                key=lambda d: d.tool_name,
            )

    def tool_names(self) -> list[str]:
        """Return all registered tool names, sorted."""
        with self._lock:
            return sorted(self._tools.keys())

    def __len__(self) -> int:
        with self._lock:
            return len(self._tools)
