"""
case_engine/tools/framework/validators.py

Sprint 2.45: Validators for the Tool Framework.

Validates:
  - ToolExecutionRequest (has tool_name or evidence_kind, valid priority)
  - ToolExecutionResult (success consistency, required fields)
  - ToolDefinition completeness (tool_name, description, required_inputs)
  - ToolHealth (status is a known value, pct in [0,100])
  - ToolRetryPolicy (max_attempts >= 1, backoff >= 0)
  - ToolTimeout (seconds > 0)
  - Dependency graph (no circular dependencies)
  - Registry state (all capability-mapped tools actually registered)

All validators return (bool, list[str]) — (valid, issues).
Never raise — return structured validation failures instead.

Dependency direction:
  validators.py → framework/models.py
  validators.py → stdlib only
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from case_engine.tools.framework.models import (
    ToolExecutionRequest,
    ToolExecutionResult,
    ToolHealth,
    ToolRetryPolicy,
    ToolTimeout,
)

if TYPE_CHECKING:
    from case_engine.tools.framework.registry import ProductionToolRegistry
    from case_engine.tools.tool_models import ToolDefinition


# ── Validators ─────────────────────────────────────────────────────────────────

class ToolFrameworkValidators:
    """
    Collection of validation helpers for the Tool Framework.

    All methods are stateless and side-effect-free.
    """

    # ── Request ────────────────────────────────────────────────────────────────

    def validate_request(
        self, request: ToolExecutionRequest
    ) -> tuple[bool, list[str]]:
        """Validate a ToolExecutionRequest before dispatch."""
        issues: list[str] = []

        if not request.tool_name and not request.evidence_kind:
            issues.append(
                "ToolExecutionRequest must specify tool_name or evidence_kind"
            )
        if not (1 <= request.priority <= 10):
            issues.append(
                f"priority must be in [1, 10], got {request.priority}"
            )
        if not request.request_id:
            issues.append("request_id must not be empty")
        if not request.context.invocation_id:
            issues.append("context.invocation_id must not be empty")

        return (len(issues) == 0, issues)

    # ── Result ─────────────────────────────────────────────────────────────────

    def validate_result(
        self, result: ToolExecutionResult
    ) -> tuple[bool, list[str]]:
        """Validate a ToolExecutionResult for consistency."""
        issues: list[str] = []

        if result.success and result.error_code is not None:
            issues.append(
                "success=True result must not have error_code set"
            )
        if not result.success and not result.error_code:
            issues.append(
                "success=False result must have error_code set"
            )
        if result.success and not result.payload and result.tool_name not in (
            "<none>", "<unresolved>", "<capability>"
        ):
            issues.append(
                "success=True result should have a non-empty payload"
            )
        if not result.invocation_id:
            issues.append("invocation_id must not be empty")
        if not result.request_id:
            issues.append("request_id must not be empty")
        if result.duration_ms < 0:
            issues.append("duration_ms must be non-negative")

        return (len(issues) == 0, issues)

    # ── Tool definition ────────────────────────────────────────────────────────

    def validate_tool_definition(
        self, definition: "ToolDefinition"
    ) -> tuple[bool, list[str]]:
        """Validate a ToolDefinition for completeness."""
        issues: list[str] = []

        if not definition.tool_name or not definition.tool_name.strip():
            issues.append("tool_name must not be empty")
        if not definition.description or len(definition.description) < 10:
            issues.append(
                "description must be at least 10 characters"
            )
        if not definition.required_inputs:
            issues.append(
                "required_inputs should not be empty — use an empty tuple explicitly if intentional"
            )
        if not definition.output_schema:
            issues.append("output_schema must not be empty")
        if not definition.version:
            issues.append("version must not be empty")

        return (len(issues) == 0, issues)

    # ── Health ─────────────────────────────────────────────────────────────────

    def validate_health(
        self, health: ToolHealth
    ) -> tuple[bool, list[str]]:
        """Validate a ToolHealth record."""
        issues: list[str] = []
        valid_statuses = {
            "REGISTERED", "READY", "DEGRADED", "FAILED",
            "DISABLED", "OFFLINE", "UNKNOWN",
        }
        if health.status not in valid_statuses:
            issues.append(
                f"status {health.status!r} is not a valid ToolStatus value"
            )
        if not (0.0 <= health.availability_pct <= 100.0):
            issues.append(
                f"availability_pct {health.availability_pct} must be in [0.0, 100.0]"
            )
        if health.consecutive_failures < 0:
            issues.append(
                "consecutive_failures must be non-negative"
            )
        if not health.tool_name:
            issues.append("tool_name must not be empty")

        return (len(issues) == 0, issues)

    # ── Retry policy ───────────────────────────────────────────────────────────

    def validate_retry_policy(
        self, policy: ToolRetryPolicy
    ) -> tuple[bool, list[str]]:
        """Validate a ToolRetryPolicy."""
        issues: list[str] = []
        if policy.max_attempts < 1:
            issues.append("max_attempts must be >= 1")
        if policy.backoff_seconds < 0:
            issues.append("backoff_seconds must be non-negative")
        return (len(issues) == 0, issues)

    # ── Timeout ────────────────────────────────────────────────────────────────

    def validate_timeout(
        self, timeout: ToolTimeout
    ) -> tuple[bool, list[str]]:
        """Validate a ToolTimeout."""
        issues: list[str] = []
        if timeout.total_seconds <= 0:
            issues.append("total_seconds must be > 0")
        if timeout.per_attempt_seconds <= 0:
            issues.append("per_attempt_seconds must be > 0")
        if timeout.grace_seconds < 0:
            issues.append("grace_seconds must be >= 0")
        return (len(issues) == 0, issues)

    # ── Registry ───────────────────────────────────────────────────────────────

    def validate_registry(
        self, registry: "ProductionToolRegistry"
    ) -> tuple[bool, list[str]]:
        """
        Validate the registry state: all capability-mapped tools are registered.
        """
        issues: list[str] = []
        cap_map = registry.list_capabilities()
        for kind, tool_name in cap_map.items():
            if not registry.has_tool(tool_name):
                issues.append(
                    f"capability '{kind}' maps to tool '{tool_name}' "
                    f"which is not registered"
                )
        return (len(issues) == 0, issues)

    # ── Dependency graph ───────────────────────────────────────────────────────

    def validate_dependency_graph(
        self,
        dependency_map: dict[str, list[str]],
    ) -> tuple[bool, list[str]]:
        """
        Detect circular dependencies in a tool → dependencies map.

        dependency_map: {tool_name: [depends_on_tool, ...]}
        """
        issues: list[str] = []
        visited: set[str] = set()
        path:    set[str] = set()

        def _dfs(node: str) -> bool:
            if node in path:
                return True  # cycle
            if node in visited:
                return False
            path.add(node)
            for dep in dependency_map.get(node, []):
                if _dfs(dep):
                    return True
            path.discard(node)
            visited.add(node)
            return False

        for tool_name in dependency_map:
            path.clear()
            if _dfs(tool_name):
                issues.append(
                    f"Circular dependency detected starting from '{tool_name}'"
                )

        return (len(issues) == 0, issues)
